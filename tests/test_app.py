from pathlib import Path

import streamlit as st
import pytest
from streamlit.testing.v1 import AppTest


def test_bundled_sample_generates_reviewable_results_without_claiming_acceptance():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    assert not app.exception
    app.radio[0].set_value("Use WWI sample package").run()
    assert not app.exception
    run_button = next(button for button in app.button if button.label == "🚀 Run Migration")
    run_button.click().run(timeout=30)
    assert not app.exception
    report = app.session_state["migration_result"]
    assert report["migrated_package_count"] == 1
    assert report["lifecycle_status"] == "BLOCKED"
    assert report["acceptance"]["accepted"] is False
    assert any(metric.label == "Generated" for metric in app.metric)
    assert any("blocking findings" in warning.value for warning in app.warning)


class UploadedFile:
    def __init__(self, name, content):
        self.name = name
        self.content = content

    def getvalue(self):
        return self.content


@pytest.mark.parametrize('relative,platform',[
    ('informatica/simple_mapping.xml','informatica'),('bods/structural_job.xml','bods'),('odi/structural_export.xml','odi'),
])
def test_native_xml_upload_produces_downloadable_review_without_notebooks(monkeypatch,relative,platform):
    root=Path(__file__).resolve().parents[1]
    source=root/'examples'/relative
    upload=UploadedFile(source.name,source.read_bytes())
    monkeypatch.setattr(st,'file_uploader',lambda *a,**k:upload if k.get('key')=='package_upload' else None)
    app=AppTest.from_file(str(root/'app.py')).run()
    assert not app.exception
    assert next(w for w in app.checkbox if w.label=='Enable AI-assisted generation').disabled
    button=next(w for w in app.button if w.label=='🚀 Run Migration')
    assert not button.disabled
    button.click().run(timeout=30)
    assert not app.exception
    report=app.session_state['migration_result']
    assert report['source_platform']==platform and report['scope']=='REVIEW_ONLY'
    assert report['generated_artifact_count']==0 and report['migration_accepted'] is False
    assert Path(report['artifacts']['normalized_ir']).is_file()


def test_public_powercenter_dependency_requires_selection_then_resolves_in_workbench(monkeypatch):
    import json
    root=Path(__file__).resolve().parents[1]
    sources=root/'examples/public/powercenter_scd1/upstream/SCD1'
    workflow=UploadedFile('w_scd1.XML',(sources/'w_scd1.XML').read_bytes())
    mapping=UploadedFile('m_scd1.XML',(sources/'m_scd1.XML').read_bytes())
    uploads={'package_upload':workflow,'powercenter_dependency_uploads':[mapping]}
    monkeypatch.setattr(st,'file_uploader',lambda *a,**k:uploads.get(k.get('key')))
    app=AppTest.from_file(str(root/'app.py')).run()
    assert not app.exception
    selection=next(w for w in app.multiselect if w.key=='powercenter_dependencies')
    assert selection.value==[]
    next(w for w in app.button if w.label=='🚀 Run Migration').click().run(timeout=30)
    assert not app.exception
    unbound=app.session_state['migration_result']
    assert len(unbound['source_files'])==1 and len(unbound['missing_dependencies'])==1
    inventory=json.loads((Path(app.session_state['migration_output'])/'configuration/uploaded_input_inventory.json').read_text())
    assert {Path(f['path']).name for f in inventory}=={'m_scd1.XML','w_scd1.XML'}
    saved=Path(app.session_state['dependency_upload_directory'])/'m_scd1.XML'
    selection=next(w for w in app.multiselect if w.key=='powercenter_dependencies')
    selection.set_value([str(saved)]).run()
    assert not app.exception
    next(w for w in app.button if w.label=='🚀 Run Migration').click().run(timeout=30)
    assert not app.exception
    bound=app.session_state['migration_result']
    assert len(bound['source_files'])==2 and bound['missing_dependencies']==[]
    assert bound['preservation']['node_count']==bound['preservation']['assigned_node_count']==320
    assert bound['migration_accepted'] is False and bound['generated_artifact_count']==0
    assert bound['behavior_analysis']['requirement_count']==33
    assert any(e.label=='PowerCenter behavior requirements' for e in app.expander)
    assert Path(bound['artifacts']['source_behavior_analysis']).is_file()
    assert saved.read_bytes()==mapping.content


@pytest.mark.parametrize('upload_mode',['archive','xml'])
def test_public_oracle_odi_upload_offers_versioned_normalization_and_legacy_compatibility(monkeypatch,upload_mode):
    from zipfile import ZipFile
    from ssis_migration import migrate
    root=Path(__file__).resolve().parents[1]
    archive=root/'examples/public/oracle_odi_etl_offload/upstream/CloudAtCustomerODIETLOffload/ODISmartExport_ETLOffload_BigDataCloud@Customer.zip'
    if upload_mode=='archive':
        upload=UploadedFile(archive.name,archive.read_bytes());key='bundle_upload'
    else:
        with ZipFile(archive) as source:
            name,=source.namelist();upload=UploadedFile(name,source.read(name))
        key='package_upload'
    monkeypatch.setattr(st,'file_uploader',lambda *a,**k:upload if k.get('key')==key else None)
    def forbidden(*args,**kwargs):raise AssertionError('Over-limit source must not reach migration/IR generation.')
    monkeypatch.setattr(migrate,'run_migration',forbidden)
    app=AppTest.from_file(str(root/'app.py'),default_timeout=20).run()
    if upload_mode=='archive':
        widget(app,'radio','Package source').set_value('Upload a source bundle (.zip)').run()
    assert not app.exception
    assert not widget(app,'button','🚀 Run Migration').disabled
    assert any('ODI 12.2.1 mapping normalizer' in item.value for item in app.info)
    assert any('ISO-8859-1' in item.value for item in app.markdown)
    assert any(item.label=='ODI export compatibility' for item in app.expander)
    assert any(item.label=='Download ODI compatibility report' for item in app.get('download_button'))
    assert app.session_state['migration_result'] is None and app.session_state['migration_output'] is None


def test_public_oracle_odi_workbench_run_downloads_normalized_contracts_and_generation_findings(monkeypatch,tmp_path):
    import json
    root=Path(__file__).resolve().parents[1]
    archive=root/'examples/public/oracle_odi_etl_offload/upstream/CloudAtCustomerODIETLOffload/ODISmartExport_ETLOffload_BigDataCloud@Customer.zip'
    upload=UploadedFile(archive.name,archive.read_bytes())
    monkeypatch.setattr(st,'file_uploader',lambda *a,**k:upload if k.get('key')=='bundle_upload' else None)
    app=AppTest.from_file(str(root/'app.py'),default_timeout=30).run()
    widget(app,'radio','Package source').set_value('Upload a source bundle (.zip)').run()
    widget(app,'text_input','Artifact destination path').set_value(str(tmp_path/'artifacts')).run()
    assert not app.exception and not widget(app,'button','🚀 Run Migration').disabled
    widget(app,'button','🚀 Run Migration').click().run(timeout=180)
    assert not app.exception and app.session_state['last_attempt_error'] is None
    report=app.session_state['migration_result']
    assert report['export_profile']=='odi_12c_mapping_declarations_v1'
    assert report['behavior_analysis']['requirement_count']==247
    assert report['target_expression_review']['record_count']==89
    assert report['target_expression_review']['candidate_count']==41
    assert report['target_generation_assessment']['blocked_mapping_count']==7
    assert report['generated_artifact_count']==0 and report['migration_accepted'] is False
    assert report['execution_status']=='NOT_RUN'
    assert any(e.label=='ODI mapping requirements' for e in app.expander)
    assert any(e.label=='ODI Databricks generation blockers' for e in app.expander)
    assert any(e.label=='ODI expression syntax candidates' for e in app.expander)
    assert any(b.label=='Download ODI generation assessment' for b in app.get('download_button'))
    graph=json.loads(Path(report['artifacts']['normalized_ir']).read_text())
    assert len(graph['nodes'])==2500 and len(graph['edges'])==4760
    assert not list(Path(app.session_state['migration_output']).rglob('*.ipynb'))


def test_workbench_compares_supplied_observations_without_databricks(monkeypatch):
    root=Path(__file__).resolve().parents[1]
    uploads={key:UploadedFile(key+'.json',(root/'examples/validation'/(key+'.json')).read_bytes())
             for key in ('source_observation','target_observation')}
    monkeypatch.setattr(st,'file_uploader',lambda *a,**k:uploads.get(k.get('key')))
    app=AppTest.from_file(str(root/'app.py')).run()
    assert not app.exception
    assert any('SYNTHETIC_FIXTURES_ONLY' in item.value for item in app.markdown)


def test_upload_roles_and_platform_are_explicit_and_every_dependency_is_preserved(monkeypatch):
    from ssis_migration import migrate

    uploads = [
        UploadedFile("mapping.json", b'{"mappings": [{"connection_reference": "source", "metadata_file": "metadata.json", "role": "source"}]}'),
        UploadedFile("metadata.json", b'{"tables": [{"schema": "dbo", "name": "Records", "columns": []}]}'),
        UploadedFile("first.sql", b"CREATE PROCEDURE dbo.First AS SELECT 1;"),
        UploadedFile("second.sql", b"CREATE PROCEDURE dbo.Second AS SELECT 2;"),
        UploadedFile("notes.json", b'{"note": "not a connection mapping"}'),
    ]
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: uploads if kwargs.get("key") == "deps_upload" else None)
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {
            "package_count": 0, "analyzed_package_count": 0,
            "migrated_package_count": 0, "failed_package_count": 0,
            "lifecycle_status": "BLOCKED", "packages": [],
            "source_intake": {
                "detected_platform": "ssis", "confirmed_platform": kwargs["source_platform"],
                "status": "REVIEW_REQUIRED", "diagnostics": [{
                    "code": "TEST_UNRESOLVED", "severity": "WARNING", "message": "Resolve supplied metadata roles.",
                }],
            },
        }

    monkeypatch.setattr(migrate, "run_migration", fake_run)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    assert not app.exception
    next(widget for widget in app.selectbox if widget.label == "Confirm source platform").set_value("SSIS").run()
    next(widget for widget in app.selectbox if widget.label == "Confirm role for metadata.json").set_value("Database metadata").run()
    app.radio[0].set_value("Use WWI sample package").run()
    next(button for button in app.button if button.label == "🚀 Run Migration").click().run(timeout=30)
    assert not app.exception
    assert captured["source_platform"] == "ssis"
    assert len(captured["dependency_files"]) == len(uploads)
    paths = {Path(path).name: Path(path) for path in captured["dependency_files"]}
    assert set(paths) == {upload.name for upload in uploads}
    assert all(paths[upload.name].read_bytes() == upload.content for upload in uploads)
    assert captured["connection_mapping"] == str(paths["mapping.json"])
    assert captured["procedures_path"] is None  # No arbitrary first procedure file.
    assert captured["file_roles"][str(paths["metadata.json"])] == "database_metadata"
    assert str(paths["notes.json"]) not in captured["file_roles"]
    assert Path(captured["base_path"]) == paths["mapping.json"].parent
    assert any("Multiple files have role `procedures`" in warning.value for warning in app.warning)
    assert any("TEST_UNRESOLVED" in element.value for element in app.markdown)


def test_duplicate_uploaded_names_are_retained_without_selecting_a_mapping(monkeypatch):
    from ssis_migration import migrate

    uploads = [
        UploadedFile("mapping.json", b'{"mappings": [{"metadata_file": "first.json"}]}'),
        UploadedFile("mapping.json", b'{"mappings": [{"metadata_file": "second.json"}]}'),
    ]
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: uploads if kwargs.get("key") == "deps_upload" else None)
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"packages": [], "lifecycle_status": "BLOCKED"}

    monkeypatch.setattr(migrate, "run_migration", fake_run)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    assert not app.exception
    app.radio[0].set_value("Use WWI sample package").run()
    next(button for button in app.button if button.label == "🚀 Run Migration").click().run(timeout=30)
    assert not app.exception
    assert len(set(captured["dependency_files"])) == 2
    assert {Path(path).read_bytes() for path in captured["dependency_files"]} == {upload.content for upload in uploads}
    assert captured["connection_mapping"] is None
    assert any("All are retained separately" in warning.value for warning in app.warning)


def widget(app,kind,label):
    return next(item for item in getattr(app,kind) if item.label==label)


def test_tm1_workbench_generates_from_ui_and_applies_developer_settings(tmp_path):
    import json
    from ssis_migration.generator.tm1.contract import validate_contract
    root=Path(__file__).resolve().parents[1]
    app=AppTest.from_file(str(root/'app.py'),default_timeout=20).run()
    widget(app,'radio','Package source').set_value('Use TM1 sample bundle').run()
    widget(app,'text_input','Target catalog').set_value('review_catalog')
    widget(app,'selectbox','Target layer').set_value('gold')
    widget(app,'text_input','Gold schema').set_value('published')
    widget(app,'text_input','Destination workspace folder').set_value('/Workspace/Shared/review')
    widget(app,'text_input','Existing cluster ID (optional)').set_value('0123-review-cluster')
    widget(app,'text_input','Artifact destination path').set_value(str(tmp_path/'artifacts')).run()
    assert not app.exception
    widget(app,'button','🚀 Run Migration').click().run(timeout=45)
    assert not app.exception
    report=app.session_state['migration_result']
    assert report['status']=='GENERATED' and not report['migration_accepted']
    config=report['workspace_config']
    assert config['catalog']=='review_catalog' and config['schemas']['gold']=='published'
    assert config['output_tables']['load']=='review_catalog.published.load'
    assert Path(app.session_state['migration_output']).is_relative_to(tmp_path)
    contract=json.loads(Path(report['artifacts']['generation_contract.json']).read_text());validate_contract(contract)
    assert [case['status'] for case in report['fixture_results']]==['PASS']*4
    assert any('NOT_RUN' in text.value for text in app.markdown)


def test_ui_table_mode_requires_complete_bindings_and_invalid_catalog_blocks_run():
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=20).run()
    widget(app,'radio','Package source').set_value('Use TM1 sample bundle').run()
    widget(app,'selectbox','Notebook input mode').set_value('tables').run()
    assert widget(app,'button','🚀 Run Migration').disabled
    assert any('Assign a cell snapshot table' in error.value for error in app.error)
    widget(app,'selectbox','Notebook input mode').set_value('fixtures')
    widget(app,'text_input','Target catalog').set_value('invalid catalog').run()
    assert widget(app,'button','🚀 Run Migration').disabled


def test_zip_upload_provides_native_bundle_without_cli(monkeypatch,tmp_path):
    from ssis_migration.ui import artifact_archive
    root=Path(__file__).resolve().parents[1]
    upload=UploadedFile('my_exports.zip',artifact_archive(root/'examples/tm1/databricks_demo'))
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:upload if kw.get('key')=='bundle_upload' else None)
    app=AppTest.from_file(str(root/'app.py'),default_timeout=20).run()
    widget(app,'radio','Package source').set_value('Upload a source bundle (.zip)').run()
    assert not app.exception and not widget(app,'button','🚀 Run Migration').disabled
    widget(app,'text_input','Artifact destination path').set_value(str(tmp_path/'out')).run()
    widget(app,'button','🚀 Run Migration').click().run(timeout=45)
    assert not app.exception and app.session_state['migration_result']['status']=='GENERATED'
    assert Path(app.session_state['migration_result']['artifacts']['workspace_config']).is_file()


def test_malformed_zip_is_a_visible_input_error_and_not_an_app_crash(monkeypatch):
    upload=UploadedFile('broken.zip',b'not a ZIP')
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:upload if kw.get('key')=='bundle_upload' else None)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=20).run()
    widget(app,'radio','Package source').set_value('Upload a source bundle (.zip)').run()
    assert not app.exception and app.error and widget(app,'button','🚀 Run Migration').disabled


def test_source_path_mode_selects_manifest_and_mapping_without_upload():
    root=Path(__file__).resolve().parents[1]
    app=AppTest.from_file(str(root/'app.py'),default_timeout=20).run()
    widget(app,'radio','Package source').set_value('Use source path').run()
    widget(app,'text_input','Source export path').set_value(str(root/'examples/tm1/databricks_demo')).run()
    assert not app.exception
    assert widget(app,'selectbox','Source package / manifest').value=='manifest.json'
    assert not widget(app,'button','🚀 Run Migration').disabled


def test_blocked_native_generation_is_shown_as_blocked_without_success(monkeypatch,tmp_path):
    import hashlib,json
    from ssis_migration.ui import artifact_archive
    root=Path(__file__).resolve().parents[1]
    import shutil
    bundle=tmp_path/'input';shutil.copytree(root/'examples/tm1/databricks_demo',bundle)
    (bundle/'epilog.ti').write_text('ProcessQuit;')
    process=json.loads((bundle/'process.json').read_text())
    process['sections']['epilog']['sha256']=hashlib.sha256((bundle/'epilog.ti').read_bytes()).hexdigest()
    (bundle/'process.json').write_text(json.dumps(process))
    manifest=json.loads((bundle/'manifest.json').read_text());manifest['units'][0]['sha256']=hashlib.sha256((bundle/'process.json').read_bytes()).hexdigest()
    (bundle/'manifest.json').write_text(json.dumps(manifest))
    mapping=json.loads((bundle/'target_mapping.json').read_text());mapping['source_bundle_sha256']=hashlib.sha256((bundle/'manifest.json').read_bytes()).hexdigest()
    (bundle/'target_mapping.json').write_text(json.dumps(mapping))
    upload=UploadedFile('blocked.zip',artifact_archive(bundle))
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:upload if kw.get('key')=='bundle_upload' else None)
    app=AppTest.from_file(str(root/'app.py'),default_timeout=20).run()
    widget(app,'radio','Package source').set_value('Upload a source bundle (.zip)').run()
    widget(app,'text_input','Artifact destination path').set_value(str(tmp_path/'out')).run()
    widget(app,'button','🚀 Run Migration').click().run(timeout=45)
    assert not app.exception
    report=app.session_state['migration_result']
    assert report['status']=='BLOCKED' and report['generated_artifact_count']==0
    assert any('blocking findings' in w.value for w in app.warning)
    assert not app.success


def test_workspace_storage_path_is_blocked_before_run_and_can_reset_to_tmp():
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=20).run()
    widget(app,'radio','Package source').set_value('Use WWI sample package').run()
    widget(app,'text_input','Destination workspace folder').set_value('/Workspace/Users/developer/etl')
    widget(app,'text_input','Artifact destination path').set_value('/Workspace/Users/developer/etl').run()
    assert not app.exception and widget(app,'button','🚀 Run Migration').disabled
    assert any('not local app storage' in error.value for error in app.error)
    widget(app,'button','Use app temporary artifact storage').click().run()
    assert widget(app,'text_input','Artifact destination path').value.startswith('/tmp/')
    assert widget(app,'text_input','Destination workspace folder').value=='/Workspace/Users/developer/etl'
    assert not widget(app,'button','🚀 Run Migration').disabled


def test_renamed_uploaded_metadata_is_explicitly_bound_and_consumed(monkeypatch,tmp_path):
    import json,hashlib
    root=Path(__file__).resolve().parents[1]
    uploads=[UploadedFile('connection_mapping.json',(root/'examples/wwi/connection_mapping.json').read_bytes()),
             UploadedFile('database_metadata_wwi (1).json',(root/'examples/wwi/database_metadata_wwi.json').read_bytes()),
             UploadedFile('database_metadata_wwi_dw (1).json',(root/'examples/wwi/database_metadata_wwi_dw.json').read_bytes())]
    package=UploadedFile('DailyETLMain.dtsx',(root/'examples/wwi/DailyETLMain.dtsx').read_bytes())
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:uploads if kw.get('key')=='deps_upload' else package if kw.get('key')=='package_upload' else None)
    app=AppTest.from_file(str(root/'app.py'),default_timeout=20).run()
    assert not app.exception
    assert widget(app,'button','🚀 Run Migration').disabled
    for index,name in [(1,'database_metadata_wwi (1).json'),(2,'database_metadata_wwi_dw (1).json')]:
        selection=widget(app,'selectbox',f'Metadata file for connection {index}')
        # AppTest formats selectbox options; use its stored original values.
        candidates=[value for value in selection.options if name in value]
        assert len(candidates)==1
        # Match the original full path from the current upload directory.
        selected=Path(app.session_state['dependency_upload_directory'])/name
        selection.set_value(str(selected)).run()
    widget(app,'text_input','Artifact destination path').set_value(str(tmp_path/'artifacts')).run()
    assert not widget(app,'button','🚀 Run Migration').disabled
    widget(app,'button','🚀 Run Migration').click().run(timeout=45)
    assert not app.exception
    report=app.session_state['migration_result']
    assert report['analyzed_package_count']==1
    intake=json.loads(Path(report['intake_manifest']).read_text())
    assert not any(d['code'] in {'MISSING_FILE','MISSING_DEPENDENCY','CONFLICTING_CONNECTION_MAPPING'} for d in intake['diagnostics'])
    assert not any(d['code']=='UNCONSUMED_DEPENDENCY' and '(1)' in d['message'] for d in intake['diagnostics'])
    config_dir=Path(app.session_state['migration_output'])/'configuration'
    assert (config_dir/'original_connection_mapping.json').read_bytes()==uploads[0].content
    audit=json.loads((config_dir/'connection_metadata_bindings.json').read_text())
    assert len(audit['bindings'])==2
    assert audit['original_sha256']==hashlib.sha256(uploads[0].content).hexdigest()
    assert audit['effective_sha256']==hashlib.sha256((config_dir/'effective_connection_mapping.json').read_bytes()).hexdigest()
