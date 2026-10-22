from pathlib import Path

import streamlit as st
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
