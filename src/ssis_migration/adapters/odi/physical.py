"""Evidence-bound physical design declarations; no selected design or execution.

Typed field identities are resolved here, independently of graph edges, so added
analysis never changes the frozen native graph or repairs conflicting exports.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy

from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ..base import UnsupportedSourceError
from ..xml_export import validate_export_graph
from .native import CLASSES, EXTERNAL_CLASSES, PROFILE as GRAPH_PROFILE, numeric, value

PROFILE = 'odi_12c_physical_dependencies_v1'
MAX_DESIGNS = 256
MAX_PHYSICAL_NODES = 4096
MAX_EXECUTION_UNITS = 2048
MAX_EXECUTION_GROUPS = 2048
MAX_PROPERTIES = 4096
MAX_SCOPE_DEPTH = 64
MAX_REFERENCE_CANDIDATES = 256

_PHYSICAL_CLASSES = ('SnpDeploySpec', 'SnpPhyNode', 'SnpExecUnit', 'SnpExecUnitGrp')
_PROPERTY_SCOPE = (('IDeploySpec', 'SnpDeploySpec'), ('IExecUnit', 'SnpExecUnit'), ('IPhyNode', 'SnpPhyNode'))
_REFERENCE_FIELDS = {
    'SnpPhyNode': [('IOwnerDs', 'SnpDeploySpec', 'mapping'), ('IMapComp', 'SnpMapComp', 'mapping'),
                   ('IMapCp', 'SnpMapCp', 'mapping'), ('IExecUnit', 'SnpExecUnit', 'design'),
                   ('IParPhyNode', 'SnpPhyNode', 'design'), ('ICheckKm', 'SnpMapRef', 'mapping'),
                   ('ISrcCompKm', 'SnpMapRef', 'mapping'), ('ITgtCompKm', 'SnpMapRef', 'mapping')],
    'SnpExecUnit': [('IOwnerDs', 'SnpDeploySpec', 'mapping'), ('IExecUnitGrp', 'SnpExecUnitGrp', 'design'),
                    ('IParExecUnit', 'SnpExecUnit', 'design'),
                    ('IEuKmRef', 'SnpMapRef', 'mapping'), ('ILschemaRef', 'SnpMapRef', 'mapping')],
    'SnpExecUnitGrp': [('IOwnerDs', 'SnpDeploySpec', 'mapping')],
    'SnpMapProp': [('IOwnerMapping', 'SnpMapping', None), ('IMapPropParent', 'SnpMapProp', 'mapping'),
                   *[(name, cls, 'mapping') for name, cls in _PROPERTY_SCOPE],
                   ('IMapComp', 'SnpMapComp', 'mapping'), ('IMapCp', 'SnpMapCp', 'mapping'),
                   ('IMapAttr', 'SnpMapAttr', 'mapping')],
}
_INTERFACES = {'IMapRef': 'IContext', 'ICheckKm': 'IKnowledgeModule',
               'ISrcCompKm': 'IKnowledgeModule', 'ITgtCompKm': 'IKnowledgeModule',
               'IEuKmRef': 'IKnowledgeModule', 'ILschemaRef': 'ILogicalSchema'}


def _evidence(nodes, names=None):
    refs = []; seen = set()
    for node in nodes:
        if node is None: continue
        for evidence in [*node['evidence'], *[e for f in node['attributes'].get('declared_fields', []) if names is None or f['name'] in names for e in f['evidence']]]:
            key = tuple(sorted(evidence.items()))
            if key not in seen: seen.add(key); refs.append(copy.deepcopy(evidence))
    return refs


class PhysicalIndex:
    """Resolve only exact declared class/key identities and explicit ownership."""
    def __init__(self, index):
        self.index = index; self.keys = defaultdict(list)
        for node in index.graph['nodes']:
            cls = node['attributes'].get('object_class'); key = CLASSES.get(cls, (None, None, None))[2]
            if key is not None: self.keys[(cls, value(node, key))].append(node)

    def candidates(self, node, name, cls):
        declarations = [f for f in node['attributes'].get('declared_fields', []) if f['name'] == name]
        if not declarations: return 'MISSING_DECLARATION', None, []
        if len(declarations) != 1 or declarations[0]['nested_child_count']:
            return 'AMBIGUOUS_DECLARATION', None, []
        identity = declarations[0]['value']
        if not numeric(identity): return 'NONNUMERIC_DECLARATION', identity, []
        matches = self.keys.get((cls, identity), [])
        if len(matches) > MAX_REFERENCE_CANDIDATES:
            raise UnsupportedSourceError('ODI physical reference candidate limit exceeded.')
        return 'RESOLVED' if len(matches) == 1 else 'AMBIGUOUS_TARGET' if matches else 'MISSING_TARGET', identity, matches

    def unique(self, node, name, cls):
        status, _, candidates = self.candidates(node, name, cls)
        return candidates[0] if status == 'RESOLVED' else None

    def mapping(self, node, seen=None):
        if node is None: return None
        seen = set() if seen is None else seen
        if node['node_id'] in seen: return None
        if len(seen) >= MAX_SCOPE_DEPTH: raise UnsupportedSourceError('ODI physical ownership depth limit exceeded.')
        seen.add(node['node_id']); cls = node['attributes'].get('object_class')
        if cls == 'SnpMapping': return node['node_id']
        parents = {'SnpDeploySpec': ('IOwnerMapping', 'SnpMapping'),
                   'SnpMapComp': ('IOwnerMapping', 'SnpMapping'), 'SnpMapRef': ('IOwnerMapping', 'SnpMapping'),
                   'SnpMapProp': ('IOwnerMapping', 'SnpMapping'), 'SnpMapCp': ('IOwnerMapComp', 'SnpMapComp'),
                   'SnpMapAttr': ('IOwnerMapCp', 'SnpMapCp'),
                   **{name: ('IOwnerDs', 'SnpDeploySpec') for name in _PHYSICAL_CLASSES[1:]}}
        parent = parents.get(cls)
        return self.mapping(self.unique(node, *parent), seen) if parent else None

    def design(self, node):
        if node is None: return None
        if node['attributes'].get('object_class') == 'SnpDeploySpec': return node
        if node['attributes'].get('object_class') in _PHYSICAL_CLASSES[1:]:
            return self.unique(node, 'IOwnerDs', 'SnpDeploySpec')
        return None

    def definition(self, reference):
        interface = value(reference, 'AdapterIntfType'); cls = EXTERNAL_CLASSES.get(interface)
        status, identity, candidates = self.candidates(reference, 'IRefId', cls)
        guid = value(reference, 'RefGuid'); target = candidates[0] if status == 'RESOLVED' else None
        if cls is None or value(reference, 'AdapterName') != 'ODI': status = 'UNSUPPORTED_INTERFACE'
        elif target is not None and (guid in (None, '', 'null') or value(target, 'GlobalId') != guid): status = 'GUID_MISMATCH'
        return {'reference_node_id': reference['node_id'], 'interface': interface, 'declared_identity': identity,
                'declared_guid': guid, 'qualified_name': value(reference, 'QualifiedName'), 'target_class': cls,
                'candidate_node_ids': [n['node_id'] for n in candidates],
                'target_node_id': target['node_id'] if status == 'RESOLVED' else None,
                'resolution': status, 'rule': 'class_key_and_exact_guid',
                'evidence': _evidence([reference, *candidates], ('IRefId', 'RefGuid', 'GlobalId', 'AdapterName', 'AdapterIntfType', 'QualifiedName', CLASSES.get(cls, (None, None, None))[2]))}

    def reference(self, node, name, cls, scope=None):
        status, identity, candidates = self.candidates(node, name, cls)
        target = candidates[0] if status == 'RESOLVED' else None
        if target is not None and scope is not None:
            owner = self.mapping(node); target_owner = self.mapping(target)
            if owner is None or target_owner is None: status = 'UNRESOLVED_OWNER'
            elif owner != target_owner: status = 'CROSS_MAPPING'
            elif scope == 'design':
                own_design = self.design(node); target_design = self.design(target)
                if own_design is None or target_design is None: status = 'UNRESOLVED_OWNER'
                elif own_design['node_id'] != target_design['node_id']: status = 'CROSS_DESIGN'
        definition = self.definition(target) if target is not None and cls == 'SnpMapRef' else None
        if status == 'RESOLVED' and name in _INTERFACES and value(target, 'AdapterIntfType') != _INTERFACES[name]:
            status = 'INTERFACE_MISMATCH'
        return {'field': name, 'target_class': cls, 'declared_identity': identity,
                'candidate_node_ids': [n['node_id'] for n in candidates],
                'target_node_id': target['node_id'] if status == 'RESOLVED' else None,
                'resolution': status, 'rule': 'class_key_same_'+scope if scope else 'class_key',
                'definition_reference': definition,
                'evidence': _evidence([node, *candidates], (name, CLASSES[cls][2], 'IOwnerMapping', 'IOwnerDs', 'IOwnerMapComp', 'IOwnerMapCp'))}

    def diagnostics(self, node):
        diagnostics = []; cls = node['attributes']['object_class']
        if cls == 'SnpPhyNode':
            component = self.unique(node, 'IMapComp', 'SnpMapComp'); cp = self.unique(node, 'IMapCp', 'SnpMapCp')
            owner = self.unique(cp, 'IOwnerMapComp', 'SnpMapComp') if cp else None
            if component is not None and cp is not None and component != owner:
                diagnostics.append({'code': 'COMPONENT_CONNECTION_POINT_MISMATCH',
                    'message': 'The declared connection point does not have the uniquely declared component as owner; no reference was repaired.',
                    'node_ids': [n['node_id'] for n in (node, component, cp, owner) if n is not None],
                    'evidence': _evidence([node, component, cp, owner])})
        parent_field = {'SnpPhyNode': 'IParPhyNode', 'SnpExecUnit': 'IParExecUnit'}.get(cls)
        if parent_field:
            visited = {}; current = node
            while current is not None:
                if current['node_id'] in visited:
                    related = list(visited.values())
                    diagnostics.append({'code': 'PHYSICAL_PARENT_CYCLE' if cls == 'SnpPhyNode' else 'EXECUTION_UNIT_PARENT_CYCLE',
                        'message': 'Declared physical parent relationships contain a cycle; they establish no execution order.',
                        'node_ids': list(visited), 'evidence': _evidence(related)})
                    break
                if len(visited) >= MAX_SCOPE_DEPTH: raise UnsupportedSourceError('ODI physical parent depth limit exceeded.')
                visited[current['node_id']] = current
                reference = self.reference(current, parent_field, cls, 'design')
                current = self.index.nodes[reference['target_node_id']] if reference['resolution'] == 'RESOLVED' else None
        return diagnostics

    def record(self, node, *, chain, designs, resolution):
        cls = node['attributes']['object_class']; key = CLASSES[cls][2]
        diagnostics = self.diagnostics(node)
        if resolution == 'CYCLE':
            diagnostics.append({'code': 'PROPERTY_SCOPE_CYCLE',
                'message': 'The declared property parent chain contains a cycle; no physical scope or effective option is selected.',
                'node_ids': chain, 'evidence': _evidence([self.index.nodes[n] for n in chain if n in self.index.nodes])})
        return {'node_id': node['node_id'], 'object_class': cls, 'declared_identity': value(node, key),
                'name': node['name'], 'declared_fields': copy.deepcopy(node['attributes']['declared_fields']),
                'references': [self.reference(node, *spec) for spec in _REFERENCE_FIELDS[cls]],
                'scope_chain_node_ids': chain, 'scope_design_node_ids': designs, 'scope_resolution': resolution,
                'diagnostics': diagnostics,
                'evidence': _evidence([node, *[self.index.nodes[n] for n in chain if n in self.index.nodes]])}

    def property_scope(self, node):
        chain = []; designs = []; anchors = []; visited = set(); current = node
        resolution = 'BOUND'; physical = False; mapping = self.mapping(node)
        while current is not None:
            identity = current['node_id']
            if identity in visited: resolution = 'CYCLE'; physical = True; break
            if len(visited) >= MAX_SCOPE_DEPTH: raise UnsupportedSourceError('ODI physical property scope depth limit exceeded.')
            visited.add(identity); chain.append(identity)
            active = []
            for name, cls in _PROPERTY_SCOPE:
                declarations = [f for f in current['attributes']['declared_fields'] if f['name'] == name]
                if not declarations or (len(declarations) == 1 and not declarations[0]['nested_child_count'] and declarations[0]['value'] in ('', 'null')):
                    continue
                physical = True; active.append(name); ref = self.reference(current, name, cls, 'mapping')
                if ref['resolution'] != 'RESOLVED':
                    resolution = 'AMBIGUOUS' if ref['resolution'].startswith('AMBIGUOUS') else 'UNRESOLVED'
                    continue
                target = self.index.nodes[ref['target_node_id']]; design = self.design(target)
                anchors.append(target['node_id'])
                if design is None or self.mapping(target) != mapping: resolution = 'UNRESOLVED'
                elif design['node_id'] not in designs: designs.append(design['node_id'])
            if len(active) > 1: resolution = 'CONFLICT'
            parents = [f for f in current['attributes']['declared_fields'] if f['name'] == 'IMapPropParent']
            if not parents or (len(parents) == 1 and not parents[0]['nested_child_count'] and parents[0]['value'] in ('', 'null')):
                break
            parent = self.reference(current, 'IMapPropParent', 'SnpMapProp', 'mapping')
            if active: resolution = 'CONFLICT'
            if parent['resolution'] != 'RESOLVED':
                resolution = 'AMBIGUOUS' if parent['resolution'].startswith('AMBIGUOUS') else 'UNRESOLVED'
                physical = True; break
            current = self.index.nodes[parent['target_node_id']]
        if len(designs) > 1: resolution = 'CONFLICT'
        if not designs and physical and resolution == 'BOUND': resolution = 'UNRESOLVED'
        chain.extend(n for n in [*anchors, *designs] if n not in chain)
        return physical, chain, designs, resolution


def analyze_physical(index):
    graph = index.graph
    if graph['source']['platform'] != 'odi' or graph['extensions']['source_analysis']['profile'] != GRAPH_PROFILE:
        raise SchemaValidationError('ODI physical analysis requires the original native mapping graph profile.')
    for cls, limit in [('SnpDeploySpec', MAX_DESIGNS), ('SnpPhyNode', MAX_PHYSICAL_NODES),
                       ('SnpExecUnit', MAX_EXECUTION_UNITS), ('SnpExecUnitGrp', MAX_EXECUTION_GROUPS),
                       ('SnpMapProp', MAX_PROPERTIES)]:
        if len(index.classes[cls]) > limit: raise UnsupportedSourceError('ODI physical '+cls+' declaration count limit exceeded.')
    resolver = PhysicalIndex(index); designs = []; by_design = {}; unscoped = []
    for node in index.classes['SnpDeploySpec']:
        document = {'design_node_id': node['node_id'], 'mapping_node_id': resolver.mapping(node),
                    'name': node['name'], 'declared_fields': copy.deepcopy(node['attributes']['declared_fields']),
                    'owner_reference': resolver.reference(node, 'IOwnerMapping', 'SnpMapping'),
                    'context_reference': resolver.reference(node, 'IMapRef', 'SnpMapRef', 'mapping'),
                    'physical_nodes': [], 'execution_units': [], 'execution_groups': [], 'properties': [],
                    'selected_for_execution': None, 'execution_order_status': 'NOT_ESTABLISHED',
                    'unresolved_items': [
                        'Physical designs, parent/group links, cleanup/concurrency flags and option scopes are declarations; no design or execution order is selected.',
                        'KM definitions, templates/effective options, source runtime behavior and Databricks physical table/path/parameter bindings require verification.'],
                    'evidence': _evidence([node])}
        designs.append(document); by_design[node['node_id']] = document
    for cls, bucket in [('SnpPhyNode', 'physical_nodes'), ('SnpExecUnit', 'execution_units'), ('SnpExecUnitGrp', 'execution_groups')]:
        for node in index.classes[cls]:
            target = resolver.design(node); mapping = resolver.mapping(node)
            bound = target is not None and mapping is not None
            chain = [node['node_id'], target['node_id']] if target is not None else [node['node_id']]
            record = resolver.record(node, chain=chain, designs=[target['node_id']] if target else [], resolution='BOUND' if bound else 'UNRESOLVED')
            if bound: by_design[target['node_id']][bucket].append(record)
            else: unscoped.append(record)
    for node in index.classes['SnpMapProp']:
        physical, chain, scopes, resolution = resolver.property_scope(node)
        if not physical: continue
        record = resolver.record(node, chain=chain, designs=scopes, resolution=resolution)
        if resolution == 'BOUND' and len(scopes) == 1: by_design[scopes[0]]['properties'].append(record)
        else: unscoped.append(record)
    references = []; records = []
    for design in designs:
        related = [index.nodes[design['design_node_id']]]
        if design['mapping_node_id'] is not None: related.append(index.nodes[design['mapping_node_id']])
        for bucket in ('physical_nodes', 'execution_units', 'execution_groups', 'properties'):
            records.extend(design[bucket]); related.extend(index.nodes[r['node_id']] for r in design[bucket])
        design['evidence'] = _evidence(related)
        references.extend([design['owner_reference'], design['context_reference']])
    records.extend(unscoped)
    for record in records: references.extend(record['references'])
    destructive = [r['node_id'] for r in records if r['object_class'] == 'SnpMapProp'
                   and _record_value(r, 'Name') in ('TRUNCATE', 'TRUNCATE_TARGET_TABLE', 'OVERWRITE_TARGET_TABLE')
                   and _record_value(r, 'PropValue') == 'true']
    summary = {'design_count': len(designs), 'physical_node_count': sum(r['object_class'] == 'SnpPhyNode' for r in records),
               'execution_unit_count': sum(r['object_class'] == 'SnpExecUnit' for r in records),
               'execution_group_count': sum(r['object_class'] == 'SnpExecUnitGrp' for r in records),
               'property_count': sum(r['object_class'] == 'SnpMapProp' for r in records),
               'unscoped_record_count': len(unscoped),
               'destructive_property_count': len(destructive),
               'reference_resolution_counts': dict(Counter(r['resolution'] for r in references)),
               'definition_resolution_counts': dict(Counter(r['definition_reference']['resolution'] for r in references if r['definition_reference'] is not None))}
    document = {'analysis_version': '1.0', 'profile': PROFILE, 'ir_id': graph['ir_id'],
                'source_analysis_sha256': graph['provenance']['source_analysis_sha256'],
                'evidence_files': copy.deepcopy(graph['evidence_files']), 'designs': designs,
                'unscoped_records': unscoped, 'summary': summary, 'status': 'REVIEW_REQUIRED',
                'destructive_property_node_ids': destructive,
                'execution_status': 'NOT_RUN', 'semantic_status': 'NOT_VERIFIED', 'migration_accepted': False}
    validate_document(document, 'odi_physical_analysis'); validate_evidence_references(document)
    return document


def _record_value(record, name):
    fields = [f for f in record['declared_fields'] if f['name'] == name]
    return fields[0]['value'] if len(fields) == 1 and not fields[0]['nested_child_count'] else None


def _validate_accounting(document):
    references = []; records = []; designs = document['designs']
    for design in designs:
        references.extend([design['owner_reference'], design['context_reference']])
        for bucket, cls in [('physical_nodes', 'SnpPhyNode'), ('execution_units', 'SnpExecUnit'),
                            ('execution_groups', 'SnpExecUnitGrp'), ('properties', 'SnpMapProp')]:
            for record in design[bucket]:
                if (record['object_class'] != cls or record['scope_resolution'] != 'BOUND'
                        or record['scope_design_node_ids'] != [design['design_node_id']]):
                    raise SchemaValidationError('Physical record class or scope differs from its design bucket.')
                records.append(record)
    if any(r['scope_resolution'] == 'BOUND' for r in document['unscoped_records']):
        raise SchemaValidationError('Unscoped physical records must retain an unresolved scope finding.')
    records.extend(document['unscoped_records'])
    identities = [d['design_node_id'] for d in designs] + [r['node_id'] for r in records]
    if len(identities) != len(set(identities)):
        raise SchemaValidationError('Physical records are duplicated across design or unscoped buckets.')
    for record in records: references.extend(record['references'])
    destructive = [r['node_id'] for r in records if r['object_class'] == 'SnpMapProp'
                   and _record_value(r, 'Name') in ('TRUNCATE', 'TRUNCATE_TARGET_TABLE', 'OVERWRITE_TARGET_TABLE')
                   and _record_value(r, 'PropValue') == 'true']
    expected = {'design_count': len(designs), 'physical_node_count': sum(r['object_class'] == 'SnpPhyNode' for r in records),
                'execution_unit_count': sum(r['object_class'] == 'SnpExecUnit' for r in records),
                'execution_group_count': sum(r['object_class'] == 'SnpExecUnitGrp' for r in records),
                'property_count': sum(r['object_class'] == 'SnpMapProp' for r in records),
                'unscoped_record_count': len(document['unscoped_records']), 'destructive_property_count': len(destructive),
                'reference_resolution_counts': dict(Counter(r['resolution'] for r in references)),
                'definition_resolution_counts': dict(Counter(r['definition_reference']['resolution'] for r in references if r['definition_reference'] is not None))}
    if expected != document['summary'] or destructive != document['destructive_property_node_ids']:
        raise SchemaValidationError('Physical summary or destructive-property declarations do not account for every retained record.')


def validate_physical_analysis(document, graph):
    from .behavior import MappingIndex
    validate_document(document, 'odi_physical_analysis'); validate_evidence_references(document)
    _validate_accounting(document)
    validate_export_graph(graph)
    if document != analyze_physical(MappingIndex(graph)):
        raise SchemaValidationError('ODI physical analysis does not replay from all original scoped declarations.')


def extract_physical_analysis(behavior_analysis):
    from .behavior_v4 import PROFILE as BEHAVIOR_PROFILE
    validate_document(behavior_analysis, 'source_behavior_analysis'); validate_evidence_references(behavior_analysis)
    if behavior_analysis['profile'] != BEHAVIOR_PROFILE:
        raise SchemaValidationError('Physical analysis extraction requires behavior profile version 4.')
    roots = [r for r in behavior_analysis['requirements'] if r['kind'] == 'ODI_EXPORT_REVIEW']
    if len(roots) != 1 or 'physical_analysis_manifest' not in roots[0]['declarations']:
        raise SchemaValidationError('Physical analysis manifest is missing or ambiguous.')
    document = copy.deepcopy(roots[0]['declarations']['physical_analysis_manifest'])
    if 'designs' in document: raise SchemaValidationError('Physical manifest must not duplicate design requirements.')
    document['designs'] = [copy.deepcopy(r['declarations']) for r in behavior_analysis['requirements'] if r['kind'] == 'ODI_PHYSICAL_DESIGN']
    if (document.get('ir_id') != behavior_analysis['ir_id'] or document.get('source_analysis_sha256') != behavior_analysis['source_analysis_sha256']
            or document.get('evidence_files') != behavior_analysis['evidence_files']):
        raise SchemaValidationError('Physical analysis manifest belongs to another source.')
    validate_document(document, 'odi_physical_analysis'); validate_evidence_references(document)
    _validate_accounting(document)
    if len(document['designs']) != document['summary']['design_count'] or len({d['design_node_id'] for d in document['designs']}) != len(document['designs']):
        raise SchemaValidationError('Physical design requirements are missing or duplicated.')
    subjects = [r['subject_node_id'] for r in behavior_analysis['requirements'] if r['kind'] == 'ODI_PHYSICAL_DESIGN']
    if subjects != [d['design_node_id'] for d in document['designs']]:
        raise SchemaValidationError('Physical design requirements differ from the original mapping design declarations.')
    mapped = {}
    for requirement in behavior_analysis['requirements']:
        if requirement['kind'] != 'ODI_MAPPING': continue
        for design in requirement['declarations']['physical_designs']:
            if design['node_id'] in mapped:
                raise SchemaValidationError('A physical design has multiple declared mapping owners.')
            mapped[design['node_id']] = requirement['subject_node_id']
    retained_mapped = set()
    for design in document['designs']:
        owner = design['owner_reference']; mapping = design['mapping_node_id']; identity = design['design_node_id']
        if owner['field'] != 'IOwnerMapping' or owner['target_class'] != 'SnpMapping':
            raise SchemaValidationError('Physical design ownership requires its original mapping reference.')
        if mapping is None:
            if owner['resolution'] == 'RESOLVED' or owner['target_node_id'] is not None or identity in mapped:
                raise SchemaValidationError('An orphan physical design cannot claim a resolved mapping owner.')
        elif owner['resolution'] != 'RESOLVED' or owner['target_node_id'] != mapping or mapped.get(identity) != mapping:
            raise SchemaValidationError('Physical design ownership differs from its declared mapping requirement.')
        else:
            retained_mapped.add(identity)
    if retained_mapped != set(mapped):
        raise SchemaValidationError('A mapping physical-design declaration is missing its review requirement.')
    return document
