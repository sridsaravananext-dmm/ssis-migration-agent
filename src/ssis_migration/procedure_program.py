"""Bounded DECLARE/SET/IF programs compiled to inspectable native statements."""
import re
import sqlglot
from sqlglot import exp,ErrorLevel
from ssis_migration.mapping import ConversionGap,translate_sql
from ssis_migration.bound_sql import bindable_sql,result_bindings,_result_code,require_targets,sql_call


def program(text):
    clean=bindable_sql(text);position=0;count=0
    begin=re.compile(r'\bBEGIN\b',re.I);case_end=re.compile(r'\b(CASE|END)\b',re.I)
    def block(depth=0,closing=False):
        nonlocal position,count
        if depth>16:raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Procedure nesting exceeds the supported bound.')
        nodes=[]
        while position<len(clean):
            while position<len(clean) and (clean[position].isspace() or clean[position]==';'):position+=1
            if position==len(clean):break
            word=re.match(r'[A-Za-z_]\w*',clean[position:])
            keyword=word[0].upper() if word else ''
            if keyword=='END':
                if not closing:raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Unexpected procedure END.')
                position+=3;return nodes
            if keyword=='IF':
                position+=2;start=position;level=0
                while position<len(clean):
                    if clean[position]=='(':level+=1
                    elif clean[position]==')':level-=1
                    if level==0 and begin.match(clean,position):break
                    position+=1
                if position==len(clean):raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','IF requires an explicit BEGIN/END block.')
                condition=text[start:position].strip();position+=5
                yes=block(depth+1,True)
                while position<len(clean) and (clean[position].isspace() or clean[position]==';'):position+=1
                no=[]
                if re.match(r'ELSE\b',clean[position:],re.I):
                    position+=4
                    while position<len(clean) and clean[position].isspace():position+=1
                    if not re.match(r'BEGIN\b',clean[position:],re.I):raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','ELSE requires an explicit BEGIN/END block.')
                    position+=5;no=block(depth+1,True)
                nodes.append(('if',condition,yes,no));count+=1
            else:
                start=position;level=0;case_depth=0
                while position<len(clean):
                    if clean[position]=='(':level+=1
                    elif clean[position]==')':level-=1
                    token=case_end.match(clean,position)
                    if token:
                        if token[1].upper()=='CASE':case_depth+=1
                        elif case_depth:case_depth-=1
                        elif level==0:break
                        position+=len(token[0]);continue
                    if level==0 and case_depth==0 and clean[position]==';':break
                    position+=1
                statement=text[start:position].strip()
                if not statement:raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Incomplete procedure statement.')
                try:trees=[n for n in sqlglot.parse(statement,read='tsql',error_level=ErrorLevel.RAISE) if n]
                except Exception as exc:raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Cannot parse complete procedure statement.') from exc
                nodes.extend(('sql',tree) for tree in trees);count+=len(trees)
            if count>128:raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Procedure exceeds 128 statements.')
        if closing:raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Missing procedure END.')
        return nodes
    return block()


def compile_program(body,properties,owner,connection,config,runtime):
    parent=runtime;runtime=runtime.fork();nodes=program(body);locals={};bindings={}
    for b in properties.get('parameter_bindings',[]):bindings[str(b['ParameterName']).lstrip('@').casefold()]=b['DtsVariableName']
    def declarations(nodes):
        for node in nodes:
            if node[0]=='if':declarations(node[2]);declarations(node[3]);continue
            tree=node[1]
            if isinstance(tree,exp.Declare):
                for item in tree.expressions:
                    names=item.this
                    if not isinstance(names,list) or len(names)!=1 or not isinstance(names[0],exp.Parameter):raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Expected scalar local declaration.')
                    name=names[0].name.casefold();kind=item.args.get('kind')
                    if name in locals or name in bindings or not isinstance(kind,exp.DataType) or kind.this.name not in {'INT','BIGINT','SMALLINT','TINYINT','BOOLEAN','BIT','DECIMAL','FLOAT','DOUBLE','DATE','DATETIME','DATETIME2','TIMESTAMP','VARCHAR','NVARCHAR','CHAR','NCHAR','TEXT'}:
                        raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Local variable name/type requires review.')
                    locals[name]=kind.sql(dialect='tsql')
    declarations(nodes)
    results=result_bindings(properties);uses=[];serial=0
    def native(text):
        args={};edits=[]
        for m in re.finditer(r'(?<!@)@([A-Za-z_]\w*)',bindable_sql(text)):
            key=m[1].casefold();marker='ssis_p'+str(len(args))
            if key in locals:value=f'_ssis_proc_values[{key!r}]';replacement=f'CAST(:{marker} AS {locals[key]})'
            elif key in bindings:value=runtime.read(bindings[key],owner);replacement=':'+marker
            else:raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Undeclared local/formal SQL variable: '+key)
            args[marker]=value;edits.append((m.start(),m.end(),replacement))
        for a,b,v in reversed(edits):text=text[:a]+v+text[b:]
        emitted,mapped=translate_sql(text,connection,config,parameter_names=args.keys());require_targets(mapped);uses.extend(mapped)
        return sql_call(emitted,args)
    def scalar(query,target,indent):
        nonlocal serial
        serial+=1;frame='_ssis_proc_frame_'+str(serial)
        return [' '*indent+frame+' = '+native(query),' '*indent+frame+' = '+frame+'.limit(2).collect()',
            ' '*indent+f'if len({frame}) != 1:', ' '*(indent+4)+"raise RuntimeError('Procedure scalar query requires exactly one row.')",
            ' '*indent+target+' = '+frame+'[0][0]']
    def emit(nodes,indent=0,root=True):
        lines=[]
        for i,node in enumerate(nodes):
            if node[0]=='if':
                lines+=scalar('SELECT CASE WHEN '+node[1]+' THEN TRUE ELSE FALSE END','_ssis_proc_condition',indent)
                lines+=[' '*indent+'if _ssis_proc_condition:']+(emit(node[2],indent+4,False) or [' '*(indent+4)+'pass'])
                if node[3]:lines+=[' '*indent+'else:']+emit(node[3],indent+4,False)
                continue
            tree=node[1]
            if isinstance(tree,exp.Declare):
                for item in tree.expressions:
                    name=item.this[0].name.casefold();default=item.args.get('default')
                    if isinstance(default,exp.Expression):lines+=scalar('SELECT CAST('+default.sql(dialect='tsql')+' AS '+locals[name]+')',f'_ssis_proc_values[{name!r}]',indent)
            elif isinstance(tree,exp.Set):
                for item in tree.expressions:
                    eq=item.this
                    if isinstance(eq,exp.EQ) and isinstance(eq.this,exp.Column) and eq.this.name.upper()=='NOCOUNT' and getattr(eq.expression,'name','').upper()=='ON':
                        continue  # Spark does not emit SQL Server DONE rowcount messages.
                    if not isinstance(eq,exp.EQ) or not isinstance(eq.this,exp.Parameter) or eq.this.name.casefold() not in locals:
                        raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Only assignments to declared scalar locals are supported; session SET remains pending.')
                    name=eq.this.name.casefold()
                    lines+=scalar('SELECT CAST('+eq.expression.sql(dialect='tsql')+' AS '+locals[name]+')',f'_ssis_proc_values[{name!r}]',indent)
            elif isinstance(tree,(exp.Query,exp.Insert,exp.Update,exp.Delete,exp.Merge,exp.TruncateTable)):
                if isinstance(tree,exp.Query) and any(isinstance(n,exp.EQ) and isinstance(n.this,exp.Parameter) for n in tree.expressions):
                    raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','SELECT variable assignment requires explicit row/ordering semantics.')
                last=root and i==len(nodes)-1 and isinstance(tree,exp.Query)
                call=native(tree.sql(dialect='tsql'))
                lines+=[' '*indent+'_ssis_proc_result = '+call]
                if last and results:lines+=[' '*indent+s for s in _result_code('_ssis_proc_result',results,owner,runtime)]
                elif isinstance(tree,exp.Query) and results:raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Only a final unconditional query may supply result bindings.')
            else:raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Unsupported procedure statement: '+type(tree).__name__)
        return lines
    if results and (not nodes or nodes[-1][0]!='sql' or not isinstance(nodes[-1][1],exp.Query)):
        raise ConversionGap('PROCEDURE_PROGRAM_UNSUPPORTED','Single-row procedure results require a final unconditional query.')
    lines=['_ssis_proc_values = '+repr({name:None for name in locals})]+emit(nodes)
    parent.adopt(runtime)
    return '\n'.join(lines),uses,{'procedure_program':True,'batch':True,'identity_review':False,'local_types':locals}
