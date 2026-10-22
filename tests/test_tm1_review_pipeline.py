import copy
import json
from pathlib import Path

import pytest

from ssis_migration.adapters.tm1.pipeline import validate_tm1_review_result
from ssis_migration.ir.normalized import normalized_ir_id
from ssis_migration.migrate import run_migration
from ssis_migration.schemas import SchemaValidationError
from ssis_migration.validator.acceptance import load_package

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'examples/tm1/bundle/manifest.json'


@pytest.fixture(scope='module')
def native_output(tmp_path_factory):
    output=tmp_path_factory.mktemp('tm1-native-output')
    report=run_migration(str(SOURCE),str(output),source_platform='tm1')
    artifacts={key:json.loads(Path(path).read_text()) for key,path in report['artifacts'].items()}
    return report,artifacts


def test_main_pipeline_emits_review_artifacts_before_any_generation(native_output):
    report,artifacts=native_output
    assert report['status']==report['lifecycle_status']=='BLOCKED'
    assert report['source_analysis_status']=='REVIEW_REQUIRED'
    assert report['analyzed_package_count']==1
    assert report['migrated_package_count']==report['generated_artifact_count']==0
    assert report['scope']=='REVIEW_ONLY' and report['migration_accepted'] is False
    assert report['acceptance']['accepted'] is False and report['acceptance']['required_semantic_coverage_percent']==80
    assert report['coverage']['semantic']=={'status':'NOT_VERIFIED','verified_percent':None}
    assert report['coverage']['execution']==report['coverage']['data_equivalence']=={'status':'NOT_RUN','verified_percent':None}
    assert all(status=='STRUCTURED' for status in report['parser_status'].values())
    validate_tm1_review_result(artifacts['source_envelope'],artifacts['normalized_ir'],artifacts['plan'],artifacts['contracts']['contracts'])
    assert artifacts['plan_ir_binding']['node_count']==artifacts['plan_ir_binding']['assigned_node_count']==254
    assert artifacts['plan_ir_binding']['edge_count']==artifacts['plan_ir_binding']['assigned_edge_count']==394
    directory=Path(report['artifacts']['report']).parent
    assert len(list(directory.iterdir()))==7
    assert not list(directory.glob('*.ipynb')) and not list(directory.glob('*job.json'))
    assert not (directory/'databricks.yml').exists() and not (directory/'package_ir.json').exists()


def test_tm1_bundle_auto_detection_does_not_call_ssis_sql_ai_or_generation(tmp_path,monkeypatch):
    import sqlglot
    import ssis_migration.analyzer as analyzer
    from ssis_migration.ir.builder import IRBuilder
    from ssis_migration.generator.dispatcher import GeneratorDispatcher
    import ssis_migration.agent.provider_factory as factory
    def forbidden(*args,**kwargs):raise AssertionError('TM1 native analysis must not use SSIS/SQL parsing, target generation or providers.')
    monkeypatch.setattr(IRBuilder,'build',forbidden)
    monkeypatch.setattr(analyzer,'analyze_package',forbidden)
    monkeypatch.setattr(sqlglot,'parse',forbidden);monkeypatch.setattr(sqlglot,'tokenize',forbidden)
    monkeypatch.setattr(GeneratorDispatcher,'generate',forbidden)
    monkeypatch.setattr(factory,'create_provider',forbidden)
    result=run_migration(str(SOURCE),str(tmp_path))
    assert result['source_platform']=='tm1' and result['execution_status']=='NOT_RUN'


@pytest.mark.parametrize('option',[
    {'ai':True},{'execute':True},{'catalog':'Target'},{'ddl_path':'schema.sql'},
    {'procedures_path':'procedures.sql'},{'connection_mapping':'connections.json'},
    {'dependency_files':['extra.json']},{'file_roles':{'extra.json':'parameters'}},{'base_path':'/tmp'},
])
def test_unsupported_call_options_are_rejected_without_ignoring_inputs(tmp_path,option):
    with pytest.raises(ValueError):run_migration(str(SOURCE),str(tmp_path),source_platform='tm1',**option)
    assert not list(tmp_path.iterdir())


def test_acceptance_loader_refuses_review_outputs_even_with_fake_target_artifacts(native_output,tmp_path):
    report,_=native_output
    (tmp_path/'source_envelope.json').write_bytes(Path(report['artifacts']['source_envelope']).read_bytes())
    (tmp_path/'databricks.yml').write_text('resources: {}')
    (tmp_path/'Fake_Migration.ipynb').write_text('{}')
    (tmp_path/'Fake_job.json').write_text('{}')
    with pytest.raises(ValueError,match='cannot be accepted'):load_package(tmp_path)


@pytest.mark.parametrize('change',['envelope','graph','contracts'])
def test_saved_review_bundle_disagreement_or_omission_is_rejected(native_output,change):
    _,data=native_output;data=copy.deepcopy(data)
    if change=='envelope':data['source_envelope']['status']='BLOCKED'
    elif change=='graph':
        data['normalized_ir']['edges'].pop()
        data['normalized_ir']['ir_id']=normalized_ir_id(data['normalized_ir'])
    else:data['contracts']['contracts'].pop()
    with pytest.raises(SchemaValidationError):validate_tm1_review_result(data['source_envelope'],data['normalized_ir'],data['plan'],data['contracts']['contracts'])


def test_repeated_runs_preserve_previous_outputs_and_sources(tmp_path):
    kept=tmp_path/'migration_report.json';kept.write_text('keep previous output')
    original=SOURCE.read_bytes()
    first=run_migration(str(SOURCE),str(tmp_path))
    second=run_migration(str(SOURCE),str(tmp_path))
    assert first['artifacts']['report']!=second['artifacts']['report']
    assert Path(first['artifacts']['report']).is_file() and Path(second['artifacts']['report']).is_file()
    assert kept.read_text()=='keep previous output' and SOURCE.read_bytes()==original
