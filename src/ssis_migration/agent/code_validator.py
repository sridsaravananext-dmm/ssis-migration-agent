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
    *,
    language: str = "pyspark",
) -> dict[str, Any]:
    """
    Static checks for generated Databricks SQL or Python/PySpark.

    This function never executes generated code.
    A PASS means only that these static checks passed.
    """
    issues: list[ValidationIssue] = []

    normalized_language = (
        language.strip().lower().replace("-", "_").replace(" ", "_")
        if isinstance(language, str) else ""
    )
    if normalized_language in {"sql", "spark_sql", "databricks_sql", "databricks"}:
        return _validate_sql(code)
    if normalized_language not in {"python", "py", "pyspark"}:
        return {
            "status": "REJECTED",
            "issues": [asdict(ValidationIssue(
                "UNSUPPORTED_LANGUAGE", "ERROR",
                f"No static validator is available for language {language!r}.",
            ))],
        }

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

    if not tree.body:
        add("EMPTY_IMPLEMENTATION", "ERROR", "Generated Python contains no executable statements.")

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

    # Temporal obligations come from source evidence, never a task's name.
    if any(re.search(r"\bFOR\s+SYSTEM_TIME\s+AS\s+OF\b", value, re.I)
           for value, _ in string_literals):
        add(
            "SQL_SERVER_TEMPORAL_SYNTAX", "ERROR",
            "SQL Server temporal syntax remains in generated code; supply a "
            "verified historical-data representation before translating it.",
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


def _validate_sql(code: str) -> dict[str, Any]:
    """Parse the declared target dialect, without executing any SQL.

    SQLGlot's generic Command fallback and bare expressions do not prove that
    a target statement is understood. Reject them rather than granting credit.
    This is syntax/structure evidence only, not execution or equivalence.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ErrorLevel, SqlglotError
    from sqlglot.tokens import TokenType

    issues: list[ValidationIssue] = []
    try:
        statements = [statement for statement in sqlglot.parse(
            code, read="databricks", error_level=ErrorLevel.RAISE,
        ) if statement is not None]
    except (SqlglotError, TypeError, ValueError) as exc:
        return {
            "status": "REJECTED",
            "issues": [asdict(ValidationIssue("SQL_SYNTAX", "ERROR", str(exc)))],
        }
    if not statements:
        issues.append(ValidationIssue("EMPTY_CODE", "ERROR", "Generated SQL contains no statements."))
    for statement in statements:
        if statement.find(exp.Command) is not None or not isinstance(
            statement, (exp.Query, exp.DML, exp.DDL),
        ):
            issues.append(ValidationIssue(
                "UNSUPPORTED_SQL", "ERROR",
                f"Target SQL statement is not structurally understood: {type(statement).__name__}.",
            ))
        if any(
            isinstance(node, (exp.Delete, exp.Update, exp.Merge, exp.Drop, exp.TruncateTable))
            or (isinstance(node, exp.Create) and node.args.get("replace"))
            or (isinstance(node, exp.Insert) and node.args.get("overwrite"))
            for node in statement.walk()
        ):
            issues.append(ValidationIssue(
                "SQL_WRITE_REVIEW", "WARNING",
                "SQL can replace, update or remove target data; verify the source behavior and execution scope.",
            ))
    if re.search(r"\b(?:AI_REQUIRED|REVIEW_REQUIRED|TODO|placeholder)\b", code, re.I):
        issues.append(ValidationIssue(
            "INCOMPLETE_SQL", "ERROR", "Generated SQL contains an unresolved implementation marker.",
        ))
    tokens = sqlglot.tokenize(code, read="databricks")
    if any(
        [token.text.upper() for token in tokens[index:index + 4]]
        == ["FOR", "SYSTEM_TIME", "AS", "OF"]
        and all(token.token_type not in {TokenType.STRING, TokenType.IDENTIFIER}
                for token in tokens[index:index + 4])
        for index in range(len(tokens) - 3)
    ):
        issues.append(ValidationIssue(
            "SQL_SERVER_TEMPORAL_SYNTAX", "ERROR",
            "Source temporal syntax requires an explicitly verified target historical-data mapping.",
        ))
    return {
        "status": "REJECTED" if any(issue.severity == "ERROR" for issue in issues)
        else ("REVIEW_REQUIRED" if issues else "PASS"),
        "issues": [asdict(issue) for issue in issues],
    }
