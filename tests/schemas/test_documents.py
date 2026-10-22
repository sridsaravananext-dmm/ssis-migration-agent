from __future__ import annotations

import copy
import json
from importlib import resources

import pytest

from ssis_migration.schemas import (
    SchemaValidationError,
    validate_document,
    validate_evidence_references,
)


def evidence(file_id: str, locator: str = "/") -> dict:
    return {"file_id": file_id, "locator": locator, "rule": "supplied_file"}


def file_record(file_id: str = "file-package", **changes) -> dict:
    record = {
        "file_id": file_id,
        "path": "/inputs/Package.dtsx",
        "relative_path": "Package.dtsx",
        "sha256": "a" * 64,
        "size_bytes": 42,
        "role": "package",
        "detected_roles": ["package"],
        "confirmed_role": None,
        "platform": "ssis",
        "format": "ssis_dtsx_xml",
        "export_version": "8",
        "status": "AVAILABLE",
        "evidence": [evidence(file_id)],
    }
    record.update(changes)
    return record


@pytest.fixture
def manifest() -> dict:
    return {
        "manifest_version": "1.0",
        "source_root": "/inputs",
        "requested_platform": None,
        "detected_platform": "ssis",
        "confirmed_platform": None,
        "files": [
            file_record(),
            file_record(
                "file-metadata", path="/inputs/metadata.json",
                relative_path="metadata.json", role="database_metadata",
                detected_roles=["database_metadata"], platform="unknown",
                format="json", export_version=None,
            ),
        ],
        "dependencies": [{
            "source_file_id": "file-package",
            "target_file_id": "file-metadata",
            "reference": "metadata.json",
            "role": "database_metadata",
            "status": "RESOLVED",
            "evidence": [evidence("file-package", "/metadata_file")],
        }],
        "diagnostics": [{
            "code": "SOURCE_DETECTED", "severity": "INFO",
            "message": "SSIS package signature found.",
            "file_ids": ["file-package"],
            "evidence": [evidence("file-package", "/DTS:Executable")],
        }],
        "status": "READY",
        "excluded_paths": [],
    }


@pytest.fixture
def envelope() -> dict:
    return {
        "envelope_version": "1.0",
        "source": {
            "platform": "ssis", "format": "ssis_dtsx_xml",
            "export_version": "8", "file_id": "file-package",
        },
        "evidence_files": [file_record()],
        "diagnostics": [],
        "status": "READY",
        "legacy_ir": {
            "package_name": "Package", "executables": [],
            "data_flows": [], "metadata": {"existing_extension": True},
        },
    }


def test_valid_manifest_and_envelope_are_accepted(manifest, envelope):
    for kind, document in (("intake_manifest", manifest), ("source_envelope", envelope)):
        assert validate_document(document, kind) is None
        assert validate_evidence_references(document) is None


@pytest.mark.parametrize("kind,version_key", [
    ("intake_manifest", "manifest_version"),
    ("source_envelope", "envelope_version"),
])
def test_unknown_version_is_rejected_with_field_path(request, kind, version_key):
    document = request.getfixturevalue("manifest" if kind == "intake_manifest" else "envelope")
    document[version_key] = "2.0"
    with pytest.raises(SchemaValidationError, match=version_key):
        validate_document(document, kind)


@pytest.mark.parametrize("field", ["files", "dependencies", "diagnostics", "status"])
def test_missing_manifest_field_is_rejected(manifest, field):
    del manifest[field]
    with pytest.raises(SchemaValidationError, match=field):
        validate_document(manifest, "intake_manifest")


@pytest.mark.parametrize("sha256", ["a" * 63, "a" * 65, "not-a-checksum", "g" * 64])
def test_sha256_must_be_a_complete_hex_digest(manifest, sha256):
    manifest["files"][0]["sha256"] = sha256
    with pytest.raises(SchemaValidationError, match="sha256"):
        validate_document(manifest, "intake_manifest")


def test_missing_file_can_retain_null_hash_and_size(manifest):
    manifest["files"][1].update({"status": "MISSING", "sha256": None, "size_bytes": None})
    manifest["dependencies"][0]["status"] = "MISSING"
    manifest["status"] = "BLOCKED"
    validate_document(manifest, "intake_manifest")
    validate_evidence_references(manifest)


@pytest.mark.parametrize("size_bytes", [-1, 0.5, True])
def test_size_is_nonnegative_integer_or_null(manifest, size_bytes):
    manifest["files"][0]["size_bytes"] = size_bytes
    with pytest.raises(SchemaValidationError, match="size_bytes"):
        validate_document(manifest, "intake_manifest")


@pytest.mark.parametrize("field,value", [
    ("role", "imaginary_metadata"),
    ("status", "VERIFIED"),
    ("confirmed_role", "imaginary_metadata"),
])
def test_unrecognized_file_states_and_roles_are_rejected(manifest, field, value):
    manifest["files"][0][field] = value
    with pytest.raises(SchemaValidationError, match=field):
        validate_document(manifest, "intake_manifest")


def test_unexpected_manifest_and_file_fields_are_rejected(manifest):
    changed = copy.deepcopy(manifest)
    changed["silently_ignored"] = True
    with pytest.raises(SchemaValidationError):
        validate_document(changed, "intake_manifest")
    manifest["files"][0]["silently_ignored"] = True
    with pytest.raises(SchemaValidationError):
        validate_document(manifest, "intake_manifest")


def test_legacy_ir_fields_remain_compatible_but_envelope_metadata_is_strict(envelope):
    envelope["legacy_ir"]["previously_supported_vendor_field"] = {"opaque": [1, 2]}
    validate_document(envelope, "source_envelope")
    envelope["source"]["unvalidated_inference"] = "history_table"
    with pytest.raises(SchemaValidationError):
        validate_document(envelope, "source_envelope")


@pytest.mark.parametrize("field,value", [
    ("platform", "tm1"), ("format", "unknown"),
])
def test_envelope_does_not_claim_unimplemented_adapter_support(envelope, field, value):
    envelope["source"][field] = value
    with pytest.raises(SchemaValidationError, match=field):
        validate_document(envelope, "source_envelope")


def test_duplicate_evidence_file_ids_are_rejected(manifest):
    manifest["files"].append(copy.deepcopy(manifest["files"][0]))
    with pytest.raises(SchemaValidationError, match="file-package"):
        validate_evidence_references(manifest)


def test_duplicate_envelope_file_ids_are_rejected(envelope):
    envelope["evidence_files"].append(copy.deepcopy(envelope["evidence_files"][0]))
    with pytest.raises(SchemaValidationError, match="file-package"):
        validate_evidence_references(envelope)


@pytest.mark.parametrize("reference_kind", [
    "file_evidence", "diagnostic_file_ids", "diagnostic_evidence",
    "dependency_source", "dependency_target", "dependency_evidence",
])
def test_every_manifest_reference_resolves_to_an_inventoried_file(manifest, reference_kind):
    unknown = "file-never-supplied"
    if reference_kind == "file_evidence":
        manifest["files"][0]["evidence"][0]["file_id"] = unknown
    elif reference_kind == "diagnostic_file_ids":
        manifest["diagnostics"][0]["file_ids"].append(unknown)
    elif reference_kind == "diagnostic_evidence":
        manifest["diagnostics"][0]["evidence"][0]["file_id"] = unknown
    elif reference_kind == "dependency_source":
        manifest["dependencies"][0]["source_file_id"] = unknown
    elif reference_kind == "dependency_target":
        manifest["dependencies"][0]["target_file_id"] = unknown
    else:
        manifest["dependencies"][0]["evidence"][0]["file_id"] = unknown
    with pytest.raises(SchemaValidationError, match=unknown):
        validate_evidence_references(manifest)


def test_missing_dependency_may_have_no_target_file_id(manifest):
    manifest["dependencies"][0].update({"target_file_id": None, "status": "MISSING"})
    manifest["status"] = "BLOCKED"
    validate_document(manifest, "intake_manifest")
    validate_evidence_references(manifest)


def test_envelope_source_must_refer_to_supplied_evidence(envelope):
    envelope["source"]["file_id"] = "invented-file"
    with pytest.raises(SchemaValidationError, match="invented-file"):
        validate_evidence_references(envelope)


def test_schema_resources_are_packaged():
    package = resources.files("ssis_migration.schemas")
    for name in ("intake_manifest.v1.json", "source_envelope.v1.json"):
        schema = json.loads(package.joinpath(name).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_schema_validation_resolves_only_packaged_resources(monkeypatch, manifest, envelope):
    import socket
    import urllib.request

    def no_network(*args, **kwargs):
        raise AssertionError("Schema validation must not fetch network resources")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    validate_document(manifest, "intake_manifest")
    validate_document(envelope, "source_envelope")


def test_unknown_schema_kind_is_rejected(manifest):
    with pytest.raises(ValueError, match="not_a_schema"):
        validate_document(manifest, "not_a_schema")
