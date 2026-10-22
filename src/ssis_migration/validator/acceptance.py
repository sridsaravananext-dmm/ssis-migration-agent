"""Offline acceptance of reviewer-supplied evidence; never executes workloads.

Hashes bind evidence to bytes, not to truth. Report contents and reviewer identity
are attestations supplied by the operator, not independently authenticated here.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Any

from ssis_migration.migration import validate_contract
from ssis_migration.generator.notebook import NotebookAssembler
from ssis_migration.generator.job_generator import JobGenerator
from ssis_migration.schemas import validate_document
from ssis_migration.validator.readiness import summarize_readiness

DIMENSIONS = ("semantic", "execution", "data_equivalence")
MANDATORY_GATES = ("source_review", "execution", "data_equivalence")


def _read(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def nonfinite(value: str) -> None:
        raise ValueError(f"Non-finite JSON number: {value}")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=nonfinite)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def document_digest(value: Any) -> str:
    """Canonical SHA-256 used to bind a reviewed policy to its evidence."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def load_package(package_dir: Path) -> dict[str, Any]:
    """Read the current generation and bind every contract back to its plan step."""
    root = package_dir.resolve(strict=True)
    if (root / 'source_envelope.json').is_file() and _read(root / 'source_envelope.json').get('envelope_version') == '2.0':
        raise ValueError('Native source review outputs have no target implementation and cannot be accepted as a migration.')
    bundle = _read(root / "migration_contracts.json")
    if bundle.get("contract_bundle_version") != "1.0":
        raise ValueError("Unsupported contract bundle version.")
    contracts = bundle["contracts"]
    plan = _read(root / "migration_plan.json")
    steps = {step["step_id"]: step for step in plan["steps"]}
    if len(steps) != len(plan["steps"]) or not contracts:
        raise ValueError("Duplicate plan steps or empty contract bundle.")
    if len({c["contract_id"] for c in contracts}) != len(contracts):
        raise ValueError("Duplicate contract IDs.")
    if len({c["step_id"] for c in contracts}) != len(contracts) or set(steps) != {c["step_id"] for c in contracts}:
        raise ValueError("Contract bundle must cover every plan step exactly once.")
    for contract in contracts:
        step = steps[contract["step_id"]]
        validate_contract(contract, step)
        if step["metadata"].get("migration_contract") != contract:
            raise ValueError("Plan and contract bundle disagree.")
    paths = [root / name for name in (
        "migration_contracts.json", "migration_plan.json", "validation_results.json", "databricks.yml",
    )]
    # New packages include a neutral graph. Older package outputs remain
    # readable; evidence for either form binds every available IR artifact.
    for name in ("package_ir.json", "source_envelope.json", "normalized_ir.json", "plan_ir_binding.json"):
        if (root / name).exists():
            paths.append(root / name)
    normalization_checks = []
    if (root / "normalized_ir.json").exists():
        from ssis_migration.ir.normalized import validate_normalized_ir

        normalized = _read(root / "normalized_ir.json")
        validate_normalized_ir(normalized, legacy_ir=_read(root / "package_ir.json"))
        if (plan.get('metadata', {}).get('normalized_ir_binding') is not None
                or (root / 'plan_ir_binding.json').exists()
                or any(s.get('metadata', {}).get('normalized_source') is not None for s in plan['steps'])):
            from ssis_migration.migration import validate_plan_ir_binding

            validate_plan_ir_binding(plan, normalized)
            if _read(root / 'plan_ir_binding.json') != plan['metadata']['normalized_ir_binding']:
                raise ValueError('Neutral plan binding artifact disagrees with the plan.')
        normalization_checks.append({
            "validator": "current_normalized_ir", "status": "PASS",
            "issues": normalized["diagnostics"],
        })
    notebooks = sorted(root.glob("*_Migration.ipynb"))
    jobs = sorted(root.glob("*_job.json"))
    if not notebooks or not jobs:
        raise ValueError("Notebook and job artifacts are required.")
    paths.extend(notebooks + jobs)
    paths.extend(sorted(p for p in (root / "generated_artifacts").rglob("*") if p.is_file()))
    hashes = {}
    for path in paths:
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("Artifact paths must remain inside the package directory.")
        hashes[path.relative_to(root).as_posix()] = _hash(path)
    source_issues = []
    for contract in contracts:
        for record in contract["evidence_files"]:
            path = Path(record["path"])
            if not path.is_file() or not record["sha256"] or _hash(path) != record["sha256"]:
                source_issues.append(f"Source evidence missing or changed: {record['path']}")
    fresh_checks = [{"validator": "current_notebook", **NotebookAssembler.validate(_read(p))}
                    for p in notebooks]
    for path, check in zip(notebooks, fresh_checks):
        if check["status"] == "PASS":
            cells = _read(path)["cells"]
            has_code = any(
                ast.parse("".join(cell["source"]) if isinstance(cell["source"], list)
                          else cell["source"]).body
                for cell in cells if cell["cell_type"] == "code")
            if not has_code:
                check.update(status="REVIEW_REQUIRED", issues=[{
                    "severity": "WARNING", "message": "Notebook contains no executable statements."}])
    # Regenerate only the in-memory expected graph, without deployment or code
    # execution. Cluster settings may be operator-edited; task identities and
    # declared dependency ordering must still match the plan.
    expected = JobGenerator().generate(plan, str(notebooks[0])).to_dict()
    def graph(job: dict) -> dict:
        tasks = job["tasks"]
        result = {t["task_key"]: {
            "depends_on": sorted(d["task_key"] for d in t.get("depends_on", [])),
            "step_id": t["notebook_task"]["base_parameters"]["step_id"],
        } for t in tasks}
        if len(result) != len(tasks):
            raise ValueError("Duplicate job task keys.")
        return result
    for path in jobs:
        if graph(_read(path)) != graph(expected):
            raise ValueError("Current job dependency graph disagrees with the plan.")
    static = summarize_readiness(
        [*_read(root / "validation_results.json"), *fresh_checks, *normalization_checks],
        unresolved_items=plan.get("unresolved", []),
        generated_artifact_count=len(notebooks),
    )
    return {"contracts": contracts, "artifact_hashes": hashes,
            "static": static, "source_issues": list(dict.fromkeys(source_issues))}


def draft_documents(package: dict[str, Any]) -> tuple[dict, dict]:
    """Produce unassessed drafts; no weights, criticality or results are invented."""
    behaviors = [{"contract_id": c["contract_id"], "behavior_id": b["behavior_id"],
                  "weight": None, "critical": None}
                 for c in package["contracts"] for b in c["required_behavior"]["obligations"]]
    policy = {"policy_version": "1.0", "minimum_semantic_percent": 80,
              "additional_gates": [], "behaviors": behaviors}
    evidence = {"evidence_version": "1.0", "artifact_hashes": dict(package["artifact_hashes"]),
                "policy_digest": document_digest(policy), "checks": [],
                "gates": [{"gate_id": name, "status": "NOT_RUN", "reports": []}
                          for name in MANDATORY_GATES]}
    return policy, evidence


def evaluate_acceptance(package: dict[str, Any], policy: dict, evidence: dict,
                        *, report_root: Path) -> dict[str, Any]:
    """Evaluate supplied attestations conservatively, keeping all obligations.

    Missing/unassessed behavior blocks acceptance. Unsupported behavior keeps
    its weight but never earns semantic credit. Contract and static blockers
    cannot be waived by a report. Critical behavior must pass all dimensions.
    """
    validate_document(policy, "acceptance_policy")
    validate_document(evidence, "acceptance_evidence")
    # JSON Schema number comparisons alone do not reliably reject NaN/Infinity.
    document_digest(policy)
    document_digest(evidence)
    blockers = list(package["static"]["blocking_issues"]) + list(package["source_issues"])
    if evidence["artifact_hashes"] != package["artifact_hashes"]:
        blockers.append("Evidence is bound to different or incomplete artifacts.")
    if evidence["policy_digest"] != document_digest(policy):
        blockers.append("Evidence is bound to a different acceptance policy.")
    bound = (evidence["artifact_hashes"] == package["artifact_hashes"]
             and evidence["policy_digest"] == document_digest(policy) and not package["source_issues"])
    obligations = {}
    for contract in package["contracts"]:
        blockers.extend(f"{contract['contract_id']}: {x}" for x in contract["unresolved_items"])
        for item in contract["required_behavior"]["obligations"]:
            key = (contract["contract_id"], item["behavior_id"])
            if key in obligations:
                raise ValueError("Duplicate contract behavior identity.")
            obligations[key] = contract
    weights = {}
    for item in policy["behaviors"]:
        key = (item["contract_id"], item["behavior_id"])
        if key in weights or key not in obligations:
            raise ValueError(f"Duplicate or unknown policy behavior: {key}")
        weights[key] = item
    if set(weights) != set(obligations):
        blockers.append("Policy must assess every contract behavior; missing behaviors cannot be excluded.")
    assessed = bool(obligations) and set(weights) == set(obligations) and all(
        item["weight"] is not None and item["critical"] is not None for item in weights.values())
    if not assessed:
        blockers.append("Behavior weights or criticality remain unassessed; coverage is unknown.")

    def report_valid(report: dict) -> bool:
        path = (report_root / report["path"]).resolve()
        return (path.is_relative_to(report_root.resolve()) and path.is_file()
                and _hash(path) == report["sha256"])

    def verified(record: dict) -> bool:
        valid = bool(record["reports"]) and all(report_valid(r) for r in record["reports"])
        if record["reports"] and not valid:
            blockers.append("Missing, changed or external evidence report.")
        if record["status"] == "FAIL":
            blockers.append("Supplied evidence contains a failed check or gate.")
        return record["status"] == "PASS" and valid and bound

    checks = {}
    for check in evidence["checks"]:
        key = (check["contract_id"], check["behavior_id"])
        identity = (*key, check["dimension"])
        if key not in obligations or identity in checks:
            raise ValueError(f"Duplicate or unknown evidence check: {identity}")
        passed = verified(check)
        unsupported = obligations[key]["implementation_strategy"]["decision"] == "UNSUPPORTED"
        checks[identity] = passed and not unsupported

    gates = {}
    for gate in evidence["gates"]:
        if gate["gate_id"] in gates:
            raise ValueError("Duplicate acceptance gate.")
        gates[gate["gate_id"]] = verified(gate)
    required_gates = list(dict.fromkeys((*MANDATORY_GATES, *policy["additional_gates"])))
    for gate in required_gates:
        if not gates.get(gate, False):
            blockers.append(f"Mandatory acceptance gate did not pass: {gate}")
    for dimension in ("execution", "data_equivalence"):
        if not any(passed for identity, passed in checks.items() if identity[2] == dimension):
            blockers.append(f"No behavior has passing {dimension} evidence.")
    # Any failed additional evidence remains visible and cannot be hidden by an
    # aggregate PASS. Non-PASS semantic checks may represent the allowed gap.
    for gate, passed in gates.items():
        if not passed and gate not in required_gates:
            blockers.append(f"Supplied acceptance gate did not pass: {gate}")
    coverage = {"structural": package["static"]["coverage"]["structural"]}
    total = math.fsum(item["weight"] for item in weights.values()) if assessed else None
    for dimension in DIMENSIONS:
        earned = math.fsum(item["weight"] for key, item in weights.items()
                           if checks.get((*key, dimension), False)) if assessed else None
        percent = 100 * (earned / total) if total else None
        relevant = [c for c in evidence["checks"] if c["dimension"] == dimension]
        status = "EVALUATED" if assessed else "NOT_VERIFIED"
        if dimension != "semantic" and not any(c["status"] != "NOT_RUN" for c in relevant):
            status = "NOT_RUN"
        coverage[dimension] = {"status": status,
                               "verified_percent": percent, "verified_weight": earned,
                               "total_weight": total, "scope": "REVIEWER_SUPPLIED_EVIDENCE"}
        for key, item in weights.items():
            if item["critical"] is True and not checks.get((*key, dimension), False):
                blockers.append(f"Critical behavior lacks passing {dimension} evidence: {key}")
    # Compare exact decimal values, rather than rounded report percentages.
    # Large integer weights can otherwise round a value just below 80 up to 80.
    exact_total = sum(Fraction(str(item["weight"])) for item in weights.values()) if assessed else 0
    exact_verified = sum(Fraction(str(item["weight"])) for key, item in weights.items()
                         if checks.get((*key, "semantic"), False)) if assessed else 0
    if not exact_total or 100 * exact_verified < Fraction(str(policy["minimum_semantic_percent"])) * exact_total:
        blockers.append("Weighted verified semantic coverage is below the required threshold or unknown.")
    raw_checks = {(c["contract_id"], c["behavior_id"], c["dimension"]): c for c in evidence["checks"]}
    behaviors = []
    for key, contract in obligations.items():
        strategy = contract["implementation_strategy"]["decision"]
        statuses = {}
        for dimension in DIMENSIONS:
            identity = (*key, dimension)
            supplied = raw_checks.get(identity, {}).get("status", "NO_EVIDENCE")
            statuses[dimension] = ("UNSUPPORTED" if strategy == "UNSUPPORTED" else
                                   "VERIFIED" if checks.get(identity) else
                                   "NOT_VERIFIED" if supplied == "PASS" else supplied)
        assessment = weights.get(key, {})
        behaviors.append({"contract_id": key[0], "behavior_id": key[1],
                          "weight": assessment.get("weight"), "critical": assessment.get("critical"),
                          "strategy": strategy, "dimensions": statuses})
    blockers = list(dict.fromkeys(blockers))
    return {"result_version": "1.0", "lifecycle_status": "BLOCKED" if blockers else "ACCEPTED",
            "validation_scope": "OFFLINE_EVIDENCE_REVIEW", "blocking_issues": blockers,
            "acceptance": {"accepted": not blockers, "status": "BLOCKED" if blockers else "ACCEPTED",
                           "required_semantic_coverage_percent": policy["minimum_semantic_percent"],
                           "basis": "Reviewer-attested reports; no workloads executed or reviewer identities authenticated."},
            "coverage": coverage, "behavior_results": behaviors,
            "artifact_hashes": package["artifact_hashes"],
            "policy_digest": document_digest(policy), "evidence_digest": document_digest(evidence),
            "mandatory_gates": {name: gates.get(name, False) for name in required_gates}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--prepare", type=Path, help="Create unassessed policy/evidence drafts in a new directory.")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        package = load_package(args.package_dir)
        if args.prepare:
            if args.policy or args.evidence or args.output:
                parser.error("--prepare cannot be combined with evaluation arguments")
            args.prepare.mkdir(parents=True, exist_ok=False)
            for name, doc in zip(("policy.json", "evidence.json"), draft_documents(package)):
                (args.prepare / name).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            return
        if not args.policy or not args.evidence or not args.output:
            parser.error("evaluation requires --policy, --evidence and --output")
        # Refuse to overwrite the evidence, policy, or current generation.
        target = args.output.resolve()
        protected = [args.policy.resolve(), args.evidence.resolve(),
                     *(args.package_dir.resolve() / p for p in package["artifact_hashes"])]
        if target in protected or target.exists():
            raise ValueError("Output must be a new file outside the evaluated inputs.")
        result = evaluate_acceptance(package, _read(args.policy), _read(args.evidence),
                                     report_root=args.evidence.resolve().parent)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(result["lifecycle_status"])
        if not result["acceptance"]["accepted"]:
            raise SystemExit(1)
    except (ValueError, OSError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        parser.exit(2, f"Acceptance evaluation failed: {exc}\n")


if __name__ == "__main__":
    main()
