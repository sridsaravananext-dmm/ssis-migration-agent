"""Behavior requirements from original native exports; no invented workload rows."""
import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.export_pipeline import validate_export_review
from ssis_migration.adapters.informatica.behavior import PROFILE, analyze_behavior, validate_behavior_analysis
from ssis_migration.adapters.informatica.expressions import analyze_expression
from ssis_migration.migration.contract_bridge import _contract_id, build_migration_contract, validate_contract
from ssis_migration.migration.native_review import build_native_review_plan
from ssis_migration.migrate import run_migration
from ssis_migration.schemas import SchemaValidationError, validate_evidence_references

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / 'examples/public/powercenter_scd1/upstream/SCD1'


@pytest.fixture(scope='module')
def original():
    return get_adapter('powercenter').analyze_bundle(SOURCES/'w_scd1.XML', dependency_files=[SOURCES/'m_scd1.XML'])


@pytest.fixture(scope='module')
def behavior(original):
    return analyze_behavior(original.normalized_ir, profile=PROFILE)


def requirement(document, kind, name=None):
    matches = [r for r in document['requirements'] if r['kind'] == kind]
    if name is not None:matches = [r for r in matches if r['declarations'].get('name') == name]
    assert len(matches) == 1
    return matches[0]


def test_all_declared_requirements_are_evidenced_without_mutating_structural_graph(original, behavior, monkeypatch):
    import sqlglot
    monkeypatch.setattr(sqlglot, 'parse', lambda *a, **k:pytest.fail('PowerCenter expressions are not SQL.'))
    before = copy.deepcopy(original.normalized_ir)
    validate_behavior_analysis(behavior, original.normalized_ir)
    assert original.normalized_ir == before
    assert Counter(r['kind'] for r in behavior['requirements']) == {
        'TRANSFORMATION': 6, 'ROUTER_GROUP': 4, 'TARGET_LOAD_ORDER': 2, 'SESSION': 1,
        'SESSION_CONFIGURATION': 1, 'SESSION_TRANSFORMATION': 9, 'TARGET_WRITER': 2,
        'CONNECTION_BINDING': 4, 'SOURCE_DEFINITION': 1, 'TARGET_DEFINITION': 1}
    assert behavior['semantic_status'] == 'NOT_VERIFIED'
    files = {f['file_id'] for f in behavior['evidence_files']}
    assert all(r['evidence'] and {e['file_id'] for e in r['evidence']} <= files for r in behavior['requirements'])


def test_lookup_and_sequence_keep_arbitrary_match_policy_and_exact_state_declarations(behavior):
    lookup = requirement(behavior, 'TRANSFORMATION', 'LKPTRANS')
    assert lookup['declarations']['multiple_match_policy'] == 'Use Any Value'
    ast = lookup['declarations']['condition']['ast']
    assert ast['kind'] == 'COMPARE' and ast['operator'] == '='
    assert ast['left']['name'] == 'ID' and ast['right']['name'] == 'lkp_ID'
    assert any('no unique key or tie-break' in item for item in lookup['unresolved_items'])
    sequence = requirement(behavior, 'TRANSFORMATION', 'SEQTRANS')
    assert sequence['declarations']['sequence']['End Value'] == '9223372036854775807'
    assert sequence['declarations']['sequence']['Current Value'] == '1'
    assert sequence['declarations']['sequence']['Reset'] == 'NO'
    assert any('state equivalence is unimplemented' in item for item in sequence['unresolved_items'])


def test_router_native_predicates_field_references_and_unconnected_default_are_retained(behavior):
    groups = {r['declarations']['group']['NAME']: r for r in behavior['requirements'] if r['kind'] == 'ROUTER_GROUP'}
    assert groups['INSERT']['declarations']['expression']['ast']['kind'] == 'ISNULL'
    update = groups['UPDATE']['declarations']['expression']
    assert update['status'] == 'PARSED' and update['semantic_status'] == 'NOT_VERIFIED'
    assert update['ast']['kind'] == 'AND' and update['ast']['left']['kind'] == 'NOT'
    assert update['ast']['right']['operator'] == '!='
    bindings = [b for r in groups.values() for b in r['declarations']['field_bindings']]
    assert len(bindings) == 15 and all(b['resolution'] == 'DECLARATION_FOUND' for b in bindings)
    default = groups['DEFAULT1']
    assert default['declarations']['expression'] is None
    assert default['declarations']['outgoing_column_link_ids'] == []
    assert any('no declared outgoing' in value for value in default['unresolved_items'])
    ranks = [r['declarations'] for r in behavior['requirements'] if r['kind'] == 'TARGET_LOAD_ORDER']
    assert len(ranks) == 2 and all(r['declaration']['ORDER'] == '1' and len(r['same_declared_rank_node_ids']) == 2 for r in ranks)


def test_session_transaction_layers_and_writer_permissions_are_not_emitted_row_operations(behavior):
    session = requirement(behavior, 'SESSION')['declarations']
    assert session['source_row_mode'] == 'Data driven'
    assert session['transaction_policy']['Commit Interval'] == '10000'
    assert session['transaction_policy']['Rollback Transactions on Errors'] == 'NO'
    assert session['transaction_policy']['Recovery Strategy'] == 'Fail task and continue workflow'
    assert session['effective_configuration'] is None
    assert session['configuration_references'][0]['resolution'] == 'DECLARATION_FOUND'
    writers = [r['declarations'] for r in behavior['requirements'] if r['kind'] == 'TARGET_WRITER']
    assert len(writers) == 2 and writers[0]['definition_node_id'] == writers[1]['definition_node_id']
    assert all(w['declared_operation_options']['Delete'] == 'YES' and w['effective_row_operations'] is None for w in writers)
    update = requirement(behavior, 'TRANSFORMATION', 'UPDTRANS')['declarations']['row_operation_expression']
    assert update['ast'] == {'kind': 'ROW_OPERATION_FLAG', 'name': 'DD_UPDATE'}


def test_unsupported_native_default_expression_is_preserved_for_review(original):
    field = next(n for n in original.normalized_ir['nodes'] if n['attributes']['export_attributes'].get('NAME') == 'NEXTVAL')
    text = field['attributes']['export_attributes']['DEFAULTVALUE']
    result = analyze_expression(text, {})
    assert result['text'] == text and result['status'] == 'REVIEW_REQUIRED'
    assert result['ast'] is None and result['unresolved_items']


def test_missing_expression_port_metadata_does_not_invent_column(original):
    group = next(n for n in original.normalized_ir['nodes'] if n['operation'] == 'GROUP' and n['attributes']['export_attributes'].get('NAME') == 'INSERT')
    text = group['attributes']['export_attributes']['EXPRESSION']
    result = analyze_expression(text, {})
    assert result['status'] == 'REVIEW_REQUIRED' and result['ast'] is None
    assert any('absent or ambiguous' in message for message in result['unresolved_items'])


def test_contracts_preserve_all_graph_and_behavior_obligations(original, behavior):
    plan, contracts = build_native_review_plan(original.normalized_ir, 'w_scd1', behavior_analysis=behavior)
    obligations = [o for c in contracts for o in c['required_behavior']['obligations']]
    assert len([o for o in obligations if o['kind'].startswith('normalized_')]) == 702
    requirements = [o for o in obligations if o['kind'] == 'source_requirement']
    assert len(requirements) == len({o['source_value']['requirement_id'] for o in requirements}) == 31
    assert all(o['critical'] is None and o['weight'] is None and o['verification'] == 'NOT_VERIFIED' for o in requirements)
    validate_export_review(original.source_envelope, original.normalized_ir, plan, contracts, behavior_analysis=behavior)
    assert all(c['source_behavior']['migration_intent'] == {} and c['target_capabilities']['generation_languages'] == [] for c in contracts)


@pytest.mark.parametrize('change', ['drop_requirement', 'change_scope', 'change_sequence', 'change_writer', 'remove_evidence'])
def test_behavior_artifact_cannot_omit_or_fabricate_source_declarations(original, behavior, change):
    altered = copy.deepcopy(behavior)
    if change == 'drop_requirement':altered['requirements'].pop()
    elif change == 'change_scope':altered['source_analysis_sha256'] = altered['ir_id'][3:]
    elif change == 'change_sequence':requirement(altered, 'TRANSFORMATION', 'SEQTRANS')['declarations']['sequence']['Reset'] = None
    elif change == 'change_writer':next(r for r in altered['requirements'] if r['kind'] == 'TARGET_WRITER')['declarations']['effective_row_operations'] = []
    else:altered['requirements'][0]['evidence'].pop()
    with pytest.raises(SchemaValidationError):validate_behavior_analysis(altered, original.normalized_ir)


@pytest.mark.parametrize('change', ['obligation', 'evidence', 'validation', 'unresolved'])
def test_rehashed_contract_cannot_omit_behavior_or_its_review_requirements(original, behavior, change):
    plan, contracts = build_native_review_plan(original.normalized_ir, 'w_scd1', behavior_analysis=behavior)
    document = copy.deepcopy(contracts[0]); step = plan['steps'][0]
    obligation = next(o for o in document['required_behavior']['obligations'] if o['kind'] == 'source_requirement')
    if change == 'obligation':document['required_behavior']['obligations'].remove(obligation)
    elif change == 'evidence':obligation['evidence'] = []
    elif change == 'validation':document['validation_requirements'].remove(obligation['source_value']['preservation_requirements'][0])
    else:document['unresolved_items'].remove(obligation['source_value']['unresolved_items'][0])
    document['contract_id'] = _contract_id(document)
    with pytest.raises(SchemaValidationError):validate_contract(document, step)


def test_rebuilt_plan_cannot_silently_discard_behavior_requirements(original, behavior):
    graph = original.normalized_ir
    plan, _ = build_native_review_plan(graph, 'w_scd1', behavior_analysis=behavior)
    contracts = []
    for step in plan['steps']:
        step['metadata']['source_requirements'] = []
        contract = build_migration_contract(step, source_platform='informatica', evidence_files=graph['evidence_files'], source_diagnostics=graph['diagnostics']).to_dict()
        step['metadata']['migration_contract'] = contract;contracts.append(contract)
    with pytest.raises(SchemaValidationError):
        validate_export_review(original.source_envelope, graph, plan, contracts)


def test_behavior_evidence_references_cannot_point_to_non_file_id(original, behavior):
    altered = copy.deepcopy(behavior)
    altered['requirements'][0]['evidence'][0]['file_id'] = original.normalized_ir['nodes'][0]['node_id']
    with pytest.raises(SchemaValidationError):validate_evidence_references(altered)


def test_review_pipeline_publishes_behavior_without_acceptance_or_notebooks(tmp_path):
    report = run_migration(str(SOURCES/'w_scd1.XML'), str(tmp_path), dependency_files=[str(SOURCES/'m_scd1.XML')])
    artifacts = {key:json.loads(Path(path).read_text()) for key,path in report['artifacts'].items()}
    validate_export_review(artifacts['source_envelope'], artifacts['normalized_ir'], artifacts['migration_plan'],
                           artifacts['migration_contracts']['contracts'], behavior_analysis=artifacts['source_behavior_analysis'])
    assert report['behavior_analysis']['requirement_count'] == 33
    assert report['behavior_analysis']['semantic_status'] == 'NOT_VERIFIED'
    assert report['behavior_analysis']['unresolved_items']
    assert report['coverage']['semantic']['verified_percent'] is None
    assert report['status'] == 'BLOCKED' and report['migration_accepted'] is False
    assert report['execution_status'] == 'NOT_RUN' and not list(tmp_path.rglob('*.ipynb'))
