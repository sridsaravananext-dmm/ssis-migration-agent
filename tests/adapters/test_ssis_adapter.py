from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from ssis_migration.adapters import (
    SourceAdapter,
    SourceEvidenceError,
    UnsupportedSourceError,
    get_adapter,
)
from ssis_migration.adapters.ssis import SSISAdapter
from ssis_migration.analyzer import analyze_package
from ssis_migration.ir.builder import IRBuilder
from ssis_migration.parser.package_parser import SSISPackageParser
from ssis_migration.schemas import validate_document, validate_evidence_references


ROOT = Path(__file__).resolve().parents[2]
WWI = ROOT / "examples/wwi/DailyETLMain.dtsx"


def make_package(path: Path, version: str | None = "8", *, task_type: str | None = None) -> Path:
    property_xml = (
        f'<DTS:Property DTS:Name="PackageFormatVersion">{version}</DTS:Property>'
        if version is not None else ""
    )
    executable_xml = (
        f'<DTS:Executable DTS:refId="Package\\Task" DTS:ObjectName="Task" '
        f'DTS:ExecutableType="{task_type}" DTS:CreationName="{task_type}" />'
        if task_type is not None else ""
    )
    path.write_text(
        '<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" '
        'DTS:refId="Package" DTS:ObjectName="Example" '
        'DTS:ExecutableType="Microsoft.Package" DTS:CreationName="Microsoft.Package">'
        f'{property_xml}{executable_xml}</DTS:Executable>', encoding="utf-8",
    )
    return path


def source_record(path):
    from ssis_migration.intake import build_intake_manifest

    return next(
        item for item in build_intake_manifest(path)["files"]
        if Path(item["path"]).resolve() == path.resolve()
    )


def test_registry_exposes_shared_protocol_and_only_implemented_adapter():
    assert isinstance(get_adapter("ssis"), SourceAdapter)
    assert isinstance(get_adapter("SSIS"), SSISAdapter)


@pytest.mark.parametrize("platform", ["informatica", "powercenter", "bods", "odi"])
def test_native_structural_adapters_do_not_claim_legacy_ssis_ir(platform):
    assert get_adapter(platform).platform != 'ssis'


@pytest.mark.parametrize("platform", ["datastage", "IBM InfoSphere DataStage", "ab_initio", "Ab Initio"])
def test_excluded_platforms_never_fall_through_to_ssis(platform):
    with pytest.raises(UnsupportedSourceError, match="explicitly excluded"):
        get_adapter(platform)


def test_unknown_platform_cannot_select_default_parser():
    with pytest.raises(UnsupportedSourceError, match="unknown"):
        get_adapter("new_vendor")


def test_adapter_preserves_existing_wwi_parsed_ir_and_analysis():
    parsed = SSISPackageParser(WWI).parse()
    legacy_ir = IRBuilder().build(parsed)
    legacy_analysis = analyze_package(legacy_ir.to_dict())

    result = get_adapter("ssis").analyze(WWI)
    assert result.parsed == parsed
    assert result.package_ir.to_dict() == legacy_ir.to_dict()
    assert result.analysis == legacy_analysis
    compatibility = result.to_legacy_result()
    assert compatibility["parsed"] is result.parsed
    assert compatibility["package_ir"] is result.package_ir
    assert compatibility["analysis"] is result.analysis
    assert compatibility["source_envelope"] == result.source_envelope
    assert result.source_envelope["legacy_ir"] == legacy_ir.to_dict()

    envelope = result.source_envelope
    evidence = envelope["evidence_files"][0]
    assert envelope["envelope_version"] == "1.0"
    assert envelope["source"] == {
        "platform": "ssis", "format": "ssis_dtsx_xml", "export_version": "8",
        "file_id": evidence["file_id"],
    }
    assert evidence["sha256"] == hashlib.sha256(WWI.read_bytes()).hexdigest()
    assert evidence["size_bytes"] == WWI.stat().st_size
    assert Path(evidence["path"]) == WWI
    validate_document(envelope, "source_envelope")
    validate_evidence_references(envelope)


def test_unsupported_script_behavior_remains_explicit(tmp_path):
    path = make_package(tmp_path / "script.dtsx", task_type="Microsoft.ScriptTask")
    result = SSISAdapter().analyze(path)
    assert result.source_envelope["status"] == "REVIEW_REQUIRED"
    diagnostics = result.source_envelope["diagnostics"]
    unsupported = next(d for d in diagnostics if d["code"] == "UNSUPPORTED_SSIS_EXECUTABLE")
    assert "Microsoft.ScriptTask" in unsupported["message"]
    assert "Task" in unsupported["evidence"][0]["locator"]
    assert result.package_ir.executables[0].executable_type == "Microsoft.ScriptTask"


def test_missing_export_version_preserves_unknown_compatibility(tmp_path):
    path = make_package(tmp_path / "unknown_version.dtsx", version=None)
    result = SSISAdapter().analyze(path)
    assert result.source_envelope["source"]["export_version"] is None
    assert result.source_envelope["status"] == "REVIEW_REQUIRED"
    assert any("VERSION" in d["code"] for d in result.source_envelope["diagnostics"])
    assert result.package_ir.package_name == "Example"


def test_unsupported_version_is_rejected_before_legacy_parser(tmp_path, monkeypatch):
    from ssis_migration.adapters.ssis import adapter as implementation

    path = make_package(tmp_path / "future.dtsx", version="999")

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported exports must not enter the legacy parser")

    monkeypatch.setattr(implementation, "SSISPackageParser", forbidden)
    with pytest.raises(UnsupportedSourceError, match="PackageFormatVersion.*unsupported") as exc:
        SSISAdapter().analyze(path)
    assert exc.value.diagnostics


@pytest.mark.parametrize("content", ["<ROOT>not SSIS</ROOT>", "SELECT {[Measures].[Sales]} ON COLUMNS FROM [Cube]"])
def test_non_ssis_content_is_not_parsed_based_on_dtsx_suffix(tmp_path, monkeypatch, content):
    from ssis_migration.adapters.ssis import adapter as implementation

    path = tmp_path / "disguised.dtsx"
    path.write_text(content)
    monkeypatch.setattr(implementation, "SSISPackageParser", lambda *args: pytest.fail("wrong parser selected"))
    with pytest.raises(UnsupportedSourceError, match="not a supported SSIS"):
        SSISAdapter().analyze(path)


def test_changed_source_bytes_invalidate_previously_inventoried_evidence(tmp_path):
    path = make_package(tmp_path / "changed.dtsx")
    record = source_record(path)
    path.write_text(path.read_text() + "\n<!-- changed after inventory -->")
    with pytest.raises(SourceEvidenceError, match="no longer match"):
        SSISAdapter().analyze(path, file_record=record)


def test_record_cannot_substitute_another_path_or_claimed_format(tmp_path):
    path = make_package(tmp_path / "first.dtsx")
    other = make_package(tmp_path / "second.dtsx")
    record = source_record(path)
    record["path"] = str(other)
    with pytest.raises(SourceEvidenceError, match="different source path"):
        SSISAdapter().analyze(path, file_record=record)
    record = source_record(path)
    record["export_version"] = "999"
    with pytest.raises(SourceEvidenceError, match="conflicts with current source"):
        SSISAdapter().analyze(path, file_record=record)


def test_supplied_record_is_preserved_without_mutating_caller(tmp_path):
    path = make_package(tmp_path / "provided.dtsx")
    record = source_record(path)
    record["file_id"] = "selected-inventory-identity"
    for evidence in record["evidence"]:
        evidence["file_id"] = record["file_id"]
    before = copy.deepcopy(record)
    result = SSISAdapter().analyze(path, file_record=record)
    assert record == before
    assert result.source_envelope["source"]["file_id"] == "selected-inventory-identity"
    assert result.source_envelope["evidence_files"][0] == record
    validate_evidence_references(result.source_envelope)


def test_adapter_builds_ir_before_analyzer_and_keeps_supplied_procedure_file(tmp_path, monkeypatch):
    from ssis_migration.adapters.ssis import adapter as implementation

    path = make_package(tmp_path / "order.dtsx")
    procedures = tmp_path / "procedures.sql"
    procedures.write_text("CREATE PROCEDURE dbo.ReadSource AS SELECT 1;")
    calls = []
    parsed = {"executables": [], "raw_parser_result": True}

    class Parser:
        def __init__(self, source):
            assert source == path
            import xml.etree.ElementTree as ET
            self.root = ET.parse(source).getroot()

        def parse(self):
            calls.append("parse")
            return parsed

    class IR:
        def to_dict(self):
            return {"normalized_ir": True}

    class Builder:
        def build(self, actual):
            assert actual is parsed
            calls.append("ir")
            return IR()

    def analyze(actual, procedure_bodies=None):
        assert actual == {"normalized_ir": True}
        assert "dbo.ReadSource" in procedure_bodies
        calls.append("analyze")
        return {"analyzed_ir": True}

    monkeypatch.setattr(implementation, "SSISPackageParser", Parser)
    monkeypatch.setattr(implementation, "IRBuilder", Builder)
    monkeypatch.setattr(implementation, "analyze_package", analyze)
    result = SSISAdapter().analyze(path, procedures_path=procedures)
    assert calls == ["parse", "ir", "analyze"]
    assert result.analysis == {"analyzed_ir": True}
    assert len(result.source_envelope["evidence_files"]) == 2
    assert any(Path(item["path"]) == procedures for item in result.source_envelope["evidence_files"])


def test_missing_procedure_file_is_not_treated_as_empty_source_metadata(tmp_path):
    path = make_package(tmp_path / "missing_procedure.dtsx")
    with pytest.raises(FileNotFoundError, match="Supplied procedure definitions"):
        SSISAdapter().analyze(path, procedures_path=tmp_path / "absent.sql")


def test_symlink_source_cannot_bypass_inventory_regular_file_requirement(tmp_path, monkeypatch):
    from ssis_migration.adapters.ssis import adapter as implementation

    source = make_package(tmp_path / "real.dtsx")
    link = tmp_path / "linked.dtsx"
    link.symlink_to(source)
    monkeypatch.setattr(implementation, "SSISPackageParser", lambda *args: pytest.fail("symlink target was parsed"))
    with pytest.raises(SourceEvidenceError, match="regular-file evidence"):
        SSISAdapter().analyze(link)


def test_symlink_procedure_dependency_is_rejected_before_parser_reads_it(tmp_path, monkeypatch):
    from ssis_migration.adapters.ssis import adapter as implementation

    source = make_package(tmp_path / "source.dtsx")
    procedure = tmp_path / "actual.sql"
    procedure.write_text("CREATE PROC dbo.Source AS SELECT 1")
    link = tmp_path / "linked.sql"
    link.symlink_to(procedure)
    monkeypatch.setattr(implementation, "SSISPackageParser", lambda *args: pytest.fail("dependency lacked evidence"))
    with pytest.raises(SourceEvidenceError, match="regular-file evidence"):
        SSISAdapter().analyze(source, procedures_path=link)
