"""Packaged, offline JSON Schemas for source intake and compatibility metadata.

These schemas validate the source/evidence envelope. They do not validate every
legacy IR behavior or establish source-to-target semantic equivalence.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource


class SchemaValidationError(ValueError):
    """A source document violates its schema or evidence-reference contract."""


_SCHEMA_FILES = {
    "intake_manifest": "intake_manifest.v1.json",
    "source_envelope": "source_envelope.v1.json",
    "migration_contract": "migration_contract.v1.json",
    "acceptance_policy": "acceptance_policy.v1.json",
    "acceptance_evidence": "acceptance_evidence.v1.json",
    "normalized_ir": "normalized_ir.v1.json",
    "sql_analysis": "sql_analysis.v1.json",
    "mdx_analysis": "mdx_analysis.v1.json",
    "csharp_assessment": "csharp_assessment.v1.json",
    "csharp_syntax": "csharp_syntax.v1.json",
    "csharp_translation_spec": "csharp_translation_spec.v1.json",
    "csharp_compilation_request": "csharp_compilation_request.v1.json",
    "csharp_compilation_report": "csharp_compilation_report.v1.json",
    "csharp_execution_trace": "csharp_execution_trace.v1.json",
    "csharp_equivalence_report": "csharp_equivalence_report.v1.json",
}
_COMMON_FILE = "common.v1.json"


def _pointer(parts: Any) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _deny_retrieval(uri: str) -> Resource:
    # Every schema resource is bundled. Validation must never request a
    # user-controlled or remote URL while inspecting exported source files.
    raise NoSuchResource(ref=uri)


@lru_cache(maxsize=None)
def _validator(kind: str) -> Draft202012Validator:
    filename = _SCHEMA_FILES.get(kind)
    if filename is None:
        raise SchemaValidationError(f"Unknown source schema kind: {kind!r}.")
    schema_root = resources.files(__name__)
    documents = [
        json.loads(schema_root.joinpath(name).read_text(encoding="utf-8"))
        for name in (_COMMON_FILE, *_SCHEMA_FILES.values())
    ]
    for document in documents:
        Draft202012Validator.check_schema(document)
    registry = Registry(retrieve=_deny_retrieval).with_resources(
        (document["$id"], Resource.from_contents(document)) for document in documents
    )
    schema = next(document for document in documents if document["$id"].endswith(f":{kind}:1.0"))
    return Draft202012Validator(schema, registry=registry)


def validate_document(document: dict[str, Any], kind: str) -> None:
    """Validate a versioned document using bundled schemas without network I/O.

    Raises :class:`SchemaValidationError` with the first failing JSON pointer.
    The evidence IDs themselves are checked by ``validate_evidence_references``.
    """
    errors = sorted(
        _validator(kind).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        raise SchemaValidationError(
            f"{kind} validation failed at {_pointer(error.absolute_path)}: {error.message}"
        )


def validate_evidence_references(document: dict[str, Any]) -> None:
    """Reject duplicate or dangling file/evidence references in a document.

    This validates references in envelope metadata, not arbitrary vendor fields
    inside ``legacy_ir``. It does not read files or claim their hashes are fresh.
    """
    if not isinstance(document, dict):
        raise SchemaValidationError("Evidence document must be an object at /.")
    file_key = "files" if "files" in document else "evidence_files"
    files = document.get(file_key)
    if not isinstance(files, list):
        raise SchemaValidationError(f"Expected a file inventory at /{file_key}.")
    file_ids: set[str] = set()
    for index, record in enumerate(files):
        file_id = record.get("file_id") if isinstance(record, dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise SchemaValidationError(f"Missing file ID at /{file_key}/{index}/file_id.")
        if file_id in file_ids:
            raise SchemaValidationError(f"Duplicate file ID {file_id!r} at /{file_key}/{index}/file_id.")
        file_ids.add(file_id)

    def require_file_id(value: Any, path: str, *, nullable: bool = False) -> None:
        if value is None and nullable:
            return
        if not isinstance(value, str) or value not in file_ids:
            raise SchemaValidationError(f"Unknown evidence file ID {value!r} at {path}.")

    def check_evidence(items: Any, path: str) -> None:
        if not isinstance(items, list):
            raise SchemaValidationError(f"Expected evidence references at {path}.")
        for index, ref in enumerate(items):
            value = ref.get("file_id") if isinstance(ref, dict) else None
            require_file_id(value, f"{path}/{index}/file_id")

    for index, record in enumerate(files):
        check_evidence(record.get("evidence", []), f"/{file_key}/{index}/evidence")

    for index, diagnostic in enumerate(document.get("diagnostics", [])):
        if not isinstance(diagnostic, dict):
            raise SchemaValidationError(f"Expected diagnostic object at /diagnostics/{index}.")
        for offset, file_id in enumerate(diagnostic.get("file_ids", [])):
            require_file_id(file_id, f"/diagnostics/{index}/file_ids/{offset}")
        check_evidence(diagnostic.get("evidence", []), f"/diagnostics/{index}/evidence")

    for index, dependency in enumerate(document.get("dependencies", [])):
        if not isinstance(dependency, dict):
            raise SchemaValidationError(f"Expected dependency object at /dependencies/{index}.")
        if "contract_version" not in document:
            require_file_id(dependency.get("source_file_id"), f"/dependencies/{index}/source_file_id")
            require_file_id(
                dependency.get("target_file_id"), f"/dependencies/{index}/target_file_id", nullable=True,
            )
        check_evidence(dependency.get("evidence", []), f"/dependencies/{index}/evidence")

    if "contract_version" in document:
        for index, diagnostic in enumerate(document.get("source_behavior", {}).get("diagnostics", [])):
            for offset, file_id in enumerate(diagnostic.get("file_ids", [])):
                require_file_id(file_id, f"/source_behavior/diagnostics/{index}/file_ids/{offset}")
            check_evidence(diagnostic.get("evidence", []), f"/source_behavior/diagnostics/{index}/evidence")
        for key in ("inputs", "outputs"):
            for index, item in enumerate(document.get(key, [])):
                check_evidence(item.get("evidence", []), f"/{key}/{index}/evidence")
        for index, item in enumerate(document.get("required_behavior", {}).get("obligations", [])):
            check_evidence(item.get("evidence", []), f"/required_behavior/obligations/{index}/evidence")
        check_evidence(document.get("provenance", {}).get("evidence", []), "/provenance/evidence")

    if "envelope_version" in document:
        source = document.get("source")
        require_file_id(
            source.get("file_id") if isinstance(source, dict) else None, "/source/file_id",
        )


__all__ = ["SchemaValidationError", "validate_document", "validate_evidence_references"]
