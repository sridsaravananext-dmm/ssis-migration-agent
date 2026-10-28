"""Evidence-bound ODI mapping review requirements, independent of target code."""
from __future__ import annotations

from collections import defaultdict
import copy

from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ..xml_export import validate_export_graph
from .native import PROFILE as GRAPH_PROFILE, value

PROFILE = 'odi_12c_mapping_requirements_v1'
SYNTAX_PROFILE = 'odi_12c_mapping_requirements_v2'
WINDOW_PROFILE = 'odi_12c_mapping_requirements_v3'
PHYSICAL_PROFILE = 'odi_12c_mapping_requirements_v4'
LATEST_PROFILE = 'odi_12c_mapping_requirements_v5'


class MappingIndex:
    def __init__(self, graph):
        self.graph = graph
        self.nodes = {n['node_id']: n for n in graph['nodes']}
        self.classes = defaultdict(list)
        self.references = defaultdict(list)
        self.outgoing = defaultdict(list)
        for node in graph['nodes']:
            self.classes[node['attributes'].get('object_class')].append(node)
        for edge in graph['edges']:
            self.outgoing[edge['source_id']].append(edge)
            if edge['kind'] == 'REQUIRES':
                self.references[(edge['source_id'], edge['attributes']['field'])].append(edge)

    def target(self, node, name):
        refs = self.references[(node['node_id'], name)] if node else []
        return self.nodes.get(refs[0]['target_id']) if len(refs) == 1 and refs[0]['resolution'] == 'RESOLVED' else None

    def owner(self, node, visited=None):
        if node is None: return None
        visited = set() if visited is None else visited
        if node['node_id'] in visited: return None
        visited.add(node['node_id']); cls = node['attributes'].get('object_class')
        if cls == 'SnpMapping': return node['node_id']
        names = {'SnpMapComp': ['IOwnerMapping'], 'SnpMapCp': ['IOwnerMapComp'],
                 'SnpMapAttr': ['IOwnerMapCp'], 'SnpMapConn': ['IOwnerMapping'],
                 'SnpMapRef': ['IOwnerMapping'], 'SnpMapProp': ['IOwnerMapping'],
                 'SnpMapExpr': ['IOwnerMapAttr', 'IOwnerMapProp'], 'SnpMapExprRef': ['IOwnerMapExpr'],
                 'SnpDeploySpec': ['IOwnerMapping'], 'SnpPhyNode': ['IOwnerDs'],
                 'SnpExecUnit': ['IOwnerDs'], 'SnpExecUnitGrp': ['IOwnerDs']}
        parents = [self.target(node, name) for name in names.get(cls, [])]
        parents = [p for p in parents if p is not None]
        return self.owner(parents[0], visited) if len(parents) == 1 else None

    def scoped(self, mapping, cls):
        return [n for n in self.classes[cls] if self.owner(n) == mapping['node_id']]

    def mapping_declarations(self, mapping):
        components = []
        for component in self.scoped(mapping, 'SnpMapComp'):
            reference = self.target(component, 'IMapRef'); definition = self.target(reference, 'IRefId')
            components.append({'node_id': component['node_id'], 'name': component['name'],
                'component_type': value(component, 'TypeName'), 'reference_node_id': reference['node_id'] if reference else None,
                'declared_data_flow_role': component['attributes'].get('declared_data_flow_role'),
                'definition_node_id': definition['node_id'] if definition else None,
                'physical_binding': 'NOT_ESTABLISHED'})
        expressions = []
        for expression in self.scoped(mapping, 'SnpMapExpr'):
            attributes = self.target(expression, 'IOwnerMapAttr'); prop = self.target(expression, 'IOwnerMapProp')
            references = [n for n in self.scoped(mapping, 'SnpMapExprRef') if self.target(n, 'IOwnerMapExpr') == expression]
            expressions.append({'node_id': expression['node_id'],
                'owner_attribute_node_id': attributes['node_id'] if attributes else None,
                'owner_property_node_id': prop['node_id'] if prop else None,
                'declarations': {k: value(expression, k) for k in ('Txt', 'ParsedTxt', 'Text', 'ParsedText', 'IsParsed', 'TextOnly')},
                'references': [{'node_id': n['node_id'], 'reference_key': value(n, 'RefKey'),
                                'reference_text': value(n, 'RefText'), 'validity_declaration': value(n, 'IsValid'),
                                'attribute_node_id': self.target(n, 'IRefMapAttr')['node_id'] if self.target(n, 'IRefMapAttr') else None}
                               for n in references], 'syntax_status': 'DECLARATIONS_ONLY', 'semantic_status': 'NOT_VERIFIED'})
        external = []
        for reference in self.scoped(mapping, 'SnpMapRef'):
            target = self.target(reference, 'IRefId')
            external.append({'node_id': reference['node_id'], 'interface': value(reference, 'AdapterIntfType'),
                'declared_identity': value(reference, 'IRefId'), 'declared_guid': value(reference, 'RefGuid'),
                'qualified_name': value(reference, 'QualifiedName'), 'target_node_id': target['node_id'] if target else None,
                'resolution': 'RESOLVED' if target else 'UNRESOLVED'})
        properties = [{'node_id': n['node_id'], 'name': value(n, 'Name'), 'value': value(n, 'PropValue'),
                       'parent_node_id': self.target(n, 'IMapPropParent')['node_id'] if self.target(n, 'IMapPropParent') else None}
                      for n in self.scoped(mapping, 'SnpMapProp')]
        ids = {n['node_id'] for n in self.graph['nodes'] if self.owner(n) == mapping['node_id']}
        return {
            'declared_identity': value(mapping, 'IMapping'), 'name': mapping['name'],
            'components': components,
            'connection_point_node_ids': [n['node_id'] for n in self.scoped(mapping, 'SnpMapCp')],
            'connection_node_ids': [n['node_id'] for n in self.scoped(mapping, 'SnpMapConn')],
            'attribute_node_ids': [n['node_id'] for n in self.scoped(mapping, 'SnpMapAttr')],
            'declared_data_flow_edge_ids': [e['edge_id'] for e in self.graph['edges'] if e['kind'] == 'DATA_FLOW' and e['attributes'].get('mapping_node_id') == mapping['node_id']],
            'declared_column_lineage_edge_ids': [e['edge_id'] for e in self.graph['edges'] if e['kind'] == 'COLUMN_LINEAGE' and e['source_id'] in ids and e['target_id'] in ids],
            'expressions': expressions, 'properties': properties, 'external_references': external,
            'physical_designs': [{'node_id': n['node_id'], 'name': n['name'], 'declared_identity': value(n, 'IDeploySpec')}
                                 for n in self.scoped(mapping, 'SnpDeploySpec')],
            'selected_physical_design': None, 'execution_order': 'NOT_ESTABLISHED',
        }


def _analyze_v1(graph, *, profile=PROFILE):
    if profile != PROFILE:
        raise SchemaValidationError('Unsupported ODI mapping requirements profile.')
    validate_export_graph(graph)
    if graph['source']['platform'] != 'odi' or graph['extensions']['source_analysis']['profile'] != GRAPH_PROFILE:
        raise SchemaValidationError('ODI mapping requirements need the registered native mapping profile.')
    index = MappingIndex(graph); requirements = []

    def emit(node, kind, declarations, preservation, unresolved, related):
        evidence = []
        for item in [node, *related]:
            for ref in item['evidence']:
                if ref not in evidence: evidence.append(copy.deepcopy(ref))
        item = {'subject_node_id': node['node_id'], 'kind': kind, 'declarations': declarations,
                'preservation_requirements': preservation, 'unresolved_items': unresolved,
                'evidence': evidence, 'semantic_status': 'NOT_VERIFIED'}
        item['requirement_id'] = 'requirement-' + content_digest([PROFILE, item])[:24]
        requirements.append(item)

    for mapping in index.classes['SnpMapping']:
        declarations = index.mapping_declarations(mapping)
        related = [n for n in graph['nodes'] if index.owner(n) == mapping['node_id']]
        unresolved = [
            'Native expression/type/null, component, join and aggregate semantics require version-specific translation and independent verification.',
            'Physical design selection, context/physical bindings and Knowledge Module command/option semantics are not established.',
            'Target write/default/error/transaction behavior and run parameters must be preserved; source observations and business criticality/weights are not supplied.',
        ]
        if any(r['resolution'] != 'RESOLVED' for r in declarations['external_references']):
            unresolved.append('Some class/key/GUID external references lack a unique exported definition; reference descriptions are not behavior definitions.')
        if any(p['name'] == 'TRUNCATE' and p['value'] == 'true' for p in declarations['properties']):
            unresolved.append('Explicit TRUNCATE=true options are retained; their physical scope and destructive-write replacement require review before generation.')
        emit(mapping, 'ODI_MAPPING', declarations,
             ['Preserve every component, connection point, connector, attribute, expression/reference and native datatype declaration.',
              'Preserve all physical designs, Knowledge Module options, property nesting and target write/error/transaction behavior without choosing a design by name or XML order.'],
             unresolved, related)
    unknown = [n for n in graph['nodes'] if not n['attributes']['structure_supported']]
    root = next(n for n in graph['nodes'] if n['attributes'].get('analysis_unit'))
    emit(root, 'ODI_EXPORT_REVIEW', {
        'unsupported_node_ids': [n['node_id'] for n in unknown],
        'unresolved_edge_ids': [e['edge_id'] for e in graph['edges'] if e['resolution'] != 'RESOLVED'],
        'source_structure': copy.deepcopy(graph['extensions']['source_analysis']['metadata']),
        'execution_order': 'NOT_ESTABLISHED'},
        ['Preserve every exported object and field, unknown classes, package/procedure commands, topology, defaults and error/control behavior.'],
        ['Unsupported object classes and unresolved dependencies remain review obligations; generation, execution and semantic equivalence are not established.'], unknown)
    document = {'analysis_version': '1.0', 'profile': PROFILE, 'ir_id': graph['ir_id'],
                'source_analysis_sha256': graph['provenance']['source_analysis_sha256'],
                'evidence_files': copy.deepcopy(graph['evidence_files']), 'requirements': requirements,
                'semantic_status': 'NOT_VERIFIED', 'scope': 'DECLARATIONS_AND_REVIEW_REQUIREMENTS'}
    validate_document(document, 'source_behavior_analysis'); validate_evidence_references(document)
    return document


def analyze_behavior(graph, *, profile=LATEST_PROFILE):
    if profile == PROFILE:
        return _analyze_v1(graph)
    if profile == SYNTAX_PROFILE:
        from .behavior_v2 import analyze
        return analyze(graph)
    if profile == WINDOW_PROFILE:
        from .behavior_v3 import analyze
        return analyze(graph)
    if profile == PHYSICAL_PROFILE:
        from .behavior_v4 import analyze
        return analyze(graph)
    if profile == LATEST_PROFILE:
        from .behavior_v5 import analyze
        return analyze(graph)
    raise SchemaValidationError('Unsupported ODI mapping requirements profile.')


def validate_behavior_analysis(document, graph):
    validate_document(document, 'source_behavior_analysis'); validate_evidence_references(document)
    if document != analyze_behavior(graph, profile=document['profile']):
        raise SchemaValidationError('ODI mapping requirements do not replay from original source evidence.')
