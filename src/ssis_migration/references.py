"""Read supporting notebook archives as evidence, without importing or running them."""
from __future__ import annotations
import ast
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
from urllib.parse import unquote
import zipfile

import sqlglot
from sqlglot import exp, ErrorLevel

MAX_FILE=4*1024*1024
MAX_TOTAL=32*1024*1024
MAX_MEMBERS=512
SUPPORTED={'.py','.html','.ipynb','.sql','.txt'}


def digest(raw):return hashlib.sha256(raw).hexdigest()


def decode(raw):
    if len(raw)>MAX_FILE:raise ValueError('Reference file exceeds the size limit.')
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')


def archive_members(raw):
    if len(raw)>MAX_TOTAL:raise ValueError('Reference archive exceeds the size limit.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist()
        if len(infos)>MAX_MEMBERS or sum(i.file_size for i in infos)>MAX_TOTAL:
            raise ValueError('Reference archive exceeds member/expanded-size limits.')
        seen=set()
        for info in infos:
            name=info.filename.replace('\\','/')
            if name.startswith('/') or '..' in PurePosixPath(name).parts or re.match(r'^[A-Za-z]:',name):
                raise ValueError('Reference archive contains an unsafe member path.')
            if stat.S_ISLNK(info.external_attr>>16) or info.flag_bits & 1:
                raise ValueError('Symlink/encrypted reference archive members are unsupported.')
            if info.is_dir():continue
            key=name.casefold()
            if key in seen:raise ValueError('Reference archive contains duplicate member paths.')
            seen.add(key)
            if info.file_size>MAX_FILE:raise ValueError('Reference archive member exceeds the size limit.')
            if name.casefold().endswith('.zip'):raise ValueError('Nested reference archives are unsupported.')
            yield name,archive.read(info)


def cells(name,raw):
    suffix=PurePosixPath(name).suffix.casefold();text=decode(raw);default_language='python'
    if suffix=='.html':
        match=re.search(r'__DATABRICKS_NOTEBOOK_MODEL\s*=\s*[\x27\"]([A-Za-z0-9+/=\s]+)[\x27\"]',text)
        if not match:raise ValueError('HTML reference lacks a supported Databricks notebook model.')
        model=json.loads(unquote(base64.b64decode(''.join(match[1].split()),validate=True).decode('utf-8')))
        if not isinstance(model,dict) or not isinstance(model.get('commands'),list):raise ValueError('Invalid Databricks HTML notebook model.')
        default_language=str(model.get('language','python')).casefold()
        source=[]
        for command in model['commands']:
            if not isinstance(command,dict) or not isinstance(command.get('command',''),str):raise ValueError('Invalid notebook command.')
            source.append(command.get('command',''))
    elif suffix=='.ipynb':
        model=json.loads(text)
        if not isinstance(model,dict) or not isinstance(model.get('cells'),list):raise ValueError('Invalid IPython notebook model.')
        source=[]
        for cell in model['cells']:
            if not isinstance(cell,dict):raise ValueError('Invalid notebook cell.')
            if cell.get('cell_type')!='code':continue
            value=cell.get('source',[])
            if isinstance(value,str):source.append(value)
            elif isinstance(value,list) and all(isinstance(v,str) for v in value):source.append(''.join(value))
            else:raise ValueError('Invalid notebook code source.')
    elif suffix=='.py':
        source=[]
        for cell in re.split(r'^# COMMAND -+\s*$',text,flags=re.M):
            lines=[line[8:] if line.startswith('# MAGIC ') else '' if line=='# MAGIC' else line for line in cell.splitlines()]
            source.append('\n'.join(line for line in lines if not line.startswith(('# Databricks notebook source','# DBTITLE '))))
    else:source=['%sql\n'+text]
    if len(source)>MAX_MEMBERS or sum(len(s.encode()) for s in source)>MAX_TOTAL:raise ValueError('Notebook source exceeds supported limits.')
    result=[]
    for number,source_text in enumerate(source,1):
        source_text=source_text.strip()
        if not source_text:continue
        match=re.match(r'%([A-Za-z]+)\b[ \t]*',source_text)
        language=match[1].casefold() if match else default_language
        result.append({'cell':number,'language':language,'text':source_text[match.end():] if match else source_text})
    return result


def _template(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,str):return node.value
    if isinstance(node,ast.JoinedStr):return ''.join(v.value if isinstance(v,ast.Constant) else '${python_value}' for v in node.values)
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):return _template(node.left)+_template(node.right)
    return '${python_expression}'


def _sql_name(text):
    # Preserve template identities in evidence. Replace them only in a local
    # parser view; no widget, Python, or notebook expression is evaluated.
    return re.sub(r'\$\{[^{}]+\}','reference_value',text)


def _column(column):
    constraints=column.args.get('constraints',[])
    return {'name':column.name,'data_type':column.args['kind'].sql(dialect='databricks') if column.args.get('kind') else None,
        'nullable':not any(isinstance(c.args.get('kind'),exp.NotNullColumnConstraint) for c in constraints)}


def _sql_evidence(text,cell):
    data={'tables':[],'table_changes':[],'inserts':[],'udfs':[],'statements':[],'parse_review':False}
    clean=_sql_name(text)
    try:trees=[t for t in sqlglot.parse(clean,read='databricks',error_level=ErrorLevel.RAISE) if t]
    except Exception:trees=[];data['parse_review']=True
    for tree in trees:
        data['statements'].append(type(tree).__name__)
        if isinstance(tree,exp.Command):data['parse_review']=True
        if isinstance(tree,exp.Create) and str(tree.args.get('kind','')).upper()=='TABLE' and isinstance(tree.this,exp.Schema):
            target=tree.this.this
            if not isinstance(target,exp.Table):continue
            columns=[]
            for column in tree.this.expressions:
                if isinstance(column,exp.ColumnDef):columns.append(_column(column))
            data['tables'].append({'name':target.name,'parser_location':target.sql(dialect='databricks'),'columns':columns,'cell':cell,
                'templated':bool(re.search(r'\$\{',text)),'template_symbols':sorted(set(re.findall(r'\$\{([^{}]+)\}',text)))})
        if isinstance(tree,exp.Create) and str(tree.args.get('kind','')).upper()=='FUNCTION':
            data['udfs'].append({'cell':cell,'kind':'USER_DEFINED_FUNCTION'})
        if isinstance(tree,exp.Alter) and isinstance(tree.this,exp.Table):
            actions=tree.args.get('actions',[])
            added=[_column(action) for action in actions if isinstance(action,exp.ColumnDef)]
            if added:data['table_changes'].append({'name':tree.this.name,'parser_location':tree.this.sql(dialect='databricks'),
                'added_columns':added,'cell':cell})
            if any(isinstance(a,(exp.Drop,exp.AlterColumn,exp.RenameColumn)) for a in actions):data['parse_review']=True
    # A Python-built INSERT can have an unparsed SELECT/VALUES tail while its
    # target and explicit column contract are still visible and reviewable.
    from ssis_migration.sql_text import mask_sql
    ident=r'(?:`(?:``|[^`])+`|[A-Za-z_]\w*)'
    for match in re.finditer(rf'\bINSERT\s+INTO\s+({ident}(?:\s*\.\s*{ident}){{0,2}})\s*\(([^()]*)\)',mask_sql(clean),re.I):
        names=[n.strip().strip('`') for n in match[2].split(',')]
        if names and all(re.fullmatch(r'[A-Za-z_]\w*',n) for n in names):
            data['inserts'].append({'name':match[1].split('.')[-1].strip('` '),'parser_location':match[1],'columns':names,'cell':cell})
    return data


def inspect_file(name,raw):
    record={'member':name,'sha256':digest(raw),'kind':'other','notebook_cells':0,'symbols':[],'includes':[],
        'tables':[],'table_changes':[],'inserts':[],'udfs':[],'statement_kinds':[],'findings':[]}
    if PurePosixPath(name).suffix.casefold() not in SUPPORTED:return record
    record['kind']='notebook_reference' if PurePosixPath(name).suffix.casefold() in {'.py','.html','.ipynb'} else 'sql_reference'
    notebook_cells=cells(name,raw);record['notebook_cells']=len(notebook_cells)
    for cell in notebook_cells:
        sql=[]
        if cell['language']=='sql':sql.append(cell['text'])
        elif cell['language']=='run':
            try:
                arguments=shlex.split(cell['text'].strip())
                if not arguments or any(token in arguments[0] for token in ('://','?','`','${')):raise ValueError('Dynamic include')
                record['includes'].append({'path':arguments[0],'has_arguments':len(arguments)>1,'cell':cell['cell']})
            except ValueError:record['findings'].append({'code':'REFERENCE_INCLUDE_REVIEW','cell':cell['cell']})
        elif cell['language']=='python':
            try:tree=ast.parse(cell['text'])
            except (SyntaxError,ValueError):
                record['findings'].append({'code':'REFERENCE_PYTHON_PARSE_REVIEW','cell':cell['cell']});continue
            from ssis_migration.validator.native_spark import validate_native_spark
            checked=validate_native_spark(cell['text'])
            for issue in checked['issues']:
                if issue['code']=='UNBOUNDED_DRIVER_COLLECTION' or (issue['code']=='UDF_POLICY_VIOLATION' and 'imports' not in issue['message']):
                    record['findings'].append({'code':'REFERENCE_'+issue['code'],'cell':cell['cell'],'line':issue.get('line'),
                        'message':issue['message']})
            parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    args=node.args
                    ancestor=parents.get(node);owners=[]
                    while ancestor is not None:
                        if isinstance(ancestor,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):owners.append(ancestor.name)
                        ancestor=parents.get(ancestor)
                    record['symbols'].append({'name':node.name,'qualified_name':'.'.join(list(reversed(owners))+[node.name]),'kind':'function','cell':cell['cell'],
                        'arguments':[a.arg for a in args.posonlyargs+args.args+args.kwonlyargs],
                        'varargs':bool(args.vararg),'kwargs':bool(args.kwarg)})
                elif isinstance(node,ast.ClassDef):record['symbols'].append({'name':node.name,'kind':'class','cell':cell['cell']})
                elif isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='sql' and isinstance(node.func.value,ast.Name) and node.func.value.id=='spark' and node.args:
                    sql.append(_template(node.args[0]))
        elif cell['language'] not in {'md','markdown'}:record['findings'].append({'code':'REFERENCE_LANGUAGE_REVIEW','cell':cell['cell']})
        for statement in sql:
            if statement=='${python_expression}':
                record['findings'].append({'code':'REFERENCE_SQL_DYNAMIC_REVIEW','cell':cell['cell']});continue
            evidence=_sql_evidence(statement,cell['cell'])
            for key in ('tables','inserts','udfs'):record[key].extend(evidence[key])
            for change in evidence['table_changes']:
                record['table_changes'].append(change)
                matching=[t for t in record['tables'] if t['parser_location']==change['parser_location']]
                if matching:
                    table=matching[-1];known={c['name'].casefold() for c in table['columns']}
                    table['columns'].extend(c for c in change['added_columns'] if c['name'].casefold() not in known)
                else:record['findings'].append({'code':'REFERENCE_DDL_EVOLUTION_REVIEW','cell':cell['cell']})
            record['statement_kinds'].extend(evidence['statements'])
            if evidence['parse_review']:record['findings'].append({'code':'REFERENCE_SQL_TEMPLATE_REVIEW','cell':cell['cell']})
    if record['tables']:record['kind']='definition_notebook' if record['kind']=='notebook_reference' else 'sql_reference'
    elif record['udfs']:record['kind']='function_notebook'
    elif record['symbols']:record['kind']='library_notebook'
    elif record['kind']=='notebook_reference':record['kind']='configuration_notebook'
    if record['udfs']:record['findings'].append({'code':'REFERENCE_UDF_NOT_APPLIED','count':len(record['udfs'])})
    return record


def inspect_references(paths):
    inputs=[];files=[];issues=[];total=0;expanded=0
    prepared=[];input_bytes=0
    for value in paths:
        path=Path(value)
        if path.stat().st_size>MAX_TOTAL:raise ValueError('Reference input exceeds the size limit.')
        raw=path.read_bytes();input_bytes+=len(raw)
        if input_bytes>MAX_TOTAL:raise ValueError('Combined reference inputs exceed the size limit.')
        prepared.append((path,raw))
    for path,raw in sorted(prepared,key=lambda p:(p[0].name,digest(p[1]))):
        total+=len(raw)
        if total>MAX_TOTAL:raise ValueError('Combined reference inputs exceed the size limit.')
        source={'name':path.name,'sha256':digest(raw)};inputs.append(source)
        members=list(archive_members(raw)) if path.suffix.casefold()=='.zip' else [(path.name,raw)]
        expanded+=sum(len(r) for _,r in members)
        if expanded>MAX_TOTAL:raise ValueError('Combined reference expansion exceeds the size limit.')
        for name,content in members:
            try:record=inspect_file(name,content)
            except (ValueError,UnicodeError,RecursionError) as exc:
                record={'member':name,'sha256':digest(content),'kind':'uninspected','symbols':[],'includes':[],'tables':[],'inserts':[],'udfs':[],
                    'findings':[{'code':'REFERENCE_FORMAT_REVIEW','message':type(exc).__name__}]}
            record['input']=source
            record['reference_id']=digest((source['sha256']+'|'+name).encode())[:24]
            files.append(record)
    definitions={}
    for file in files:
        for table in file['tables']:
            if 'reference_value' not in table['name']:definitions.setdefault(table['name'].casefold(),[]).append((file,table))
    for file in files:
        for insert in file['inserts']:
            candidates=definitions.get(insert['name'].casefold(),[])
            signatures={tuple(sorted(c['name'].casefold() for c in t['columns'])) for _,t in candidates}
            if len(signatures)==1:
                missing=sorted({c.casefold() for c in insert['columns']}-set(next(iter(signatures))))
                if missing:issues.append({'code':'REFERENCE_INSERT_COLUMN_MISMATCH','reference_id':file['reference_id'],'cell':insert['cell'],
                    'table_name':insert['name'],'missing_columns':missing,'definition_reference_ids':sorted({f['reference_id'] for f,_ in candidates}),
                    'basis':'Unique column signature by table name; physical template identity still requires review.'})
            elif len(signatures)>1:issues.append({'code':'REFERENCE_TABLE_DEFINITION_AMBIGUOUS','reference_id':file['reference_id'],'table_name':insert['name']})
    for file in files:
        for include in file['includes']:
            leaf=PurePosixPath(include['path'].strip('"\x27')).name.removesuffix('.py')
            candidates=sorted({f['reference_id'] for f in files if PurePosixPath(f['member']).stem==leaf})
            include['candidate_reference_ids']=candidates
            include['resolution']='UNIQUE_CANDIDATE' if len(candidates)==1 else 'AMBIGUOUS' if candidates else 'UNAVAILABLE'
            if len(candidates)!=1:issues.append({'code':'REFERENCE_INCLUDE_'+include['resolution'],'reference_id':file['reference_id'],
                'cell':include['cell'],'include_path':include['path']})
        issues.extend({**issue,'reference_id':file['reference_id']} for issue in file['findings'])
    grouped={}
    for issue in issues:
        key=json.dumps(issue,sort_keys=True)
        if key not in grouped:grouped[key]=dict(issue,occurrences=0)
        grouped[key]['occurrences']+=1
    issues=list(grouped.values())
    return {'status':'REFERENCE_ONLY','executed':False,'applied_as_legacy_definitions':False,'inputs':inputs,'files':files,'findings':issues,
        'summary':{'input_count':len(inputs),'file_count':len(files),'table_definition_count':sum(len(f['tables']) for f in files),
            'finding_count':len(issues)},'note':'Reference metadata only. No notebook, library, DDL, UDF, or archive code was executed or copied into generated workload code.'}


def review_text(inventory):
    lines=['# Supporting notebook reference review','','Status: REFERENCE_ONLY. Files were inspected as data; no code was executed.',
        'These target notebooks do not supply missing SQL Server procedure bodies or confirm source/target equivalence.','',
        '| Input archive/file | Member | Role |','|---|---|---|']
    for file in inventory['files']:lines.append(f"| {file['input']['name']} | {file['member']} | {file['kind']} |")
    lines+=['','## Contract findings','',
        'Table-name comparisons are review candidates; dynamic catalog/schema templates have not been assigned physical identities.']
    by_id={file['reference_id']:file for file in inventory['files']}
    for finding in inventory['findings']:
        detail=(' Missing columns: '+', '.join(finding['missing_columns'])) if finding.get('missing_columns') else ''
        file=by_id[finding['reference_id']]
        location=f"{file['input']['name']} :: {file['member']}"
        if finding.get('cell') is not None:location+=f", cell {finding['cell']}"
        if finding.get('line') is not None:location+=f", line {finding['line']}"
        if finding.get('table_name'):detail=f" Table: {finding['table_name']}."+detail
        if finding.get('message'):detail+=' '+finding['message']
        if finding.get('include_path'):detail+=' Include: '+finding['include_path']
        lines.append(f"- **{finding['code']}** — `{location}`.{detail}")
    if not inventory['findings']:lines.append('No reference contract findings were detected by the bounded inspector.')
    return '\n'.join(lines)+'\n'
