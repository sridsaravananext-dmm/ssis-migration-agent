"""Evidence-aware wrapper for the existing SSIS parser → IR → analysis flow."""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
from typing import Any

from ssis_migration.analyzer import analyze_package
from ssis_migration.ir.builder import IRBuilder
from ssis_migration.parser.package_parser import SSISPackageParser

from ..base import SourceAdapterResult, SourceEvidenceError, UnsupportedSourceError


class SSISAdapter:
    """Analyze structurally supported DTSX without claiming full equivalence."""

    platform = "ssis"
    supported_export_versions = frozenset({"8"})

    def analyze(
        self,
        source_path: str | Path,
        *,
        procedures_path: str | Path | None = None,
        file_record: dict[str, Any] | None = None,
    ) -> SourceAdapterResult:
        from ssis_migration.intake import build_intake_manifest
        from ssis_migration.schemas import validate_document, validate_evidence_references

        source = Path(os.path.abspath(Path(source_path).expanduser()))
        if not source.is_file():
            raise FileNotFoundError(f"Source package does not exist: {source}")
        if procedures_path is not None and not Path(procedures_path).expanduser().is_file():
            raise FileNotFoundError(f"Supplied procedure definitions do not exist: {procedures_path}")

        # Detection is checked even when a caller supplies an inventory record:
        # a filename or self-declared platform is not proof of a DTSX signature.
        intake = build_intake_manifest(
            source,
            dependencies=[procedures_path] if procedures_path is not None else (),
            role_overrides={
                str(Path(procedures_path).expanduser().absolute()): "procedures"
            } if procedures_path is not None else None,
        )
        fresh = next(
            record for record in intake["files"]
            if Path(record["path"]) == source
        )
        record = copy.deepcopy(file_record if file_record is not None else fresh)
        self._verify_record(source, record, fresh)
        if fresh["platform"] != self.platform or fresh["format"] != "ssis_dtsx_xml":
            raise UnsupportedSourceError(
                "Source content is not a supported SSIS DTSX export; no SSIS parsing was attempted.",
                diagnostics=intake["diagnostics"],
            )
        version = fresh["export_version"]
        if version is not None and version not in self.supported_export_versions:
            raise UnsupportedSourceError(
                f"SSIS PackageFormatVersion {version!r} is unsupported; only version '8' has a validated adapter fixture.",
                diagnostics=intake["diagnostics"],
            )
        for evidence_file in intake["files"]:
            if Path(evidence_file["path"]) != source:
                self._verify_bytes(Path(evidence_file["path"]), evidence_file)

        parser = SSISPackageParser(source)
        parsed = parser.parse()
        package_ir = IRBuilder().build(parsed)

        procedure_bodies: dict[str, str] | None = None
        if procedures_path is not None:
            from ssis_migration.procedure_file_loader import load_procedures

            procedure_bodies = load_procedures(procedures_path)

        # Analyze the built IR, never the parser dictionary. These objects and
        # their existing semantics remain unchanged for legacy callers.
        analysis = analyze_package(package_ir.to_dict(), procedure_bodies=procedure_bodies)
        self._verify_bytes(source, record)

        evidence_files = [record]
        evidence_files.extend(
            copy.deepcopy(item) for item in intake["files"]
            if Path(item["path"]) != source
        )
        for evidence_file in evidence_files:
            self._verify_bytes(Path(evidence_file["path"]), evidence_file)
        # A supplied record can use a different inventory root and therefore
        # file identity. Remap fresh detection diagnostics to that stable ID.
        source_id = record["file_id"]
        diagnostics = copy.deepcopy(intake["diagnostics"])
        for diagnostic in diagnostics:
            diagnostic["file_ids"] = [source_id if item == fresh["file_id"] else item for item in diagnostic["file_ids"]]
            for evidence in diagnostic["evidence"]:
                if evidence["file_id"] == fresh["file_id"]:
                    evidence["file_id"] = source_id
        diagnostics.extend(self._unsupported_executables(parsed, source_id))
        if version is None and not any(
            diagnostic["code"] in {"MISSING_EXPORT_VERSION", "UNVERIFIED_SSIS_EXPORT_VERSION"}
            for diagnostic in diagnostics
        ):
            diagnostics.append(self._diagnostic(
                "UNVERIFIED_SSIS_EXPORT_VERSION",
                "PackageFormatVersion is missing; structural parsing is available but export compatibility is unverified.",
                source_id, "xml:/Executable/Property[@Name='PackageFormatVersion']",
            ))
        envelope = {
            "envelope_version": "1.0",
            "source": {
                "platform": self.platform,
                "format": "ssis_dtsx_xml",
                "export_version": version,
                "file_id": source_id,
            },
            "evidence_files": evidence_files,
            "diagnostics": diagnostics,
            "status": (
                "BLOCKED" if any(d["severity"] == "ERROR" for d in diagnostics)
                else "REVIEW_REQUIRED" if any(d["severity"] == "WARNING" for d in diagnostics)
                else "READY"
            ),
            "legacy_ir": package_ir.to_dict(),
        }
        validate_document(envelope, "source_envelope")
        validate_evidence_references(envelope)
        from .normalizer import normalize_ssis_ir
        from .hierarchy import executable_hierarchy
        from .scripts import extract_script_assessments

        normalized_ir = normalize_ssis_ir(package_ir.to_dict(), envelope,
                                          hierarchy=executable_hierarchy(parser.root),
                                          script_assessments=extract_script_assessments(
                                              parser.root, package_ir.to_dict(), source_id))
        for evidence_file in evidence_files:
            self._verify_bytes(Path(evidence_file["path"]), evidence_file)
        return SourceAdapterResult(
            parsed=parsed, package_ir=package_ir, analysis=analysis,
            source_envelope=envelope,
            normalized_ir=normalized_ir,
        )

    @staticmethod
    def _verify_bytes(source: Path, record: dict[str, Any]) -> None:
        if record.get("status") != "AVAILABLE" or source.is_symlink():
            raise SourceEvidenceError(
                f"Input file has no available regular-file evidence: {source}"
            )
        data = source.read_bytes()
        if (
            record.get("sha256") != hashlib.sha256(data).hexdigest()
            or record.get("size_bytes") != len(data)
        ):
            raise SourceEvidenceError(
                f"Source package bytes no longer match the supplied file evidence: {source}"
            )

    @classmethod
    def _verify_record(
        cls, source: Path, record: dict[str, Any], fresh: dict[str, Any],
    ) -> None:
        recorded_path = Path(os.path.abspath(Path(record.get("path", "")).expanduser()))
        if recorded_path != source:
            raise SourceEvidenceError("The supplied file record identifies a different source path.")
        cls._verify_bytes(source, record)
        if fresh.get("platform") == cls.platform and fresh.get("format") == "ssis_dtsx_xml" and record.get("role") != "package":
            raise SourceEvidenceError("The supplied file record does not designate this source as a package.")
        if any(record.get(key) != fresh.get(key) for key in ("platform", "format", "export_version")):
            raise SourceEvidenceError("The supplied platform, export format or version conflicts with current source content.")

    @staticmethod
    def _diagnostic(
        code: str, message: str, file_id: str, locator: str,
    ) -> dict[str, Any]:
        return {
            "code": code, "severity": "WARNING", "message": message,
            "file_ids": [file_id],
            "evidence": [{"file_id": file_id, "locator": locator, "rule": code}],
        }

    @classmethod
    def _unsupported_executables(
        cls, parsed: dict[str, Any], file_id: str,
    ) -> list[dict[str, Any]]:
        diagnostics = []
        supported_types = {
            "microsoft.package", "stock:sequence", "microsoft.executesqltask",
            "microsoft.pipeline", "ssis.pipeline.3",
        }
        for executable in parsed.get("executables", []):
            executable_type = executable.get("executable_type") or executable.get("creation_name") or ""
            if executable_type.lower() in supported_types:
                continue
            attributes = executable.get("attributes", {})
            reference = attributes.get("refId") or executable.get("name") or "unknown"
            diagnostics.append(cls._diagnostic(
                "UNSUPPORTED_SSIS_EXECUTABLE",
                f"Executable {reference!r} has type {executable_type or 'unknown'!r}; its complete behavior is not represented by the existing SSIS parser.",
                file_id, f"xml:Executable[@refId={reference!r}]",
            ))
        return diagnostics
