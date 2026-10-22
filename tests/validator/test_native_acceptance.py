import json
from pathlib import Path
import shutil

import pytest

from ssis_migration.migrate import run_migration
from ssis_migration.validator.acceptance import draft_documents, evaluate_acceptance, load_package, document_digest, DIMENSIONS

DEMO=Path(__file__).resolve().parents[2]/'examples/tm1/databricks_demo'


@pytest.fixture(scope='module')
def native_candidate(tmp_path_factory):
    output=tmp_path_factory.mktemp('native-acceptance')
    report=run_migration(str(DEMO/'manifest.json'),str(output),target_mapping=str(DEMO/'target_mapping.json'))
    return Path(report['artifacts']['report']).parent


def test_native_acceptance_drafts_preserve_every_obligation_and_earn_no_fixture_credit(native_candidate,tmp_path):
    package=load_package(native_candidate)
    contract=json.loads((native_candidate/'generation_contract.json').read_text())
    policy,evidence=draft_documents(package)
    assert {b['behavior_id'] for b in policy['behaviors']}==set(contract['obligation_ids'])
    assert all(b['weight'] is None and b['critical'] is None for b in policy['behaviors'])
    assert 'independent_source_observations' in policy['additional_gates']
    result=evaluate_acceptance(package,policy,evidence,report_root=tmp_path)
    assert not result['acceptance']['accepted']
    assert result['coverage']['semantic']['verified_percent'] is None
    assert not any(result['mandatory_gates'].values())
    # The original generic contracts stay review-only.
    review=json.loads((native_candidate/'migration_contracts.json').read_text())
    assert all(c['implementation_strategy']['decision'] != 'DETERMINISTIC' for c in review['contracts'])


@pytest.mark.parametrize('change',['notebook','source_archive','missing_inventory_entry','extra_file','mapping','graph','source_index'])
def test_native_candidate_loader_rejects_changed_or_incomplete_artifacts(native_candidate,tmp_path,change):
    target=tmp_path/'candidate';shutil.copytree(native_candidate,target)
    if change=='notebook':(target/'Migration.ipynb').write_text('{}')
    elif change=='source_archive':next((target/'source_evidence').iterdir()).write_bytes(b'changed')
    elif change=='extra_file':(target/'untracked.py').write_text('print(1)')
    else:
        filename={'missing_inventory_entry':'artifact_inventory.json','mapping':'target_mapping.json','graph':'normalized_ir.json','source_index':'source_evidence_index.json'}[change]
        document=json.loads((target/filename).read_text())
        if change=='missing_inventory_entry':document['sha256'].pop('Migration.ipynb')
        elif change=='mapping':document['max_rows']=1
        elif change=='graph':document['nodes'].pop()
        else:document.pop()
        (target/filename).write_text(json.dumps(document))
    with pytest.raises((ValueError,KeyError)):load_package(target)


def test_native_package_gates_cannot_be_removed_by_editing_policy(native_candidate,tmp_path):
    package=load_package(native_candidate);policy,evidence=draft_documents(package)
    policy['additional_gates']=[];evidence['gates']=[]
    result=evaluate_acceptance(package,policy,evidence,report_root=tmp_path)
    assert result['mandatory_gates']['independent_source_observations'] is False
    assert result['mandatory_gates']['tm1_semantic_profile'] is False


def test_native_acceptance_cannot_replace_engine_observations_with_an_attested_pass(native_candidate,tmp_path):
    import copy,hashlib
    package=load_package(native_candidate);policy,evidence=draft_documents(package)
    for item in policy['behaviors']:item.update(weight=1,critical=True)
    path=tmp_path/'synthetic_pass.json';path.write_text('{"status":"PASS","kind":"SYNTHETIC"}')
    reference={'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'locator':'json:/',
               'reviewer':'fixture reviewer','method':'negative acceptance test'}
    evidence['policy_digest']=document_digest(policy)
    for gate in evidence['gates']:gate.update(status='PASS',reports=[copy.deepcopy(reference)])
    evidence['checks']=[{'contract_id':item['contract_id'],'behavior_id':item['behavior_id'],
                        'dimension':dimension,'status':'PASS','reports':[copy.deepcopy(reference)]}
                       for item in policy['behaviors'] for dimension in DIMENSIONS]
    result=evaluate_acceptance(package,policy,evidence,report_root=tmp_path)
    assert not result['acceptance']['accepted']
    assert result['mandatory_gates']['independent_source_observations'] is False
    assert any('Native source observation gate failed' in issue for issue in result['blocking_issues'])
    assert result['migration_scope']=='BOUNDED_PREVIEW_ONLY'


@pytest.mark.parametrize('mode',['prepare','evaluate'])
def test_native_acceptance_cli_cannot_modify_its_immutable_candidate(native_candidate,tmp_path,monkeypatch,mode):
    from ssis_migration.validator.acceptance import main
    output=native_candidate/'forbidden-acceptance-output'
    args=['acceptance','--package-dir',str(native_candidate)]
    if mode=='prepare':args+=['--prepare',str(output)]
    else:args+=['--policy',str(tmp_path/'policy.json'),'--evidence',str(tmp_path/'evidence.json'),'--output',str(output)]
    monkeypatch.setattr('sys.argv',args)
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2 and not output.exists()
