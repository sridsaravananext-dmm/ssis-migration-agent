"""Declared ODI topology and all context candidates without effective bindings."""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import re

from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ..base import UnsupportedSourceError
from ..xml_export import validate_export_graph
from .native import CLASSES, EXTERNAL_CLASSES, PROFILE as GRAPH_PROFILE, numeric, value
from .physical import PhysicalIndex, _evidence

PROFILE = 'odi_12c_topology_dependencies_v1'
MAX_RECORDS = 4096
MAX_EXECUTION_UNITS = 2048
MAX_CONTEXT_CANDIDATES = 8192
MAX_PARENT_DEPTH = 64
MAX_TEXT_RECORDS = 2048
MAX_TEXT_CHARS = 65536
MAX_TEXT_BINDINGS = 4096
MAX_BOUND_TEXT_RECORDS = 4096
MAX_BOUND_TEXT_CHARS = 4 * 1024 * 1024
MAX_BOUND_TEXT_FIELDS = 32768
MAX_EMITTED_EVIDENCE = 200000
MAX_MARKERS = 4096

_KEYS = {'SnpLschema': 'ILschema', 'SnpContext': 'IContext', 'SnpConnect': 'IConnect',
         'SnpPschema': 'IPschema', 'SnpPschemaCont': 'GlobalId', 'SnpConnectProp': 'GlobalId'}
_REFERENCES = {
    'SnpLschema': [('ITechno', 'SnpTechno'), ('ITgtDbLs', 'SnpLschema')],
    'SnpContext': [],
    'SnpConnect': [('ITechno', 'SnpTechno'), ('ParIConnect', 'SnpConnect')],
    'SnpPschema': [('IConnect', 'SnpConnect'), ('ITargetPschema', 'SnpPschema')],
    'SnpPschemaCont': [('ILschema', 'SnpLschema'), ('IContext', 'SnpContext'), ('IPschema', 'SnpPschema')],
    'SnpConnectProp': [('IConnect', 'SnpConnect')],
}
_TEXT_FIELDS = ('ITxtJavaUrl', 'ITxtJndiUrl', 'ITxtWsBaseUrl')
_PARENTS = {'SnpConnect': ('ParIConnect', 'CONNECTION_PARENT_CYCLE'),
            'SnpLschema': ('ITgtDbLs', 'LOGICAL_SCHEMA_TARGET_CYCLE'),
            'SnpPschema': ('ITargetPschema', 'PHYSICAL_SCHEMA_TARGET_CYCLE')}


def _field_value(record, name):
    fields = [f for f in record['declared_fields'] if f['name'] == name]
    return fields[0]['value'] if len(fields) == 1 and not fields[0]['nested_child_count'] else None


class TopologyIndex(PhysicalIndex):
    """Add topology keys to a local index; never register new native graph edges."""
    def __init__(self, index):
        super().__init__(index); self.emitted_evidence = 0
        for cls, key in {**_KEYS, 'SnpTechno': 'ITechno'}.items():
            if cls not in CLASSES:
                for node in index.classes[cls]: self.keys[(cls, value(node, key))].append(node)
        self.bridge_pairs = defaultdict(list); self.property_pairs = defaultdict(list)
        for node in index.classes['SnpPschemaCont']:
            pair = (value(node, 'ILschema'), value(node, 'IContext'))
            if all(numeric(part) for part in pair): self.bridge_pairs[pair].append(node)
        for node in index.classes['SnpConnectProp']:
            pair = (value(node, 'IConnect'), value(node, 'PropKey'))
            if numeric(pair[0]) and pair[1] not in (None, '', 'null'): self.property_pairs[pair].append(node)

    def _reserve_evidence(self, nodes, names=None):
        """Budget expanded references from raw evidence before any deep copies."""
        identities = set()
        for node in nodes:
            if node is None: continue
            for reference in node['evidence']: identities.add(tuple(sorted(reference.items())))
            for field in node['attributes'].get('declared_fields', []):
                if names is None or field['name'] in names:
                    for reference in field['evidence']: identities.add(tuple(sorted(reference.items())))
        self.emitted_evidence += len(identities)
        if self.emitted_evidence > MAX_EMITTED_EVIDENCE:
            raise UnsupportedSourceError('ODI topology expanded-evidence output budget exceeded.')

    def bounded_evidence(self, nodes, names=None):
        self._reserve_evidence(nodes, names)
        return _evidence(nodes, names)

    def reference(self, node, name, cls, scope=None):
        _, _, candidates = self.candidates(node, name, cls)
        self._reserve_evidence([node, *candidates], (name, CLASSES[cls][2], 'IOwnerMapping', 'IOwnerDs', 'IOwnerMapComp', 'IOwnerMapCp'))
        return super().reference(node, name, cls, scope)

    def definition(self, reference):
        cls = EXTERNAL_CLASSES.get(value(reference, 'AdapterIntfType'))
        _, _, candidates = self.candidates(reference, 'IRefId', cls)
        self._reserve_evidence([reference, *candidates], ('IRefId', 'RefGuid', 'GlobalId', 'AdapterName', 'AdapterIntfType', 'QualifiedName', CLASSES.get(cls, (None, None, None))[2]))
        return super().definition(reference)

    def global_reference(self, node, name, cls):
        status, identity, candidates = self.candidates(node, name, cls)
        key = _KEYS.get(cls, 'ITechno' if cls == 'SnpTechno' else None)
        return {'field': name, 'target_class': cls, 'declared_identity': identity,
                'candidate_node_ids': [n['node_id'] for n in candidates],
                'target_node_id': candidates[0]['node_id'] if status == 'RESOLVED' else None,
                'resolution': status, 'rule': 'class_key', 'definition_reference': None,
                'evidence': self.bounded_evidence([node, *candidates], (name, key, 'GlobalId'))}

    def parent_chain(self, node, cls):
        field, _ = _PARENTS[cls]; visited = []; current = node
        while current is not None:
            if current['node_id'] in visited: return visited, 'CYCLE'
            if len(visited) >= MAX_PARENT_DEPTH:
                raise UnsupportedSourceError('ODI topology parent-chain depth limit exceeded.')
            visited.append(current['node_id']); reference = self.global_reference(current, field, cls)
            if reference['resolution'] == 'RESOLVED': current = self.index.nodes[reference['target_node_id']]
            elif reference['resolution'] == 'NONNUMERIC_DECLARATION' and reference['declared_identity'] in ('', 'null'):
                return visited, 'NO_BOUND_PARENT_DECLARATION'
            else:
                return visited, 'AMBIGUOUS' if reference['resolution'].startswith('AMBIGUOUS') else 'UNRESOLVED'
        return visited, 'UNRESOLVED'

    def diagnostics(self, node):
        cls = node['attributes']['object_class']; key = _KEYS[cls]; identity = value(node, key)
        findings = []

        def add(code, message, nodes):
            findings.append({'code': code, 'message': message,
                             'node_ids': list(dict.fromkeys(n['node_id'] for n in nodes)), 'evidence': self.bounded_evidence(nodes)})

        if identity not in (None, '', 'null') and len(self.keys[(cls, identity)]) > 1:
            add('DUPLICATE_CLASS_KEY', 'The topology class/key has multiple exported records; no winner is selected.', self.keys[(cls, identity)])
        if cls == 'SnpPschemaCont':
            matches = self.bridge_pairs.get((value(node, 'ILschema'), value(node, 'IContext')), [])
            if len(matches) > 1:
                add('DUPLICATE_CONTEXT_ASSOCIATION', 'Multiple associations declare the same logical-schema/context pair; no physical schema is selected.', matches)
        if cls == 'SnpConnectProp':
            matches = self.property_pairs.get((value(node, 'IConnect'), value(node, 'PropKey')), [])
            if len(matches) > 1:
                add('DUPLICATE_CONNECTION_PROPERTY', 'Multiple values declare the same connection/property pair; no effective property or precedence is established.', matches)
        if cls in _PARENTS:
            chain, status = self.parent_chain(node, cls)
            if status == 'CYCLE': add(_PARENTS[cls][1], 'Declared topology links contain a cycle; no inheritance or execution order is established.', [self.index.nodes[n] for n in chain])
        return findings

    def record(self, node):
        cls = node['attributes']['object_class']
        return {'node_id': node['node_id'], 'object_class': cls, 'declared_key_field': _KEYS[cls],
                'declared_identity': value(node, _KEYS[cls]),
                'declared_fields': copy.deepcopy(node['attributes']['declared_fields']),
                'references': [self.global_reference(node, *spec) for spec in _REFERENCES[cls]],
                'diagnostics': self.diagnostics(node), 'evidence': self.bounded_evidence([node])}

    def candidate(self, association, logical, context_reference):
        logical_ref = self.global_reference(association, 'ILschema', 'SnpLschema')
        context_ref = self.global_reference(association, 'IContext', 'SnpContext')
        physical_ref = self.global_reference(association, 'IPschema', 'SnpPschema')
        physical = self.index.nodes.get(physical_ref['target_node_id'])
        connection_ref = self.global_reference(physical, 'IConnect', 'SnpConnect') if physical else None
        connection = self.index.nodes.get(connection_ref['target_node_id']) if connection_ref else None
        chain, chain_status = self.parent_chain(connection, 'SnpConnect') if connection else ([], 'UNRESOLVED')
        declared = context_reference.get('definition_reference') if context_reference and context_reference['resolution'] == 'RESOLVED' else None
        declared_context = declared['target_node_id'] if declared and declared['resolution'] == 'RESOLVED' else None
        relation = 'UNRESOLVED' if declared_context is None or context_ref['target_node_id'] is None else 'MATCH' if declared_context == context_ref['target_node_id'] else 'DIFFERENT'
        refs = [logical_ref, context_ref, physical_ref, connection_ref]
        valid = (all(r is not None and r['resolution'] == 'RESOLVED' for r in refs)
                 and logical_ref['target_node_id'] == logical['node_id'] and chain_status == 'NO_BOUND_PARENT_DECLARATION'
                 and not self.diagnostics(association))
        related = [association, logical, physical, connection, *[self.index.nodes[n] for n in chain],
                   self.index.nodes.get(context_ref['target_node_id'])]
        return {'association_node_id': association['node_id'], 'logical_schema_node_id': logical['node_id'],
                'context_node_id': context_ref['target_node_id'], 'physical_schema_node_id': physical_ref['target_node_id'],
                'connection_node_id': connection_ref['target_node_id'] if connection_ref else None,
                'logical_schema_reference': logical_ref, 'context_reference': context_ref,
                'physical_schema_reference': physical_ref, 'connection_reference': connection_ref,
                'connection_chain_node_ids': chain, 'connection_chain_status': chain_status,
                'declared_context_relation': relation, 'effective_binding': None,
                'resolution': 'RESOLVED_DECLARATIONS_ONLY' if valid else 'REVIEW_REQUIRED', 'evidence': self.bounded_evidence(related)}


def _text_bindings(index, resolver):
    texts = [*index.classes['SnpTxtHeader'], *index.classes['SnpMtxt']]
    if len(texts) > MAX_TEXT_RECORDS: raise UnsupportedSourceError('ODI topology text-record count limit exceeded.')
    keyed = defaultdict(list)
    for node in texts: keyed[value(node, 'ITxt')].append(node)
    bindings = []; bound_records = 0; bound_chars = 0; bound_fields = 0
    for connection in index.classes['SnpConnect']:
        for name in _TEXT_FIELDS:
            if len(bindings) >= MAX_TEXT_BINDINGS: raise UnsupportedSourceError('ODI topology text-binding count limit exceeded.')
            fields = [f for f in connection['attributes']['declared_fields'] if f['name'] == name]
            identity = value(connection, name); matches = keyed.get(identity, []) if numeric(identity) else []
            if not fields: resolution = 'MISSING_DECLARATION'
            elif len(fields) != 1 or fields[0]['nested_child_count']: resolution = 'AMBIGUOUS_DECLARATION'
            else: resolution = 'ONE_DECLARED_RECORD' if len(matches) == 1 else 'AMBIGUOUS' if matches else 'MISSING' if numeric(identity) else 'UNBOUND_DECLARATION'
            records = []
            for node in matches:
                if any(len(f['value']) > MAX_TEXT_CHARS for f in node['attributes']['declared_fields']):
                    raise UnsupportedSourceError('ODI topology text declaration exceeds its character limit.')
                bound_records += 1; bound_chars += sum(len(f['value']) for f in node['attributes']['declared_fields'])
                bound_fields += len(node['attributes']['declared_fields'])
                if bound_records > MAX_BOUND_TEXT_RECORDS or bound_chars > MAX_BOUND_TEXT_CHARS or bound_fields > MAX_BOUND_TEXT_FIELDS:
                    raise UnsupportedSourceError('ODI topology bound-text output budget exceeded.')
                evidence = resolver.bounded_evidence([node])
                records.append({'node_id': node['node_id'], 'object_class': node['attributes']['object_class'],
                                'declared_fields': copy.deepcopy(node['attributes']['declared_fields']), 'evidence': evidence})
            diagnostics = []
            for node in matches:
                text, full = value(node, 'Txt'), value(node, 'FullTxt')
                if text is not None and full is not None and text != full:
                    diagnostics.append({'code': 'TEXT_REPRESENTATION_DISAGREEMENT',
                        'message': 'Original Txt and FullTxt declarations disagree; no authoritative or effective text is selected.',
                        'node_ids': [node['node_id']], 'evidence': resolver.bounded_evidence([node], ('ITxt', 'Txt', 'FullTxt'))})
            bindings.append({'connection_node_id': connection['node_id'], 'field': name,
                'declared_text_identity': identity, 'resolution': resolution, 'records': records,
                'effective_text': None, 'interpretation': 'DECLARED_TEXT_ONLY', 'diagnostics': diagnostics,
                'evidence': resolver.bounded_evidence([connection, *matches], (name, 'ITxt', 'Txt', 'FullTxt', 'Enc', 'EncKey', 'EncKeyVect'))})
    return bindings


def _markers(records):
    declarations = []; total = 0
    patterns = [('ANGLE_BRACKET_TOKEN', re.compile(r'<[^<>\r\n]{1,256}>')),
                ('SHELL_PARAMETER_TEXT', re.compile(r'\$\{[^{}]{1,512}\}'))]
    for record in records:
        for field in record['declared_fields']:
            if len(field['value']) > MAX_TEXT_CHARS:
                raise UnsupportedSourceError('ODI topology field declaration exceeds its character limit.')
            markers = [{'kind': kind, 'token': match[0]} for kind, pattern in patterns for match in pattern.finditer(field['value'])]
            total += len(markers)
            if total > MAX_MARKERS: raise UnsupportedSourceError('ODI topology marker count limit exceeded.')
            if markers: declarations.append({'node_id': record['node_id'], 'field': field['name'], 'markers': markers, 'evidence': copy.deepcopy(field['evidence'])})
    return declarations


def _summary(document):
    counts = Counter(r['object_class'] for r in document['records'])
    candidates = [c for u in document['execution_unit_uses'] for c in u['context_candidates']]
    return {'record_count': len(document['records']), 'logical_schema_count': counts['SnpLschema'],
            'context_count': counts['SnpContext'], 'connection_count': counts['SnpConnect'],
            'physical_schema_count': counts['SnpPschema'], 'context_association_count': counts['SnpPschemaCont'],
            'connection_property_count': counts['SnpConnectProp'],
            'execution_unit_use_count': len(document['execution_unit_uses']), 'context_candidate_count': len(candidates),
            'declared_context_match_count': sum(c['declared_context_relation'] == 'MATCH' for c in candidates),
            'alternative_context_candidate_count': sum(c['declared_context_relation'] == 'DIFFERENT' for c in candidates),
            'text_binding_count': len(document['text_bindings']),
            'declared_text_record_count': len({r['node_id'] for b in document['text_bindings'] for r in b['records']}),
            'opaque_marker_field_count': len(document['opaque_marker_declarations']),
            'opaque_marker_token_count': sum(len(d['markers']) for d in document['opaque_marker_declarations'])}


def _validate_accounting(document):
    records = document['records']; uses = document['execution_unit_uses']
    identities = [r['node_id'] for r in records]; units = [u['execution_unit_node_id'] for u in uses]
    if len(set(identities)) != len(identities) or len(set(units)) != len(units):
        raise SchemaValidationError('Topology records or execution-unit uses are duplicated.')
    if document['summary'] != _summary(document): raise SchemaValidationError('Topology summary does not account for every retained declaration and candidate.')
    known = set(identities); by_id = {r['node_id']: r for r in records}
    for record in records:
        if record['declared_key_field'] != _KEYS[record['object_class']] or record['declared_identity'] != _field_value(record, record['declared_key_field']):
            raise SchemaValidationError('Topology identity differs from its retained typed fields.')
    for unit in uses:
        associations = [c['association_node_id'] for c in unit['context_candidates']]
        if len(set(associations)) != len(associations): raise SchemaValidationError('A unit repeats a context association candidate.')
        logical_ref = unit['logical_schema_reference']
        definition = logical_ref['definition_reference'] if logical_ref['resolution'] == 'RESOLVED' else None
        logical = by_id.get(definition['target_node_id']) if definition and definition['resolution'] == 'RESOLVED' else None
        expected = [r['node_id'] for r in records if r['object_class'] == 'SnpPschemaCont'
                    and logical is not None and numeric(_field_value(r, 'ILschema'))
                    and _field_value(r, 'ILschema') == logical['declared_identity']]
        if associations != expected: raise SchemaValidationError('A unit omits, reorders or invents a supplied logical-schema context candidate.')
        for candidate in unit['context_candidates']:
            if candidate['association_node_id'] not in by_id or by_id[candidate['association_node_id']]['object_class'] != 'SnpPschemaCont':
                raise SchemaValidationError('A topology candidate does not identify a retained association.')
            for key, cls in [('logical_schema_node_id', 'SnpLschema'), ('context_node_id', 'SnpContext'),
                             ('physical_schema_node_id', 'SnpPschema'), ('connection_node_id', 'SnpConnect')]:
                target = candidate[key]
                if target is not None and (target not in by_id or by_id[target]['object_class'] != cls):
                    raise SchemaValidationError('A topology candidate endpoint has a missing or incorrect class.')
    connections = {r['node_id'] for r in records if r['object_class'] == 'SnpConnect'}
    selectors = [(b['connection_node_id'], b['field']) for b in document['text_bindings']]
    if len(set(selectors)) != len(selectors) or set(selectors) != {(identity, name) for identity in connections for name in _TEXT_FIELDS}:
        raise SchemaValidationError('Topology text references omit or duplicate a declared connection selector.')
    for marker in document['opaque_marker_declarations']:
        if marker['node_id'] not in known and not any(marker['node_id'] == r['node_id'] for b in document['text_bindings'] for r in b['records']):
            raise SchemaValidationError('Topology marker declaration lacks its retained source record.')


def analyze_topology(index):
    graph = index.graph
    if graph['source']['platform'] != 'odi' or graph['extensions']['source_analysis']['profile'] != GRAPH_PROFILE:
        raise SchemaValidationError('ODI topology analysis requires the original native mapping profile.')
    nodes = [n for n in graph['nodes'] if n['attributes'].get('object_class') in _KEYS]
    if len(nodes) > MAX_RECORDS or len(index.classes['SnpExecUnit']) > MAX_EXECUTION_UNITS:
        raise UnsupportedSourceError('ODI topology record or execution-unit count limit exceeded.')
    resolver = TopologyIndex(index); records = [resolver.record(node) for node in nodes]
    bridges = defaultdict(list)
    for association in index.classes['SnpPschemaCont']:
        if numeric(value(association, 'ILschema')): bridges[value(association, 'ILschema')].append(association)
    uses = []; candidate_count = 0; related = list(nodes)
    for unit in index.classes['SnpExecUnit']:
        owner = resolver.reference(unit, 'IOwnerDs', 'SnpDeploySpec', 'mapping')
        design = resolver.unique(unit, 'IOwnerDs', 'SnpDeploySpec')
        logical_ref = resolver.reference(unit, 'ILschemaRef', 'SnpMapRef', 'mapping')
        logical_definition = logical_ref['definition_reference'] if logical_ref['resolution'] == 'RESOLVED' else None
        logical = index.nodes.get(logical_definition['target_node_id']) if logical_definition and logical_definition['resolution'] == 'RESOLVED' else None
        context_ref = resolver.reference(design, 'IMapRef', 'SnpMapRef', 'mapping') if design else None
        candidates = []
        for association in bridges.get(value(logical, 'ILschema'), []) if logical else []:
            candidate_count += 1
            if candidate_count > MAX_CONTEXT_CANDIDATES: raise UnsupportedSourceError('ODI topology context-candidate count limit exceeded.')
            candidates.append(resolver.candidate(association, logical, context_ref))
        related.extend([unit, design])
        for reference in [logical_ref, context_ref]:
            if reference is not None: related.extend(index.nodes[n] for n in reference['candidate_node_ids'])
        uses.append({'execution_unit_node_id': unit['node_id'], 'declared_fields': copy.deepcopy(unit['attributes']['declared_fields']),
            'design_node_id': owner['target_node_id'], 'mapping_node_id': resolver.mapping(unit),
            'owner_design_reference': owner, 'logical_schema_reference': logical_ref,
            'declared_context_reference': context_ref, 'context_candidates': candidates,
            'unresolved_items': ['Every supplied context association is a declaration candidate; design/context selection, effective values, inheritance, technology semantics and Databricks bindings remain unverified.'],
            'evidence': resolver.bounded_evidence([unit, design])})
    text_bindings = _text_bindings(index, resolver)
    text_records = {}
    for binding in text_bindings:
        for record in binding['records']:
            text_records[record['node_id']] = record
    evidence = resolver.bounded_evidence([*related, *[index.nodes[n] for n in text_records]])
    document = {'analysis_version': '1.0', 'profile': PROFILE, 'ir_id': graph['ir_id'],
        'source_analysis_sha256': graph['provenance']['source_analysis_sha256'], 'evidence_files': copy.deepcopy(graph['evidence_files']),
        'records': records, 'text_bindings': text_bindings, 'execution_unit_uses': uses,
        'opaque_marker_declarations': _markers([*records, *text_records.values()]), 'summary': {},
        'unresolved_items': ['Source models expose name-only logical-schema/context declarations; names do not create identity bindings.',
            'Default/undefined flags, JDBC text, patterns, placeholders, connection parent links and technology IDs remain opaque declarations; no context, inherited option, connection or target table is selected.'],
        'status': 'REVIEW_REQUIRED', 'execution_status': 'NOT_RUN', 'semantic_status': 'NOT_VERIFIED',
        'migration_accepted': False, 'evidence': evidence}
    document['summary'] = _summary(document)
    validate_document(document, 'odi_topology_analysis'); validate_evidence_references(document); _validate_accounting(document)
    return document


def validate_topology_analysis(document, graph):
    from .behavior import MappingIndex
    validate_document(document, 'odi_topology_analysis'); validate_evidence_references(document); _validate_accounting(document)
    validate_export_graph(graph)
    if document != analyze_topology(MappingIndex(graph)):
        raise SchemaValidationError('ODI topology analysis does not replay from every original declaration and context candidate.')


def extract_topology_analysis(analysis):
    from .behavior_v5 import PROFILE as BEHAVIOR_PROFILE
    validate_document(analysis, 'source_behavior_analysis'); validate_evidence_references(analysis)
    if analysis['profile'] != BEHAVIOR_PROFILE: raise SchemaValidationError('Topology extraction requires source behavior profile version 5.')
    roots = [r for r in analysis['requirements'] if r['kind'] == 'ODI_EXPORT_REVIEW']
    requirements = [r for r in analysis['requirements'] if r['kind'] == 'ODI_TOPOLOGY_REVIEW']
    if len(roots) != 1 or len(requirements) != 1 or requirements[0]['subject_node_id'] != roots[0]['subject_node_id']:
        raise SchemaValidationError('The topology review must uniquely identify the original export root.')
    document = copy.deepcopy(requirements[0]['declarations'])
    if any(document.get(key) != analysis[key] for key in ('ir_id', 'source_analysis_sha256', 'evidence_files')):
        raise SchemaValidationError('The topology review does not bind its original source behavior analysis.')
    validate_document(document, 'odi_topology_analysis'); validate_evidence_references(document); _validate_accounting(document)
    original_units = [u for r in analysis['requirements'] if r['kind'] == 'ODI_PHYSICAL_DESIGN' for u in r['declarations']['execution_units']]
    manifest = roots[0]['declarations'].get('physical_analysis_manifest', {})
    original_units.extend(r for r in manifest.get('unscoped_records', []) if r['object_class'] == 'SnpExecUnit')
    by_unit = {u['node_id']: u for u in original_units}
    if len(by_unit) != len(original_units) or set(by_unit) != {u['execution_unit_node_id'] for u in document['execution_unit_uses']}:
        raise SchemaValidationError('Topology review does not retain every original physical execution-unit use.')
    if any(u['declared_fields'] != by_unit[u['execution_unit_node_id']]['declared_fields'] for u in document['execution_unit_uses']):
        raise SchemaValidationError('Topology execution-unit declarations differ from the original physical requirements.')
    return document
