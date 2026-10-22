from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ssis_migration.intake import describe_file
from ssis_migration.migration import build_migration_contract
from ssis_migration.validator.acceptance import (
    DIMENSIONS, MANDATORY_GATES, document_digest, _hash, draft_documents,
    evaluate_acceptance, load_package,
)
from ssis_migration.validator.readiness import summarize_readiness
from ssis_migration.generator.job_generator import JobGenerator


@pytest.fixture
def case(tmp_path):
    source = tmp_path / 'source.sql'
    source.write_text('SELECT id FROM exported_source')
    step = {'step_id': 'load', 'name': 'Load', 'step_type': 'DATA_FLOW',
            'source_objects': [], 'target_objects': [], 'dependencies': [],
            'transformations': ['project id', 'optional diagnostic'],
            'metadata': {'migration_intent': {},
                         'migration_strategy': {'strategy': 'DETERMINISTIC'}}}
    contract = build_migration_contract(step, source_platform='ssis',
                                       evidence_files=[describe_file(source)]).to_dict()
    assert contract['unresolved_items'] == []
    step['metadata']['migration_contract'] = contract
    package = {'contracts': [contract], 'artifact_hashes': {'notebook.ipynb': 'a' * 64},
               'source_issues': [],
               'static': summarize_readiness([{'status': 'PASS'}], generated_artifact_count=1)}
    policy, evidence = draft_documents(package)
    assert len(policy['behaviors']) == 2
    for item, weight in zip(policy['behaviors'], (4, 1)):
        item.update(weight=weight, critical=False)
    report = tmp_path / 'report.txt'
    report.write_text('Reviewed comparison suite: expected and actual results match.')
    ref = {'path': report.name, 'sha256': _hash(report), 'reviewer': 'Reviewer',
           'method': 'controlled comparison of exported source and target results', 'locator': 'case-1'}
    evidence['policy_digest'] = document_digest(policy)
    for gate in evidence['gates']:
        gate.update(status='PASS', reports=[copy.deepcopy(ref)])
    for item in policy['behaviors']:
        for dimension in DIMENSIONS:
            evidence['checks'].append({
                'contract_id': item['contract_id'], 'behavior_id': item['behavior_id'],
                'dimension': dimension, 'status': 'PASS', 'reports': [copy.deepcopy(ref)],
            })
    return package, policy, evidence, tmp_path, step


def evaluate(case):
    package, policy, evidence, root, _ = case
    return evaluate_acceptance(package, policy, evidence, report_root=root)


def rebind(case):
    case[2]['policy_digest'] = document_digest(case[1])


def test_full_attested_coverage_is_accepted_without_execution(case):
    result = evaluate(case)
    assert result['acceptance']['accepted']
    assert result['lifecycle_status'] == 'ACCEPTED'
    assert all(result['coverage'][d]['verified_percent'] == 100 for d in DIMENSIONS)
    assert result['coverage']['structural']['scope'] == 'STATIC_VALIDATION_RECORDS'
    assert 'attested' in result['acceptance']['basis']


def test_exact_eighty_percent_and_separate_dimensions(case):
    case[2]['checks'][3]['status'] = 'UNVERIFIED'
    result = evaluate(case)
    assert result['acceptance']['accepted']
    assert result['coverage']['semantic']['verified_percent'] == 80
    assert result['coverage']['semantic']['total_weight'] == 5
    assert result['coverage']['execution']['verified_percent'] == 100


def test_below_eighty_blocks(case):
    case[1]['behaviors'][0]['weight'] = 3.9
    rebind(case)
    case[2]['checks'][3]['status'] = 'UNVERIFIED'
    assert not evaluate(case)['acceptance']['accepted']


def test_higher_configured_threshold(case):
    case[1]['minimum_semantic_percent'] = 90
    rebind(case)
    case[2]['checks'][3]['status'] = 'UNVERIFIED'
    assert not evaluate(case)['acceptance']['accepted']


@pytest.mark.parametrize('dimension', DIMENSIONS)
def test_critical_rule_blocks_despite_high_percentage(case, dimension):
    case[1]['behaviors'][0]['weight'] = 99
    case[1]['behaviors'][1]['critical'] = True
    rebind(case)
    case[2]['checks'][3 + DIMENSIONS.index(dimension)]['status'] = 'UNVERIFIED'
    result = evaluate(case)
    assert not result['acceptance']['accepted']
    assert result['coverage'][dimension]['verified_percent'] == 99
    assert any('Critical behavior' in s for s in result['blocking_issues'])


@pytest.mark.parametrize('status', ['FAIL', 'NOT_RUN', 'UNSUPPORTED', 'UNVERIFIED'])
@pytest.mark.parametrize('gate', MANDATORY_GATES)
def test_mandatory_gates_never_pass_for_nonpass_status(case, status, gate):
    next(g for g in case[2]['gates'] if g['gate_id'] == gate)['status'] = status
    assert not evaluate(case)['acceptance']['accepted']


def test_missing_gates_block(case):
    case[2]['gates'] = []
    assert not evaluate(case)['acceptance']['accepted']


def test_optional_policy_gate_becomes_mandatory(case):
    case[1]['additional_gates'] = ['financial_reconciliation']
    rebind(case)
    assert not evaluate(case)['acceptance']['accepted']


@pytest.mark.parametrize('value', [None, 0, -1, True, float('nan'), float('inf')])
def test_invalid_or_unassessed_weight_cannot_accept(case, value):
    case[1]['behaviors'][0]['weight'] = value
    if value is None:
        rebind(case)
        result = evaluate(case)
        assert result['coverage']['semantic']['verified_percent'] is None
        assert not result['acceptance']['accepted']
    else:
        with pytest.raises(ValueError):
            evaluate(case)


@pytest.mark.parametrize('threshold', [79.99, 101, True, float('nan'), float('inf')])
def test_threshold_must_be_finite_and_at_least_eighty(case, threshold):
    case[1]['minimum_semantic_percent'] = threshold
    with pytest.raises(ValueError):
        evaluate(case)


def test_missing_policy_behavior_does_not_shrink_denominator(case):
    case[1]['behaviors'].pop()
    rebind(case)
    result = evaluate(case)
    assert not result['acceptance']['accepted']
    assert result['coverage']['semantic']['verified_percent'] is None


def test_unknown_criticality_blocks(case):
    case[1]['behaviors'][0]['critical'] = None
    rebind(case)
    assert not evaluate(case)['acceptance']['accepted']


def test_missing_semantic_evidence_stays_in_denominator(case):
    case[2]['checks'] = [c for c in case[2]['checks'] if c['dimension'] != 'semantic']
    result = evaluate(case)
    assert result['coverage']['semantic']['total_weight'] == 5
    assert result['coverage']['semantic']['verified_percent'] == 0
    assert not result['acceptance']['accepted']


@pytest.mark.parametrize('target', ['checks', 'gates'])
def test_duplicates_rejected(case, target):
    case[2][target].append(copy.deepcopy(case[2][target][0]))
    with pytest.raises(ValueError, match='Duplicate'):
        evaluate(case)


def test_duplicate_policy_behavior_rejected(case):
    case[1]['behaviors'].append(copy.deepcopy(case[1]['behaviors'][0]))
    rebind(case)
    with pytest.raises(ValueError, match='Duplicate'):
        evaluate(case)


@pytest.mark.parametrize('target', ['policy', 'evidence'])
def test_unknown_contract_identity_rejected(case, target):
    if target == 'policy':
        case[1]['behaviors'][0]['contract_id'] = 'contract-stale'
    else:
        case[2]['checks'][0]['behavior_id'] = 'missing-behavior'
    with pytest.raises(ValueError, match='unknown'):
        evaluate(case)


@pytest.mark.parametrize('binding', ['artifacts', 'policy', 'source'])
def test_stale_binding_blocks_and_gives_no_credit(case, binding):
    if binding == 'artifacts':
        case[2]['artifact_hashes']['notebook.ipynb'] = 'b' * 64
    elif binding == 'policy':
        case[2]['policy_digest'] = 'b' * 64
    else:
        case[0]['source_issues'] = ['Source changed']
    result = evaluate(case)
    assert not result['acceptance']['accepted']
    assert result['coverage']['semantic']['verified_percent'] == 0


@pytest.mark.parametrize('change', ['missing', 'changed', 'external', 'no_reports'])
def test_unbound_or_missing_reports_cannot_verify(case, change):
    if change == 'missing':
        (case[3] / 'report.txt').unlink()
    elif change == 'changed':
        (case[3] / 'report.txt').write_text('Changed after review')
    elif change == 'external':
        for record in [*case[2]['checks'], *case[2]['gates']]:
            record['reports'][0]['path'] = '../report.txt'
    else:
        for record in [*case[2]['checks'], *case[2]['gates']]:
            record['reports'] = []
    result = evaluate(case)
    assert not result['acceptance']['accepted']
    assert result['coverage']['semantic']['verified_percent'] == 0


def test_no_report_may_override_static_or_contract_blockers(case):
    case[0]['static']['blocking_issues'] = ['Invalid SQL']
    case[0]['contracts'][0]['unresolved_items'] = ['Missing source rule']
    result = evaluate(case)
    assert not result['acceptance']['accepted']
    assert 'Invalid SQL' in result['blocking_issues']
    assert any('Missing source rule' in s for s in result['blocking_issues'])


def test_unsupported_behavior_is_not_credited(case):
    case[0]['contracts'][0]['implementation_strategy']['decision'] = 'UNSUPPORTED'
    result = evaluate(case)
    assert result['coverage']['semantic']['total_weight'] == 5
    assert result['coverage']['semantic']['verified_weight'] == 0
    assert not result['acceptance']['accepted']


def write_package(case):
    package, _, _, root, step = case
    out = root / 'package'
    out.mkdir()
    plan = {'package_name': 'Test', 'steps': [step], 'unresolved': []}
    docs = {
        'migration_contracts.json': {'contract_bundle_version': '1.0', 'contracts': package['contracts']},
        'migration_plan.json': plan,
        'validation_results.json': [{'status': 'PASS'}],
        'Test_Migration.ipynb': {'nbformat': 4, 'nbformat_minor': 5,
                               'metadata': {'application/databricks': {'name': 'Test', 'language': 'python'}},
                               'cells': [{'id': 'load', 'cell_type': 'code', 'metadata': {},
                                          'execution_count': None, 'outputs': [], 'source': 'value = 1'}]},
        'Test_job.json': JobGenerator().generate(plan, 'Test_Migration.ipynb').to_dict(),
    }
    for name, doc in docs.items():
        (out / name).write_text(json.dumps(doc))
    (out / 'databricks.yml').write_text('{}')
    return out


def test_load_package_binds_generation_and_checks_source(case):
    out = write_package(case)
    first = load_package(out)
    assert not first['source_issues']
    assert 'migration_plan.json' in first['artifact_hashes']
    (case[3] / 'source.sql').write_text('changed source')
    assert load_package(out)['source_issues']


def test_changed_plan_does_not_match_contract(case):
    out = write_package(case)
    doc = json.loads((out / 'migration_plan.json').read_text())
    doc['steps'][0]['transformations'].append('added')
    (out / 'migration_plan.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        load_package(out)


def test_changed_job_dependencies_rejected(case):
    out = write_package(case)
    path = out / 'Test_job.json'
    doc = json.loads(path.read_text())
    doc['tasks'][0]['depends_on'] = [{'task_key': 'unknown'}]
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='dependency graph'):
        load_package(out)


def test_fresh_notebook_checks_catch_stale_static_results(case):
    out = write_package(case)
    (out / 'Test_Migration.ipynb').write_text('{}')
    assert load_package(out)['static']['blocking_issues']


def test_cli_prepare_then_evaluate_drafts_blocks_without_execution(case):
    out = write_package(case)
    drafts = case[3] / 'review'
    command = [sys.executable, '-m', 'ssis_migration.validator.acceptance', '--package-dir', str(out)]
    proc = subprocess.run(command + ['--prepare', str(drafts)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    policy = json.loads((drafts / 'policy.json').read_text())
    assert all(b['weight'] is None and b['critical'] is None for b in policy['behaviors'])
    result_path = case[3] / 'acceptance.json'
    proc = subprocess.run(command + ['--policy', str(drafts / 'policy.json'), '--evidence', str(drafts / 'evidence.json'),
                                     '--output', str(result_path)], capture_output=True, text=True)
    assert proc.returncode == 1, proc.stderr
    assert json.loads(result_path.read_text())['lifecycle_status'] == 'BLOCKED'
    proc = subprocess.run(command + ['--prepare', str(drafts)], capture_output=True, text=True)
    assert proc.returncode == 2  # never overwrite an earlier review


def test_draft_evidence_does_not_share_mutable_hash_snapshot(case):
    _, draft = draft_documents(case[0])
    draft['artifact_hashes']['new'] = 'b' * 64
    assert 'new' not in case[0]['artifact_hashes']


def test_cli_acceptance_with_current_attestations_never_executes_notebook(case):
    out = write_package(case)
    sentinel = case[3] / 'workload-executed'
    notebook = json.loads((out / 'Test_Migration.ipynb').read_text())
    notebook['cells'] = [{
        'id': 'load', 'cell_type': 'code', 'metadata': {}, 'execution_count': None, 'outputs': [],
        'source': f"open({str(sentinel)!r}, 'w').write('executed')",
    }]
    (out / 'Test_Migration.ipynb').write_text(json.dumps(notebook))
    current = load_package(out)
    case[2]['artifact_hashes'] = current['artifact_hashes']
    policy_path = case[3] / 'policy.json'
    evidence_path = case[3] / 'evidence.json'
    output = case[3] / 'accepted.json'
    policy_path.write_text(json.dumps(case[1]))
    evidence_path.write_text(json.dumps(case[2]))
    proc = subprocess.run([
        sys.executable, '-m', 'ssis_migration.validator.acceptance', '--package-dir', str(out),
        '--policy', str(policy_path), '--evidence', str(evidence_path), '--output', str(output),
    ], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(output.read_text())['acceptance']['accepted']
    assert not sentinel.exists()


def test_changed_step_selection_in_job_rejected(case):
    out = write_package(case)
    path = out / 'Test_job.json'
    job = json.loads(path.read_text())
    job['tasks'][0]['notebook_task']['base_parameters']['step_id'] = 'wrong'
    path.write_text(json.dumps(job))
    with pytest.raises(ValueError, match='dependency graph'):
        load_package(out)


def test_symlink_report_outside_evidence_directory_cannot_pass(case):
    reports = case[3] / 'reports'
    reports.mkdir()
    (reports / 'linked.txt').symlink_to(case[3] / 'report.txt')
    for record in [*case[2]['checks'], *case[2]['gates']]:
        record['reports'][0]['path'] = 'linked.txt'
    result = evaluate_acceptance(*case[:3], report_root=reports)
    assert not result['acceptance']['accepted']


def test_empty_execution_evidence_stays_not_run(case):
    case[2]['checks'] = [c for c in case[2]['checks'] if c['dimension'] == 'semantic']
    result = evaluate(case)
    assert result['coverage']['execution']['status'] == 'NOT_RUN'
    assert result['coverage']['data_equivalence']['status'] == 'NOT_RUN'


def test_large_weights_fail_safely_instead_of_overflowing_to_acceptance(case):
    for item in case[1]['behaviors']:
        item['weight'] = 1e308
    rebind(case)
    with pytest.raises(OverflowError):
        evaluate(case)


def test_invalid_json_duplicate_keys_rejected(case):
    from ssis_migration.validator.acceptance import _read
    path = case[3] / 'invalid.json'
    path.write_text('{"status":"FAIL","status":"PASS"}')
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        _read(path)


def test_aggregate_gate_cannot_hide_absent_behavior_execution(case):
    case[2]['checks'] = [c for c in case[2]['checks'] if c['dimension'] == 'semantic']
    assert not evaluate(case)['acceptance']['accepted']


def test_empty_notebook_is_not_a_valid_generation(case):
    out = write_package(case)
    path = out / 'Test_Migration.ipynb'
    notebook = json.loads(path.read_text())
    notebook['cells'] = []
    path.write_text(json.dumps(notebook))
    assert load_package(out)['static']['blocking_issues']


def test_report_identifies_unverified_noncritical_gap(case):
    case[2]['checks'][3]['status'] = 'UNVERIFIED'
    result = evaluate(case)
    assert result['acceptance']['accepted']
    assert len(result['behavior_results']) == 2
    gap = result['behavior_results'][1]
    assert gap['weight'] == 1
    assert gap['critical'] is False
    assert gap['dimensions']['semantic'] == 'UNVERIFIED'
    assert gap['dimensions']['execution'] == 'VERIFIED'


def test_rounded_eighty_must_not_accept_exactly_below_threshold(case):
    case[1]['behaviors'][0]['weight'] = 40000000000000000
    case[1]['behaviors'][1]['weight'] = 10000000000000001
    rebind(case)
    case[2]['checks'][3]['status'] = 'UNVERIFIED'
    assert not evaluate(case)['acceptance']['accepted']
