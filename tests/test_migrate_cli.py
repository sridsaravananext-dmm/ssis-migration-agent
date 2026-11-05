"""End-to-end fixture checks for the assessment output contract."""
import json
from pathlib import Path
import pytest
from ssis_migration.migrate import run_migration

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope='module')
def conversion(tmp_path_factory):
    output = tmp_path_factory.mktemp('wwi-assessment')
    return run_migration(str(ROOT / 'examples/wwi/DailyETLMain.dtsx'), str(output),
        connection_mapping=str(ROOT / 'examples/wwi/connection_mapping.json'), base_path=str(ROOT))


def test_wwi_generation_is_not_certified_as_migrated(conversion):
    assert conversion['status'] == 'REVIEW_REQUIRED'
    assert conversion['analyzed_package_count'] == 1
    assert conversion['generated_package_count'] == 1
    assert conversion['failed_package_count'] == 0
    assert conversion['verification'] == {'status': 'NOT_MEASURED', 'legacy_match_percent': None}


def test_output_contract_and_manifest(conversion):
    package = conversion['packages'][0]
    assert {'notebook', 'review', 'manifest', 'ir', 'analysis', 'plan', 'validation', 'assessment', 'source_inventory'} <= set(package['artifacts'])
    for path in package['artifacts'].values(): assert Path(path).is_file()
    manifest = json.loads(Path(package['artifacts']['manifest']).read_text())
    assert len(manifest['fingerprint']) == 64
    assert manifest['artifacts']['notebook'] == package['artifacts']['notebook']
    assert len(manifest['database_metadata']) == 2


def test_source_inventory_contains_every_task_and_component(conversion):
    artifacts = conversion['packages'][0]['artifacts']
    ir = json.loads(Path(artifacts['ir']).read_text())
    inventory = json.loads(Path(artifacts['source_inventory']).read_text())
    expected = len(ir['executables']) + sum(len(f['components']) for f in ir['data_flows'])
    assert len(inventory) == expected
    assert all(i['disposition'] in {'PLANNED_CANDIDATE', 'REVIEW_ONLY'} for i in inventory)
    plan = json.loads(Path(artifacts['plan']).read_text())
    assert plan['unresolved']
    assert any(s['step_type'] == 'REVIEW_ONLY' for s in plan['steps'])


def test_validation_results_and_runtime_gate(conversion):
    entry = conversion['packages'][0]
    validations = json.loads(Path(entry['artifacts']['validation']).read_text())
    assert validations
    assert any(v['status'] == 'REJECTED' for v in validations)
    assert all(v['status'] in {'PASS', 'REJECTED'} for v in validations)
    notebook = json.loads(Path(entry['artifacts']['notebook']).read_text())
    assert notebook['nbformat'] == 4
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code': compile(''.join(cell['source']), '<cell>', 'exec')
    assert 'raise RuntimeError' in ''.join(notebook['cells'][2]['source'])
    assert notebook['metadata']['assessment']['verification'] == 'NOT_MEASURED'


def test_report_metrics_are_explicitly_static(conversion):
    summary = conversion['packages'][0]['assessment']
    assert summary['verification']['legacy_match_percent'] is None
    assert summary['human_review_item_count'] > 0
    generation = summary['generation']
    assert generation['statically_valid_operations'] < generation['operation_count']
    assert 'not task coverage or behavioral accuracy' in generation['basis']


def test_execute_is_rejected_by_api(tmp_path):
    with pytest.raises(ValueError, match='not implemented'):
        run_migration(str(ROOT / 'examples/wwi/DailyETLMain.dtsx'), str(tmp_path), execute=True)
