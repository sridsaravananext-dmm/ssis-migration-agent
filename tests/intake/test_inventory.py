from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ssis_migration.intake import build_intake_manifest, describe_file
from ssis_migration.schemas import validate_document, validate_evidence_references


def package(path: Path, version: str | None = "8") -> Path:
    prop = f'<DTS:Property DTS:Name="PackageFormatVersion">{version}</DTS:Property>' if version is not None else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" '
        'DTS:ExecutableType="Microsoft.Package" DTS:ObjectName="Test">'
        + prop + '</DTS:Executable>',
    )
    return path


def codes(manifest: dict) -> set[str]:
    return {diagnostic["code"] for diagnostic in manifest["diagnostics"]}


def test_known_ssis_file_has_exact_byte_evidence_and_stable_identity(tmp_path):
    path = package(tmp_path / "package.dtsx")
    manifest = build_intake_manifest(path)
    repeated = build_intake_manifest(path)
    assert manifest == repeated
    assert manifest["status"] == "READY"
    assert manifest["detected_platform"] == "ssis"
    record = manifest["files"][0]
    assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record["size_bytes"] == path.stat().st_size
    assert record["platform"] == "ssis"
    assert record["format"] == "ssis_dtsx_xml"
    assert record["export_version"] == "8"
    assert all(ref["file_id"] == record["file_id"] for ref in record["evidence"])
    validate_document(manifest, "intake_manifest")
    validate_evidence_references(manifest)


def test_changed_bytes_change_hash_and_file_identity(tmp_path):
    path = package(tmp_path / "package.dtsx")
    before = describe_file(path)
    path.write_bytes(path.read_bytes() + b"\n")
    after = describe_file(path)
    assert before["sha256"] != after["sha256"]
    assert before["file_id"] != after["file_id"]


def test_file_source_does_not_scan_unrelated_siblings(tmp_path):
    source = package(tmp_path / "package.dtsx")
    (tmp_path / "unrelated.txt").write_text("not supplied")
    manifest = build_intake_manifest(source)
    assert [record["path"] for record in manifest["files"]] == [str(source)]


def test_directory_retains_unknown_and_duplicate_content_files(tmp_path):
    package(tmp_path / "main.dtsx")
    for name in ("one.bin", "two.bin"):
        (tmp_path / name).write_bytes(b"\xff\x00binary\x01")
    manifest = build_intake_manifest(tmp_path)
    assert len(manifest["files"]) == 3
    assert manifest["status"] == "REVIEW_REQUIRED"
    unknown = [record for record in manifest["files"] if record["role"] == "unknown"]
    assert len(unknown) == 2
    assert unknown[0]["sha256"] == unknown[1]["sha256"]
    assert unknown[0]["file_id"] != unknown[1]["file_id"]
    assert "UNKNOWN_FILE_ROLE" in codes(manifest)


def test_generic_json_is_not_invented_connection_mapping(tmp_path):
    source = package(tmp_path / "main.dtsx")
    other = tmp_path / "other.json"
    other.write_text('{"customer": "provided information"}')
    manifest = build_intake_manifest(source, dependencies=[other])
    record = next(record for record in manifest["files"] if record["path"] == str(other))
    assert record["role"] == "unknown"
    assert record["detected_roles"] == []
    assert manifest["status"] == "REVIEW_REQUIRED"


def test_mixed_sql_keeps_all_detected_roles_until_user_confirmation(tmp_path):
    source = package(tmp_path / "main.dtsx")
    sql = tmp_path / "mixed.sql"
    sql.write_text('CREATE TABLE dbo.T (id int);\nGO\nCREATE PROCEDURE dbo.Load AS SELECT 1;')
    manifest = build_intake_manifest(source, dependencies=[sql])
    record = next(record for record in manifest["files"] if record["path"] == str(sql))
    assert set(record["detected_roles"]) == {"ddl", "procedures"}
    assert record["role"] == "ambiguous"
    assert "AMBIGUOUS_FILE_ROLE" in codes(manifest)
    confirmed = build_intake_manifest(source, dependencies=[sql], role_overrides={"mixed.sql": "procedures"})
    record = next(record for record in confirmed["files"] if record["path"] == str(sql))
    assert record["confirmed_role"] == record["role"] == "procedures"
    assert set(record["detected_roles"]) == {"ddl", "procedures"}
    assert confirmed["status"] == "READY"
    assert any(ref["rule"] == "explicit_user_role" for ref in record["evidence"])


def test_comments_and_literals_do_not_claim_sql_dependency_roles(tmp_path):
    source = package(tmp_path / "main.dtsx")
    sql = tmp_path / "example.sql"
    sql.write_text("-- CREATE TABLE bogus\nSELECT 'CREATE PROCEDURE Fake' AS description;")
    record = describe_file(sql)
    assert record["role"] == "script"
    assert "ddl" not in record["detected_roles"]
    assert "procedures" not in record["detected_roles"]
    assert build_intake_manifest(source, dependencies=[sql])["status"] == "READY"


def test_explicit_role_and_platform_conflicts_do_not_erase_content(tmp_path):
    source = package(tmp_path / "main.dtsx")
    manifest = build_intake_manifest(source, platform="tm1", role_overrides={"main.dtsx": "ddl"})
    assert manifest["status"] == "BLOCKED"
    assert manifest["requested_platform"] == manifest["confirmed_platform"] == "tm1"
    assert manifest["detected_platform"] == "ssis"
    assert {"ROLE_CONTENT_CONFLICT", "PLATFORM_CONTENT_CONFLICT", "UNSUPPORTED_PLATFORM"} <= codes(manifest)
    assert manifest["files"][0]["detected_roles"] == ["package"]


@pytest.mark.parametrize("version,expected,status", [
    (None, "MISSING_EXPORT_VERSION", "REVIEW_REQUIRED"),
    ("999", "UNSUPPORTED_EXPORT_VERSION", "BLOCKED"),
])
def test_export_versions_are_explicit(tmp_path, version, expected, status):
    manifest = build_intake_manifest(package(tmp_path / "main.dtsx", version))
    assert manifest["status"] == status
    assert expected in codes(manifest)
    assert manifest["files"][0]["export_version"] == version


def test_malformed_or_misnamed_dtsx_is_not_registered_as_supported_ssis(tmp_path):
    source = tmp_path / "main.dtsx"
    source.write_text("<broken>")
    manifest = build_intake_manifest(source)
    assert manifest["status"] == "BLOCKED"
    assert {"INVALID_XML", "UNCONFIRMED_PACKAGE_FORMAT"} <= codes(manifest)
    assert manifest["files"][0]["platform"] == "unknown"
    assert manifest["files"][0]["format"] == "unknown"


@pytest.mark.parametrize("contents,platform", [
    ("CellPutN(1, 'Cube', 'Element');", "tm1"),
])
def test_other_export_signatures_are_detected_but_not_claimed_as_supported(tmp_path, contents, platform):
    source = tmp_path / "export.txt"
    source.write_text(contents)
    manifest = build_intake_manifest(source)
    assert manifest["detected_platform"] == platform
    assert manifest["status"] == "BLOCKED"
    assert "UNSUPPORTED_PLATFORM" in codes(manifest)


@pytest.mark.parametrize('contents,platform', [('<POWERMART/>','informatica'),('<SunopsisExport/>','odi'),('<DataServices/>','bods')])
def test_native_xml_intake_registration_does_not_establish_semantic_support(tmp_path,contents,platform):
    source = tmp_path/'export.xml';source.write_text(contents)
    manifest = build_intake_manifest(source,platform=platform)
    assert manifest['detected_platform'] == platform
    assert 'UNSUPPORTED_PLATFORM' not in codes(manifest)
    assert manifest['status'] != 'ACCEPTED'


@pytest.mark.parametrize("platform,contents", [
    ("datastage", "<DSExport/>"),
    ("ab_initio", "ABINITIO export graph"),
])
def test_excluded_platform_cannot_be_overridden_to_ssis(tmp_path, platform, contents):
    source = tmp_path / "export.txt"
    source.write_text(contents)
    manifest = build_intake_manifest(source, platform="ssis", role_overrides={"export.txt": "package"})
    assert manifest["detected_platform"] == platform
    assert manifest["status"] == "BLOCKED"
    assert "EXCLUDED_PLATFORM" in codes(manifest)
    assert "PLATFORM_CONTENT_CONFLICT" in codes(manifest)


@pytest.mark.parametrize("platform", ["datastage", "ab_initio"])
def test_excluded_explicit_platform_remains_blocked(tmp_path, platform):
    manifest = build_intake_manifest(package(tmp_path / "main.dtsx"), platform=platform)
    assert "EXCLUDED_PLATFORM" in codes(manifest)
    assert manifest["status"] == "BLOCKED"


def test_mapping_metadata_references_are_resolved_with_explicit_base_path(tmp_path):
    source = package(tmp_path / "exports/main.dtsx")
    metadata = tmp_path / "metadata/db.json"
    metadata.parent.mkdir()
    metadata.write_text('{"database": {"name": "SuppliedDb"}, "tables": []}')
    mapping = tmp_path / "exports/mapping.json"
    mapping.write_text(json.dumps({"mappings": [{"connection_reference": "Source", "metadata_file": "metadata/db.json", "role": "source"}]}))
    manifest = build_intake_manifest(source, dependencies=[mapping], base_path=tmp_path)
    assert manifest["status"] == "READY"
    assert len(manifest["files"]) == 3
    edge = manifest["dependencies"][0]
    assert edge["status"] == "RESOLVED"
    assert edge["evidence"][0]["locator"] == "json:/mappings/0/metadata_file"
    assert next(record for record in manifest["files"] if record["file_id"] == edge["target_file_id"])["path"] == str(metadata)


def test_missing_dependency_is_a_file_record_and_an_explicit_unresolved_edge(tmp_path):
    source = package(tmp_path / "main.dtsx")
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"mappings": [{"connection_reference": "Source", "metadata_file": "absent.json"}]}')
    manifest = build_intake_manifest(source, dependencies=[mapping], base_path=tmp_path)
    assert manifest["status"] == "BLOCKED"
    assert {"MISSING_FILE", "MISSING_DEPENDENCY"} <= codes(manifest)
    missing = next(record for record in manifest["files"] if record["status"] == "MISSING")
    assert missing["sha256"] is missing["size_bytes"] is None
    assert manifest["dependencies"][0]["target_file_id"] == missing["file_id"]
    assert manifest["dependencies"][0]["status"] == "MISSING"
    validate_evidence_references(manifest)


def test_conflicting_mapping_bindings_are_not_silently_replaced(tmp_path):
    source = package(tmp_path / "main.dtsx")
    mappings = []
    for index in (1, 2):
        metadata = tmp_path / f"db{index}.json"
        metadata.write_text('{"tables": []}')
        mapping = tmp_path / f"mapping{index}.json"
        mapping.write_text(json.dumps({"mappings": [{"connection_reference": "SameConnection", "metadata_file": metadata.name}]}))
        mappings.append(mapping)
    manifest = build_intake_manifest(source, dependencies=mappings, base_path=tmp_path)
    assert manifest["status"] == "BLOCKED"
    assert "CONFLICTING_CONNECTION_MAPPING" in codes(manifest)
    assert all(edge["status"] == "CONFLICTING" for edge in manifest["dependencies"])


def test_mapping_cannot_turn_unrelated_json_into_database_metadata(tmp_path):
    source = package(tmp_path / "main.dtsx")
    mapping = tmp_path / "mapping.json"
    metadata = tmp_path / "arbitrary.json"
    metadata.write_text('{"unrelated": "value"}')
    mapping.write_text(json.dumps({"mappings": [{"connection_reference": "Source", "metadata_file": metadata.name}]}))
    manifest = build_intake_manifest(source, dependencies=[mapping], base_path=tmp_path)
    assert manifest["status"] == "BLOCKED"
    assert manifest["dependencies"][0]["status"] == "AMBIGUOUS"
    forced = build_intake_manifest(source, dependencies=[mapping], base_path=tmp_path, role_overrides={metadata.name: "database_metadata"})
    assert forced["status"] == "BLOCKED"
    assert "UNSUPPORTED_METADATA_SHAPE" in codes(forced)


def test_output_exclusions_are_explicit_and_do_not_read_generated_files(tmp_path):
    package(tmp_path / "main.dtsx")
    output = tmp_path / "output"
    output.mkdir()
    (output / "generated.json").write_text("invalid JSON")
    manifest = build_intake_manifest(tmp_path, exclude_paths=[output])
    assert manifest["status"] == "READY"
    assert manifest["excluded_paths"] == [str(output)]
    assert "EXCLUDED_INPUT_PATH" in codes(manifest)
    assert len(manifest["files"]) == 1


def test_symlink_is_retained_without_following_external_content(tmp_path):
    source = package(tmp_path / "main.dtsx")
    link = tmp_path / "linked.json"
    link.symlink_to(source)
    manifest = build_intake_manifest(source, dependencies=[link])
    linked = next(record for record in manifest["files"] if record["path"] == str(link))
    assert linked["status"] == "UNREADABLE"
    assert linked["sha256"] is None
    assert "UNREADABLE_FILE" in codes(manifest)


def test_unreadable_directory_is_a_blocking_diagnostic(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    original = Path.iterdir

    def denied(path):
        if path == source:
            raise PermissionError("fixture denied")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    manifest = build_intake_manifest(source)
    assert manifest["status"] == "BLOCKED"
    assert "UNREADABLE_DIRECTORY" in codes(manifest)


def test_unknown_override_path_and_invalid_values_are_explicit(tmp_path):
    source = package(tmp_path / "main.dtsx")
    manifest = build_intake_manifest(source, role_overrides={"missing.sql": "procedures"})
    assert "UNKNOWN_ROLE_OVERRIDE_FILE" in codes(manifest)
    with pytest.raises(ValueError, match="Unknown source platform"):
        build_intake_manifest(source, platform="invented")
    with pytest.raises(ValueError, match="metadata role"):
        build_intake_manifest(source, role_overrides={"main.dtsx": "invented"})


def test_mixed_source_platforms_remain_visible_and_blocked(tmp_path):
    package(tmp_path / "main.dtsx")
    (tmp_path / "process.pro").write_text("CellPutN(1, 'Cube', 'Member');")
    manifest = build_intake_manifest(tmp_path, platform="ssis")
    assert manifest["detected_platform"] == "mixed"
    assert manifest["status"] == "BLOCKED"
    assert "MIXED_SOURCE_PLATFORMS" in codes(manifest)
    assert len(manifest["files"]) == 2
