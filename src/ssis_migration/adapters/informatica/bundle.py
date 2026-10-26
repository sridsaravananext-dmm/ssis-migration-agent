"""Explicit PowerCenter exports with exact replay and conservative scoped links.

Files retain independent XML evidence, nodes and containment. Only declared
session-to-mapping references gain cross-file resolution; no vendor XML is
manufactured and no runtime behavior is inferred from matching identities.
"""
from __future__ import annotations

import copy
import os
from collections import defaultdict
from pathlib import Path

from ssis_migration.intake import describe_file
from ssis_migration.ir.normalized import content_digest, normalized_ir_id, validate_normalized_ir
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ..base import SourceAdapterResult, SourceEvidenceError, UnsupportedSourceError
from ..xml_export import MAX_BYTES, MAX_ELEMENTS, snapshot_bytes

PROFILE = 'powercenter_bundle_structure_v1'
RULE = 'powercenter_repository_folder_uuid_v1'
MAX_FILES = 16
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_CANDIDATES = 64
MAX_MAPPING_REFERENCES = 256


def _file_id(node):
    return node['evidence'][0]['file_id']


def _scope(node, nodes, parents):
    identity = {}
    scope_nodes = {}
    current = node
    while current is not None:
        if current['operation'] in {'POWERMART', 'REPOSITORY', 'FOLDER'}:
            scope_nodes.setdefault(current['operation'], current)
        current = nodes.get(parents.get(current['node_id']))
    for kind, keys in (('REPOSITORY', ('NAME', 'VERSION', 'CODEPAGE', 'DATABASETYPE')),
                       ('FOLDER', ('NAME', 'UUID'))):
        attrs = scope_nodes.get(kind, {}).get('attributes', {}).get('export_attributes', {})
        identity.update({kind.lower()+'_'+key.lower(): attrs.get(key) for key in keys})
    identity['export_version'] = scope_nodes.get('POWERMART', {}).get('attributes', {}).get('export_attributes', {}).get('REPOSITORY_VERSION')
    return identity, list(scope_nodes.values())


def _complete(identity):
    return all(isinstance(value, str) and value.strip() for value in identity.values())


def _bind_reference(edge, nodes, parents, mappings):
    source = nodes[edge['source_id']]
    identity, source_scope = _scope(source, nodes, parents)
    definition = edge['attributes'].get('definition')
    candidates = mappings.get(definition, []) if isinstance(definition, str) and definition else []
    if len(candidates) > MAX_REFERENCE_CANDIDATES:
        raise UnsupportedSourceError('PowerCenter mapping reference exceeds the 64-candidate resolution limit.')
    evidence = list(edge['evidence'])
    for scoped in source_scope:
        evidence.extend(scoped['evidence'])
    eligible, unverified, conflicts = [], [], []
    for candidate in candidates:
        other, target_scope = _scope(candidate, nodes, parents)
        for scoped in [candidate, *target_scope]:
            evidence.extend(scoped['evidence'])
        # Existing same-file, same-folder definitions retain their local scope.
        source_folder = next((n['node_id'] for n in source_scope if n['operation'] == 'FOLDER'), None)
        target_folder = next((n['node_id'] for n in target_scope if n['operation'] == 'FOLDER'), None)
        if _file_id(source) == _file_id(candidate) and source_folder is not None and source_folder == target_folder:
            eligible.append(candidate)
            continue
        namespace = ('repository_name', 'folder_name', 'folder_uuid')
        if any(identity[k] and other[k] and identity[k] != other[k] for k in namespace):
            continue
        if not _complete(identity) or not _complete(other):
            unverified.append(candidate)
        elif identity != other or _file_id(source) == _file_id(candidate):
            conflicts.append(candidate)
        else:
            eligible.append(candidate)
    if conflicts:
        reason = 'CONFLICTING_EXPORT_SCOPE'
    elif unverified:
        reason = 'MISSING_SCOPE_EVIDENCE'
    elif len(eligible) > 1:
        reason = 'AMBIGUOUS_MAPPING_DEFINITION'
    elif len(eligible) == 1:
        reason = 'UNIQUE_DECLARED_SCOPE'
    else:
        reason = 'NO_MATCHING_MAPPING_DEFINITION'
    target = eligible[0] if reason == 'UNIQUE_DECLARED_SCOPE' else None
    bound = copy.deepcopy(edge)
    bound.update(target_id=target['node_id'] if target else None,
                 resolution='RESOLVED' if target else 'UNRESOLVED')
    bound['attributes'].update(resolution_scope=RULE, resolution_reason=reason,
                               scope_identity=identity, original_edge_id=edge['edge_id'],
                               candidate_node_ids=[n['node_id'] for n in candidates])
    bound['evidence'] = [dict(items) for items in dict.fromkeys(tuple(item.items()) for item in evidence)]
    bound['edge_id'] = 'edge-' + content_digest([RULE, bound])[:24]
    diagnostic = None
    if target is None:
        diagnostic = {'code': 'UNRESOLVED_GRAPH_REFERENCE', 'severity': 'ERROR',
                      'message': bound['edge_id']+': Session mapping '+str(edge['attributes']['definition'])+' cannot bind: '+reason+'.',
                      'file_ids': list(dict.fromkeys(item['file_id'] for item in bound['evidence'])),
                      'evidence': copy.deepcopy(bound['evidence'])}
    return bound, diagnostic


def _combine(graphs):
    if not 2 <= len(graphs) <= MAX_FILES:
        raise UnsupportedSourceError('PowerCenter bundles require 2 to 16 explicit XML exports.')
    if sum(g['evidence_files'][0]['size_bytes'] for g in graphs) > MAX_TOTAL_BYTES:
        raise UnsupportedSourceError('PowerCenter exports exceed the 64 MiB combined analysis limit.')
    if sum(len(g['nodes']) for g in graphs) > MAX_ELEMENTS:
        raise UnsupportedSourceError('PowerCenter exports exceed the 10,000 combined element limit.')
    graph = copy.deepcopy(graphs[0])
    for key in ('evidence_files', 'nodes', 'edges', 'diagnostics'):
        graph[key] = [copy.deepcopy(item) for component in graphs for item in component[key]]
    files = {f['file_id']: f for f in graph['evidence_files']}
    if len(files) != len(graphs) or len({f['path'] for f in files.values()}) != len(files):
        raise SourceEvidenceError('PowerCenter exports must have distinct file identities and paths.')
    if any(f['platform'] != 'informatica' or f['format'] != 'powercenter_xml' or f['status'] != 'AVAILABLE'
           for f in files.values()):
        raise SourceEvidenceError('Every PowerCenter bundle file must be an available native POWERMART export.')
    nodes = {n['node_id']: n for n in graph['nodes']}
    parents = {e['target_id']: e['source_id'] for e in graph['edges'] if e['kind'] == 'CONTAINS'}
    mappings = defaultdict(list)
    for node in graph['nodes']:
        if node['operation'] == 'MAPPING':
            mappings[node['attributes']['export_attributes'].get('NAME')].append(node)
    references = [e for e in graph['edges'] if e['attributes'].get('role') == 'SESSION_MAPPING']
    if len(references) > MAX_MAPPING_REFERENCES:
        raise UnsupportedSourceError('PowerCenter exports exceed the 256 session-mapping reference limit.')
    original_ids = {e['edge_id'] for e in references}
    graph['diagnostics'] = [d for d in graph['diagnostics'] if not (
        d['code'] == 'UNRESOLVED_GRAPH_REFERENCE' and d['message'].split(':', 1)[0] in original_ids)]
    for index, edge in enumerate(graph['edges']):
        if edge['edge_id'] in original_ids:
            graph['edges'][index], diagnostic = _bind_reference(edge, nodes, parents, mappings)
            if diagnostic:
                graph['diagnostics'].append(diagnostic)
    analysis = {'analysis_version': '1.0', 'profile': PROFILE, 'source': copy.deepcopy(graph['source']),
                'binding_rule': RULE, 'semantic_status': 'NOT_VERIFIED',
                'source_snapshots': [{'file_id': g['evidence_files'][0]['file_id'],
                                      'snapshot': copy.deepcopy(g['extensions']['source_analysis']['source_snapshot'])}
                                     for g in graphs]}
    graph['extensions']['source_analysis'] = analysis
    graph['provenance'].update(builder=PROFILE, source_analysis_sha256=content_digest(analysis))
    graph['provenance']['limitations'].append('Cross-export identity resolution does not verify runtime semantics or execution order.')
    graph['status'] = 'BLOCKED' if any(d['severity'] == 'ERROR' for d in graph['diagnostics']) else 'REVIEW_REQUIRED'
    graph['ir_id'] = normalized_ir_id(graph)
    validate_normalized_ir(graph)
    return graph


def analyze_bundle(adapter, source, dependency_files):
    if not isinstance(dependency_files, (list, tuple)):
        raise ValueError('PowerCenter dependencies must be an explicit list of XML file paths.')
    paths = [Path(os.path.abspath(Path(p).expanduser())) for p in [source, *dependency_files]]
    if not 2 <= len(paths) <= MAX_FILES or len(set(paths)) != len(paths):
        raise ValueError('Select 1 to 15 distinct dependency exports, excluding the primary source.')
    paths = paths[:1] + sorted(paths[1:])
    for path in paths:
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise SourceEvidenceError('Supply regular PowerCenter XML export files without symlink ancestors: '+str(path))
        if path.stat().st_size > MAX_BYTES:
            raise UnsupportedSourceError('XML export exceeds the 16 MiB offline analysis limit.')
    if sum(path.stat().st_size for path in paths) > MAX_TOTAL_BYTES:
        raise UnsupportedSourceError('PowerCenter exports exceed the 64 MiB combined analysis limit.')
    graphs, element_count = [], 0
    for path in paths:
        component = adapter.analyze(path, file_record=describe_file(path, source_root=paths[0].parent)).normalized_ir
        element_count += len(component['nodes'])
        if element_count > MAX_ELEMENTS:
            raise UnsupportedSourceError('PowerCenter exports exceed the 10,000 combined element limit.')
        graphs.append(component)
    graph = _combine(graphs)
    envelope = {'envelope_version': '2.0', 'source': copy.deepcopy(graph['source']),
                'evidence_files': copy.deepcopy(graph['evidence_files']), 'diagnostics': copy.deepcopy(graph['diagnostics']),
                'status': graph['status'], 'native_analysis': copy.deepcopy(graph['extensions']['source_analysis'])}
    validate_document(envelope, 'source_envelope_v2'); validate_evidence_references(envelope)
    return SourceAdapterResult(parsed={'name': paths[0].stem}, package_ir=None,
                               analysis={'platform': adapter.platform, 'status': graph['status'], 'semantic_status': 'NOT_VERIFIED'},
                               source_envelope=envelope, normalized_ir=graph)


def validate_bundle_graph(adapter, graph):
    records = graph['evidence_files']
    snapshots = graph['extensions']['source_analysis'].get('source_snapshots')
    if (not isinstance(snapshots, list) or len(snapshots) != len(records)
            or not 2 <= len(records) <= MAX_FILES
            or graph['source']['file_id'] != records[0]['file_id']):
        raise SchemaValidationError('PowerCenter bundle snapshots do not identify every original export in order.')
    graphs, element_count = [], 0
    for item, record in zip(snapshots, records):
        if not isinstance(item, dict) or set(item) != {'file_id', 'snapshot'} or item['file_id'] != record['file_id']:
            raise SchemaValidationError('PowerCenter source snapshot identity is missing or out of order.')
        component = adapter._build(snapshot_bytes(item['snapshot'], record), record)
        if component['source']['export_version'] != record['export_version']:
            raise SchemaValidationError('PowerCenter inventory version disagrees with its original export declaration.')
        element_count += len(component['nodes'])
        if element_count > MAX_ELEMENTS:
            raise SchemaValidationError('PowerCenter bundle exceeds the combined element limit.')
        graphs.append(component)
    if graph != _combine(graphs):
        raise SchemaValidationError('PowerCenter graph does not replay from all original export files and scope declarations.')
