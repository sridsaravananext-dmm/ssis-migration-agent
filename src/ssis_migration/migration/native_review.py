"""Source-neutral review planning and contracts; no target implementation claims."""
from __future__ import annotations

from ssis_migration.ir.normalized import validate_normalized_ir
from ssis_migration.planner.models import MigrationPlan, MigrationStep
from ssis_migration.schemas import SchemaValidationError
from .normalized_plan import bind_plan_to_ir, validate_plan_ir_binding
from .contract_bridge import build_migration_contract, validate_contract


def build_native_review_plan(graph, package_name):
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
    contracts = []
    for step in plan['steps']:
        contract = build_migration_contract(step, source_platform=graph['source']['platform'],
                                            evidence_files=graph['evidence_files'], source_diagnostics=graph['diagnostics']).to_dict()
        step['metadata']['migration_contract'] = contract
        contracts.append(contract)
    return plan, contracts


def validate_native_review_plan(graph, plan, contracts):
    """Check every node/edge is conserved and review decisions cannot be upgraded."""
    validate_plan_ir_binding(plan, graph)
    expected_plan, expected_contracts = build_native_review_plan(graph, plan['package_name'])
    if plan != expected_plan or contracts != expected_contracts:
        raise SchemaValidationError('Native review plan/contracts omit source obligations or change the capability decision.')
    for step, contract in zip(plan['steps'], contracts):
        validate_contract(contract, step)
