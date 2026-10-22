"""Versioned, lossless contract bridge; no new source semantics are inferred.

Legacy planner conclusions remain unverified conclusions. Binding a contract
to its input protects against drift, not against errors in those conclusions.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ssis_migration.agent.migration_contract import MigrationContract
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from .normalized_plan import validate_normalized_scope


_RUNTIME_FIELDS = {"databases", "package_ir"}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _snapshot(step: dict[str, Any]) -> dict[str, Any]:
    snapshot = copy.deepcopy({key: value for key, value in step.items() if key not in _RUNTIME_FIELDS})
    metadata = snapshot.setdefault("metadata", {})
    metadata.pop("migration_contract", None)
    metadata.pop("static_validation", None)
    return snapshot


def _contract_id(document: dict[str, Any]) -> str:
    return "contract-" + _digest({key: value for key, value in document.items() if key != "contract_id"})[:24]


def _collections(legacy: dict[str, Any]) -> dict[str, list[Any]]:
    metadata = legacy.get("metadata", {})
    intent = metadata.get("migration_intent", {})
    flow_outline = intent.get("data_flows", [])
    flow_detail = metadata.get("data_flows", [])
    if not isinstance(flow_outline, list) or not isinstance(flow_detail, list):
        raise ValueError("Legacy data-flow obligations must be lists.")
    flows = list(flow_outline)
    for flow in flow_detail:
        if flow not in flows:
            flows.append(flow)
    collections = {
        "control_flow": intent.get("control_flow_operations", []),
        "data_flow": flows,
        "transformation": legacy.get("transformations", []),
        "source_procedure": intent.get("source_procedures", []),
        "destination_procedure": intent.get("destination_procedures", []),
    }
    scope = metadata.get("normalized_source")
    if scope is not None:
        validate_normalized_scope(scope)
        collections["normalized_node"] = scope["nodes"]
        collections["normalized_edge"] = scope["edges"]
    return collections


def validate_contract(document: dict[str, Any], step: dict[str, Any] | None = None) -> None:
    """Check schema, evidence IDs, content identity and the legacy binding."""
    validate_document(document, "migration_contract")
    validate_evidence_references(document)
    legacy = document["source_behavior"]["legacy_step"]
    scope = legacy.get("metadata", {}).get("normalized_source")
    if scope is not None and scope["source_platform"] != document["source_platform"]:
        raise SchemaValidationError("Neutral graph platform disagrees with the migration contract.")
    if _digest(legacy) != document["binding_digest"]:
        raise SchemaValidationError("Migration contract legacy binding digest does not match its snapshot.")
    if document["source_behavior"]["migration_intent"] != legacy.get("metadata", {}).get("migration_intent", {}):
        raise SchemaValidationError("Migration contract does not preserve the original migration_intent.")
    if document["step_id"] != legacy.get("step_id"):
        raise SchemaValidationError("Migration contract step identity does not match its snapshot.")
    expected = [(f"{kind}-{index}", kind, value) for kind, values in _collections(legacy).items() for index, value in enumerate(values)]
    actual = [(item["behavior_id"], item["kind"], item["source_value"]) for item in document["required_behavior"]["obligations"]]
    if expected != actual:
        raise SchemaValidationError("Migration contract omitted or changed a required legacy preservation obligation.")
    for contract_key, legacy_key in (("inputs", "source_objects"), ("outputs", "target_objects")):
        if [item["reference"] for item in document[contract_key]] != legacy.get(legacy_key, []):
            raise SchemaValidationError(f"Migration contract {contract_key} do not preserve original object references.")
    strategy = legacy.get("metadata", {}).get("migration_strategy", {})
    if document["implementation_strategy"]["legacy_details"] != strategy or document["implementation_strategy"]["decision"] != strategy.get("strategy", "REVIEW_REQUIRED"):
        raise SchemaValidationError("Migration contract strategy does not match the original planner decision.")
    if [item["reference"] for item in document["dependencies"] if item["kind"] == "step"] != legacy.get("dependencies", []):
        raise SchemaValidationError("Migration contract omitted a declared step dependency.")
    if document["contract_id"] != _contract_id(document):
        raise SchemaValidationError("Migration contract content identity is stale; rebuild the contract after editing inputs.")
    if step is not None and _digest(_snapshot(step)) != document["binding_digest"]:
        raise SchemaValidationError("Migration inputs changed after contract creation; rebuild before code generation.")


def build_migration_contract(
    step: dict[str, Any], *, source_platform: str = "unknown",
    evidence_files: list[dict[str, Any]] | None = None,
    file_dependencies: list[dict[str, Any]] | None = None,
    supporting_definitions: dict[str, str] | None = None,
    source_diagnostics: list[dict[str, Any]] | None = None,
) -> MigrationContract:
    """Preserve a planner step and expose explicit, unverified obligations.

    File references identify contributing inputs; they are not evidence that
    the planner's inferred semantics, schemas or criticality are correct.
    """
    legacy = _snapshot(step)
    metadata = legacy.get("metadata", {})
    intent = metadata.get("migration_intent", {})
    if not isinstance(intent, dict):
        raise ValueError("migration_intent must be an object before bridging.")
    evidence_files = copy.deepcopy(evidence_files or [])
    refs = [{
        "file_id": record["file_id"], "locator": "file:/",
        "rule": "legacy_planner_input_unverified",
    } for record in evidence_files if record["status"] == "AVAILABLE"]
    unresolved = [] if refs else ["No original source-file evidence was supplied for this contract."]
    unresolved.extend(
        f"{diagnostic['code']}: {diagnostic['message']}"
        for diagnostic in (source_diagnostics or [])
        if diagnostic["severity"] in {"WARNING", "ERROR"}
    )
    scope = metadata.get("normalized_source")
    if scope is not None:
        validate_normalized_scope(scope)
        unresolved.extend(f"{d['code']}: {d['message']}" for d in scope['diagnostics']
                          if d['severity'] in {'WARNING', 'ERROR'})
    strategy = copy.deepcopy(metadata.get("migration_strategy", {}))
    decision = strategy.get("strategy", "REVIEW_REQUIRED")
    if decision not in {"DETERMINISTIC", "AI_ASSISTED", "REVIEW_REQUIRED", "UNSUPPORTED"}:
        raise ValueError(f"Unsupported migration strategy: {decision!r}")
    if decision in {"REVIEW_REQUIRED", "UNSUPPORTED"}:
        unresolved.append(f"Legacy strategy requires {decision.lower()} handling; translation is not complete.")

    obligations = []
    collections = _collections(legacy)
    for kind, values in collections.items():
        if not isinstance(values, list):
            raise ValueError(f"Legacy {kind} obligations must be a list.")
        for index, value in enumerate(values):
            obligations.append({
                "behavior_id": f"{kind}-{index}", "kind": kind,
                "source_value": copy.deepcopy(value),
                "evidence": copy.deepcopy(value['record']['evidence'] if kind.startswith('normalized_') else refs),
                "verification": "NOT_VERIFIED", "critical": None, "weight": None,
            })
    if not obligations:
        unresolved.append("No source-behavior obligations were identified; do not treat an empty contract as coverage.")

    dependencies = [{
        "kind": "step", "reference": reference, "status": "DECLARED",
        "evidence": copy.deepcopy(refs),
    } for reference in legacy.get("dependencies", [])]
    definitions = copy.deepcopy(supporting_definitions or {})
    if not isinstance(definitions, dict) or any(not isinstance(name, str) or not isinstance(body, str) for name, body in definitions.items()):
        raise ValueError("Supporting procedure definitions must map names to exported text.")

    def has_body(item: dict[str, Any]) -> bool:
        return isinstance(item.get("definition"), str) and bool(item["definition"].strip())

    def procedure_key(value: str) -> str:
        return value.replace("[", "").replace("]", "").lower()

    supplemental_groups: dict[str, set[str]] = {}
    for name, body in definitions.items():
        if body.strip():
            supplemental_groups.setdefault(procedure_key(name), set()).add(body)
    supplemental = {name: next(iter(bodies)) for name, bodies in supplemental_groups.items() if len(bodies) == 1}
    for name, bodies in supplemental_groups.items():
        if len(bodies) > 1:
            unresolved.append(f"Conflicting supplemental procedure definitions for {name}; no definition was selected.")
    available: dict[tuple[str, str], set[str]] = {}
    for kind in ("source_procedure", "destination_procedure"):
        for item in collections[kind]:
            if item.get("procedure") and has_body(item):
                available.setdefault((kind, procedure_key(item["procedure"])), set()).add(item["definition"])
    for kind, collection in (("source_procedure", "procedure_semantics"), ("destination_procedure", "destination_procedure_semantics")):
        for item in metadata.get(collection, []):
            name = item.get("procedure") or ".".join(str(item[key]) for key in ("schema", "name") if item.get(key))
            if name and has_body(item):
                available.setdefault((kind, procedure_key(name)), set()).add(item["definition"])
    for kind in ("source_procedure", "destination_procedure"):
        for item in collections[kind]:
            name = item.get("procedure") if isinstance(item, dict) else None
            if not name:
                unresolved.append(f"Unnamed {kind} dependency must be resolved.")
                continue
            key = procedure_key(name)
            bodies = set(available.get((kind, key), set()))
            if has_body(item):
                bodies.add(item["definition"])
            if len(bodies) > 1 or (bodies and key in supplemental and any(body != supplemental[key] for body in bodies)):
                unresolved.append(f"Conflicting exported procedure definitions for {kind} {name}; source equivalence must be reviewed.")
            other_kind = "destination_procedure" if kind == "source_procedure" else "source_procedure"
            opposite = available.get((other_kind, key), set())
            ambiguous_supplement = key in supplemental and bool(opposite) and not bodies
            if ambiguous_supplement:
                unresolved.append(f"Supplemental definition {name} has ambiguous source/destination ownership; confirm the dependency role.")
            supplied = bool(bodies) or (key in supplemental and not ambiguous_supplement)
            dependencies.append({
                "kind": "procedure", "reference": name,
                "source_role": "source" if kind == "source_procedure" else "destination",
                "status": "SUPPLIED" if supplied else "MISSING", "evidence": copy.deepcopy(refs),
            })
            if not supplied:
                unresolved.append(f"Missing exported procedure definition: {name}")
    for edge in file_dependencies or []:
        dependencies.append({
            "kind": "file", "reference": edge["reference"],
            "status": edge["status"], "evidence": copy.deepcopy(edge["evidence"]),
        })
        if edge["status"] != "RESOLVED":
            unresolved.append(f"Unresolved supplied file dependency: {edge['reference']}")

    def objects(field: str) -> list[dict[str, Any]]:
        return [{"reference": name, "schema_status": "UNVERIFIED", "evidence": copy.deepcopy(refs)} for name in legacy.get(field, [])]

    contract = MigrationContract(
        step_id=legacy["step_id"], source_platform=source_platform.lower(), target_platform="databricks",
        source_behavior={
            "migration_intent": copy.deepcopy(intent), "legacy_step": legacy,
            "supporting_definitions": definitions,
            "analysis_status": "UNVERIFIED_LEGACY_ANALYSIS",
            "diagnostics": copy.deepcopy(source_diagnostics or []),
        },
        required_behavior={"obligations": obligations, "criticality_status": "UNASSESSED", "coverage_status": "NOT_VERIFIED"},
        target_capabilities={
            "generation_languages": ["spark_sql", "pyspark"],
            "execution_status": "NOT_RUN", "semantic_equivalence": "NOT_VERIFIED",
        },
        target_design={"execution_mode": "GENERATE_ONLY", "deployment": "DISABLED"},
        validation_requirements=[
            "Validate contract schema, evidence references and input binding before generation.",
            "Independently validate generated SQL/Python, notebook structure and dependency graphs.",
            "Verify every required behavior against original source evidence; assess criticality and weights explicitly.",
            "Run controlled execution and data reconciliation before acceptance; static checks provide no semantic credit.",
        ],
        unresolved_items=list(dict.fromkeys(unresolved)),
        assumptions=copy.deepcopy(metadata.get("assumptions", [])),
        status="BLOCKED" if decision == "UNSUPPORTED" else "REVIEW_REQUIRED" if unresolved else "DRAFT",
        binding_digest=_digest(legacy), inputs=objects("source_objects"), outputs=objects("target_objects"),
        dependencies=dependencies,
        implementation_strategy={"decision": decision, "legacy_details": strategy, "verification": "UNVERIFIED_LEGACY_DECISION"},
        evidence_files=evidence_files,
        provenance={
            "builder": "migration_intent_bridge", "version": "1.0",
            "evidence": refs,
            "limitation": "File references identify contributing inputs, not verified behavior-to-source mappings. Legacy inference is retained without promotion to truth.",
        },
    )
    contract.contract_id = _contract_id(contract.to_dict())
    validate_contract(contract.to_dict(), step)
    return contract


def prepare_generation_step(step: dict[str, Any]) -> dict[str, Any]:
    """Use the validated contract as the input bridge when one is attached.

    Legacy standalone callers without a contract retain their existing API.
    The main migration pipeline always attaches one before calling consumers.
    """
    contract = step.get("metadata", {}).get("migration_contract")
    if contract is None:
        return step
    validate_contract(contract, step)
    prepared = copy.deepcopy(step)
    prepared["metadata"]["migration_intent"] = copy.deepcopy(contract["source_behavior"]["migration_intent"])
    prepared["metadata"]["migration_strategy"] = copy.deepcopy(contract["implementation_strategy"]["legacy_details"])
    return prepared
