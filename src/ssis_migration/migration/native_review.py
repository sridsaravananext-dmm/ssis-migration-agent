"""Source-neutral review planning and contracts; no target implementation claims."""
from __future__ import annotations

import copy

from ssis_migration.ir.normalized import content_digest, validate_normalized_ir
from ssis_migration.planner.models import MigrationPlan, MigrationStep
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from .normalized_plan import bind_plan_to_ir, validate_plan_ir_binding
from .contract_bridge import build_migration_contract, validate_contract


def build_native_review_plan(graph, package_name, *, behavior_analysis=None):
    validate_normalized_ir(graph)
    if graph['ir_version'] != '2.0':
        raise ValueError('Native review planning requires source-analysis IR v2.')
    roots = [n for n in graph['nodes'] if n['kind'] == 'TASK' and n['attributes'].get('analysis_unit') is True]
    if not roots:
        raise SchemaValidationError('Native source graph has no explicit analysis units to review.')
    steps = []
    for node in roots:
        blocked = graph['status'] == 'BLOCKED' or node['attributes'].get('analysis_status') != 'STRUCTURED'
        steps.append(MigrationStep(
            step_id='native-'+node['node_id'], name=node['name'] or node['node_id'], step_type='SOURCE_REVIEW',
            metadata={'source_node_id':node['node_id'], 'source_platform':graph['source']['platform'],
                      'native_review_only':True, 'migration_intent':{},
                      'migration_strategy':{'strategy':'UNSUPPORTED' if blocked else 'REVIEW_REQUIRED',
                                            'requires_ai':False, 'capability_rule':'NATIVE_TRANSLATION_UNIMPLEMENTED',
                                            'reason':'Source behavior is retained for review; no faithful target translation is registered.'}}))
    plan = MigrationPlan(package_name=package_name, steps=steps,
                         metadata={'source_platform':graph['source']['platform'], 'scope':'REVIEW_ONLY',
                                   'execution_order':'NOT_ESTABLISHED', 'semantic_status':'NOT_VERIFIED'}).to_dict()
    plan = bind_plan_to_ir(plan, graph)
    if behavior_analysis is not None:
        validate_document(behavior_analysis, 'source_behavior_analysis')
        validate_evidence_references(behavior_analysis)
        if (behavior_analysis['ir_id'] != graph['ir_id']
                or behavior_analysis['source_analysis_sha256'] != graph['provenance']['source_analysis_sha256']
                or behavior_analysis['evidence_files'] != graph['evidence_files']):
            raise SchemaValidationError('Source behavior analysis does not bind this original source graph.')
        requirements = behavior_analysis['requirements']
        if len({r['requirement_id'] for r in requirements}) != len(requirements):
            raise SchemaValidationError('Duplicate source behavior requirement identities.')
        ownership = {}
        for step in plan['steps']:
            step['metadata']['source_requirements'] = []
            for node in step['metadata']['normalized_source']['nodes']:
                ownership.setdefault(node['record']['node_id'], []).append(step)
        for requirement in requirements:
            owners = ownership.get(requirement['subject_node_id'], [])
            if len(owners) != 1:
                raise SchemaValidationError('Source requirement subject must have one explicit review owner.')
            owners[0]['metadata']['source_requirements'].append(copy.deepcopy(requirement))
        plan['metadata']['behavior_analysis_binding'] = {
            'profile': behavior_analysis['profile'], 'analysis_sha256': content_digest(behavior_analysis),
            'requirement_count': len(requirements)}
    contracts = []
    for step in plan['steps']:
        contract = build_migration_contract(step, source_platform=graph['source']['platform'],
                                            evidence_files=graph['evidence_files'], source_diagnostics=graph['diagnostics']).to_dict()
        step['metadata']['migration_contract'] = contract
        contracts.append(contract)
    return plan, contracts


def validate_native_review_plan(graph, plan, contracts, *, behavior_analysis=None):
    """Check every node/edge is conserved and review decisions cannot be upgraded."""
    validate_plan_ir_binding(plan, graph)
    expected_plan, expected_contracts = build_native_review_plan(graph, plan['package_name'], behavior_analysis=behavior_analysis)
    if plan != expected_plan or contracts != expected_contracts:
        raise SchemaValidationError('Native review plan/contracts omit source obligations or change the capability decision.')
    for step, contract in zip(plan['steps'], contracts):
        validate_contract(contract, step)
