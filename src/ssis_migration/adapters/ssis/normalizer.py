"""SSIS-only projection from the established PackageIR into a neutral graph.

Native identities and explicit port references drive links. Names, list order,
SQL-looking text and legacy heuristic component labels never establish behavior.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from graphlib import CycleError, TopologicalSorter
from typing import Any

from ssis_migration.ir.normalized import content_digest, normalized_ir_id, validate_normalized_ir
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references

_TASKS = {
    'microsoft.executesqltask': 'EXECUTE_SQL',
    'microsoft.pipeline': 'DATA_FLOW', 'ssis.pipeline.3': 'DATA_FLOW',
    'stock:sequence': 'SEQUENCE',
}
_COMPONENTS = {
    'microsoft.oledbsource': ('SOURCE', 'READ'),
    'microsoft.oledbdestination': ('TARGET', 'WRITE'),
    'microsoft.flatfilesource': ('SOURCE', 'READ_FILE'),
    'microsoft.flatfiledestination': ('TARGET', 'WRITE_FILE'),
    'microsoft.lookup': ('TRANSFORMATION', 'LOOKUP'),
    'microsoft.derivedcolumn': ('TRANSFORMATION', 'DERIVE'),
    'microsoft.conditionalsplit': ('TRANSFORMATION', 'ROUTE'),
    'microsoft.aggregate': ('TRANSFORMATION', 'AGGREGATE'),
}


def normalize_ssis_ir(legacy_ir: dict[str, Any], envelope: dict[str, Any], *,
                      hierarchy: list[dict[str, Any]] | None = None,
                      script_assessments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Preserve all known collections, including unresolved and unknown constructs."""
    validate_document(envelope, 'source_envelope')
    validate_evidence_references(envelope)
    if envelope['legacy_ir'] != legacy_ir:
        raise SchemaValidationError('SSIS normalization input disagrees with its source envelope.')
    source_id = envelope['source']['file_id']
    nodes, edges = [], []
    diagnostics = copy.deepcopy(envelope['diagnostics'])

    def evidence(pointer):
        return [{'file_id': source_id, 'locator': 'legacy-ir:' + pointer,
                 'rule': 'ssis_structural_projection_v1'}]

    def diagnostic(code, message, pointer):
        diagnostics.append({'code': code, 'severity': 'WARNING', 'message': message,
                            'file_ids': [source_id], 'evidence': evidence(pointer)})

    def node(kind, operation, record, pointer, attributes=None):
        item = {'node_id': 'node-' + content_digest([source_id, pointer])[:24],
                'kind': kind, 'operation': operation, 'name': record.get('name'),
                'source_ref': record.get('ref_id'), 'semantic_status': 'NOT_VERIFIED',
                'attributes': attributes or {}, 'extensions': {'ssis': copy.deepcopy(record)},
                'evidence': evidence(pointer)}
        if script_assessments and pointer in script_assessments:
            item['attributes']['script_assessment'] = copy.deepcopy(script_assessments[pointer])
            diagnostic('UNVERIFIED_CSHARP_SCRIPT',
                       'C# script assessment is structural triage only; source dependencies and behavior require review.', pointer)
        nodes.append(item)
        if kind in {'TASK', 'DATA_FLOW', 'SOURCE', 'TARGET', 'TRANSFORMATION'} and not record.get('ref_id'):
            diagnostic('MISSING_NATIVE_REFERENCE', f'{item["node_id"]}: native identity is absent; no reference was invented.', pointer)
        return item['node_id']

    def index_add(index, reference, node_id, occurrence=None):
        if reference is not None and reference != '':
            index[reference].add((node_id, occurrence or node_id))

    def resolve(index, reference):
        candidates = index.get(reference, set())
        return next(iter(candidates))[0] if len(candidates) == 1 else None

    def edge(kind, start, end, pointer, record, attributes=None, identity_key=None):
        identity = [source_id, pointer, kind]
        if identity_key is not None:
            identity.append(identity_key)
        item = {'edge_id': 'edge-' + content_digest(identity)[:24],
                'kind': kind, 'source_id': start, 'target_id': end,
                'resolution': 'RESOLVED' if start and end else 'UNRESOLVED',
                'attributes': attributes or {}, 'extensions': {'ssis': copy.deepcopy(record)},
                'evidence': evidence(pointer)}
        edges.append(item)
        if item['resolution'] == 'UNRESOLVED':
            diagnostic('UNRESOLVED_GRAPH_REFERENCE', item['edge_id'] + ': Missing or ambiguous native reference; no endpoint was guessed.', pointer)
        return item

    def expression(owner, text, pointer, language):
        expr = node('EXPRESSION', None, {}, pointer,
                    {'text': text, 'language': language, 'dialect': None,
                     'binding_status': 'UNVERIFIED'})
        edge('HAS_EXPRESSION', owner, expr, pointer, {})

    tasks, connections = defaultdict(set), defaultdict(set)
    for i, record in enumerate(legacy_ir.get('executables', [])):
        pointer = f'/executables/{i}'
        native_type = (record.get('executable_type') or record.get('creation_name') or '').lower()
        operation = _TASKS.get(native_type, 'UNKNOWN')
        identity = node('TASK', operation, record, pointer)
        index_add(tasks, record.get('ref_id'), identity)
        if operation == 'UNKNOWN':
            diagnostic('UNSUPPORTED_TASK_SEMANTICS', f'Task {record.get("ref_id")!r}: native type {native_type!r} is not normalized.', pointer)
        props = record.get('properties', {})
        # The existing parser does not reliably extract statement-source mode.
        # Preserve the value as an unresolved binding, never as executable SQL.
        if 'SqlStatementSource' in props:
            expression(identity, props['SqlStatementSource'], pointer + '/properties/SqlStatementSource', 'unknown')
    for reference, owners in tasks.items():
        if len(owners) > 1:
            diagnostic('AMBIGUOUS_TASK_REFERENCE', f'Duplicate task reference {reference!r}; all tasks are retained.', '/executables')
    if hierarchy is not None:
        # Evidence anchors the corresponding child record. Containment comes
        # from XML parentage, never names or refId string-prefix guesses.
        for i, relation in enumerate(hierarchy):
            if relation['parent_is_package']:
                continue
            child = resolve(tasks, relation['child_ref'])
            indices = [j for j, record in enumerate(legacy_ir.get('executables', []))
                       if record.get('ref_id') == relation['child_ref']]
            pointer = f'/executables/{indices[0]}' if len(indices) == 1 else '/executables'
            edge('CONTAINS', resolve(tasks, relation['parent_ref']), child, pointer,
                 relation, {'relationship': 'xml_executable_parentage', 'hierarchy_index': i}, identity_key=i)

    for i, record in enumerate(legacy_ir.get('connection_manager_references', [])):
        identity = node('CONNECTION', None, record, f'/connection_manager_references/{i}',
                        {'endpoint': None, 'schema_status': 'NOT_SUPPLIED'})
        for field in ('ref_id', 'connection_manager_id', 'connection_manager_ref_id'):
            index_add(connections, record.get(field), identity)
    for i, record in enumerate(legacy_ir.get('executables', [])):
        props = record.get('properties', {})
        reference_key = 'connection_manager_ref_id' if props.get('connection_manager_ref_id') else 'Connection'
        reference = props.get(reference_key)
        if reference:
            # Source and target IDs remain independent even when task refs collide.
            owner = 'node-' + content_digest([source_id, f'/executables/{i}'])[:24]
            edge('USES_CONNECTION', owner, resolve(connections, reference),
                 f'/executables/{i}/properties/{reference_key}', props, {'native_reference': reference})

    for i, record in enumerate(legacy_ir.get('variables', [])):
        pointer = f'/variables/{i}'
        owner = node('PARAMETER', None, record, pointer,
                     {'namespace': record.get('namespace'), 'source_data_type': record.get('data_type'),
                      'initial_value': record.get('value'), 'scope': None})
        if record.get('expression') is not None:
            expression(owner, record['expression'], pointer + '/expression', 'ssis_expression')

    for i, record in enumerate(legacy_ir.get('precedence_constraints', [])):
        pointer = f'/precedence_constraints/{i}'
        attributes = {key: copy.deepcopy(record.get(key)) for key in (
            'logical_and', 'evaluation_operation', 'value', 'expression', 'defaulted_fields')}
        attributes['expression_language'] = 'ssis_expression' if record.get('expression') is not None else None
        edge('CONTROL_FLOW', resolve(tasks, record.get('from_executable')),
             resolve(tasks, record.get('to_executable')), pointer, record, attributes)
        if record.get('defaulted_fields'):
            diagnostic('DEFAULTED_CONTROL_CONDITION', 'Control condition includes legacy defaults; source verification is required.', pointer)

    for i, flow in enumerate(legacy_ir.get('data_flows', [])):
        pointer = f'/data_flows/{i}'
        # Flow-level extensions retain properties outside the normalized graph.
        flow_record = {k: v for k, v in flow.items() if k not in ('components', 'paths', 'column_mappings')}
        flow_id = node('DATA_FLOW', None, flow_record, pointer)
        edge('CONTAINS', resolve(tasks, flow.get('ref_id')), flow_id, pointer, {},
             {'relationship': 'task_data_flow'})
        inputs, outputs, lineage, target_columns = (defaultdict(set) for _ in range(4))
        for j, component in enumerate(flow.get('components', [])):
            location = pointer + f'/components/{j}'
            native_class = (component.get('component_class_id') or '').lower()
            kind, operation = _COMPONENTS.get(native_class, ('TRANSFORMATION', 'UNKNOWN'))
            owner = node(kind, operation, component, location,
                         {'inputs': copy.deepcopy(component.get('inputs', [])),
                          'outputs': copy.deepcopy(component.get('outputs', []))})
            edge('CONTAINS', flow_id, owner, location, {})
            if operation == 'UNKNOWN':
                diagnostic('UNSUPPORTED_COMPONENT_SEMANTICS', f'Component class {native_class!r} is not normalized; heuristic labels are not promoted to verified behavior.', location)
            for p, port in enumerate(component.get('inputs', [])):
                port_location = location + f'/inputs/{p}'
                index_add(inputs, port.get('ref_id'), owner, port_location)
                for c, column in enumerate(port.get('columns', [])):
                    index_add(target_columns, column.get('ref_id'), owner, port_location + f'/columns/{c}')
            for p, port in enumerate(component.get('outputs', [])):
                port_location = location + f'/outputs/{p}'
                index_add(outputs, port.get('ref_id'), owner, port_location)
                for c, column in enumerate(port.get('columns', [])):
                    for key in ('ref_id', 'lineage_id'):
                        index_add(lineage, column.get(key), owner, port_location + f'/columns/{c}')
            for k, connection in enumerate(component.get('connection_manager_refs', [])):
                reference = connection.get('connection_manager_ref_id') or connection.get('connection_manager_id') or connection.get('ref_id')
                edge('USES_CONNECTION', owner, resolve(connections, reference), location + f'/connection_manager_refs/{k}',
                     connection, {'native_reference': reference})
        for j, path in enumerate(flow.get('paths', [])):
            edge('DATA_FLOW', resolve(outputs, path.get('start_id')), resolve(inputs, path.get('end_id')),
                 pointer + f'/paths/{j}', path,
                 {'source_port_ref': path.get('start_id'), 'target_port_ref': path.get('end_id')})
        for j, mapping in enumerate(flow.get('column_mappings', [])):
            edge('COLUMN_LINEAGE', resolve(lineage, mapping.get('source_lineage_id')),
                 resolve(target_columns, mapping.get('destination_column_ref')), pointer + f'/column_mappings/{j}', mapping,
                 {key: copy.deepcopy(value) for key, value in mapping.items()})

    graph = {n['node_id']: set() for n in nodes if n['kind'] == 'TASK'}
    for item in edges:
        if item['kind'] == 'CONTROL_FLOW' and item['resolution'] == 'RESOLVED':
            graph[item['target_id']].add(item['source_id'])
    try:
        list(TopologicalSorter(graph).static_order())
    except CycleError:
        diagnostic('CYCLIC_CONTROL_FLOW', 'Control-flow cycle retained; loop semantics require explicit analysis.', '/precedence_constraints')

    collections = {'executables', 'variables', 'connection_manager_references', 'precedence_constraints', 'data_flows'}
    document = {
        'ir_version': '1.0', 'source': copy.deepcopy(envelope['source']),
        'evidence_files': copy.deepcopy(envelope['evidence_files']),
        'nodes': nodes, 'edges': edges, 'diagnostics': diagnostics,
        'semantic_status': 'NOT_VERIFIED',
        'status': 'BLOCKED' if any(d['severity'] == 'ERROR' for d in diagnostics) else 'REVIEW_REQUIRED',
        'capability_gaps': ['task_hierarchy', 'incremental_logic', 'transaction_boundaries',
                            'complete_error_handling', 'parameter_binding', 'source_schema_resolution',
                            'transformation_expression_analysis'],
        'provenance': {
            'builder': 'ssis_legacy_ir_projection', 'version': '1.0',
            'legacy_ir_sha256': content_digest(legacy_ir), 'evidence_scope': 'LEGACY_IR_PROJECTION',
            'limitations': ['Legacy-IR pointers identify contributing input records, not verified source semantics.',
                            'Native payloads are retained in extensions; expressions are not translated or executed.'],
        },
        'extensions': {'ssis': copy.deepcopy({k: v for k, v in legacy_ir.items() if k not in collections})},
    }
    if hierarchy is not None:
        document['extensions']['ssis']['executable_hierarchy'] = copy.deepcopy(hierarchy)
        document['capability_gaps'].remove('task_hierarchy')
    document['ir_id'] = normalized_ir_id(document)
    validate_normalized_ir(document, legacy_ir=legacy_ir)
    return document
