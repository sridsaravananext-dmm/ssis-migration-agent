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

        return artifact