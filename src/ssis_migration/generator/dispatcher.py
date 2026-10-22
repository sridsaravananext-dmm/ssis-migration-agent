from __future__ import annotations

from typing import Any

from ssis_migration.strategy.language_selector import (
    select_databricks_language,
)

from .models import GeneratedArtifact
from .pyspark import PySparkGenerator
from .spark_sql import SparkSQLGenerator


class GeneratorDispatcher:

    def __init__(self) -> None:
        self._generators = {
            "spark_sql": SparkSQLGenerator(),
            "pyspark": PySparkGenerator(),
        }

    def generate(
    self,
    migration_step: dict[str, Any],
    sql: str | None = None,
    *,
    target_platform: str = "databricks",
    ) -> GeneratedArtifact:

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
        contract = metadata.get("migration_contract")
        if contract and target_platform.lower() != contract["target_platform"]:
            raise ValueError("Target platform override conflicts with the validated migration contract.")
        if contract and sql is not None:
            source_procedures = migration_intent.get("source_procedures", [])
            original_sql = source_procedures[0].get("definition") if source_procedures else None
            if sql != original_sql:
                raise ValueError("SQL override is not bound to the migration contract; rebuild its source inputs.")
        if contract and "csharp_translation" in metadata:
            from .csharp import generate_csharp_candidate
            if sql is not None:
                raise ValueError("SQL overrides are not valid for a C# source contract.")
            return generate_csharp_candidate(contract)
        if contract and contract["implementation_strategy"]["decision"] == "UNSUPPORTED":
            return GeneratedArtifact(
                artifact_id=f"{migration_step['step_id']}_unsupported",
                name=migration_step["name"], artifact_type="UNRESOLVED",
                target_platform=target_platform, language="pyspark",
                code="# REVIEW_REQUIRED: Contract explicitly marks source behavior unsupported.",
                source_step_id=migration_step["step_id"],
                metadata={"migration_contract_id": contract["contract_id"], "status": "UNSUPPORTED"},
            )

        # ---------------------------------------------------------
        # Resolve source SQL
        # ---------------------------------------------------------

        if not sql:

            source_procedures = migration_intent.get(
                "source_procedures",
                [],
            )

            if source_procedures:

                sql = source_procedures[0].get(
                    "definition"
                )

        # ---------------------------------------------------------
        # Existing deterministic language selection
        # ---------------------------------------------------------

        decision = select_databricks_language(
            sql,
            target_platform=target_platform,
        )

        # ---------------------------------------------------------
        # Migration strategy overrides generic language selection
        #
        # AI_ASSISTED must never accidentally fall through to a
        # deterministic generator.
        # ---------------------------------------------------------

        strategy = migration_strategy.get(
            "strategy"
        )

        requires_ai = migration_strategy.get(
            "requires_ai",
            False,
        )

        if strategy == "AI_ASSISTED" or requires_ai:

            language = "pyspark"

        else:

            language = decision.language

        generator = self._generators.get(
            language
        )

        if generator is None:

            step_id = migration_step["step_id"]

            return GeneratedArtifact(
                artifact_id=f"{step_id}_unresolved",
                name=f"{migration_step['name']}_unresolved",
                artifact_type="UNRESOLVED",
                target_platform=target_platform,
                language=language,
                code="",
                source_step_id=step_id,
                metadata={
                    "status": "UNRESOLVED_LANGUAGE",
                    "migration_strategy": migration_strategy,
                    "language_decision": decision.to_dict(),
                    **({"migration_contract_id": contract["contract_id"]} if contract else {}),
                },
            )

        artifact = generator.generate(
            migration_step,
            language,
        )

        # ---------------------------------------------------------
        # Preserve routing evidence
        # ---------------------------------------------------------

        artifact.metadata["migration_strategy"] = (
            migration_strategy
        )

        artifact.metadata["language_decision"] = (
            decision.to_dict()
        )
        if "migration_contract" in metadata:
            artifact.metadata["migration_contract_id"] = metadata["migration_contract"]["contract_id"]

        return artifact
