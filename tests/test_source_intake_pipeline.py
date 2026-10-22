from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from ssis_migration.migrate import run_migration
from ssis_migration.schemas import validate_document, validate_evidence_references


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/wwi/DailyETLMain.dtsx"
MAPPING = ROOT / "examples/wwi/connection_mapping.json"


def migrate(output, **kwargs):
    return run_migration(str(SOURCE), str(output), base_path=str(ROOT), **kwargs)


def read_manifest(report):
    return json.loads(Path(report["intake_manifest"]).read_text())


def test_default_ssis_workflow_adds_schema_valid_file_and_ir_evidence(tmp_path):
    report = migrate(tmp_path, source_platform="ssis")
    intake = read_manifest(report)
    assert report["migrated_package_count"] == 1
    assert intake["confirmed_platform"] == intake["detected_platform"] == "ssis"
    assert len(intake["files"]) == 4  # DTSX, mapping and two actual metadata files.
    assert len(intake["dependencies"]) == 2
    assert all(edge["status"] == "RESOLVED" for edge in intake["dependencies"])
    validate_document(intake, "intake_manifest")
    validate_evidence_references(intake)
    for record in intake["files"]:
        assert record["sha256"] == hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest()
    package = report["packages"][0]
    envelope = json.loads(Path(package["artifacts"]["source_envelope"]).read_text())
    ir = json.loads(Path(package["artifacts"]["ir"]).read_text())
    assert envelope["legacy_ir"] == ir
    assert envelope["source"]["export_version"] == "8"
    validate_document(envelope, "source_envelope")
    validate_evidence_references(envelope)
    assert any(d["code"] == "UNSUPPORTED_SSIS_EXECUTABLE" for d in envelope["diagnostics"])
    assert package["stages"]["source_intake"] == "REVIEW_REQUIRED"
    findings = json.loads(Path(package["artifacts"]["validation"]).read_text())
    assert any(f["validator"] == "source_intake" and f["status"] == "REVIEW_REQUIRED" for f in findings)
    assert package["acceptance"]["accepted"] is False


@pytest.mark.parametrize("content,platform", [
    ("<POWERMART/>", "informatica"),
    ("<DSExport/>", "datastage"),
    ("<SunopsisExport/>", "odi"),
    ("CellPutN(1, 'Cube', 'Member');", "tm1"),
])
def test_unsupported_or_excluded_exports_never_reach_ssis_parser(tmp_path, monkeypatch, content, platform):
    from ssis_migration import migrate as module

    def forbidden(*args, **kwargs):
        raise AssertionError("Unsupported export reached the SSIS adapter")

    monkeypatch.setattr(module, "analyze_one_package", forbidden)
    source = tmp_path / "export.txt"
    source.write_text(content)
    report = run_migration(str(source), str(tmp_path / "output"))
    assert report["source_intake"]["detected_platform"] == platform
    assert report["failed_package_count"] == 1
    assert report["lifecycle_status"] == "BLOCKED"
    assert "Source intake is blocked" in report["packages"][0]["error"]
    assert "notebook" not in report["packages"][0]["artifacts"]


def test_mdx_is_inventoried_without_sql_interpretation(tmp_path):
    source = tmp_path / "query.mdx"
    source.write_text("SELECT [Measures].Members ON COLUMNS FROM [Sales]")
    report = run_migration(str(source), str(tmp_path / "output"), source_platform="ssis")
    intake = read_manifest(report)
    assert intake["files"][0]["format"] == "mdx"
    assert any(d["code"] == "UNSUPPORTED_LANGUAGE" for d in intake["diagnostics"])
    assert report["migrated_package_count"] == 0
    assert report["lifecycle_status"] == "BLOCKED"


def test_unknown_and_unconsumed_inputs_remain_evidenced_and_block_execution(tmp_path):
    notes = tmp_path / "notes.bin"
    notes.write_bytes(b"\xffuser-supplied")
    params = tmp_path / "parameters.json"
    params.write_text('{"parameters": {"cutoff": "supplied value"}}')
    report = migrate(tmp_path / "output", dependency_files=[str(notes), str(params)])
    intake = read_manifest(report)
    assert {str(notes), str(params)} <= {record["path"] for record in intake["files"]}
    assert {"UNKNOWN_FILE_ROLE", "UNCONSUMED_DEPENDENCY"} <= {d["code"] for d in intake["diagnostics"]}
    package = report["packages"][0]
    notebook = json.loads(Path(package["artifacts"]["notebook"]).read_text())
    assert all(cell["metadata"]["blocked"] for cell in notebook["cells"] if cell.get("metadata", {}).get("migration_role") == "step")
    assert package["acceptance"]["accepted"] is False


def test_missing_mapped_metadata_produces_a_missing_file_and_dependency(tmp_path):
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"mappings": [{"connection_reference": "Source", "metadata_file": "missing.json"}]}')
    report = run_migration(str(SOURCE), str(tmp_path / "output"), connection_mapping=str(mapping), base_path=str(tmp_path))
    intake = read_manifest(report)
    assert intake["dependencies"][0]["status"] == "MISSING"
    assert any(record["status"] == "MISSING" for record in intake["files"])
    assert report["failed_package_count"] == 1
    assert "notebook" not in report["packages"][0]["artifacts"]


def test_multiple_procedure_exports_are_not_silently_reduced_to_one(tmp_path):
    files = []
    for name in ("One", "Two"):
        path = tmp_path / f"{name}.sql"
        path.write_text(f"CREATE PROCEDURE dbo.{name} AS SELECT 1;")
        files.append(str(path))
    report = migrate(tmp_path / "output", dependency_files=files)
    intake = read_manifest(report)
    assert any(d["code"] == "AMBIGUOUS_DEPENDENCY_ROLE" for d in intake["diagnostics"])
    assert set(files) <= {record["path"] for record in intake["files"]}
    assert report["failed_package_count"] == 1


def test_conflicting_explicit_role_decisions_do_not_choose_a_winner(tmp_path):
    mapping = tmp_path / "both.json"
    mapping.write_text('{"mappings": [], "configuration": {}}')
    report = migrate(
        tmp_path / "output", connection_mapping=str(mapping),
        file_roles={str(mapping): "configuration"},
    )
    assert any(d["code"] == "CONFLICTING_ROLE_CONFIRMATIONS" for d in read_manifest(report)["diagnostics"])
    assert report["failed_package_count"] == 1


def test_metadata_change_during_generation_invalidates_current_output(tmp_path, monkeypatch):
    from ssis_migration import migrate as module

    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"tables": []}')
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"mappings": [{"connection_reference": "Source", "metadata_file": "metadata.json"}]}')
    original = module.generate_migration_artifacts

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        metadata.write_text('{"tables": [], "database": {"changed": true}}')
        return result

    monkeypatch.setattr(module, "generate_migration_artifacts", changed)
    report = run_migration(str(SOURCE), str(tmp_path / "output"), connection_mapping=str(mapping), base_path=str(tmp_path))
    assert report["failed_package_count"] == 1
    assert "Source evidence changed after intake" in report["packages"][0]["error"]
    assert "manifest" not in report["packages"][0]["artifacts"]


def test_cli_role_and_platform_confirmations_are_saved(tmp_path):
    mixed = tmp_path / "mixed.sql"
    mixed.write_text("CREATE TABLE dbo.T(id int);\nGO\nCREATE PROCEDURE dbo.P AS SELECT 1;")
    result = subprocess.run([
        str(ROOT / ".venv/bin/python"), "-m", "ssis_migration.migrate",
        "--source", str(SOURCE), "--output", str(tmp_path / "output"),
        "--base-path", str(ROOT), "--source-platform", "ssis",
        "--dependency", str(mixed), "--file-role", f"{mixed}=procedures",
    ], cwd=ROOT, text=True, capture_output=True, check=True)
    report = json.loads(result.stdout)
    intake = read_manifest(report)
    record = next(file for file in intake["files"] if file["path"] == str(mixed))
    assert record["confirmed_role"] == "procedures"
    assert intake["confirmed_platform"] == "ssis"
    assert report["migrated_package_count"] == 1
    package = report["packages"][0]
    envelope = json.loads(Path(package["artifacts"]["source_envelope"]).read_text())
    assert not any(d["code"] == "AMBIGUOUS_FILE_ROLE" for d in envelope["diagnostics"])


def test_source_directory_output_is_explicitly_excluded_on_reruns(tmp_path):
    source_dir = tmp_path / "inputs"
    source_dir.mkdir()
    (source_dir / SOURCE.name).write_bytes(SOURCE.read_bytes())
    output = source_dir / "generated"
    report = run_migration(str(source_dir), str(output), connection_mapping=str(MAPPING), base_path=str(ROOT))
    repeated = run_migration(str(source_dir), str(output), connection_mapping=str(MAPPING), base_path=str(ROOT))
    assert report["package_count"] == repeated["package_count"] == 1
    assert any(d["code"] == "EXCLUDED_INPUT_PATH" for d in read_manifest(repeated)["diagnostics"])
    assert not any(str(output) in record["path"] for record in read_manifest(repeated)["files"])


def test_symlink_source_is_not_resolved_before_inventory(tmp_path):
    source = tmp_path / "linked.dtsx"
    source.symlink_to(SOURCE)
    report = run_migration(str(source), str(tmp_path / "output"))
    record = next(file for file in read_manifest(report)["files"] if file["path"] == str(source))
    assert record["status"] == "UNREADABLE"
    assert report["failed_package_count"] == 1
