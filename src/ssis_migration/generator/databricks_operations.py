from __future__ import annotations

import re
from typing import Any

from ssis_migration.generator.sql_translator import (
    SQLServerToSparkSQLTranslator,
)


class DatabricksOperationGenerator:

    # WWI schemas that live under the target catalog. Schema-qualified
    # table references in translated SQL become `{_catalog}.schema.table`
    # so the emitted f-string resolves against the notebook catalog
    # parameter.
    _CATALOG_SCHEMA_RE = re.compile(
        r"(?<![\w.`$])`?(?:Integration|Dimension|Fact|Application"
        r"|Sales|Purchasing|Warehouse)`?\s*\.\s*`?",
        re.IGNORECASE,
    )

    # Deterministic runtime parameter bindings. The notebook cell
    # defines these variables in earlier CONTROL/STAGING operations.
    _RUNTIME_PARAM_RULES = [
        (re.compile(r"lineage\s*key", re.IGNORECASE), "lineage_key", True),
        (re.compile(r"last\s*cutoff", re.IGNORECASE), "last_cutoff", False),
        (re.compile(r"new\s*cutoff", re.IGNORECASE), "new_cutoff", False),
    ]

    _RUNTIME_TOKEN_RE = re.compile(
        r"[:@]([A-Za-z_][A-Za-z0-9_]*)",
    )

    def __init__(self) -> None:
        self.translator = SQLServerToSparkSQLTranslator()

    def generate_operation(
        self,
        operation: dict[str, Any],
    ) -> str:

        operation_type = operation["operation_type"]

        handlers = {
            "CONTROL": self._generate_control,
            "STAGING": self._generate_staging,
            "EXTRACT": self._generate_extract,
            "LOOKUP_UPDATE": self._generate_lookup,
            "MERGE": self._generate_merge,
            "DELETE": self._generate_delete,
            "TRUNCATE": self._generate_truncate,
            "INSERT": self._generate_insert,
            "UPDATE": self._generate_update,
        }

        handler = handlers.get(operation_type)

        if not handler:
            raise ValueError(
                f"Unsupported operation type: {operation_type}"
            )

        return handler(operation)

    # ---------------------------------------------------------
    # CONTROL
    # ---------------------------------------------------------

    def _generate_control(
        self,
        operation: dict[str, Any],
    ) -> str:

        operation_id = operation["operation_id"]

        if operation_id == "set_table_name":
            target = (
                operation.get("target")
                or ["target_table"]
            )[0]

            return f"""
table_name = f"{{_catalog}}.{target}"
""".strip()

        if operation_id == "get_last_cutoff":
            return """
last_cutoff = get_last_etl_cutoff(
    table_name=table_name
)
""".strip()

        if operation_id == "get_lineage_key":
            return """
lineage_key = get_lineage_key(
    table_name=table_name,
    cutoff_time=new_cutoff
)
""".strip()

        return f"""
# TODO: implement control operation: {operation_id}
""".strip()

    # ---------------------------------------------------------
    # STAGING
    # ---------------------------------------------------------

    def _generate_staging(
        self,
        operation: dict[str, Any],
    ) -> str:

        if operation["operation_id"] == "clear_staging":

            target = (
                operation.get("target")
                or ["staging_table"]
            )[0]

            return f'''
spark.sql(f"""
DELETE FROM {{_catalog}}.{target}
""")
'''.strip()

        return f"""
# TODO: implement staging operation:
# {operation["operation_id"]}
""".strip()

    # ---------------------------------------------------------
    # EXTRACT
    # ---------------------------------------------------------

    def _generate_extract(
        self,
        operation: dict[str, Any],
    ) -> str:

        sql = operation.get("sql")

        if not sql:
            return """
# AI_REQUIRED
# Source procedure definition is unavailable.
# Original SSIS extraction logic must be supplied
# to the migration agent.
""".strip()

        translation = self.translator.translate(sql)

        if translation.status != "TRANSLATED":
            unsupported = translation.evidence.get(
                "unsupported_features",
                [],
            )

            unsupported_text = ", ".join(
                unsupported
            ) if unsupported else "unknown"

            return f"""
# AI_REQUIRED
#
# Source procedure cannot be deterministically translated.
#
# Translation status:
# {translation.status}
#
# Unsupported/complex features:
# {unsupported_text}
#
# The original procedure is preserved in operation metadata.
#
# The migration agent must convert this procedure
# into executable Databricks logic.
""".strip()

        translated_sql = translation.sql or ""

        emitted, unresolved = self._prepare_sql_for_notebook(
            translated_sql
        )

        # A bare SELECT (SSIS data-flow source procedure) is wrapped
        # into a staging INSERT so the extraction persists to the
        # staging table, reproducing the OLE DB Destination write.
        # The prefix is added AFTER preparation so its braces are
        # not escaped.
        if not translated_sql.lstrip().upper().startswith(
            ("INSERT", "MERGE"),
        ):
            target = (
                operation.get("target")
                or ["staging_table"]
            )[0]

            emitted = (
                "INSERT INTO {_catalog}." + target + "\n" + emitted
            )

        code = (
            'staging_extract_sql = f"""\n'
            + emitted
            + '\n"""\n'
            + "spark.sql(staging_extract_sql)"
        )

        if unresolved:
            code += (
                "\n# REVIEW_REQUIRED: unresolved runtime parameters: "
                + ", ".join(sorted(set(unresolved)))
            )

        return code

    # ---------------------------------------------------------
    # LOOKUP
    # ---------------------------------------------------------

    def _generate_lookup(
        self,
        operation: dict[str, Any],
    ) -> str:

        targets = operation.get("target", [])

        target_list = ", ".join(
            f'"{target}"'
            for target in targets
        )

        semantic = operation.get(
            "metadata",
            {},
        ).get(
            "semantic",
            {},
        )

        return f"""
dimension_targets = [
    {target_list}
]

# Resolve dimension/surrogate keys according to
# the semantic analysis of the source procedure.

# Semantic operation:
# {semantic.get("operation", "RESOLVE_DIMENSION_KEYS")}

# Required target dimensions:
# {target_list}
""".strip()

    # ---------------------------------------------------------
    # MERGE
    # ---------------------------------------------------------

    def _generate_merge(
        self,
        operation: dict[str, Any],
    ) -> str:

        return self._emit_procedure_sql(operation)


    # ---------------------------------------------------------
    # DELETE
    # ---------------------------------------------------------

    # T-SQL extension DELETE pattern:
    #   DELETE <alias> FROM <table> AS <alias>
    #   WHERE <alias>.<key> IN (SELECT <key> FROM <staging>);
    # becomes a Delta DELETE with the alias stripped.
    _DELETE_RE = re.compile(
        r"\bDELETE\s+(?P<alias>\w+)\s*\r?\n?\s*FROM\s+"
        r"(?P<table>[\w.\[\]`]+)(?:\s+AS\s+\w+)?\s+"
        r"WHERE\s+(?P<condition>.+?);",
        re.IGNORECASE | re.DOTALL,
    )

    def _generate_delete(
        self,
        operation: dict[str, Any],
    ) -> str:

        target = (
            operation.get("target")
            or ["target_table"]
        )[0]

        semantic = operation.get(
            "metadata",
            {},
        ).get(
            "semantic",
            {},
        )

        metadata = operation.get("metadata", {})

        definition = metadata.get("procedure_definition")

        if definition:
            match = self._DELETE_RE.search(definition)

            if match:
                alias = re.escape(match.group("alias"))

                condition = match.group("condition")

                # Strip the T-SQL alias qualifier; the emitted
                # Delta DELETE targets the table directly.
                condition = re.sub(
                    rf"\b{alias}\.",
                    "",
                    condition,
                )

                # Bracketed identifiers become backticks.
                condition = re.sub(
                    r"\[([^\]]+)\]",
                    r"`\1`",
                    condition,
                )

                emitted, unresolved = self._prepare_sql_for_notebook(
                    condition
                )

                sql = (
                    "DELETE FROM {_catalog}." + target
                    + "\nWHERE " + emitted
                )

                code = (
                    'delete_target_sql = f"""\n'
                    + sql
                    + '\n"""\n'
                    + "spark.sql(delete_target_sql)"
                )

                if unresolved:
                    code += (
                        "\n# REVIEW_REQUIRED: unresolved runtime "
                        "parameters: "
                        + ", ".join(sorted(set(unresolved)))
                    )

                return code

        return f"""
# REVIEW_REQUIRED
#
# No deterministically translatable DELETE statement is available.
#
# Target:
# {target}
#
# Semantic analysis:
# {semantic}
""".strip()

    # ---------------------------------------------------------
    # TRUNCATE
    # ---------------------------------------------------------

    def _generate_truncate(
        self,
        operation: dict[str, Any],
    ) -> str:

        target = (
            operation.get("target")
            or ["target_table"]
        )[0]

        # Backtick the table name so multi-word WWI table
        # names (e.g. "Fact.Stock Holding") remain valid
        # Spark SQL after catalog qualification.
        if "." in target:
            schema, _, table = target.rpartition(".")
            qualified = f"{schema}.`{table}`"
        else:
            qualified = f"`{target}`"

        return f'''
spark.sql(f"""
TRUNCATE TABLE {{_catalog}}.{qualified}
""")
'''.strip()

    # ---------------------------------------------------------
    # INSERT
    # ---------------------------------------------------------

    def _generate_insert(
        self,
        operation: dict[str, Any],
    ) -> str:

        return self._emit_procedure_sql(operation)

    # ---------------------------------------------------------
    # PROCEDURE-SQL EMISSION (INSERT / MERGE)
    # ---------------------------------------------------------

    def _emit_procedure_sql(
        self,
        operation: dict[str, Any],
    ) -> str:
        """
        Emit executable Spark SQL from the destination procedure
        definition preserved in operation metadata.

        Falls back to an explicit REVIEW placeholder when the
        definition is missing or cannot be deterministically
        translated — the generator never fabricates SQL.
        """

        metadata = operation.get("metadata", {})

        definition = metadata.get("procedure_definition")

        target = (
            operation.get("target")
            or ["target_table"]
        )[0]

        semantic = metadata.get("semantic", {})

        if not definition:
            return f"""
# REVIEW_REQUIRED
#
# No destination procedure definition is available.
#
# Target:
# {target}
#
# Semantic analysis:
# {semantic}
""".strip()

        translation = self.translator.translate(definition)

        if translation.status != "TRANSLATED":
            unsupported = translation.evidence.get(
                "unsupported_features",
                [],
            )

            unsupported_text = ", ".join(
                unsupported
            ) if unsupported else "unknown"

            return f"""
# REVIEW_REQUIRED
#
# Destination procedure cannot be deterministically translated.
#
# Target:
# {target}
#
# Translation status:
# {translation.status}
#
# Unsupported/complex features:
# {unsupported_text}
#
# Semantic analysis:
# {semantic}
#
# The original procedure is preserved in operation metadata.
""".strip()

        emitted, unresolved = self._prepare_sql_for_notebook(
            translation.sql or ""
        )

        operation_id = operation.get("operation_id") or "operation"
        sql_var = f"{operation_id}_sql"

        code = (
            f'{sql_var} = f"""\n'
            + emitted
            + '\n"""\n'
            + f"spark.sql({sql_var})"
        )

        if unresolved:
            code += (
                "\n# REVIEW_REQUIRED: unresolved runtime parameters: "
                + ", ".join(sorted(set(unresolved)))
            )

        return code

    # ---------------------------------------------------------
    # NOTEBOOK SQL PREPARATION
    # ---------------------------------------------------------

    def _prepare_sql_for_notebook(
        self,
        sql: str,
    ) -> tuple[str, list[str]]:
        """
        Prepare translated SQL for embedding in a notebook f-string.

        Order matters:

        1. Escape literal braces (the SQL will live in an f-string).
        2. Qualify schema references with the catalog parameter.
        3. Bind runtime parameters to notebook cell variables.
        """

        prepared = sql.replace("{", "{{").replace("}", "}}")

        prepared = self._qualify_catalog(prepared)

        prepared, unresolved = self._bind_runtime_params(prepared)

        return prepared, unresolved

    @classmethod
    def _qualify_catalog(
        cls,
        sql: str,
    ) -> str:

        return cls._CATALOG_SCHEMA_RE.sub(
            "{_catalog}." + "\\g<0>",
            sql,
        )

    @classmethod
    def _bind_runtime_params(
        cls,
        sql: str,
    ) -> tuple[str, list[str]]:

        unresolved: list[str] = []

        def replace(match: re.Match) -> str:
            token = match.group(1)

            for rule, variable, numeric in cls._RUNTIME_PARAM_RULES:
                if rule.search(token):
                    if numeric:
                        return "{" + variable + "}"
                    return "'{" + variable + "}'"

            unresolved.append(token)
            return match.group(0)

        bound = cls._RUNTIME_TOKEN_RE.sub(replace, sql)

        return bound, unresolved

    # ---------------------------------------------------------
    # UPDATE
    # ---------------------------------------------------------

    def _generate_update(
        self,
        operation: dict[str, Any],
    ) -> str:

        operation_id = operation["operation_id"]

        if operation_id == "complete_lineage":
            return """
# Mark lineage record as successfully completed.
complete_lineage(lineage_key)
""".strip()

        if operation_id == "update_etl_cutoff":
            return """
# Advance ETL cutoff after successful load.
update_etl_cutoff(
    table_name=table_name,
    cutoff_time=new_cutoff
)
""".strip()

        return f"""
# TODO: implement update operation: {operation_id}
""".strip()


def generate_databricks_operations(
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    generator = DatabricksOperationGenerator()

    generated = []

    for operation in operations:

        code = generator.generate_operation(
            operation
        )

        generated.append(
            {
                "operation_id":
                    operation["operation_id"],
                "operation_type":
                    operation["operation_type"],
                "name":
                    operation["name"],
                "order":
                    operation["order"],
                "dependencies":
                    operation.get(
                        "dependencies",
                        [],
                    ),
                "code":
                    code,
                "metadata":
                    operation.get(
                        "metadata",
                        {},
                    ),
            }
        )

    return generated