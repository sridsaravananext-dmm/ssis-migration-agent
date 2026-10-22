import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.generator.tm1.contract import build_contract,load_mapping,validate_contract
from ssis_migration.generator.tm1.emitter import render,validate_rendered
from ssis_migration.ui import (artifact_archive,bundle_candidates,effective_mapping,extract_bundle,
    report_summary,run_frontend_migration,validate_bundle_scope)
from ssis_migration.workspace import validate_workspace_config,widget_code

ROOT=Path(__file__).resolve().parents[1]
DEMO=ROOT/'examples/tm1/databricks_demo'


@pytest.mark.parametrize('relative,platform',[
    ('informatica/simple_mapping.xml','informatica'),('bods/structural_job.xml','bods'),('odi/structural_export.xml','odi'),
])
def test_workbench_discovers_and_routes_native_xml_without_ssis_options(tmp_path,relative,platform):
    source=ROOT/'examples'/relative
    assert source in bundle_candidates(source.parent)
    report=run_frontend_migration(source,tmp_path,catalog='unused_ui_catalog',base_path=ROOT)
    assert report['source_platform']==platform and report['scope']=='REVIEW_ONLY'
    assert report['preservation']['node_count']==report['preservation']['assigned_node_count']
    assert report['generated_artifact_count']==0 and not report['migration_accepted']


def test_workbench_comparison_archive_is_portable_and_cannot_promote_synthetic_results():
    from ssis_migration.ui import reconciliation_bundle
    source=(ROOT/'examples/validation/source_observation.json').read_bytes()
    target=(ROOT/'examples/validation/target_observation.json').read_bytes()
    report,raw=reconciliation_bundle(source,target)
    assert report['status']=='MATCH' and report['source_equivalence']=='NOT_VERIFIED'
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        assert set(bundle.namelist())=={'source_observation.json','target_observation.json','reconciliation.json'}
        assert bundle.read('source_observation.json')==source
        assert json.loads(bundle.read('reconciliation.json'))==report
        for record in report['observation_files'].values():
            assert hashlib.sha256(bundle.read(record['path'])).hexdigest()==record['sha256']


@pytest.fixture
def settings(tmp_path):
    return {'config_version':'1.0','catalog':'analytics','schemas':{'bronze':'raw','silver':'curated','gold':'serving'},
            'target_layer':'gold','source_path':str(DEMO/'manifest.json'),'artifact_path':str(tmp_path/'outputs'),
            'data_destination_path':'/Volumes/analytics/serving/review/results',
            'notebook_root':'/Workspace/Shared/etl_review','existing_cluster_id':'0123-456789-cluster',
            'input_mode':'fixtures','output_tables':{'load':'analytics.serving.sales_load','query':'analytics.serving.cell_query','rules':'analytics.serving.rule_results'}}


def archive(entries):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in entries:z.writestr(name,data)
    return stream.getvalue()


def test_uploaded_bundle_retains_paths_hashes_and_explicit_source_roles(tmp_path):
    raw=artifact_archive(DEMO);root=extract_bundle(raw,tmp_path/'input')
    candidates=bundle_candidates(root)
    assert candidates==[root/'manifest.json']
    document,consumed=validate_bundle_scope(candidates[0],root)
    assert len(document['units'])==3 and len(consumed)==9
    for original in DEMO.iterdir():assert (root/original.name).read_bytes()==original.read_bytes()
    assert get_adapter('tm1').analyze(candidates[0]).normalized_ir['ir_version']=='2.0'


@pytest.mark.parametrize('name',['../outside','/absolute','C:/drive','nested/../../outside','nested\\outside','./file','nested/./file','file\nname'])
def test_archive_traversal_is_rejected_before_writing(tmp_path,name):
    root=tmp_path/'extracted'
    with pytest.raises(ValueError):extract_bundle(archive([(name,b'payload')]),root)
    assert not root.exists()


def test_archive_links_duplicates_and_expanded_size_are_rejected(tmp_path,monkeypatch):
    info=zipfile.ZipInfo('link');info.external_attr=(stat.S_IFLNK|0o777)<<16
    with pytest.raises(ValueError,match='links'):extract_bundle(archive([(info,b'../outside')]),tmp_path/'link')
    with pytest.raises(ValueError,match='colliding'):extract_bundle(archive([('a.json',b'1'),('A.json',b'2')]),tmp_path/'duplicates')
    import ssis_migration.ui as ui
    monkeypatch.setattr(ui,'MAX_ARCHIVE_BYTES',1024)
    with pytest.raises(ValueError,match='Uncompressed'):extract_bundle(archive([('large.txt',b'a'*2048)]),tmp_path/'large')


@pytest.mark.parametrize('role',['metadata','unit','section'])
def test_manifest_cannot_read_files_outside_uploaded_bundle(tmp_path,role):
    root=extract_bundle(artifact_archive(DEMO),tmp_path/'bundle')
    doc=json.loads((root/'manifest.json').read_text())
    outside=tmp_path/'outside.json';outside.write_text('{}')
    if role=='metadata':doc['metadata_files'][0]['path']='../outside.json'
    elif role=='unit':doc['units'][0]['path']='../outside.json'
    else:
        process=json.loads((root/'process.json').read_text());process['sections']['data']['path']='../outside.json'
        (root/'process.json').write_text(json.dumps(process))
    (root/'manifest.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='escapes'):validate_bundle_scope(root/'manifest.json',root)


def test_runtime_edits_do_not_change_source_binding_or_expected_results():
    doc=json.loads((DEMO/'target_mapping.json').read_text());before=copy.deepcopy(doc)
    updated=effective_mapping(doc,{'load':'analytics.raw.cells'},
        {'load':{'datasource_table':'analytics.raw.rows','parameters':{'pFactor':5}}},input_mode='fixtures')
    assert doc==before
    assert updated['source_bundle_sha256']==doc['source_bundle_sha256']
    assert updated['fixture_cases']==doc['fixture_cases']
    assert updated['models'][0]['input_table']=='analytics.raw.cells' and updated['units'][0]['parameters']=={'pFactor':5}
    with pytest.raises(ValueError,match='unknown'):effective_mapping(doc,{'missing':'a.b.c'},{},input_mode='fixtures')
    with pytest.raises(ValueError,match='snapshot table'):effective_mapping(doc,{},{},input_mode='tables')


@pytest.mark.parametrize('change',['identifier','duplicate_layers','traversal','foreign_output','unknown_field','cluster'])
def test_invalid_workspace_settings_are_rejected(settings,change):
    if change=='identifier':settings['catalog']='catalog; DROP TABLE data'
    if change=='duplicate_layers':settings['schemas']['gold']='RAW'
    if change=='traversal':settings['notebook_root']='/Workspace/Shared/../other'
    if change=='foreign_output':settings['output_tables']['load']='other.serving.load'
    if change=='unknown_field':settings['execute']=True
    if change=='cluster':settings['existing_cluster_id']='not a cluster/id'
    with pytest.raises(ValueError):validate_workspace_config(settings)


def test_workspace_paths_are_data_and_not_executable_expressions(settings):
    settings['source_path']="/Volumes/source'; raise RuntimeError('injected') #"
    code=widget_code(settings)
    tree=ast.parse(code)
    assert 'injected' in code
    assert not any(isinstance(node,ast.Raise) for node in ast.walk(tree))
    assert not any(isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {'eval','exec'} for node in ast.walk(tree))


def test_ui_native_route_omits_ssis_arguments_and_does_not_deploy(monkeypatch,settings,tmp_path):
    from ssis_migration import migrate
    captured={}
    monkeypatch.setattr(migrate,'run_migration',lambda **kw:captured.update(kw) or {'status':'GENERATED'})
    run_frontend_migration(DEMO/'manifest.json',tmp_path/'out',platform='tm1',target_mapping=DEMO/'target_mapping.json',
        workspace_config=settings,catalog='ignored_legacy_argument',base_path='/unused',mapping='unused.json')
    assert captured['workspace_config']==settings and captured['execute'] is False
    assert captured['target_mapping']==str(DEMO/'target_mapping.json')
    assert not any(k in captured for k in ['catalog','base_path','connection_mapping','dependency_files'])


def test_config_is_bound_before_tm1_code_and_reaches_notebooks_jobs_and_bundle(settings):
    graph=get_adapter('tm1').analyze(DEMO/'manifest.json').normalized_ir
    contract=build_contract(graph,load_mapping(DEMO/'target_mapping.json'),workspace_config=settings)
    validate_contract(contract)
    artifacts,_=render(contract);validate_rendered(contract,artifacts)
    code='\n'.join(''.join(c['source']) for c in artifacts['Migration.ipynb']['cells'] if c['cell_type']=='code')
    assert "'catalog', 'analytics'" in code and "'schema', 'serving'" in code
    assert 'analytics.serving.sales_load' in code and settings['data_destination_path'] in code
    assert all(t['existing_cluster_id']==settings['existing_cluster_id'] for t in artifacts['job.json']['tasks'])
    assert {t['notebook_task']['notebook_path'] for t in artifacts['job.json']['tasks']}=={
        '/Workspace/Shared/etl_review/Migration','/Workspace/Shared/etl_review/Validation'}
    assert artifacts['databricks.yml']['variables']['notebook_root']['default']==settings['notebook_root']
    changed=copy.deepcopy(contract);changed['workspace_config']['catalog']='other'
    with pytest.raises(ValueError):validate_contract(changed)


def test_reports_show_native_blocking_and_unknown_acceptance_honestly():
    summary=report_summary({'status':'BLOCKED','generated_artifact_count':0,'migration_accepted':False})
    assert summary['status']=='BLOCKED' and summary['generated']==0 and not summary['accepted']
    assert report_summary({'status':'GENERATED','execution_status':'NOT_RUN'})['execution']=='NOT_RUN'


def test_ssis_workspace_defaults_reach_notebook_and_job_without_changing_source_schemas(settings,tmp_path):
    from ssis_migration.migrate import run_migration
    settings['source_path']=str(ROOT/'examples/wwi/DailyETLMain.dtsx')
    settings['artifact_path']=str(tmp_path)
    settings['output_tables']={}
    settings['existing_cluster_id']=None
    settings['notebook_root']=None
    report=run_migration(settings['source_path'],str(tmp_path/'generated'),base_path=str(ROOT),workspace_config=settings)
    assert report['workspace_config']==settings
    assert report['acceptance']['accepted'] is False
    package=report['packages'][0]
    files=package['artifacts']
    notebook=json.loads(Path(files['notebook']).read_text())
    parameters='\n'.join(''.join(c['source']) for c in notebook['cells'] if c.get('metadata',{}).get('migration_role')=='parameters')
    assert "'catalog', 'analytics'" in parameters and "'schema', 'serving'" in parameters
    job=json.loads(Path(files['job']).read_text())
    assert not job.get('job_clusters')
    assert all(t['existing_cluster_id']=='${var.existing_cluster_id}' for t in job['tasks'])
    assert all(t['notebook_task']['notebook_path'].startswith('${var.notebook_root}/') for t in job['tasks'])
    assert all(not t['notebook_task']['notebook_path'].endswith('.ipynb') for t in job['tasks'])
    dab=json.loads(Path(files['dabs']).read_text())
    assert {'notebook_root','existing_cluster_id'}<=set(dab['variables'])
    # Source schemas survive instead of being guessed into a medallion layer.
    plan=json.loads(Path(files['plan']).read_text())
    assert plan['metadata']['workspace_config']==settings


@pytest.mark.parametrize('change',['source','catalog','output','missing_mapping'])
def test_api_does_not_ignore_conflicting_workspace_configuration(settings,tmp_path,change):
    from ssis_migration.migrate import run_migration
    kwargs={'target_mapping':str(DEMO/'target_mapping.json')}
    output=Path(settings['artifact_path'])/'run'
    if change=='source':settings['source_path']=str(tmp_path/'different.json')
    if change=='catalog':kwargs['catalog']='other'
    if change=='output':output=tmp_path/'outside'
    if change=='missing_mapping':kwargs={}
    with pytest.raises(ValueError):run_migration(str(DEMO/'manifest.json'),str(output),workspace_config=settings,**kwargs)


@pytest.mark.parametrize('field',['artifact_path','data_destination_path'])
@pytest.mark.parametrize('value',['/Workspace','/Workspace/Users/developer/etl','/tmp/../Workspace/etl'])
def test_workspace_folders_are_not_local_artifact_or_data_storage(settings,field,value):
    settings[field]=value
    with pytest.raises(ValueError,match='[Ww]orkspace'):validate_workspace_config(settings)


def test_run_directory_permission_error_is_actionable_without_silent_fallback(monkeypatch,tmp_path):
    from ssis_migration.ui import prepare_run_directory
    original=Path.mkdir
    root=tmp_path/'not-writable'
    def deny(path,*args,**kwargs):
        if path==root:raise PermissionError('app cannot write this path')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'mkdir',deny)
    with pytest.raises(ValueError,match='not writable.*default /tmp'):
        prepare_run_directory(root)
    assert not root.exists()


def test_metadata_rebinding_is_explicit_and_keeps_original_mapping_evidence(tmp_path):
    from ssis_migration.ui import bind_connection_metadata
    source=tmp_path/'database_metadata_wwi (1).json';source.write_text('{"tables": []}')
    destination=tmp_path/'database_metadata_wwi_dw (1).json';destination.write_text('{"tables": []}')
    doc=json.loads((ROOT/'examples/wwi/connection_mapping.json').read_text());original=copy.deepcopy(doc)
    unchanged,bindings,issues=bind_connection_metadata(doc,{},allowed_files=[source,destination],base_path=tmp_path)
    assert unchanged==doc and not bindings and len(issues)==2
    effective,bindings,issues=bind_connection_metadata(doc,{0:source,1:destination},allowed_files=[source,destination],base_path=tmp_path)
    assert not issues and doc==original
    assert [entry['metadata_file'] for entry in effective['mappings']]==[str(source),str(destination)]
    for index,entry in enumerate(effective['mappings']):
        assert {k:v for k,v in entry.items() if k!='metadata_file'}=={k:v for k,v in doc['mappings'][index].items() if k!='metadata_file'}
    assert bindings[0]['original_reference']=='examples/wwi/database_metadata_wwi.json'
    assert bindings[0]['sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert all(b['resolution']=='USER_SELECTED' for b in bindings)
    with pytest.raises(ValueError,match='supplied'):
        bind_connection_metadata(doc,{0:tmp_path/'not-supplied.json'},allowed_files=[source,destination],base_path=tmp_path)
