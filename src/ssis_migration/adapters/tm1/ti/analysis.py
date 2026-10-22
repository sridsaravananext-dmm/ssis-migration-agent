"""Evidence-bound TI section/control-flow and declared side-effect analysis.

No source code is evaluated, parameter/row bindings inferred, or external
process/SQL/command executed. All target/runtime behavior remains unverified.
"""
from __future__ import annotations

import hashlib

from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import SchemaValidationError, validate_document
from ..syntax import TM1SyntaxError
from .parser import Parser, SECTIONS
from .control_flow import flatten, build_control_flow

_CONFIG = {'DATASOURCETYPE','DATASOURCENAMEFORSERVER','DATASOURCENAMEFORCLIENT','DATASOURCEQUERY',
           'DATASOURCECUBEVIEW','DATASOURCEASCIIDELIMITERCHARACTER','DATASOURCEASCIIDECIMALSEPARATOR',
           'DATASOURCEASCIIHEADERRECORDS','DATASOURCEASCIITHOUSANDSSEPARATOR'}
_EFFECTS = {
    'CELLGETN':('CUBE_READ',0,'CUBE_SCHEMA',False),'CELLGETS':('CUBE_READ',0,'CUBE_SCHEMA',False),
    'CELLPUTN':('CUBE_WRITE',1,'CUBE_SCHEMA_AND_WRITE_BINDINGS',False),
    'CELLPUTS':('CUBE_WRITE',1,'CUBE_SCHEMA_AND_WRITE_BINDINGS',False),
    'CELLINCREMENTN':('CUBE_INCREMENT',1,'CUBE_SCHEMA_AND_WRITE_BINDINGS',False),
    'EXECUTEPROCESS':('SYNCHRONOUS_PROCESS_CALL',0,'CHILD_PROCESS_DEFINITION',False),
    'RUNPROCESS':('ASYNCHRONOUS_PROCESS_CALL',0,'CHILD_PROCESS_DEFINITION',False),
    'ASCIIOUTPUT':('FILE_WRITE',0,'FILE_MAPPING_AND_ENCODING',False),
    'TEXTOUTPUT':('FILE_WRITE',0,'FILE_MAPPING_AND_ENCODING',False),
    'ODBCOPEN':('CONNECTION_OPEN',0,'CONNECTION_MAPPING',False),
    'ODBCOUTPUT':('EXTERNAL_SQL',0,'CONNECTION_AND_SQL_DIALECT',False),
    'ODBCCLOSE':('CONNECTION_CLOSE',0,'CONNECTION_MAPPING',False),
    'EXECUTECOMMAND':('EXTERNAL_COMMAND',0,'EXTERNAL_COMMAND_REDESIGN',True),
    'VIEWZEROOUT':('DESTRUCTIVE_CUBE_ACTION',0,'DESTRUCTIVE_ACTION_REVIEW',True),
    'CUBECLEARDATA':('DESTRUCTIVE_CUBE_ACTION',0,'DESTRUCTIVE_ACTION_REVIEW',True),
    'CUBEDESTROY':('DESTRUCTIVE_CUBE_ACTION',0,'DESTRUCTIVE_ACTION_REVIEW',True),
    'DIMENSIONDESTROY':('DESTRUCTIVE_DIMENSION_ACTION',0,'DESTRUCTIVE_ACTION_REVIEW',True),
    'SUBSETDESTROY':('DESTRUCTIVE_SUBSET_ACTION',0,'DESTRUCTIVE_ACTION_REVIEW',True),
    'DIMENSIONELEMENTINSERT':('DIMENSION_MUTATION',0,'DIMENSION_BINDINGS',False),
    'VIEWCREATE':('VIEW_MUTATION',0,'CUBE_VIEW_DEFINITION',False),
    'SUBSETCREATE':('SUBSET_MUTATION',0,'DIMENSION_SUBSET_DEFINITION',False),
}


def ti_analysis_id(report):
    return 'ti-'+content_digest({key:value for key,value in report.items() if key!='analysis_id'})


def _literal(node):
    return node['value'] if node['kind']=='literal' and node['literal_type']=='STRING' else None


def _walk(nodes):
    stack=list(reversed(nodes))
    while stack:
        value=stack.pop()
        if isinstance(value,dict):
            if 'kind' in value and 'span' in value:yield value
            for part in reversed(list(value.values())):
                if isinstance(part,(dict,list)):stack.append(part)
        elif isinstance(value,list):stack.extend(reversed(value))


def analyze_ti_sections(sections: dict, *, export_format: str, process_name: str | None = None,
                        source_export_version: str | None = None) -> dict:
    """Analyze explicitly labelled section text, preserving missing vs empty.

    Each supplied section record has exactly text, file_id and locator. The
    process/version, when provided, are declarations from supplied metadata.
    """
    if export_format!='ti_section_files_v1':raise ValueError('Explicit ti_section_files_v1 format is required; native serialized .pro is not decoded.')
    if not isinstance(sections,dict) or set(sections)-set(SECTIONS):raise ValueError('Only explicitly named prolog, metadata, data and epilog sections are supported.')
    if process_name is not None and (not isinstance(process_name,str) or not process_name):raise ValueError('Process name must be a supplied nonempty declaration or None.')
    if source_export_version is not None and (not isinstance(source_export_version,str) or not source_export_version):raise ValueError('Source version must be a supplied nonempty declaration or None.')
    report={'analysis_version':'1.0','language':'tm1_turbointegrator','export_format':export_format,
            'process_name':process_name,'source_export_version':source_export_version,
            'status':'STRUCTURED','scope':'BOUNDED_TI_SECTION_STRUCTURE','semantic_status':'NOT_VERIFIED',
            'migration_strategy':'REVIEW_REQUIRED','migration_accepted':False,'manifest_file':None,
            'section_order':list(SECTIONS),'missing_sections':[name for name in SECTIONS if name not in sections],
            'lifecycle_status':'SOURCE_DEPENDENT_NOT_VERIFIED','incremental_logic':'NOT_DETERMINED',
            'sections':[],'references':[],'dependencies':[],'effects':[],'configuration_assignments':[],
            'features':[],'diagnostics':[],'assumptions':[],
            'validation_requirements':[
                'Supply source version, original process metadata, parameters, variable roles/types and datasource mappings.',
                'Verify separate Metadata/Data source-record passes; do not combine them into one row loop.',
                'Resolve child processes, return codes, asynchronous completion and early process/record controls.',
                'Review write/clear/external command behavior, transaction/error boundaries and runtime lifecycle.',
                'Require source review, critical-rule gates, controlled execution and data reconciliation before acceptance.']}
    def evidence(section,span,rule):
        source=sections[section]
        return {'file_id':source['file_id'],'locator':source['locator']+'#ti-chars='+str(span['start'])+':'+str(span['end']),'rule':rule}
    def diagnostic(code,message,section=None,span=None):
        report['diagnostics'].append({'code':code,'severity':'WARNING','message':message,'section':section,'span':span,
                                      'evidence':evidence(section,span,code) if section is not None and span is not None else None})
    def dependency(kind,value=None,section=None,span=None,details=None):
        report['dependencies'].append({'dependency_id':'ti-dep-'+content_digest([section,kind,span,value])[:24],
                                      'kind':kind,'declared_value':value,'status':'NOT_SUPPLIED','details':details or {},
                                      'section':section,'span':span,
                                      'evidence':evidence(section,span,'ti_dependency_requirement') if section is not None and span is not None else None})
    for kind in ['PROCESS_AND_VERSION_METADATA','PARAMETERS_AND_VARIABLE_BINDINGS','DATASOURCE_AND_SECTION_LIFECYCLE',
                 'ERROR_TRANSACTION_AND_RETURN_CONTRACT','EXECUTION_AND_DATA_EQUIVALENCE']:dependency(kind)
    features=set();statuses=[]
    for name in SECTIONS:
        source=sections.get(name)
        record={'name':name,'iteration_scope':'SOURCE_DEPENDENT_SEPARATE_RECORD_PASS' if name in {'metadata','data'} else 'NOMINAL_PROCESS_STAGE',
                'status':'MISSING','structure_complete':False,'source':None,'source_file':None,'ast':[],'statements':[],'control_flow':None}
        if name not in sections:
            diagnostic('MISSING_TI_SECTION','Required section is absent, not an inferred empty section: '+name)
            statuses.append('UNSUPPORTED');report['sections'].append(record);continue
        if (not isinstance(source,dict) or set(source)!={'text','file_id','locator'} or not isinstance(source['text'],str)
            or not isinstance(source['file_id'],str) or not source['file_id'] or not isinstance(source['locator'],str) or not source['locator']):
            raise ValueError('Each TI section requires exactly decoded text, original file_id and locator.')
        raw=source['text'].encode('utf-8')
        record['source']={**source,'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw),
                          'digest_scope':'DECODED_UTF8_TEXT','offset_unit':'UNICODE_CODE_POINTS'}
        record['status']='STRUCTURED'
        try:
            parser=Parser(source['text']);ast=parser.document();record['ast']=ast;record['structure_complete']=True
            for finding in parser.findings:diagnostic(finding['code'],finding['message'],name,finding['span'])
            if parser.findings:record['status']='UNSUPPORTED'
            record['statements']=flatten(name,ast);record['control_flow']=build_control_flow(name,ast)
            for node in _walk(ast):
                kind=node['kind'];span=node['span']
                if kind in {'assignment','variable'}:
                    refkind='SYMBOL_WRITE' if kind=='assignment' else 'SYMBOL_READ'
                    report['references'].append({'reference_id':'ti-ref-'+content_digest([name,refkind,span])[:24],
                                                 'kind':refkind,'value':node['name'],'section':name,'span':span,
                                                 'resolution':'UNRESOLVED','evidence':evidence(name,span,'ti_symbol_reference')})
                    dependency('SYMBOL_BINDING',node['name'],name,span,{'access':refkind})
                    if kind=='assignment' and node['name'].upper() in _CONFIG:
                        features.add('DATASOURCE_CONFIGURATION')
                        report['configuration_assignments'].append({'name':node['name'],'value_expression':node['value'],
                                                                    'literal_string':_literal(node['value']),'section':name,'span':span,
                                                                    'verification':'NOT_VERIFIED','evidence':evidence(name,span,'ti_explicit_configuration_assignment')})
                if kind in {'if','while'}:
                    features.add('CONTROL:'+kind.upper())
                    if kind=='while':dependency('LOOP_TERMINATION_REVIEW',None,name,span)
                if kind=='control' or (kind=='call' and node['name'].upper()=='ITEMREJECT'):
                    control=node['name'].upper();features.add('CONTROL:'+control)
                    dependency('EARLY_CONTROL_RUNTIME_TARGET',control,name,span)
                    diagnostic('UNRESOLVED_CONTROL_TRANSFER','Early control target and lifecycle effects remain unresolved: '+control,name,span)
                if kind=='call':
                    upper=node['name'].upper();features.add('CALL:'+upper)
                    if upper in _EFFECTS:
                        effect,index,dep,redesign=_EFFECTS[upper]
                        target=node['arguments'][index] if len(node['arguments'])>index else None
                        literal=_literal(target) if target is not None else None
                        report['effects'].append({'call_name':node['name'],'kind':effect,'target_literal':literal,
                                                  'section':name,'span':span,'runtime_status':'NOT_VERIFIED','requires_redesign':redesign,
                                                  'evidence':evidence(name,span,'ti_declared_call_site_not_execution')})
                        dependency(dep,literal,name,span,{'target_expression':target,'argument_count':len(node['arguments'])})
                        if literal is None or literal=='':
                            diagnostic('UNRESOLVED_CALL_TARGET','Call target is dynamic/empty; no object or file path was invented.',name,span)
                        if upper=='RUNPROCESS':dependency('ASYNCHRONOUS_COMPLETION_REVIEW',literal,name,span)
                        if upper=='EXECUTEPROCESS':dependency('PROCESS_RETURN_STATUS_REVIEW',literal,name,span)
                        if upper=='ODBCOUTPUT':diagnostic('UNRESOLVED_SQL_DIALECT','Embedded SQL is preserved as an argument; no SQL dialect or translation is inferred.',name,span)
                        if redesign:diagnostic('REVIEW_EXTERNAL_OR_DESTRUCTIVE_EFFECT','External/destructive call requires explicit design review; nothing was executed.',name,span)
        except TM1SyntaxError as exc:
            record['status']=exc.status;record['structure_complete']=False;record['ast']=[];record['statements']=[];record['control_flow']=None
            diagnostic(exc.code,str(exc),name,{'start':exc.start,'end':exc.end})
        statuses.append(record['status']);report['sections'].append(record)
    for status in ('LIMIT_EXCEEDED','INVALID','UNSUPPORTED'):
        if status in statuses:report['status']=status;report['migration_strategy']='UNSUPPORTED';break
    report['features']=sorted(features)
    diagnostic('UNVERIFIED_TI_RUNTIME','Section structure does not establish variable binding, execution, incremental logic or target equivalence.')
    report['analysis_id']=ti_analysis_id(report);validate_ti_analysis(report)
    return report



def _validate_statement_order(nodes, start, end):
    previous=start
    for node in nodes:
        span=node['span']
        if not previous<=span['start']<=span['end']<=end:raise SchemaValidationError('TI source statement order/containment is inconsistent.')
        previous=span['end']
        if node['kind']=='while':
            condition=node['condition']['span']
            if not span['start']<=condition['start']<=condition['end']<=span['end']:raise SchemaValidationError('TI loop condition is outside its source block.')
            _validate_statement_order(node['body'],condition['end'],span['end'])
        elif node['kind']=='if':
            branch_end=span['start']
            for i,branch in enumerate(node['branches']):
                condition=branch['condition']
                if condition is None:
                    if i==0 or i!=len(node['branches'])-1:raise SchemaValidationError('TI ELSE must follow all guarded branches.')
                else:
                    condition=condition['span']
                    if not branch_end<=condition['start']<=condition['end']<=span['end']:raise SchemaValidationError('TI branch guards do not preserve source order.')
                    branch_end=condition['end']
                _validate_statement_order(branch['body'],branch_end,span['end'])
                if branch['body']:branch_end=branch['body'][-1]['span']['end']


def validate_ti_analysis(report):
    validate_document(report,'tm1_ti_analysis')
    if report['analysis_id']!=ti_analysis_id(report):raise SchemaValidationError('TI analysis content identity is stale.')
    if [s['name'] for s in report['sections']]!=list(SECTIONS):raise SchemaValidationError('TI sections must preserve the explicit lifecycle slots.')
    if report['missing_sections']!=[s['name'] for s in report['sections'] if s['status']=='MISSING']:
        raise SchemaValidationError('TI missing/empty section declarations conflict.')
    sources={s['name']:s['source'] for s in report['sections']}
    for section in report['sections']:
        source=section['source']
        expected_iteration='SOURCE_DEPENDENT_SEPARATE_RECORD_PASS' if section['name'] in {'metadata','data'} else 'NOMINAL_PROCESS_STAGE'
        if section['iteration_scope']!=expected_iteration:raise SchemaValidationError('TI section iteration scope conflicts with its explicit lifecycle role.')
        if section['status']=='STRUCTURED' and not section['structure_complete']:raise SchemaValidationError('A structured TI section requires complete source consumption.')
        if source is None:
            if section['status']!='MISSING' or section['source_file'] is not None or section['structure_complete'] or section['ast'] or section['statements'] or section['control_flow'] is not None:
                raise SchemaValidationError('Missing TI source cannot be replaced by an empty parsed section.')
            continue
        if section['status']=='MISSING':raise SchemaValidationError('A supplied TI source cannot be labelled as a missing section.')
        raw=source['text'].encode('utf-8')
        if source['sha256']!=hashlib.sha256(raw).hexdigest() or source['size_bytes']!=len(raw):raise SchemaValidationError('TI section source hash/size is stale.')
        _validate_statement_order(section['ast'],0,len(source['text']))
        if section['structure_complete']:
            if section['status'] not in {'STRUCTURED','UNSUPPORTED'}:raise SchemaValidationError('TI completion flag conflicts with its status.')
            if section['statements']!=flatten(section['name'],section['ast']):raise SchemaValidationError('TI statement index omitted or reordered source behavior.')
            if section['control_flow']!=build_control_flow(section['name'],section['ast']):raise SchemaValidationError('TI control-flow edges do not match the section AST.')
        elif section['ast'] or section['statements'] or section['control_flow'] is not None:raise SchemaValidationError('Incomplete TI sections cannot retain a parsed prefix.')
        for node in _walk(section['ast']):
            span=node['span']
            if not 0<=span['start']<=span['end']<=len(source['text']):raise SchemaValidationError('TI AST span is outside its original source.')
    for collection in ('references','dependencies','effects','configuration_assignments','diagnostics'):
        for item in report[collection]:
            section=item['section'];span=item['span'];ref=item['evidence']
            if section is None:
                if span is not None or ref is not None:raise SchemaValidationError('Global TI requirements cannot claim invented source evidence.')
            else:
                source=sources.get(section)
                if source is None or span is None or ref is None or not 0<=span['start']<=span['end']<=len(source['text']):raise SchemaValidationError('TI evidence lacks available section source.')
                expected=source['locator']+'#ti-chars='+str(span['start'])+':'+str(span['end'])
                if ref['file_id']!=source['file_id'] or ref['locator']!=expected:raise SchemaValidationError('TI evidence is not bound to its source file/span.')
    for collection,key in (('references','reference_id'),('dependencies','dependency_id')):
        ids=[item[key] for item in report[collection]]
        if len(ids)!=len(set(ids)):raise SchemaValidationError('Duplicate TI evidence identities.')
    statuses=[s['status'] for s in report['sections']]
    expected=next((s for s in ('LIMIT_EXCEEDED','INVALID','UNSUPPORTED') if s in statuses),'UNSUPPORTED' if 'MISSING' in statuses else 'STRUCTURED')
    if report['status']!=expected or report['migration_strategy']!=('REVIEW_REQUIRED' if expected=='STRUCTURED' else 'UNSUPPORTED'):
        raise SchemaValidationError('TI report status cannot conceal incomplete/unsupported sections.')
