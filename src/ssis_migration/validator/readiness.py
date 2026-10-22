"""Static generation evidence, deliberately separate from migration acceptance.

No function in this module executes generated Python, SQL, or target workloads.
Passing these checks establishes only static validity of the supplied artifacts;
it cannot establish source behavior coverage or data equivalence.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from typing import Any

from ssis_migration.agent.code_validator import validate_generated_code


_PENDING_MARKERS = re.compile(
    r"\b(?:AI_REQUIRED|REVIEW_REQUIRED|TODO|placeholder)\b", re.IGNORECASE
)
_REJECTED_STATUSES = {"REJECTED", "FAIL", "FAILED", "ERROR"}
_PENDING_STATUSES = {
    "REVIEW_REQUIRED", "UNRESOLVED", "BLOCKED", "UNSUPPORTED",
    "NOT_IMPLEMENTED", "MOCK_RESPONSE", "SKIPPED", "NOT_RUN",
}


def _issue(code: str, message: str, severity: str = "WARNING") -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _is_spark_sql(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "sql"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "spark"
    )


def validate_operations(
    operations_by_step: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Validate operation code and literal ``spark.sql`` statements statically.

    Pending implementation markers are review results, never passing code.
    SQL assembled at runtime is explicitly unverified, even when its Python
    container compiles. The caller must supply separate binding/SQL evidence
    before treating that operation as validated.
    """
    records: list[dict[str, Any]] = []
    for step_id, operations in operations_by_step.items():
        for operation in operations:
            record: dict[str, Any] = {
                "step_id": step_id,
                "step_name": operation.get("step_name") or step_id,
                "operation_id": operation.get("operation_id", "unknown"),
                "validator": "operation_code",
                "scope": "STATIC_CODE",
                "status": "PASS",
                "issues": [],
            }
            records.append(record)
            issues = record["issues"]
            if "error" in operation:
                issues.append(_issue(
                    "OPERATION_GENERATION_ERROR",
                    str(operation["error"]),
                    "ERROR",
                ))
                record["status"] = "REJECTED"
                continue

            code = operation.get("code", "")
            if not isinstance(code, str):
                issues.append(_issue(
                    "INVALID_OPERATION_CODE", "Operation code must be text.", "ERROR"
                ))
                record["status"] = "REJECTED"
                continue
            if not code.strip():
                issues.append(_issue("EMPTY_OPERATION", "Operation has no generated code."))
                record["status"] = "REVIEW_REQUIRED"
                continue

            if _PENDING_MARKERS.search(code):
                issues.append(_issue(
                    "PENDING_IMPLEMENTATION",
                    "Operation contains an explicit pending implementation or review marker.",
                ))

            try:
                tree = ast.parse(code)
                compile(tree, filename="<generated_operation>", mode="exec")
            except (SyntaxError, ValueError, TypeError) as exc:
                issues.append(_issue("PYTHON_SYNTAX", str(exc), "ERROR"))
                record["status"] = "REJECTED"
                continue

            if not tree.body:
                issues.append(_issue(
                    "COMMENT_ONLY_OPERATION", "Operation contains comments but no implementation."
                ))
                record["status"] = "REVIEW_REQUIRED"
                continue

            # An individual control operation is not a whole migration step.
            # Do not derive business requirements from the surrounding step name.
            python_result = validate_generated_code(code, language="pyspark")
            for finding in python_result.get("issues", []):
                finding = dict(finding)
                if finding.get("code") in {"TODO_PLACEHOLDER", "PLACEHOLDER_TEXT"}:
                    finding["severity"] = "WARNING"
                issues.append(finding)

            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                if not _is_spark_sql(call):
                    continue
                argument = call.args[0] if call.args else next(
                    (kw.value for kw in call.keywords if kw.arg == "sqlQuery"), None
                )
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    sql_result = validate_generated_code(
                        argument.value, language="spark_sql"
                    )
                    issues.extend(sql_result.get("issues", []))
                else:
                    issues.append(_issue(
                        "DYNAMIC_SQL_UNVERIFIED",
                        "Runtime SQL text or bindings cannot be verified by static Python checks.",
                    ))

            if any(finding.get("severity") == "ERROR" for finding in issues):
                record["status"] = "REJECTED"
            elif issues:
                record["status"] = "REVIEW_REQUIRED"
    return records


def _walk_results(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_results(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_results(child)


def _record_problems(record: dict[str, Any]) -> tuple[str, list[str]]:
    """Inspect nested AI/static results so an outer status cannot hide failure."""
    errors: list[str] = []
    pending: list[str] = []
    nested_results = list(_walk_results(record))
    for result in nested_results:
        status = str(result.get("status", "")).upper()
        if status in _REJECTED_STATUSES:
            errors.append(f"Validation result is {status}.")
        elif status in _PENDING_STATUSES:
            pending.append(f"Validation result is {status}.")

        for key in ("unresolved_items", "ai_unresolved"):
            unresolved = result.get(key) or []
            if isinstance(unresolved, str):
                unresolved = [unresolved]
            pending.extend(str(item) for item in unresolved)
        failures = result.get("failures") or []
        if isinstance(failures, str):
            failures = [failures]
        errors.extend(str(item) for item in failures)

        severity = str(result.get("severity", "")).upper()
        if severity in {"ERROR", "WARNING"}:
            message = str(result.get("message") or result.get("code") or severity)
            (errors if severity == "ERROR" else pending).append(message)

    status = str(record.get("status", "")).upper()
    if status == "GENERATED":
        # Generation alone is not proof that independent static checks ran.
        static_results = [
            result[key]
            for result in nested_results
            for key in ("code_validation", "static_code_validation")
            if isinstance(result.get(key), dict)
        ]
        known_static_statuses = {"PASS", "SUCCEEDED", "VALIDATED"} | _REJECTED_STATUSES | _PENDING_STATUSES
        has_static_result = any(
            str(result.get("status", "")).upper() in known_static_statuses
            for result in static_results
        )
        if not has_static_result:
            pending.append("Generated code has no independent static validation result.")
    elif status not in {"PASS", "SUCCEEDED", "VALIDATED"} | _REJECTED_STATUSES | _PENDING_STATUSES:
        pending.append(f"Unrecognized or missing validation status: {status or 'missing'}.")

    if errors:
        return "REJECTED", [*errors, *pending]
    if pending:
        return "REVIEW_REQUIRED", pending
    return "PASS", []


def summarize_readiness(
    validation_results: list[dict[str, Any]],
    *,
    unresolved_items: Iterable[str] = (),
    generated_artifact_count: int = 0,
) -> dict[str, Any]:
    """Describe static readiness without claiming behavior or runtime acceptance.

    Counts below measure validation records, not source behaviors or weighted
    semantic coverage. Unknown coverage stays unknown rather than becoming 0%
    or 100% based on artifact counts.
    """
    counts = {"PASS": 0, "REVIEW_REQUIRED": 0, "REJECTED": 0}
    blockers = [str(item) for item in unresolved_items]
    for record in validation_results:
        status, problems = _record_problems(record)
        counts[status] += 1
        identity = record.get("operation_id") or record.get("step_id") or record.get("validator", "validation")
        blockers.extend(f"{identity}: {problem}" for problem in problems)

    if generated_artifact_count <= 0:
        blockers.append("No migration artifacts were generated.")
    if not validation_results:
        blockers.append("No independent static validation results were supplied.")
    blockers = list(dict.fromkeys(blockers))
    blocked = bool(blockers)
    return {
        "result_version": "1.0",
        "lifecycle_status": "BLOCKED" if blocked else "VALIDATED",
        "validation_scope": "STATIC_ONLY",
        "blocking_issues": blockers,
        "acceptance": {
            "status": "BLOCKED" if blocked else "NOT_EVALUATED",
            "accepted": False,
            "required_semantic_coverage_percent": 80,
            "reason": (
                "Static validation cannot establish verified source behavior, "
                "execution correctness or data equivalence."
            ),
        },
        "coverage": {
            "structural": {
                "status": "BLOCKED" if blocked else "VALIDATED",
                "scope": "STATIC_VALIDATION_RECORDS",
                "total_checks": len(validation_results),
                "passed_checks": counts["PASS"],
                "review_required_checks": counts["REVIEW_REQUIRED"],
                "rejected_checks": counts["REJECTED"],
            },
            "semantic": {"status": "NOT_VERIFIED", "verified_percent": None},
            "execution": {"status": "NOT_RUN", "verified_percent": None},
            "data_equivalence": {"status": "NOT_RUN", "verified_percent": None},
        },
    }
