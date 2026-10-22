"""Evidence-aware TM1 rule/feeder structure; no evaluation or code generation."""
from __future__ import annotations

import hashlib

from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import SchemaValidationError, validate_document
from .lexer import RuleSyntaxError
from .parser import Parser


def rules_analysis_id(report):
    return 'rux-' + content_digest({key:value for key,value in report.items() if key != 'analysis_id'})


def analyze_rules(source: str, *, export_format: str, file_id: str, locator: str = 'file:/') -> dict:
    """Analyze complete plain RUX text with no inferred cube name or metadata."""
    if not isinstance(source,str): raise TypeError('TM1 rules source must be decoded text.')
    if export_format != 'plain_rux_text': raise ValueError('Explicit plain_rux_text format is required; no MDX, TI or SQL fallback is selected.')
    if not isinstance(file_id,str) or not file_id or not isinstance(locator,str) or not locator:
        raise ValueError('TM1 rules analysis requires original source file identity and locator.')
    report = {'analysis_version':'1.0','language':'tm1_rules','export_format':export_format,
              'source_export_version':None,'status':'STRUCTURED','scope':'BOUNDED_TM1_RULES_AND_FEEDERS',
              'semantic_status':'NOT_VERIFIED','migration_strategy':'REVIEW_REQUIRED','migration_accepted':False,
              'source':{'file_id':file_id,'locator':locator,'text':source,'sha256':hashlib.sha256(source.encode()).hexdigest(),
                        'size_bytes':len(source.encode()),'digest_scope':'DECODED_UTF8_TEXT','offset_unit':'UNICODE_CODE_POINTS'},
              'source_file':None,'source_cube':None,'ordering_scope':'SOURCE_ORDER_ONLY',
              'statements':[],'references':[],'dependencies':[],'feeder_relations':[],
              'features':[],'diagnostics':[],'assumptions':[],
              'validation_requirements':[
                  'Supply the actual source cube, TM1 version, dimension/hierarchy/element metadata and cell types.',
                  'Verify ordered overlapping rules, qualifiers, STET/CONTINUE and consolidations against source evidence.',
                  'Verify feeders, SKIPCHECK/FEEDSTRINGS, undefined/zero/empty behavior and feeder reprocessing.',
                  'Resolve DB coordinate order, dynamic/empty targets, attribute lookups, security and external dependencies.',
                  'Require controlled execution, data reconciliation, critical-rule gates and reviewed weighted acceptance before acceptance.']}
    def evidence(span, rule):
        return {'file_id':file_id,'locator':locator+'#rux-chars='+str(span['start'])+':'+str(span['end']),'rule':rule}
    def identity(prefix, kind, span): return prefix+'-'+content_digest([file_id,kind,span])[:24]
    def diagnostic(code, message, span, severity='WARNING'):
        report['diagnostics'].append({'code':code,'severity':severity,'message':message,'span':span,'evidence':evidence(span,code)})
    def dependency(kind, value, span, details=None):
        report['dependencies'].append({'dependency_id':identity('dep',kind,span),'kind':kind,'declared_value':value,
                                       'status':'NOT_SUPPLIED','details':details or {},'span':span,
                                       'evidence':evidence(span,'tm1_rules_dependency_requirement')})
    def reference(kind, value, node, details=None):
        report['references'].append({'reference_id':identity('ref',kind,node['span']),'kind':kind,'value':value,
                                     'resolution':'UNRESOLVED','details':details or {},'span':node['span'],
                                     'evidence':evidence(node['span'],'tm1_rules_declared_reference')})
    try:
        if not source.strip(): raise RuleSyntaxError('EMPTY_RULES','No TM1 rule source was supplied.',0,len(source))
        parser = Parser(source)
        statements = parser.document()
        if not statements: raise RuleSyntaxError('EMPTY_RULES','No rule statements were supplied.',0,len(source))
        for finding in parser.findings: diagnostic(finding['code'],finding['message'],finding['span'])
        if parser.findings:
            report['status']='UNSUPPORTED'; report['migration_strategy']='UNSUPPORTED'
        whole={'start':0,'end':len(source)}
        dependency('SOURCE_CUBE_METADATA',None,whole)
        dependency('RULE_ORDER_AND_OVERLAP',None,whole)
        dependency('AGGREGATION_AND_EMPTY_CELL_CONTEXT',None,whole)
        dependency('SECURITY_CONTEXT',None,whole)
        features = set()
        for index, statement in enumerate(statements):
            span=statement['span']; statement_id=identity('statement',statement['kind'],span)
            report['statements'].append({'statement_id':statement_id,'source_order':index,'ast':statement,
                                          'span':span,'verification':'NOT_VERIFIED',
                                          'evidence':evidence(span,'tm1_rules_ordered_statement')})
            if statement['kind']=='directive': features.add('DIRECTIVE:'+statement['name'])
            elif statement['kind']=='calculation': features.add('CALCULATION:'+str(statement['qualifier'] or 'UNQUALIFIED'))
            elif statement['kind']=='feeder':
                features.add('FEEDER_DECLARATION')
                report['feeder_relations'].append({'statement_id':statement_id,'kind':'DECLARED_FEEDER',
                                                   'source_span':statement['source_area']['span'],'target_span':statement['target']['span'],
                                                   'resolution':'UNRESOLVED','span':span,
                                                   'evidence':evidence(span,'tm1_declared_feeder_not_dataflow')})
                dependency('FEEDER_ACTIVATION_AND_REPROCESSING',None,span)
            stack=[statement]
            while stack:
                node=stack.pop()
                kind=node['kind']
                if kind=='area':
                    value=source[node['span']['start']:node['span']['end']]
                    reference('AREA_COORDINATES',value,node,{'selectors':node['selectors']})
                    dependency('AREA_AND_CELL_TYPE_BINDING',value,node['span'])
                elif kind=='dimension_context':
                    reference('DIMENSION_CONTEXT',node['name'],node)
                    dependency('DIMENSION_CONTEXT_BINDING',node['name'],node['span'])
                elif kind=='control':
                    features.add('RULE_CONTROL:'+node['name'])
                    dependency('RULE_CONTROL_AND_PRECEDENCE',node['name'],node['span'])
                elif kind=='binary': features.add('OPERATOR:'+node['operator'])
                elif kind=='call':
                    name=node['name'].upper(); features.add('FUNCTION:'+name)
                    if name=='DB' and node['arguments']:
                        target=node['arguments'][0]
                        literal=target['kind']=='literal' and target['literal_type']=='STRING'
                        value=target['value'] if literal else None
                        reference('DB_CUBE_ARGUMENT',value,target,{'literal':literal})
                        dependency('DB_COORDINATE_ORDER_AND_TYPES',value,node['span'],
                                   {'coordinate_count':len(node['arguments'])-1,'target_span':target['span']})
                        if value:
                            dependency('LOOKUP_CUBE_METADATA',value,target['span'])
                        else:
                            features.add('DYNAMIC_OR_EMPTY_DB_TARGET')
                            dependency('DYNAMIC_OR_EMPTY_LOOKUP_TARGET',value,target['span'])
                            diagnostic('UNRESOLVED_DB_TARGET','Dynamic/empty DB target was preserved without inventing a cube or execution branch.',target['span'])
                    elif name in {'ATTRN','ATTRS'}:
                        dependency('ATTRIBUTE_METADATA',None,node['span'],{'function':node['name']})
                children=[]
                for value in node.values():
                    if isinstance(value,dict) and 'kind' in value and 'span' in value: children.append(value)
                    elif isinstance(value,list): children.extend(v for v in value if isinstance(v,dict) and 'kind' in v and 'span' in v)
                stack.extend(reversed(children))
        report['features']=sorted(features)
        diagnostic('UNVERIFIED_TM1_RULE_SEMANTICS','Cube identity, ordered rule behavior, sparse cells, feeders and dimensional bindings remain unresolved; no code or acceptance was inferred.',whole)
    except RuleSyntaxError as exc:
        report['status']=exc.status; report['migration_strategy']='UNSUPPORTED'
        report['statements']=[]; report['references']=[]; report['dependencies']=[]; report['feeder_relations']=[]; report['features']=[]
        diagnostic(exc.code,str(exc),{'start':exc.start,'end':exc.end},'ERROR' if exc.status=='INVALID' else 'WARNING')
    report['analysis_id']=rules_analysis_id(report)
    validate_rules_analysis(report)
    return report


def validate_rules_analysis(report):
    """Check offline schema, source/content integrity and ordered evidence links."""
    validate_document(report,'tm1_rules_analysis')
    source=report['source']; raw=source['text'].encode()
    if hashlib.sha256(raw).hexdigest()!=source['sha256'] or len(raw)!=source['size_bytes']:
        raise SchemaValidationError('TM1 rules source hash/size is stale.')
    if report['analysis_id']!=rules_analysis_id(report): raise SchemaValidationError('TM1 rules analysis identity is stale.')
    stack=[report]
    while stack:
        item=stack.pop()
        if isinstance(item,dict):
            for key in ('span','source_span','target_span'):
                if key in item:
                    span=item[key]
                    if not 0<=span['start']<=span['end']<=len(source['text']): raise SchemaValidationError('TM1 rules evidence span is outside source text.')
            if 'span' in item and 'evidence' in item:
                span=item['span']; expected=source['locator']+'#rux-chars='+str(span['start'])+':'+str(span['end'])
                if item['evidence']['file_id']!=source['file_id'] or item['evidence']['locator']!=expected:
                    raise SchemaValidationError('TM1 rules evidence file/locator is not bound to its span.')
            stack.extend(item.values())
        elif isinstance(item,list): stack.extend(item)
    for collection,key in (('statements','statement_id'),('references','reference_id'),('dependencies','dependency_id')):
        ids=[item[key] for item in report[collection]]
        if len(ids)!=len(set(ids)): raise SchemaValidationError('Duplicate TM1 rules evidence identity.')
    if [item['source_order'] for item in report['statements']]!=list(range(len(report['statements']))):
        raise SchemaValidationError('TM1 rule source order is incomplete or conflicting.')
    previous_end=0
    for item in report['statements']:
        if item['span']!=item['ast']['span'] or item['span']['start']<previous_end:
            raise SchemaValidationError('TM1 rule statement evidence does not preserve source order/spans.')
        previous_end=item['span']['end']
    by_id={item['statement_id']:item for item in report['statements']}
    for relation in report['feeder_relations']:
        statement=by_id.get(relation['statement_id'])
        if (not statement or statement['ast']['kind']!='feeder' or relation['span']!=statement['span'] or
            relation['source_span']!=statement['ast']['source_area']['span'] or relation['target_span']!=statement['ast']['target']['span']):
            raise SchemaValidationError('Declared feeder relation does not match its original statement.')
    expected=[item['statement_id'] for item in report['statements'] if item['ast']['kind']=='feeder']
    if [item['statement_id'] for item in report['feeder_relations']]!=expected:
        raise SchemaValidationError('Declared feeder relations must preserve each feeder exactly once.')
    if report['status']=='STRUCTURED' and (not report['statements'] or report['migration_strategy']!='REVIEW_REQUIRED'):
        raise SchemaValidationError('Structured TM1 rules require preserved statements and semantic review.')
    if report['status'] in {'INVALID','LIMIT_EXCEEDED'} and report['statements']:
        raise SchemaValidationError('Incomplete TM1 rules cannot retain an accepted statement prefix.')
