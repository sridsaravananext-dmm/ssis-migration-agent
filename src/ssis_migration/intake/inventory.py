"""Inventory supplied exports without connecting to legacy systems.

Detection establishes file identity and format evidence, not semantic support.
Only the fixture-verified SSIS package format is currently registered. Files
that cannot be understood remain in the inventory with explicit diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any, Sequence


ROLES = frozenset({
    "package", "procedures", "ddl", "connection_mapping", "database_metadata",
    "parameters", "configuration", "script", "unknown", "ambiguous",
})
PLATFORMS = frozenset({"ssis", "tm1", "informatica", "bods", "odi", "datastage", "ab_initio"})
_EXCLUDED = frozenset({"datastage", "ab_initio"})
_DTS_NAMESPACE = "www.microsoft.com/SqlServer/Dts"


def _absolute(path: str | Path) -> Path:
    # Do not resolve a symlink and accidentally inventory its target instead
    # of the file the user actually supplied.
    return Path(os.path.abspath(Path(path).expanduser()))


def _ref(file_id: str, locator: str, rule: str) -> dict[str, str]:
    return {"file_id": file_id, "locator": locator, "rule": rule}


def _diagnostic(code: str, severity: str, message: str, record: dict | None = None,
                *, evidence: list[dict] | None = None, file_ids: list[str] | None = None) -> dict:
    return {
        "code": code, "severity": severity, "message": message,
        "file_ids": file_ids if file_ids is not None else [record["file_id"]] if record else [],
        "evidence": evidence if evidence is not None else list(record["evidence"]) if record else [],
    }


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def has_unsafe_xml_declarations(data: bytes) -> bool:
    # Declaration-like strings in comments/CDATA are ordinary source text,
    # including embedded SSIS C# scripts; they must not change detection.
    markup = re.sub(br'<!--.*?-->|<!\[CDATA\[.*?\]\]>',b'',data.replace(b'\x00',b''),flags=re.S)
    return re.search(br'<!ENTITY\b|<!DOCTYPE[^>]*\[',markup,re.I) is not None


def _xml_detection(data: bytes) -> tuple[dict, list[tuple[str, str, str]]]:
    # Inventory runs before adapter dispatch. Reject entity declarations here
    # as well, so detection cannot expand content the adapter would reject.
    if has_unsafe_xml_declarations(data):
        raise ET.ParseError('XML entities and internal DTD subsets are unsupported.')
    root = ET.fromstring(data)
    local = _local(root.tag)
    version = None
    if root.tag == "{" + _DTS_NAMESPACE + "}Executable":
        for element in root:
            if _local(element.tag) == "Property" and any(
                _local(key) == "Name" and value == "PackageFormatVersion"
                for key, value in element.attrib.items()
            ):
                version = (element.text or "").strip() or None
                break
        diagnostics = []
        if version is None:
            diagnostics.append(("MISSING_EXPORT_VERSION", "WARNING", "SSIS PackageFormatVersion was not supplied; version compatibility is unverified."))
        elif version != "8":
            diagnostics.append(("UNSUPPORTED_EXPORT_VERSION", "ERROR", f"SSIS PackageFormatVersion {version!r} is unsupported; only version 8 is currently fixture-verified."))
        return {"roles": ["package"], "platform": "ssis", "format": "ssis_dtsx_xml", "version": version, "locator": f"xml:/{local}", "rule": "ssis_namespace_and_executable"}, diagnostics
    if local.upper() == "POWERMART":
        return {"roles": ["package"], "platform": "informatica", "format": "powercenter_xml", "version": root.attrib.get("REPOSITORY_VERSION"), "locator": "xml:/POWERMART", "rule": "powercenter_root"}, []
    if local.lower() == "sunopsisexport":
        return {"roles": ["package"], "platform": "odi", "format": "odi_xml", "version": root.attrib.get("version"), "locator": f"xml:/{local}", "rule": "odi_sunopsis_export"}, []
    if local.lower() in {"dsexport", "dsjob"}:
        return {"roles": ["package"], "platform": "datastage", "format": "datastage_export", "version": None, "locator": f"xml:/{local}", "rule": "datastage_export_root"}, []
    if local.lower() in {"abinitio", "ab_initio"}:
        return {"roles": ["package"], "platform": "ab_initio", "format": "ab_initio_export", "version": None, "locator": f"xml:/{local}", "rule": "ab_initio_export_root"}, []
    if local.lower() in {"dataservices", "diexport", "dijob"}:
        return {"roles": ["package"], "platform": "bods", "format": "bods_xml", "version": root.attrib.get("version"), "locator": f"xml:/{local}", "rule": "sap_data_services_root"}, []
    if local == "DTSConfiguration":
        return {"roles": ["configuration"], "platform": "ssis", "format": "ssis_configuration_xml", "version": None, "locator": "xml:/DTSConfiguration", "rule": "ssis_configuration_root"}, []
    return {"roles": [], "platform": "unknown", "format": "xml", "version": None, "locator": f"xml:/{local}", "rule": "unrecognized_xml_root"}, []


def _json_detection(value: Any) -> tuple[dict, list[tuple[str, str, str]]]:
    roles = []
    diagnostics = []
    if isinstance(value, dict):
        if value.get('platform') == 'tm1' and value.get('export_format') == 'tm1_source_bundle_v1':
            return {'roles':['package'], 'platform':'tm1', 'format':'tm1_source_bundle_v1',
                    'version':value.get('source_export_version') if isinstance(value.get('source_export_version'), str) else None,
                    'locator':'json:/', 'rule':'explicit_tm1_bundle_header'}, []
        if "mappings" in value:
            roles.append("connection_mapping")
            mappings = value["mappings"]
            if not isinstance(mappings, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("connection_reference"), str)
                or not item["connection_reference"].strip()
                or not isinstance(item.get("metadata_file"), str)
                or not item["metadata_file"].strip()
                for item in mappings
            ):
                diagnostics.append(("INVALID_CONNECTION_MAPPING", "ERROR", "Connection mappings require nonempty connection_reference and metadata_file strings."))
        if any(key in value for key in ("tables", "procedures", "views", "functions", "schemas", "database")):
            roles.append("database_metadata")
            if any(not isinstance(value[key], list) for key in ("tables", "procedures", "views", "functions", "schemas") if key in value):
                diagnostics.append(("INVALID_METADATA_SHAPE", "ERROR", "Database metadata collection fields must be lists."))
        if "parameters" in value:
            roles.append("parameters")
        if "configuration" in value or "config" in value:
            roles.append("configuration")
    return {"roles": roles, "platform": "unknown", "format": "json", "version": None, "locator": "json:/", "rule": "json_structural_roles"}, diagnostics


def _detect(data: bytes, path: Path) -> tuple[dict, list[tuple[str, str, str]]]:
    diagnostics: list[tuple[str, str, str]] = []
    suffix = path.suffix.lower()
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<"):
        try:
            return _xml_detection(data)
        except (ET.ParseError, UnicodeError) as exc:
            diagnostics.append(("INVALID_XML", "ERROR", f"Supplied XML is not well formed: {exc}"))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"roles": [], "platform": "unknown", "format": "binary", "version": None, "locator": "file:/", "rule": "non_utf8_content"}, diagnostics
    if stripped.startswith((b"{", b"[")) or suffix == ".json":
        try:
            detected, json_diagnostics = _json_detection(json.loads(text))
            return detected, [*diagnostics, *json_diagnostics]
        except json.JSONDecodeError as exc:
            diagnostics.append(("INVALID_JSON", "ERROR", f"Supplied JSON is invalid: {exc}"))
    if re.search(r"(?im)^\s*(?:BEGIN\s+DSJOB|IBM\s+InfoSphere\s+DataStage)\b", text):
        return {"roles": ["package"], "platform": "datastage", "format": "datastage_export", "version": None, "locator": "text:/", "rule": "datastage_export_signature"}, diagnostics
    if re.search(r"(?im)^\s*(?:ABINITIO|AB_INITIO)\b", text):
        return {"roles": ["package"], "platform": "ab_initio", "format": "ab_initio_export", "version": None, "locator": "text:/", "rule": "ab_initio_export_signature"}, diagnostics
    # An explicit MDX extension is a language hint, not platform confirmation.
    # Process it before TI hints so quoted member names cannot select TI.
    if suffix == ".mdx":
        diagnostics.append(("UNSUPPORTED_LANGUAGE", "WARNING", "MDX has a separate bounded offline analyzer; dimensional semantics and the TM1 package adapter remain unverified. No SQL interpretation is applied."))
        return {"roles": ["script"], "platform": "unknown", "format": "mdx", "version": None, "locator": "text:/", "rule": "mdx_language_hint"}, diagnostics
    from ssis_migration.adapters.tm1.detection import code_for_signature_hints
    tm1_code = code_for_signature_hints(text)
    if re.search(r"(?im)\b(?:CellPutN|CellPutS|CubeGetCell|DatasourceType)\s*(?:\(|=)", tm1_code):
        return {"roles": ["package"], "platform": "tm1", "format": "tm1_ti", "version": None, "locator": "text:/", "rule": "tm1_turbointegrator_signature"}, diagnostics
    if re.search(r"(?im)^\s*SKIPCHECK\s*;", tm1_code) or re.search(r"(?im)^\s*FEEDERS\s*;", tm1_code):
        return {"roles": ["script"], "platform": "tm1", "format": "tm1_rules", "version": None, "locator": "text:/", "rule": "tm1_rules_signature"}, diagnostics
    if suffix == ".rux":
        diagnostics.append(("UNSUPPORTED_LANGUAGE", "WARNING", "Plain-text TM1 rules have a bounded offline analyzer; the source version, dimensional semantics and TM1 package adapter remain unverified. No SQL interpretation is applied."))
        return {"roles": ["script"], "platform": "unknown", "format": "tm1_rules", "version": None, "locator": "text:/", "rule": "tm1_rules_language_hint"}, diagnostics
    if suffix in {".ti", ".pro"}:
        diagnostics.append(("UNSUPPORTED_LANGUAGE", "WARNING", "TurboIntegrator requires explicit section declarations; serialized native .pro layouts are not decoded by a filename hint. No SQL interpretation is applied."))
        return {"roles": ["script"], "platform": "unknown", "format": "tm1_ti_section_hint" if suffix == ".ti" else "tm1_pro_unverified", "version": None, "locator": "text:/", "rule": "ti_language_hint"}, diagnostics
    if suffix == ".atl" and re.search(r"(?im)\bCREATE\s+(?:DATAFLOW|WORKFLOW)\b", text):
        return {"roles": ["package"], "platform": "bods", "format": "bods_atl", "version": None, "locator": "text:/", "rule": "sap_atl_workflow_signature"}, diagnostics
    if suffix == ".mdx" or re.search(r"(?is)\bSELECT\b.*\bON\s+(?:COLUMNS|ROWS|0|1)\b.*\bFROM\s+\[", text):
        diagnostics.append(("UNSUPPORTED_LANGUAGE", "WARNING", "MDX has a separate bounded offline analyzer; dimensional semantics and the TM1 package adapter remain unverified. No SQL interpretation is applied."))
        return {"roles": ["script"], "platform": "unknown", "format": "mdx", "version": None, "locator": "text:/", "rule": "mdx_language_hint"}, diagnostics
    # Tokenization removes comments/string literals before looking for SQL
    # declarations; a name or comment alone cannot confirm a dependency role.
    import sqlglot
    from sqlglot.tokens import TokenType
    try:
        tokens = deque(sqlglot.tokenize(text, read="tsql"))
        words = []
        while tokens:
            token = tokens.popleft()
            # SQLGlot treats GO as a generic command and captures the next
            # batch as a string. That capture is source SQL, not a literal;
            # tokenize it again so subsequent file roles remain visible.
            if token.token_type == TokenType.COMMAND and token.text.upper() == "GO":
                if tokens and tokens[0].token_type == TokenType.STRING:
                    batch = tokens.popleft()
                    tokens.extendleft(reversed(sqlglot.tokenize(batch.text, read="tsql")))
                continue
            if token.token_type not in {TokenType.STRING, TokenType.IDENTIFIER}:
                words.append(token.text.upper())
    except Exception:
        words = []
    roles = []
    for index, word in enumerate(words):
        if word != "CREATE":
            continue
        following = words[index + 1:index + 5]
        if following[:2] == ["OR", "ALTER"]:
            following = following[2:]
        if following and following[0] in {"PROC", "PROCEDURE"}:
            roles.append("procedures")
        elif following and following[0] == "TABLE":
            roles.append("ddl")
    roles = list(dict.fromkeys(roles))
    if roles or suffix in {".sql", ".cs", ".py", ".pro", ".rux", ".dtsconfig"}:
        return {"roles": roles or ["script"], "platform": "unknown", "format": "sql" if roles or suffix == ".sql" else "script", "version": None, "locator": "text:/", "rule": "sql_declaration_tokens" if roles else "script_extension_hint"}, diagnostics
    return {"roles": ["package"] if suffix == ".dtsx" else [], "platform": "unknown", "format": "unknown", "version": None, "locator": "file:/", "rule": "unconfirmed_extension"}, diagnostics


def _inspect(path: Path, root: Path) -> tuple[dict, list[dict]]:
    data = None
    status = "AVAILABLE"
    error = None
    if path.is_symlink():
        status, error = "UNREADABLE", "Symbolic links are inventoried without following their targets. Supply the actual export file explicitly."
    else:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            status, error = "MISSING", "Supplied file does not exist."
        except (OSError, ValueError) as exc:
            status, error = "UNREADABLE", f"Supplied file cannot be read: {exc}"
    digest = hashlib.sha256(data).hexdigest() if data is not None else None
    relative_path = os.path.relpath(path, root)
    identity = relative_path + "\0" + (digest or status)
    record: dict[str, Any] = {
        "file_id": "file-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
        "path": str(path), "relative_path": relative_path,
        "sha256": digest, "size_bytes": len(data) if data is not None else None,
        "role": "unknown", "detected_roles": [], "confirmed_role": None,
        "platform": "unknown", "format": "unknown", "export_version": None,
        "status": status, "evidence": [],
    }
    record["evidence"].append(_ref(record["file_id"], "file:/", "sha256_exact_bytes" if data is not None else "file_presence"))
    if error:
        return record, [_diagnostic("MISSING_FILE" if status == "MISSING" else "UNREADABLE_FILE", "ERROR", error, record)]
    detected, messages = _detect(data, path)
    record.update({
        "detected_roles": detected["roles"],
        "role": detected["roles"][0] if len(detected["roles"]) == 1 else "ambiguous" if detected["roles"] else "unknown",
        "platform": detected["platform"], "format": detected["format"],
        "export_version": detected["version"],
    })
    record["evidence"].append(_ref(record["file_id"], detected["locator"], detected["rule"]))
    diagnostics = [_diagnostic(code, severity, message, record) for code, severity, message in messages]
    if record["platform"] in _EXCLUDED or path.suffix.lower() in {".dsx", ".mp"}:
        diagnostics.append(_diagnostic("EXCLUDED_PLATFORM", "ERROR", "DataStage and Ab Initio exports are explicitly excluded from this framework.", record))
    elif record["platform"] not in {"ssis", "unknown"} and record['format'] not in {'tm1_source_bundle_v1', 'powercenter_xml', 'bods_xml', 'odi_xml'}:
        diagnostics.append(_diagnostic("UNSUPPORTED_PLATFORM", "ERROR", f"Detected {record['platform']} export format; this format is not implemented by its adapter.", record))
    if path.suffix.lower() == ".dtsx" and record["format"] != "ssis_dtsx_xml":
        diagnostics.append(_diagnostic("UNCONFIRMED_PACKAGE_FORMAT", "ERROR", "A .dtsx name alone does not establish a supported SSIS export; the SSIS XML signature was not found.", record))
    return record, diagnostics


def describe_file(path: str | Path, source_root: str | Path | None = None) -> dict:
    """Return the same content-based file record used by the full inventory."""
    path = _absolute(path)
    return _inspect(path, _absolute(source_root) if source_root is not None else path.parent)[0]


def _platform(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = {"planning_analytics": "tm1", "ibm_tm1": "tm1", "powercenter": "informatica", "informatica_powercenter": "informatica", "sap_bods": "bods", "oracle_data_integrator": "odi", "abinitio": "ab_initio"}.get(normalized, normalized)
    if normalized not in PLATFORMS:
        raise ValueError(f"Unknown source platform: {value!r}")
    return normalized


def build_intake_manifest(
    source: str | Path, *, dependencies: Sequence[str | Path] = (),
    base_path: str | Path = ".", platform: str | None = None,
    role_overrides: dict[str, str] | None = None,
    exclude_paths: Sequence[str | Path] = (),
) -> dict:
    """Inventory explicit input files and mapped metadata dependencies.

    Relative metadata_file references use base_path, matching the established
    migration context loader. Roles and platform choices are recorded rather
    than used to erase contradictory content evidence.
    """
    source = _absolute(source)
    if not source.exists() and not source.is_symlink():
        raise FileNotFoundError(f"Source does not exist: {source}")
    root = source if source.is_dir() and not source.is_symlink() else source.parent
    base = _absolute(base_path)
    excluded = sorted({_absolute(path) for path in exclude_paths})
    requested = _platform(platform)
    overrides = {}
    for name, role in (role_overrides or {}).items():
        role = role.strip().lower().replace("-", "_")
        if role not in ROLES - {"unknown", "ambiguous"}:
            raise ValueError(f"Unknown or nonconcrete metadata role: {role!r}")
        candidate = Path(name)
        overrides[_absolute(candidate if candidate.is_absolute() else root / candidate)] = role
    files: dict[Path, dict] = {}
    diagnostics: list[dict] = []
    edges: list[dict] = []

    def is_excluded(path: Path) -> bool:
        return any(path == item or item in path.parents for item in excluded)

    def add(path: Path, *, explicitly_supplied: bool = False) -> dict | None:
        path = _absolute(path)
        if is_excluded(path):
            diagnostics.append(_diagnostic("EXCLUDED_INPUT_PATH", "ERROR" if explicitly_supplied else "INFO", f"Input path was explicitly excluded from inventory: {path}"))
            return None
        if path in files:
            return files[path]
        record, findings = _inspect(path, root)
        files[path] = record
        diagnostics.extend(findings)
        role = overrides.get(path)
        if role:
            record["confirmed_role"] = record["role"] = role
            record["evidence"].append(_ref(record["file_id"], "decision:/role", "explicit_user_role"))
            if record["detected_roles"] and role not in record["detected_roles"]:
                diagnostics.append(_diagnostic("ROLE_CONTENT_CONFLICT", "ERROR", f"Confirmed role {role!r} conflicts with detected roles {record['detected_roles']}.", record))
            elif role in {"connection_mapping", "database_metadata"} and role not in record["detected_roles"]:
                diagnostics.append(_diagnostic("UNSUPPORTED_METADATA_SHAPE", "ERROR", f"The supplied content does not establish a supported {role} schema.", record))
        elif record["status"] == "AVAILABLE" and record["role"] in {"unknown", "ambiguous"}:
            diagnostics.append(_diagnostic("AMBIGUOUS_FILE_ROLE" if record["role"] == "ambiguous" else "UNKNOWN_FILE_ROLE", "WARNING", "File role needs explicit confirmation; supplied content is retained.", record))
        return record

    def walk(path: Path) -> None:
        if is_excluded(path):
            diagnostics.append(_diagnostic("EXCLUDED_INPUT_PATH", "INFO", f"Directory was explicitly excluded from inventory: {path}"))
            return
        if path.is_symlink() or not path.is_dir():
            add(path)
            return
        try:
            children = sorted(path.iterdir())
        except OSError as exc:
            diagnostics.append(_diagnostic("UNREADABLE_DIRECTORY", "ERROR", f"Cannot inventory supplied directory {path}: {exc}"))
            return
        for child in children:
            walk(child)

    if source.is_dir() and not source.is_symlink():
        walk(source)
    else:
        add(source, explicitly_supplied=True)
    for dependency in dependencies or ():
        dependency = _absolute(dependency)
        if dependency.is_dir() and not dependency.is_symlink():
            walk(dependency)
        else:
            add(dependency, explicitly_supplied=True)

    processed: set[Path] = set()
    mapping_bindings: dict[str, list[tuple[Path, str, dict]]] = {}
    while True:
        pending = [(path, record) for path, record in files.items() if path not in processed]
        if not pending:
            break
        for path, record in pending:
            processed.add(path)
            if record["status"] != "AVAILABLE" or record["role"] != "connection_mapping":
                continue
            try:
                content = json.loads(path.read_bytes())
            except (OSError, ValueError):
                continue  # The file's inspection already records this error.
            if not isinstance(content, dict) or not isinstance(content.get("mappings"), list):
                continue
            for index, item in enumerate(content["mappings"]):
                if not isinstance(item, dict) or not isinstance(item.get("metadata_file"), str) or not item["metadata_file"].strip():
                    continue
                reference = item["metadata_file"]
                target_path = _absolute(base / reference)
                target = add(target_path, explicitly_supplied=True)
                evidence = [_ref(record["file_id"], f"json:/mappings/{index}/metadata_file", "mapping_metadata_file")]
                edge = {
                    "source_file_id": record["file_id"],
                    "target_file_id": target["file_id"] if target else None,
                    "reference": reference, "role": "database_metadata",
                    "status": "RESOLVED" if target and target["status"] == "AVAILABLE" else "MISSING",
                    "evidence": evidence,
                }
                edges.append(edge)
                if target is None or target["status"] != "AVAILABLE":
                    diagnostics.append(_diagnostic("MISSING_DEPENDENCY", "ERROR", f"Mapped metadata dependency is unavailable: {reference}", record, evidence=evidence, file_ids=[record["file_id"], *([target["file_id"]] if target else [])]))
                elif target["role"] != "database_metadata":
                    edge["status"] = "AMBIGUOUS" if target["role"] in {"unknown", "ambiguous"} else "CONFLICTING"
                    diagnostics.append(_diagnostic("DEPENDENCY_ROLE_CONFLICT", "ERROR", f"Mapped dependency {reference!r} does not have a confirmed database_metadata role.", target, evidence=evidence, file_ids=[record["file_id"], target["file_id"]]))
                identity = item.get("connection_reference")
                if isinstance(identity, str):
                    mapping_bindings.setdefault(identity, []).append((target_path, str(item.get("role")), edge))
    for identity, bindings in mapping_bindings.items():
        if len({(str(path), role) for path, role, _ in bindings}) > 1:
            for _, _, edge in bindings:
                edge["status"] = "CONFLICTING"
            diagnostics.append(_diagnostic("CONFLICTING_CONNECTION_MAPPING", "ERROR", f"Connection {identity!r} has conflicting metadata bindings.", file_ids=list(dict.fromkeys(edge["source_file_id"] for _, _, edge in bindings)), evidence=[ref for _, _, edge in bindings for ref in edge["evidence"]]))
    for path in overrides.keys() - files.keys():
        diagnostics.append(_diagnostic("UNKNOWN_ROLE_OVERRIDE_FILE", "ERROR", f"Role override does not identify an inventoried file: {path}"))
    observed = sorted({record["platform"] for record in files.values() if record["platform"] != "unknown"})
    detected = observed[0] if len(observed) == 1 else "mixed" if observed else "unknown"
    if len(observed) > 1:
        diagnostics.append(_diagnostic("MIXED_SOURCE_PLATFORMS", "ERROR", f"Supplied files identify multiple source platforms: {', '.join(observed)}."))
    if requested in _EXCLUDED:
        diagnostics.append(_diagnostic("EXCLUDED_PLATFORM", "ERROR", f"Explicit source platform {requested!r} is excluded."))
    elif requested not in {None, "ssis", "informatica", "bods", "odi"} and not (requested == 'tm1' and any(
        f['platform'] == 'tm1' and f['format'] == 'tm1_source_bundle_v1' for f in files.values())):
        diagnostics.append(_diagnostic("UNSUPPORTED_PLATFORM", "ERROR", f"Source platform {requested!r} has no implemented adapter."))
    if requested and any(value != requested for value in observed):
        diagnostics.append(_diagnostic("PLATFORM_CONTENT_CONFLICT", "ERROR", f"Confirmed platform {requested!r} conflicts with detected content {detected!r}."))
    if not any(record["role"] == "package" for record in files.values()):
        diagnostics.append(_diagnostic("NO_SOURCE_PACKAGE", "ERROR", "No source package was identified; roles/platforms require confirmation or a supported export."))
    manifest = {
        "manifest_version": "1.0", "source_root": str(root),
        "requested_platform": requested, "detected_platform": detected,
        "confirmed_platform": requested,
        "files": sorted(files.values(), key=lambda record: record["path"]),
        "dependencies": edges, "diagnostics": diagnostics,
        "status": "BLOCKED" if any(d["severity"] == "ERROR" for d in diagnostics) else "REVIEW_REQUIRED" if any(d["severity"] == "WARNING" for d in diagnostics) else "READY",
        "excluded_paths": [str(path) for path in excluded],
    }
    from ssis_migration.schemas import validate_document, validate_evidence_references

    validate_document(manifest, "intake_manifest")
    validate_evidence_references(manifest)
    return manifest
