"""Native TM1 analysis → source-neutral preservation graph; no SSIS projection."""
from __future__ import annotations

import copy

from ssis_migration.ir.normalized import content_digest, normalized_ir_id, validate_normalized_ir
from ssis_migration.schemas import SchemaValidationError
from .bundle import validate_bundle
from .ti.control_flow import statement_id


class Normalizer:
    def __init__(self, bundle, files):
        self.bundle, self.files = bundle, files
        self.nodes, self.edges, self.diagnostics = [], [], []
        source = bundle['manifest']
        self.source = {'platform': 'tm1', 'format': 'tm1_source_bundle_v1',
                       'export_version': source['document']['source_export_version'], 'file_id': source['file_id']}
        self.bundle_evidence = [{'file_id': source['file_id'], 'locator': 'json:/', 'rule': 'explicit_tm1_bundle'}]

    def node(self, key, kind, operation, evidence, attributes=None, name=None):
        identity = 'node-' + content_digest([self.source['file_id'], key])[:24]
        self.nodes.append({'node_id': identity, 'kind': kind, 'operation': operation,
                           'name': name, 'source_ref': key, 'semantic_status': 'NOT_VERIFIED',
                           'attributes': copy.deepcopy(attributes or {}), 'extensions': {}, 'evidence': copy.deepcopy(evidence)})
        return identity

    def diagnostic(self, code, message, evidence, severity='WARNING'):
        self.diagnostics.append({'code': code, 'severity': severity, 'message': message,
                                 'file_ids': sorted({e['file_id'] for e in evidence}), 'evidence': copy.deepcopy(evidence)})

    def edge(self, kind, start, end, evidence, attributes=None):
        value = {'kind': kind, 'source_id': start, 'target_id': end,
                 'resolution': 'RESOLVED' if start is not None and end is not None else 'UNRESOLVED',
                 'attributes': copy.deepcopy(attributes or {}), 'extensions': {}, 'evidence': copy.deepcopy(evidence)}
        value['edge_id'] = 'edge-' + content_digest([self.source['file_id'], len(self.edges), value])[:24]
        self.edges.append(value)
        if value['resolution'] == 'UNRESOLVED':
            self.diagnostic('UNRESOLVED_GRAPH_REFERENCE', value['edge_id'] + ': Declared relationship has an unresolved endpoint; no target was invented.', evidence)

    def expression(self, unit, ast, key, owner, role, section=None):
        kind = ast['kind']
        operation = {'literal':'LITERAL', 'variable':'VARIABLE_REFERENCE', 'binary':'BINARY_OPERATION',
                     'unary':'UNARY_OPERATION', 'group':'GROUP', 'call':'CALL', 'function':'CALL',
                     'member_path':'MEMBER_REFERENCE', 'area':'CELL_SELECTOR', 'dimension_context':'CONTEXT_MEMBER',
                     'control':'SOURCE_CONTROL', 'set':'SET', 'tuple':'TUPLE', 'set_operator':'SET_OPERATION'}.get(kind, 'SOURCE_EXPRESSION')
        # Scalar fields and selectors are neutral attributes; nested expressions
        # become separate nodes with explicit operand/argument position edges.
        attributes = {'language': unit['analysis']['language'], 'source_span': ast['span'], 'expression_kind': kind}
        for name, value in ast.items():
            if name in {'kind','span'}:
                continue
            if not isinstance(value, (dict, list)) or name in {'segments','selectors'}:
                attributes[name] = value
        evidence = self.evidence(unit, ast['span'], section)
        identity = self.node(key, 'EXPRESSION', operation, evidence, attributes)
        self.edge('HAS_EXPRESSION', owner, identity, evidence, role)
        for field, value in ast.items():
            if isinstance(value, dict) and 'kind' in value and 'span' in value:
                self.expression(unit, value, key+'/'+field, identity, {'operand': field}, section)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    if isinstance(child, dict) and 'kind' in child and 'span' in child:
                        self.expression(unit, child, key+'/'+field+'/'+str(index), identity, {'operand': field, 'position': index}, section)
        return identity

    def evidence(self, unit, span=None, section=None):
        source = unit['analysis'].get('source')
        language = unit['analysis']['language']
        if section is not None:
            source = next(s['source'] for s in unit['analysis']['sections'] if s['name'] == section)
        if source is None or span is None:
            return [{'file_id': unit['source_file']['file_id'], 'locator': 'file:/', 'rule': 'explicit_tm1_analysis_unit'}]
        suffix = {'mdx':'mdx', 'tm1_rules':'rux', 'tm1_turbointegrator':'ti'}[language]
        return [{'file_id': source['file_id'], 'locator': source['locator']+'#'+suffix+'-chars='+str(span['start'])+':'+str(span['end']),
                 'rule': 'tm1_source_structure_not_execution'}]

    def statement(self, unit, node, key, parent, section, index, identities, branch=None):
        evidence = self.evidence(unit, node['span'], section)
        operation = {'assignment':'ASSIGN', 'invoke':'INVOKE', 'if':'BRANCH', 'while':'LOOP', 'control':'CONTROL_TRANSFER'}[node['kind']]
        attributes = {'source_span': node['span'], 'source_order_in_block': index,
                      'symbol': node.get('name'), 'execution_status': 'NOT_VERIFIED'}
        identity = self.node(key, 'TRANSFORMATION', operation, evidence, attributes)
        identities[statement_id(section, node)] = identity
        self.edge('CONTAINS', parent, identity, evidence, {'branch': branch, 'source_order_in_block': index})
        if node['kind'] in {'assignment','invoke'}:
            field = 'value' if node['kind'] == 'assignment' else 'call'
            self.expression(unit, node[field], key+'/'+field, identity, {'operand': field}, section)
        elif node['kind'] == 'if':
            for b, part in enumerate(node['branches']):
                if part['condition'] is not None:
                    self.expression(unit, part['condition'], key+'/guard/'+str(b), identity, {'branch': b, 'operand': 'condition'}, section)
                for i, child in enumerate(part['body']):
                    self.statement(unit, child, key+'/branches/'+str(b)+'/'+str(i), identity, section, i, identities, b)
        elif node['kind'] == 'while':
            self.expression(unit, node['condition'], key+'/condition', identity, {'operand':'condition'}, section)
            for i, child in enumerate(node['body']):
                self.statement(unit, child, key+'/body/'+str(i), identity, section, i, identities, 'loop_body')

    def ti(self, unit, root, key):
        for section in unit['analysis']['sections']:
            name = section['name']; skey = key+'/section/'+name
            evidence = self.evidence(unit)
            sid = self.node(skey, 'TASK', 'SECTION', evidence,
                            {k: section[k] for k in ('name','iteration_scope','status','structure_complete')}, name)
            self.edge('CONTAINS', root, sid, evidence, {'role':'section', 'lifecycle_status':'SOURCE_DEPENDENT_NOT_VERIFIED'})
            identities = {}
            for i, statement in enumerate(section['ast']):
                self.statement(unit, statement, skey+'/statement/'+str(i), sid, name, i, identities)
            flow = section['control_flow']
            if flow is None:
                continue
            for role in ('entry','exit'):
                endpoint = flow[role+'_id']
                identities[endpoint] = self.node(skey+'/'+role, 'TASK', 'SECTION_'+role.upper(), evidence,
                                                  {'scope':'NOMINAL_INTRA_SECTION_ONLY'}, role)
                self.edge('CONTAINS', sid, identities[endpoint], evidence, {'role':role})
            for edge in flow['edges']:
                self.edge('CONTROL_FLOW', identities.get(edge['source_id']), identities.get(edge['target_id']),
                          self.evidence(unit, edge['span'], name),
                          {**{k: edge[k] for k in ('kind','guards','control_name','resolution')}, 'scope':flow['scope'],
                           'section':name, 'execution_status':'NOT_VERIFIED'})

    def rules(self, unit, root, key):
        for i, statement in enumerate(unit['analysis']['statements']):
            ast = statement['ast']; evidence = [statement['evidence']]
            kind = ast['kind']
            identity = self.node(key+'/statement/'+str(i), 'TRANSFORMATION',
                                 {'calculation':'DEFINE_CELL_RULE','feeder':'DECLARE_RELATION','directive':'CONFIGURATION_DIRECTIVE'}[kind],
                                 evidence, {'source_order':i, 'qualifier':ast.get('qualifier'), 'directive':ast.get('name')})
            self.edge('CONTAINS', root, identity, evidence, {'source_order':i, 'ordering_scope':'SOURCE_ORDER_ONLY'})
            children = {}
            for field, value in ast.items():
                if isinstance(value, dict) and 'kind' in value:
                    children[field] = self.expression(unit, value, key+'/statement/'+str(i)+'/'+field, identity, {'operand':field})
            if kind == 'feeder':
                self.edge('DECLARED_RELATION', children['source_area'], children['target'], evidence,
                          {'relationship':'DECLARED_FEEDER', 'meaning':'SPARSE_CELL_ACTIVATION_NOT_ETL_DATA_FLOW', 'runtime_status':'NOT_VERIFIED'})

    def mdx(self, unit, root, key):
        query = unit['analysis']['query']
        if query is None:
            return
        for i, axis in enumerate(query['axes']):
            evidence = self.evidence(unit, axis['expression']['span'])
            identity = self.node(key+'/axis/'+str(i), 'TRANSFORMATION', 'DEFINE_QUERY_AXIS', evidence,
                                 {k:axis[k] for k in ('ordinal','source_label','non_empty')})
            self.edge('CONTAINS', root, identity, evidence, {'role':'query_axis', 'ordinal':axis['ordinal']})
            self.expression(unit, axis['expression'], key+'/axis/'+str(i)+'/expression', identity, {'operand':'axis_expression'})
        if query['slicer'] is not None:
            self.expression(unit, query['slicer'], key+'/slicer', root, {'operand':'query_slicer'})
        self.node_reference(unit, root, key+'/query_cube', 'DECLARED_QUERY_CUBE',
                            {'name':query['cube']['name'], 'binding_status':'NOT_VERIFIED'}, self.evidence(unit, query['cube']['span']), 'SOURCE')

    def node_reference(self, unit, root, key, operation, attributes, evidence, kind='REFERENCE'):
        identity = self.node(key, kind, operation, evidence, attributes)
        self.edge('CONTAINS', root, identity, evidence, {'role':'source_requirement_or_reference'})
        return identity

    def requirements(self, unit, root, key):
        analysis = unit['analysis']
        for field, operation, kind in (('dependencies','SOURCE_DEPENDENCY','REQUIREMENT'),
                                        ('references','SOURCE_REFERENCE','REFERENCE'),('effects','DECLARED_EFFECT','TRANSFORMATION')):
            for i, item in enumerate(analysis.get(field, [])):
                evidence = [item['evidence']] if item.get('evidence') else self.evidence(unit)
                attributes = {k:v for k,v in item.items() if k != 'evidence'}
                node_kind = kind
                if field == 'effects' and item['kind'] in {'CUBE_READ','CUBE_WRITE','CUBE_INCREMENT','FILE_WRITE'}:
                    node_kind = 'SOURCE' if item['kind'] == 'CUBE_READ' else 'TARGET'
                elif field == 'references' and item['kind'] == 'DB_CUBE_ARGUMENT':
                    node_kind = 'SOURCE'
                identity = self.node_reference(unit, root, key+'/'+field+'/'+str(i), operation, attributes, evidence, node_kind)
                self.edge('DECLARES_EFFECT' if field == 'effects' else 'REQUIRES', root, identity, evidence,
                          {'scope':'DECLARED_SOURCE_ONLY', 'runtime_status':'NOT_VERIFIED'})
        binding = unit['metadata_binding']
        for i, item in enumerate(binding['bindings']):
            evidence = ([item['source_evidence']] if item['source_evidence'] else self.evidence(unit)) + item['metadata_evidence']
            identity = self.node_reference(unit, root, key+'/binding/'+str(i), 'METADATA_BINDING', item, evidence, 'REQUIREMENT')
            self.edge('REQUIRES', root, identity, evidence, {'binding_status':item['status'], 'verification':'NOT_VERIFIED'})
        for i, requirement in enumerate(binding['runtime_requirements']):
            self.node_reference(unit, root, key+'/runtime_requirement/'+str(i), 'RUNTIME_VALIDATION',
                                {'requirement':requirement, 'verification':'NOT_VERIFIED'}, self.evidence(unit), 'REQUIREMENT')
        for diagnostic in analysis['diagnostics']:
            evidence = [diagnostic['evidence']] if diagnostic.get('evidence') else self.evidence(unit)
            self.diagnostic(diagnostic['code'], unit['unit_id']+': '+diagnostic['message'], evidence, diagnostic['severity'])
        if analysis['status'] != 'STRUCTURED':
            self.diagnostic('INCOMPLETE_TM1_ANALYSIS', unit['unit_id']+': '+analysis['status']+'; complete behavior is not normalized.', self.evidence(unit), 'ERROR')
        if binding['status'] != 'BOUND_REVIEW_REQUIRED':
            self.diagnostic('UNRESOLVED_TM1_METADATA', unit['unit_id']+': '+binding['status']+' metadata binding; dependencies remain unresolved.', self.evidence(unit),
                            'ERROR' if binding['status'] == 'BLOCKED' else 'WARNING')
        for issue in binding['metadata_issues']:
            self.diagnostic(issue['code'], unit['unit_id']+': '+issue['message'], issue['evidence'] or self.evidence(unit), 'ERROR')

    def metadata(self, root, index, meta):
        source = meta['source']; document = meta['document']; prefix = 'metadata/'+str(index)

        def declare(pointer, parent, kind, operation, attributes, name=None):
            evidence = [{'file_id':source['file_id'], 'locator':source['locator']+pointer, 'rule':'supplied_metadata_not_runtime_behavior'}]
            identity = self.node(prefix+'/'+pointer, kind, operation, evidence, attributes, name)
            self.edge('CONTAINS', parent, identity, evidence, {'role':'metadata_declaration'})
            return identity, evidence

        top,_ = declare('',root,'REFERENCE','METADATA_DECLARATIONS',
                        {'format':document['export_format'],'source_system':document['source_system'],'sha256':source['sha256']})
        for d, dimension in enumerate(document['dimensions']):
            dp='dimensions/'+str(d)
            did,_=declare(dp,top,'REFERENCE','DIMENSION_DECLARATION',{'name':dimension['name']},dimension['name'])
            for h, hierarchy in enumerate(dimension['hierarchies']):
                hp=dp+'/hierarchies/'+str(h)
                hid,_=declare(hp,did,'REFERENCE','HIERARCHY_DECLARATION',
                              {'name':hierarchy['name'],'declared_default_member':hierarchy['default_member']},hierarchy['name'])
                members={};origins=[]
                for m, member in enumerate(hierarchy['members']):
                    mid, evidence=declare(hp+'/members/'+str(m),hid,'REFERENCE','MEMBER_DECLARATION',
                                          {**member,'dimension':dimension['name'],'hierarchy':hierarchy['name']},member['name'])
                    members.setdefault(member['name'],[]).append(mid);origins.append((mid,evidence,member))
                for mid,evidence,member in origins:
                    for child in member['children']:
                        targets=members.get(child['member'],[])
                        self.edge('DECLARED_RELATION',mid,targets[0] if len(targets)==1 else None,evidence,
                                  {'relationship':'DECLARED_CONSOLIDATION','weight':child['weight'],
                                   'runtime_status':'NOT_VERIFIED','aggregation_not_evaluated':True})
        for c,cube in enumerate(document['cubes']):
            declare('cubes/'+str(c),top,'REFERENCE','CUBE_DECLARATION',cube,cube['name'])
        for s,subset in enumerate(document['subsets']):
            declare('subsets/'+str(s),top,'REFERENCE','SUBSET_DECLARATION',subset,subset['name'])
        for p,process in enumerate(document['processes']):
            pp='processes/'+str(p)
            pid,_=declare(pp,top,'REFERENCE','PROCESS_SIGNATURE',
                          {'name':process['name'],'datasource':process['datasource'],'body_status':'NOT_SUPPLIED_BY_SIGNATURE'},process['name'])
            for field,kind,operation in [('parameters','PARAMETER','PARAMETER_DECLARATION'),('variables','REFERENCE','SYMBOL_DECLARATION')]:
                for n,value in enumerate(process[field]):
                    declare(pp+'/'+field+'/'+str(n),pid,kind,operation,
                            {**value,'scope_declared_process':process['name'],'runtime_value_status':'NOT_VERIFIED'},value['name'])
        for d,datasource in enumerate(document['datasources']):
            dp='datasources/'+str(d)
            did,_=declare(dp,top,'REFERENCE' if datasource['kind']=='NONE' else 'CONNECTION','DATASOURCE_DECLARATION',
                          {k:v for k,v in datasource.items() if k!='columns'},datasource['name'])
            for c,column in enumerate(datasource['columns']):
                declare(dp+'/columns/'+str(c),did,'REFERENCE','SOURCE_COLUMN_DECLARATION',
                        {**column,'datasource':datasource['name']},column['name'])

    def build(self):
        root = self.node('bundle', 'TASK', 'SOURCE_COLLECTION', self.bundle_evidence,
                         {'execution_order':'NOT_ESTABLISHED', 'scope':'SUPPLIED_SOURCE_UNITS'}, self.bundle['manifest']['document']['name'])
        for i, meta in enumerate(self.bundle['metadata_inputs']):
            self.metadata(root,i,meta)
        version = self.source['export_version']
        for unit in self.bundle['units']:
            key = 'unit/'+unit['unit_id']; analysis = unit['analysis']; evidence = self.evidence(unit)
            operation = {'mdx':'QUERY', 'tm1_rules':'RULE_SET', 'tm1_turbointegrator':'PROCESS'}[analysis['language']]
            uid = self.node(key, 'TASK', operation, evidence, {'unit_id':unit['unit_id'], 'language':analysis['language'],
                            'analysis_unit':True,
                            'analysis_status':analysis['status'], 'metadata_binding_status':unit['metadata_binding']['status'],
                            'generation_status':'NOT_IMPLEMENTED'}, unit['unit_id'])
            self.edge('CONTAINS', root, uid, evidence, {'role':'supplied_unit', 'execution_order':'NOT_ESTABLISHED'})
            {'mdx':self.mdx, 'tm1_rules':self.rules, 'tm1_turbointegrator':self.ti}[analysis['language']](unit, uid, key)
            self.requirements(unit, uid, key)
            declarations = [analysis.get('source_export_version')] + [m['document']['source_export_version'] for m in self.bundle['metadata_inputs']]
            if version is not None and any(v is not None and v != version for v in declarations):
                self.diagnostic('CONFLICTING_BUNDLE_VERSION', unit['unit_id']+': bundle version conflicts with supplied source/metadata declarations.', evidence, 'ERROR')
        self.diagnostic('TM1_TRANSLATION_NOT_IMPLEMENTED', 'Source structure and metadata are preservation obligations; faithful Databricks translation is not implemented.', self.bundle_evidence)
        graph = {'ir_version':'2.0', 'source':self.source, 'evidence_files':copy.deepcopy(self.files),
                 'nodes':self.nodes, 'edges':self.edges, 'diagnostics':self.diagnostics,
                 'semantic_status':'NOT_VERIFIED', 'status':'BLOCKED' if any(d['severity']=='ERROR' for d in self.diagnostics) else 'REVIEW_REQUIRED',
                 'capability_gaps':['target_translation','verified_source_semantics','incremental_logic','runtime_lifecycle',
                                    'complete_error_handling','cell_type_and_aggregation','security_context','execution_and_data_equivalence'],
                 'provenance':{'builder':'tm1_native_analysis', 'version':'1.0', 'source_analysis_sha256':content_digest(self.bundle),
                               'evidence_scope':'SOURCE_ANALYSIS', 'limitations':[
                                   'Structured graph identity and links are not proof of execution, semantic equivalence or complete source behavior.',
                                   'Unit containment is supplied-file grouping, not process execution order; Metadata/Data remain separate passes.']},
                 'extensions':{'source_analysis':copy.deepcopy(self.bundle)}}
        graph['ir_id'] = normalized_ir_id(graph)
        return graph


def normalize_tm1_bundle(bundle, evidence_files):
    validate_bundle(bundle, evidence_files)
    graph = Normalizer(bundle, evidence_files).build()
    validate_normalized_ir(graph)
    return graph


def validate_tm1_normalized_ir(graph):
    validate_normalized_ir(graph)
    if graph['ir_version'] != '2.0' or graph['source']['platform'] != 'tm1':
        raise SchemaValidationError('Expected native TM1 IR v2.')
    expected = normalize_tm1_bundle(graph['extensions']['source_analysis'], graph['evidence_files'])
    if graph != expected:
        raise SchemaValidationError('TM1 normalized graph omits or changes source behavior, guards, requirements or evidence.')
