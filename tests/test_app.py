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
