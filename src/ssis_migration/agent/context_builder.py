from __future__ import annotations

from typing import Any


class AIMigrationContextBuilder:
    """
    Converts deterministic migration information into a compact,
    provider-independent AI migration context.

    This layer does not call an LLM.

    Source-specific analysis is preserved as evidence, while the
    resulting AI context uses generic migration concepts.
    """

    def build(
        self,
        migration_step: dict[str, Any],
        package_ir: Any | None = None,
        databases: list[Any] | None = None,
        procedure_definitions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        metadata = migration_step.get(
            "metadata",
            {},
        )

        migration_intent = metadata.get(
            "migration_intent",
            {},
        )

        migration_strategy = metadata.get(
            "migration_strategy",
            {},
        )

        source_procedures = migration_intent.get(
            "source_procedures",
            [],
        )

        destination_procedures = migration_intent.get(
            "destination_procedures",
            [],
        )

        procedure_semantics = metadata.get(
            "procedure_semantics",
            [],
        )

        destination_procedure_semantics = metadata.get(
            "destination_procedure_semantics",
            [],
        )

        data_flows = migration_intent.get(
            "data_flows",
            [],
        )

        workflow = {
            "load_type": migration_intent.get(
                "load_type"
            ),
            "control_flow_operations": migration_intent.get(
                "control_flow_operations",
                [],
            ),
            "data_flows": data_flows,
            "transformations": migration_step.get(
                "transformations",
                [],
            ),
        }

        source_context = {
            "platform": migration_step.get(
                "source_platform"
            ) or self._first_value(
                source_procedures,
                "platform",
            ),
            "objects": migration_step.get(
                "source_objects",
                [],
            ),
            "procedures": source_procedures,
            "semantics": procedure_semantics,
            "definition": self._first_definition(
                procedure_semantics,
                source_procedures,
            ),
        }

        destination_context = {
            "platform": self._first_value(
                destination_procedures,
                "platform",
            ),
            "objects": migration_step.get(
                "target_objects",
                [],
            ),
            "procedures": destination_procedures,
            "semantics": destination_procedure_semantics,
            "definition": self._first_definition(
                destination_procedure_semantics,
                destination_procedures,
            ),
        }
        # Explicit SQL definitions override incomplete metadata definitions.
        # Pass ALL procedure definitions through — the caller
        # already filtered to only the procedures relevant to
        # this migration step.
        procedure_definitions = procedure_definitions or {}

        source_context["procedure_definitions"] = dict(
            procedure_definitions
        )

        destination_context["procedure_definitions"] = dict(
            procedure_definitions
        )

        return {
            "migration": {
                "step_id": migration_step.get(
                    "step_id"
                ),
                "step_name": migration_step.get(
                    "name"
                ),
                "source_platform": self._source_platform(
                    migration_step,
                    source_context,
                ),
                "target_platform": (
                    migration_step.get("target_platform")
                    or "Databricks"
                ),
                "target_language": (
                    migration_step.get("target_language")
                    or "python"
                ),
                "strategy": migration_strategy,
            },

            "source": source_context,

            "workflow": workflow,

            "destination": destination_context,

            "requirements": {
                "preserve_source_semantics": True,
                "preserve_destination_semantics": True,
                "preserve_data_flow_mappings": True,
                "preserve_incremental_behavior": True,
                "avoid_unverified_business_logic": True,
                "generate_executable_code": True,
            },

            "evidence": {
                "migration_intent": migration_intent,
                "migration_strategy": migration_strategy,
            },

            "database_context": self._build_database_context(
                databases=databases,
                source_roots=migration_step.get(
                    "source_objects",
                    [],
                ),
                destination_roots=migration_step.get(
                    "target_objects",
                    [],
                ),
            ),

            "temporal_context": self._build_temporal_context(
                procedure_definitions,
            ),
        }

    # ============================================================
    # Generic helpers
    # ============================================================

    @staticmethod
    def _first_value(
        items: list[dict[str, Any]],
        key: str,
    ) -> Any | None:

        for item in items:

            value = item.get(key)

            if value:
                return value

        return None

    @staticmethod
    def _first_definition(
        semantics: list[dict[str, Any]],
        procedures: list[dict[str, Any]],
    ) -> str | None:

        for item in semantics:

            definition = item.get(
                "definition"
            )

            if definition:
                return definition

        for item in procedures:

            definition = item.get(
                "definition"
            )

            if definition:
                return definition

        return None

    @classmethod
    def _source_platform(
        cls,
        migration_step: dict[str, Any],
        source_context: dict[str, Any],
    ) -> str | None:

        explicit = migration_step.get(
            "source_platform"
        )

        if explicit:
            return explicit

        return source_context.get(
            "platform"
        )

    # ============================================================
    # Database context
    # ============================================================

    def _build_database_context(
        self,
        databases: list[Any] | None,
        source_roots: list[str],
        destination_roots: list[str],
    ) -> dict[str, Any]:

        if not databases:
            return {
                "source": [],
                "destination": [],
            }

        result = {
            "source": [],
            "destination": [],
        }

        for database in databases:

            database_dict = self._to_dict(
                database
            )

            relevant_objects = database_dict.get(
                "relevant_objects",
                [],
            )

            dependency_edges = database_dict.get(
                "dependency_edges",
                [],
            )

            role = database_dict.get(
                "role"
            )

            context = {
                "connection_reference": database_dict.get(
                    "connection_reference"
                ),
                "database_name": database_dict.get(
                    "database_name"
                ),
                "role": role,
                "relevant_objects": relevant_objects,
                "dependency_edges": dependency_edges,
            }

            if role == "source":
                result["source"].append(
                    context
                )

            elif role == "destination":
                result["destination"].append(
                    context
                )

        return result

    # ============================================================
    # Temporal context
    # ============================================================

    @staticmethod
    def _build_temporal_context(
        procedure_definitions: dict[str, str],
    ) -> dict[str, Any]:
        """
        Detect FOR SYSTEM_TIME AS OF in source procedures and
        provide Delta Lake translation guidance.

        SQL Server system-versioned temporal tables maintain a
        current table and a history table (with _Archive suffix
        in WideWorldImporters). Each row has ValidFrom and
        ValidTo columns defining its validity period.

        FOR SYSTEM_TIME AS OF @timestamp reconstructs the table
        state at a point in time by UNIONing current and history
        rows and filtering on the validity window.

        In Databricks, if source tables have been ingested as
        Delta tables with ValidFrom/ValidTo columns and archive
        tables, the equivalent is:

            SELECT * FROM (
                SELECT * FROM schema.table
                UNION ALL
                SELECT * FROM schema.table_Archive
            ) combined
            WHERE @timestamp >= ValidFrom
              AND @timestamp < ValidTo
        """
        import re

        all_sql = " \n".join(
            procedure_definitions.values()
        )

        temporal_pattern = re.compile(
            r"FOR\s+SYSTEM_TIME\s+AS\s+OF",
            re.IGNORECASE,
        )

        if not temporal_pattern.search(all_sql):
            return {
                "has_temporal_queries": False,
            }

        # Extract table names referenced in
        # FOR SYSTEM_TIME AS OF clauses.
        table_pattern = re.compile(
            r"(?:FROM|JOIN)\s+"
            r"(\w+\.\w+)"
            r"\s+FOR\s+SYSTEM_TIME\s+AS\s+OF",
            re.IGNORECASE,
        )

        temporal_tables = sorted(
            set(table_pattern.findall(all_sql))
        )

        return {
            "has_temporal_queries": True,
            "source_tables_with_temporal": temporal_tables,
            "validity_columns": {
                "valid_from": "ValidFrom",
                "valid_to": "ValidTo",
            },
            "archive_table_pattern": (
                "{schema}.{table}_Archive"
            ),
            "translation_guidance": (
                "FOR SYSTEM_TIME AS OF @timestamp "
                "is equivalent to querying the UNION ALL "
                "of the current table and its _Archive "
                "table, filtered by: "
                "@timestamp >= ValidFrom AND "
                "@timestamp < ValidTo. "
                "Both current and archive tables contain "
                "ValidFrom and ValidTo columns. "
                "Do NOT use Delta Lake time travel "
                "(TIMESTAMP AS OF) as a substitute — "
                "use the row-level ValidFrom/ValidTo "
                "validity window instead."
            ),
        }

    @staticmethod
    def _to_dict(
        value: Any,
    ) -> dict[str, Any]:

        if isinstance(value, dict):
            return value

        if hasattr(value, "to_dict"):
            return value.to_dict()

        return {}