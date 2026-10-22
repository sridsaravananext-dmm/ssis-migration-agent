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
        from ssis_migration.migration import prepare_generation_step

        migration_step = prepare_generation_step(migration_step)
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
        contract = metadata.get("migration_contract") or {}
        supplemental = contract.get("source_behavior", {}).get("supporting_definitions", {})
        procedure_definitions = {**supplemental, **(procedure_definitions or {})}

        source_context["procedure_definitions"] = dict(
            procedure_definitions
        )

        destination_context["procedure_definitions"] = dict(
            procedure_definitions
        )

        # Inspect every definition actually supplied to the model, not just
        # the optional override argument. Keep provenance when definitions
        # already arrived through migration intent or semantic analysis.
        temporal_definitions = dict(procedure_definitions)
        # Retain supplemental evidence for analysis even when a same-named
        # legacy definition was supplied. The contract records conflicts;
        # an override must not erase a history prerequisite from either input.
        temporal_definitions.update({f"supplemental:{name}": body for name, body in supplemental.items()})
        for role, context in (("source", source_context), ("destination", destination_context)):
            for collection in ("procedures", "semantics"):
                for index, item in enumerate(context[collection]):
                    definition = item.get("definition")
                    if definition:
                        name = item.get("procedure") or item.get("name") or str(index)
                        temporal_definitions[f"{role}.{collection}:{name}"] = definition

        return {
            "migration_contract": metadata.get("migration_contract"),
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
                ) if not contract else contract["source_platform"],
                "target_platform": (
                    contract.get("target_platform") or migration_step.get("target_platform")
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
                temporal_definitions,
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

        result = {
            "source": [],
            "destination": [],
            "combined": [],
            "unassigned": [],
        }

        for database in databases or []:

            database_dict = self._to_dict(
                database
            )

            # Relevant-object lists can be incomplete or empty while the
            # supplied DatabaseIR still contains columns, types and keys.
            # Preserve that structure and its evidence instead of silently
            # reducing it to an inferred subset.
            context = dict(database_dict)
            context.setdefault("connection_reference", None)
            context.setdefault("database_name", None)
            context.setdefault("role", None)
            context.setdefault("relevant_objects", [])
            context.setdefault("dependency_edges", [])

            # DTSX-derived metadata has the explicit role "combined". Unknown
            # roles remain unassigned with their original value, rather than
            # being guessed from object names or silently discarded.
            role = context["role"]
            bucket = role if isinstance(role, str) and role in result else "unassigned"
            result[bucket].append(context)

        return result

    # ============================================================
    # Temporal context
    # ============================================================

    @staticmethod
    def _build_temporal_context(
        procedure_definitions: dict[str, str],
    ) -> dict[str, Any]:
        """Record temporal syntax without inventing its historical data model.

        A temporal query proves that historical state is needed. It does not
        identify period columns, a history table or a target representation.
        Token offsets retain the procedure evidence and avoid matching SQL
        comments or string literals as temporal queries.
        """
        import sqlglot
        from sqlglot.errors import TokenError
        from sqlglot.tokens import TokenType

        temporal_tables: set[str] = set()
        evidence: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for name, sql in procedure_definitions.items():
            try:
                tokens = sqlglot.tokenize(sql, read="tsql")
            except TokenError as exc:
                unresolved.append(
                    f"Cannot inspect temporal syntax in procedure {name}: {exc}"
                )
                continue
            for index in range(len(tokens) - 3):
                clause = tokens[index:index + 4]
                if [token.text.upper() for token in clause] != [
                    "FOR", "SYSTEM_TIME", "AS", "OF",
                ] or any(token.token_type in {
                    TokenType.STRING, TokenType.IDENTIFIER,
                } for token in clause):
                    continue
                evidence.append({
                    "procedure": name,
                    "start_offset": clause[0].start,
                    "end_offset": clause[-1].end + 1,
                    "source_text": sql[clause[0].start:clause[-1].end + 1],
                })
                # Only report a directly preceding FROM/JOIN table identifier.
                # More complex references remain unknown rather than guessed.
                cursor = index - 1
                names: list[str] = []
                while cursor >= 0 and tokens[cursor].token_type in {
                    TokenType.VAR, TokenType.IDENTIFIER, TokenType.DOT,
                }:
                    names.append(tokens[cursor].text)
                    cursor -= 1
                if cursor >= 0 and tokens[cursor].text.upper() in {"FROM", "JOIN"}:
                    temporal_tables.add("".join(reversed(names)))

        if evidence:
            unresolved.append(
                "Temporal queries require a supplied, verified historical-data "
                "mapping with row keys, period columns, history sources and "
                "target representations. Temporal syntax alone does not supply it."
            )

        return {
            "has_temporal_queries": True if evidence else (None if unresolved else False),
            "source_tables_with_temporal": sorted(temporal_tables),
            "validity_columns": {},
            "archive_table_pattern": None,
            "historical_data_status": "UNVERIFIED" if evidence or unresolved else "NOT_REQUIRED",
            "evidence": evidence,
            "unresolved_items": unresolved,
            "translation_guidance": (
                "Use only explicitly supplied historical-data mappings. Do not "
                "infer history-table names or validity columns from temporal "
                "syntax, and do not substitute current-state data or Delta "
                "time travel without evidence of equivalent behavior."
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
