"""Bind existing plans to a neutral graph without inventing execution behavior.

Every graph node/edge becomes a preservation obligation exactly once. Package
review carriers hold unplanned behavior for accounting; they do not implement
it or create an execution dependency merely because a source edge exists.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

from ssis_migration.ir.normalized import validate_normalized_ir
from ssis_migration.schemas import SchemaValidationError


def bind_plan_to_ir(plan: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    validate_normalized_ir(graph)
    result = copy.deepcopy(plan)
    steps = result['steps']
    ids = [s['step_id'] for s in steps]
    if len(set(ids)) != len(ids):
        raise SchemaValidationError('Duplicate plan IDs cannot bind neutral behavior.')
    nodes = {n['node_id']: n for n in graph['nodes']}
    children = defaultdict(set)
    for edge in graph['edges']:
        if edge['kind'] in {'CONTAINS', 'HAS_EXPRESSION'} and edge['resolution'] == 'RESOLVED':
            children[edge['source_id']].add(edge['target_id'])
    diagnostics = []
    source_id = graph['source']['file_id']

    def diagnostic(code, message):
        diagnostics.append({'code': code, 'severity': 'WARNING', 'message': message,
                            'file_ids': [source_id],
                            'evidence': [{'file_id': source_id, 'locator': 'file:/',
                                          'rule': 'neutral_plan_binding_unverified'}]})

    proposed = defaultdict(set)
    roots = {}
    for step in steps:
        metadata = step.setdefault('metadata', {})
        explicit = metadata.get('source_node_id')
        candidates = [explicit] if explicit in nodes else []
        if explicit is None:
            native = metadata.get('ref_id')
            candidates = [identity for identity, node in nodes.items()
                          if native is not None and node['kind'] == 'TASK' and node['source_ref'] == native]
        root = candidates[0] if len(candidates) == 1 else None
        roots[step['step_id']] = root
        if root is None:
            diagnostic('UNRESOLVED_PLAN_ROOT', f'{step["step_id"]}: missing or ambiguous native root; names were not used.')
            continue
        pending, visited = [root], set()
        while pending:
            identity = pending.pop()
            if identity in visited:
                continue
            visited.add(identity)
            proposed[identity].add(step['step_id'])
            pending.extend(children[identity])

    carrier = min(ids) if ids else None  # Accounting only, never execution order.
    owners, scope = {}, {}
    unassigned, ambiguous = [], []
    for identity, node in nodes.items():
        candidates = proposed[identity]
        if len(candidates) == 1:
            owners[identity], scope[identity] = next(iter(candidates)), 'STEP'
        else:
            owners[identity] = carrier
            shared = not candidates and node['kind'] in {'PARAMETER', 'CONNECTION'}
            scope[identity] = 'PACKAGE_SHARED' if shared else 'PACKAGE_REVIEW'
            if len(candidates) > 1:
                ambiguous.append(identity)
            elif not shared:
                unassigned.append(identity)
    if unassigned:
        diagnostic('UNPLANNED_NEUTRAL_BEHAVIOR', f'{len(unassigned)} neutral nodes have no implementing plan step; retained as package review obligations on {carrier!r}.')
    if ambiguous:
        diagnostic('AMBIGUOUS_NEUTRAL_OWNERSHIP', f'{len(ambiguous)} neutral nodes belong to overlapping step scopes; retained for review, without choosing an implementation owner.')
    if 'task_hierarchy' in graph['capability_gaps']:
        diagnostic('UNVERIFIED_TASK_OWNERSHIP', 'The graph lacks extracted task hierarchy; descendant ownership is incomplete.')
    digest_key = 'legacy_ir_sha256' if graph['ir_version'] == '1.0' else 'source_analysis_sha256'
    scopes = {s['step_id']: {
        'ir_id': graph['ir_id'], digest_key: graph['provenance'][digest_key],
        'source_platform': graph['source']['platform'], 'root_node_id': roots[s['step_id']],
        'nodes': [], 'edges': [], 'diagnostics': [], 'semantic_status': 'NOT_VERIFIED',
    } for s in steps}
    if graph['ir_version'] == '2.0':
        for scoped in scopes.values():
            scoped['ir_version'] = '2.0'
    for identity, node in nodes.items():
        if owners[identity] is not None:
            scopes[owners[identity]]['nodes'].append({'record': copy.deepcopy(node), 'obligation_scope': scope[identity]})
    boundary = []
    by_step = {s['step_id']: s for s in steps}
    for edge in graph['edges']:
        start, end = owners.get(edge['source_id']), owners.get(edge['target_id'])
        owner = start if start is not None else end if end is not None else carrier
        if owner is not None:
            scopes[owner]['edges'].append({'record': copy.deepcopy(edge), 'obligation_scope':
                                         'STEP' if start == end and start is not None
                                         and scope.get(edge['source_id']) == scope.get(edge['target_id']) == 'STEP'
                                         else 'PACKAGE_REVIEW'})
        if edge['kind'] == 'CONTROL_FLOW' and start is not None and end is not None and start != end:
            boundary.append(edge['edge_id'])
            if scope.get(edge['source_id']) == scope.get(edge['target_id']) == 'STEP' and start not in by_step[end].get('dependencies', []):
                diagnostic('MISSING_NEUTRAL_DEPENDENCY', f'{edge["edge_id"]}: plan step {end!r} omits source predecessor {start!r}. No dependency was fabricated.')
            diagnostic('CROSS_STEP_CONTROL_REVIEW', f'{edge["edge_id"]}: conditional/control semantics across steps require validation beyond job dependency ordering.')
    for step in steps:
        scoped = scopes[step['step_id']]
        scoped['diagnostics'] = copy.deepcopy(diagnostics)
        step['metadata']['normalized_source'] = scoped
    result.setdefault('metadata', {})['normalized_ir_binding'] = {
        'binding_version': '1.0', 'ir_id': graph['ir_id'],
        'scope': 'STRUCTURAL_PRESERVATION_ONLY', 'semantic_status': 'NOT_VERIFIED',
        'node_count': len(nodes), 'edge_count': len(graph['edges']),
        'assigned_node_count': sum(owner is not None for owner in owners.values()),
        'assigned_edge_count': sum(len(s['edges']) for s in scopes.values()),
        'review_carrier_step_id': carrier, 'unplanned_node_ids': unassigned,
        'ambiguous_node_ids': ambiguous, 'cross_step_edge_ids': boundary,
        'diagnostics': diagnostics,
    }
    return result


def validate_plan_ir_binding(plan: dict[str, Any], graph: dict[str, Any]) -> None:
    expected = bind_plan_to_ir(plan, graph)
    if plan.get('metadata', {}).get('normalized_ir_binding') != expected['metadata']['normalized_ir_binding']:
        raise SchemaValidationError('Neutral plan binding report is missing or stale.')
    for step, recomputed in zip(plan['steps'], expected['steps']):
        if step.get('metadata', {}).get('normalized_source') != recomputed['metadata']['normalized_source']:
            raise SchemaValidationError('Neutral plan scope omitted or changed source behavior.')


def validate_normalized_scope(scope: dict[str, Any]) -> None:
    """Validate a contract scope locally; the package also checks the whole graph."""
    required = {'ir_id', 'source_platform', 'root_node_id', 'nodes', 'edges', 'diagnostics', 'semantic_status'}
    required |= {'source_analysis_sha256', 'ir_version'} if isinstance(scope, dict) and scope.get('ir_version') == '2.0' else {'legacy_ir_sha256'}
    if not isinstance(scope, dict) or set(scope) != required or scope['semantic_status'] != 'NOT_VERIFIED':
        raise SchemaValidationError('Invalid normalized source scope.')
    identities = set()
    for field, identity in (('nodes', 'node_id'), ('edges', 'edge_id')):
        for item in scope[field]:
            if set(item) != {'record', 'obligation_scope'} or item['obligation_scope'] not in {'STEP', 'PACKAGE_SHARED', 'PACKAGE_REVIEW'}:
                raise SchemaValidationError('Invalid neutral preservation obligation scope.')
            record = item['record']
            if record[identity] in identities:
                raise SchemaValidationError('Duplicate neutral preservation identity.')
            identities.add(record[identity])
            if not record.get('evidence'):
                raise SchemaValidationError('Neutral preservation obligations require source evidence.')
