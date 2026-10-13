from __future__ import annotations

import ast
import re
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ValidationIssue:
    code: str
    severity: str
    message: str
    line: int | None = None


def _call_name(node: ast.AST) -> str:
    """Return a dotted function/method name from an AST call."""
    parts: list[str] = []
    current = node

    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value

    if isinstance(current, ast.Name):
        parts.append(current.id)

    return ".".join(reversed(parts))


def validate_generated_code(
    code: str,
    step_name: str = "",
) -> dict[str, Any]:
    """
    Static checks for generated SSIS-to-Databricks Python.

    This function never executes generated code.
    A PASS means only that these static checks passed.
    """
    issues: list[ValidationIssue] = []

    def add(
        code: str,
        severity: str,
        message: str,
        line: int | None = None,
    ) -> None:
        issues.append(
            ValidationIssue(
                code=code,
                severity=severity,
                message=message,
                line=line,
            )
        )

    if not code or not code.strip():
        add("EMPTY_CODE", "ERROR", "Generated code is empty.")
        return {
            "status": "REJECTED",
            "issues": [asdict(issue) for issue in issues],
        }

    # 1. Syntax and compilation checks. Do not execute the code.
    try:
        tree = ast.parse(code)
        compile(tree, filename="<generated_migration>", mode="exec")
    except (SyntaxError, ValueError, TypeError) as exc:
        add(
            "PYTHON_SYNTAX",
            "ERROR",
            f"Generated Python has a syntax/compile error: {exc}",
            getattr(exc, "lineno", None),
        )
        return {
            "status": "REJECTED",
            "issues": [asdict(issue) for issue in issues],
        }

    imported_modules: set[str] = set()
    imported_names: set[str] = set()
    called_names: list[tuple[str, int | None]] = []
    string_literals: list[tuple[str, int | None]] = []
    calls: list[ast.Call] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
                imported_names.add(alias.asname or alias.name.split(".")[0])

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_modules.add(module)
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)

        elif isinstance(node, ast.Call):
            calls.append(node)
            called_names.append((_call_name(node.func), node.lineno))

        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            string_literals.append((node.value, getattr(node, "lineno", None)))

    
    # Reject explicit placeholders and incomplete implementations.
    placeholder_patterns = [
        (
            r"\bNotImplementedError\b",
            "NOT_IMPLEMENTED",
            "Generated code contains NotImplementedError.",
        ),
        (
            r"\bTODO\b",
            "TODO_PLACEHOLDER",
            "Generated code contains a TODO placeholder.",
        ),
        (
            r"\bpass\s*(?:#.*)?$",
            "EMPTY_PASS",
            "Generated code contains a pass statement; review whether "
            "required migration logic is missing.",
        ),
        (
            r"\bOmitted for brevity\b",
            "OMITTED_LOGIC",
            "Generated code explicitly omits implementation logic.",
        ),
        (
            r"\bplaceholder\b",
            "PLACEHOLDER_TEXT",
            "Generated code references placeholder logic.",
        ),
    ]

    for pattern, issue_code, message in placeholder_patterns:
        for match in re.finditer(
            pattern,
            code,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            line = code.count("\n", 0, match.start()) + 1
            add(issue_code, "ERROR", message, line)

    # 2. Prohibit legacy SQL Server procedure execution / connectivity
    # in generated transformation code.
    for module in sorted(imported_modules):
        root = module.split(".")[0].lower()
        if root == "pyodbc":
            add(
                "LEGACY_CONNECTOR",
                "ERROR",
                "Generated code imports pyodbc. Translate the transformation "
                "to native Spark logic instead of calling the legacy SQL Server "
                "procedure at runtime.",
            )

    for name, line in called_names:
        lowered = name.lower()

        if lowered in {
            "pandas.read_sql",
            "pd.read_sql",
            "read_sql",
            "read_sql_query",
        }:
            add(
                "SQL_PASSTHROUGH",
                "ERROR",
                "Generated code calls read_sql; verify it is not reusing "
                "the original ETL stored procedure as a pass-through.",
                line,
            )

        if lowered.endswith(".execute") or lowered.endswith(".executemany"):
            add(
                "RAW_SQL_EXECUTION",
                "WARNING",
                "Found execute/executemany call. Review whether this invokes "
                "legacy ETL logic instead of implementing the transformation.",
                line,
            )

    legacy_proc_pattern = re.compile(
        r"\b(?:EXEC(?:UTE)?\s+)?"
        r"(?:\[?Integration\]?\s*\.\s*)?"
        r"\[?(?:GetCityUpdates|MigrateStagedCityData)\]?\b",
        flags=re.IGNORECASE,
    )

    for value, line in string_literals:
        if legacy_proc_pattern.search(value):
            add(
                "LEGACY_PROCEDURE_REFERENCE",
                "ERROR",
                "A string literal references GetCityUpdates or "
                "MigrateStagedCityData. Generated code must translate the "
                "procedure logic, not invoke the original procedure.",
                line,
            )

    # 3. Flag destructive table writes for human review.
    for call in calls:
        name = _call_name(call.func).lower()

        if name.endswith(".saveastable"):
            mode_values: list[str] = []

            for keyword in call.keywords:
                if keyword.arg == "mode":
                    if (
                        isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        mode_values.append(keyword.value.value.lower())

            if "overwrite" in mode_values:
                add(
                    "DESTRUCTIVE_OVERWRITE",
                    "ERROR",
                    "saveAsTable(mode='overwrite') may replace existing "
                    "staging or target data. Require an explicit, verified "
                    "replacement strategy before accepting this code.",
                    call.lineno,
                )

        if name.endswith(".insertinto"):
            add(
                "TABLE_INSERT_REVIEW",
                "WARNING",
                "insertInto found. Verify schema ordering, duplicate behavior, "
                "and whether append/overwrite matches the source procedure.",
                call.lineno,
            )

    # 4. City SCD/temporal checks. These are intentionally conservative:
    # they flag patterns to review, rather than claiming semantic proof.
    if "city" in step_name.lower():
        code_lower = code.lower()

        if "valid to" in code_lower and "lead(" not in code_lower:
            add(
                "TEMPORAL_NEXT_VERSION",
                "ERROR",
                "City temporal logic references Valid To but does not visibly "
                "use lead() to calculate the next Valid From. Review the "
                "end-of-time fallback and per-city ordering.",
            )

        if "for system_time as of" in code_lower:
            add(
                "SQL_SERVER_TEMPORAL_SYNTAX",
                "ERROR",
                "SQL Server FOR SYSTEM_TIME AS OF syntax appears in generated "
                "code. Translate it to an explicitly verified historical-data "
                "representation in Spark.",
            )

        history_markers = (
            "validfrom",
            "valid_from",
            "validto",
            "valid_to",
        )
        if not any(marker in code_lower for marker in history_markers):
            # AI_ASSISTED artifacts are migration contracts,
            # not final implementations. Downgrade to WARNING
            # so the contract is not REJECTED for missing column
            # names that are expected to be added during
            # AI-assisted completion.
            is_ai_contract = code.lstrip().startswith(
                "# AI_REQUIRED"
            )
            add(
                "TEMPORAL_HISTORY_NOT_EVIDENT",
                "WARNING" if is_ai_contract else "ERROR",
                "No recognizable ValidFrom/ValidTo history "
                "fields were found. Confirm that the required "
                "source history is available before claiming "
                "temporal equivalence.",
            )

    # 5. Lightweight import check for the known failures in the generated
    # City code. This is deliberately targeted, not a general name resolver.
    if "row_number(" in code and "row_number" not in imported_names:
        add(
            "MISSING_ROW_NUMBER_IMPORT",
            "ERROR",
            "row_number() is used but row_number is not imported.",
        )

    if re.search(r":\s*Column\s*=", code) and "Column" not in imported_names:
        add(
            "MISSING_COLUMN_IMPORT",
            "ERROR",
            "Column is used in a type annotation but is not imported.",
        )

    # Deduplicate identical issues while preserving order.
    unique: list[ValidationIssue] = []
    seen: set[tuple[str, str, str, int | None]] = set()

    for issue in issues:
        key = (issue.code, issue.severity, issue.message, issue.line)
        if key not in seen:
            seen.add(key)
            unique.append(issue)

    has_errors = any(issue.severity == "ERROR" for issue in unique)

    return {
        "status": "REJECTED" if has_errors else (
            "REVIEW_REQUIRED" if unique else "PASS"
        ),
        "issues": [asdict(issue) for issue in unique],
    }
