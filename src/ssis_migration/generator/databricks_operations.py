from __future__ import annotations

from typing import Any

from ssis_migration.generator.sql_translator import (
    SQLServerToSparkSQLTranslator,
)


class DatabricksOperationGenerator:

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
table_name = "{target}"
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
spark.sql("""
DELETE FROM {target}
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

        return f'''
source_df = spark.sql("""
{translated_sql}
""")
'''.strip()

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

        target = (
            operation.get("target")
            or ["target_table"]
        )[0]

        metadata = operation.get(
            "metadata",
            {},
        )

        semantic = metadata.get(
            "semantic",
            {},
        )

        return f"""
# TODO: generate executable Delta MERGE.
#
# Target:
# {target}
#
# Semantic analysis:
# {semantic}
#
# The source procedure definition is preserved
# in operation metadata for deterministic generation
# or AI-assisted refinement.
""".strip()

    # ---------------------------------------------------------
    # DELETE
    # ---------------------------------------------------------

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

        return f"""
# TODO: generate executable DELETE.
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

        return f'''
spark.sql("""
TRUNCATE TABLE {target}
""")
'''.strip()

    # ---------------------------------------------------------
    # INSERT
    # ---------------------------------------------------------

    def _generate_insert(
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

        return f"""
# TODO: generate executable INSERT.
#
# Target:
# {target}
#
# Semantic analysis:
# {semantic}
""".strip()

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