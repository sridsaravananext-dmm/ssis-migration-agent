from __future__ import annotations

from typing import Any

from .base import MigrationGenerator
from .models import GeneratedArtifact
from .sql_translator import SQLServerToSparkSQLTranslator


class SparkSQLGenerator(MigrationGenerator):

    def __init__(self) -> None:
        self._translator = SQLServerToSparkSQLTranslator()

    def generate(
        self,
        migration_step: dict[str, Any],
        target_language: str,
    ) -> GeneratedArtifact:

        step_id = migration_step["step_id"]
        step_name = migration_step["name"]

        metadata = migration_step.get("metadata", {})

        migration_intent = metadata.get(
            "migration_intent",
            {},
        )

        migration_strategy = metadata.get(
            "migration_strategy",
            {},
        )

        strategy = migration_strategy.get(
            "strategy",
            "REVIEW_REQUIRED",
        )

        requires_ai = migration_strategy.get(
            "requires_ai",
            False,
        )

        source_objects = migration_step.get(
            "source_objects",
            [],
        )

        target_objects = migration_step.get(
            "target_objects",
            [],
        )

        source_procedures = migration_intent.get(
            "source_procedures",
            [],
        )

        # ---------------------------------------------------------
        # AI-assisted migrations are not fabricated by the
        # deterministic generator.
        # ---------------------------------------------------------

        if requires_ai or strategy == "AI_ASSISTED":
            return GeneratedArtifact(
                artifact_id=f"{step_id}_spark_sql",
                name=f"{step_name}_spark_sql",
                artifact_type="AI_REQUIRED",
                target_platform="databricks",
                language=target_language,
                code="",
                source_step_id=step_id,
                metadata={
                    "status": "AI_REQUIRED",
                    "migration_strategy": migration_strategy,
                    "migration_intent": migration_intent,
                    "source_objects": source_objects,
                    "target_objects": target_objects,
                },
            )

        # ---------------------------------------------------------
        # Deterministic stored-procedure translation
        # ---------------------------------------------------------

        if source_procedures:

            procedure = source_procedures[0]

            procedure_name = procedure.get(
                "procedure",
                "<SOURCE_PROCEDURE>",
            )

            definition = procedure.get(
                "definition"
            )

            if not definition:
                return GeneratedArtifact(
                    artifact_id=f"{step_id}_spark_sql",
                    name=f"{step_name}_spark_sql",
                    artifact_type="AI_REQUIRED",
                    target_platform="databricks",
                    language=target_language,
                    code="",
                    source_step_id=step_id,
                    metadata={
                        "status": "MISSING_SOURCE_PROCEDURE_DEFINITION",
                        "procedure": procedure_name,
                        "migration_strategy": migration_strategy,
                    },
                )

            translation = self._translator.translate(
                definition
            )

            if translation.status != "TRANSLATED":
                return GeneratedArtifact(
                    artifact_id=f"{step_id}_spark_sql",
                    name=f"{step_name}_spark_sql",
                    artifact_type="TRANSLATION_REQUIRED",
                    target_platform="databricks",
                    language=target_language,
                    code="",
                    source_step_id=step_id,
                    metadata={
                        "status": translation.status,
                        "procedure": procedure_name,
                        "translation": translation.to_dict(),
                        "migration_strategy": migration_strategy,
                    },
                )

            target = (
                target_objects[0]
                if target_objects
                else "<TARGET_OBJECT>"
            )

            translated_sql = translation.sql or ""

            code = f"""-- Generated from SSIS migration step: {step_name}
-- Source procedure: {procedure_name}
-- Target staging object: {target}
--
-- Deterministic SQL translation from SQL Server
-- to Databricks SQL.

{translated_sql}
"""

            return GeneratedArtifact(
                artifact_id=f"{step_id}_spark_sql",
                name=f"{step_name}_spark_sql",
                artifact_type="NOTEBOOK_CELL",
                target_platform="databricks",
                language=target_language,
                code=code,
                source_step_id=step_id,
                metadata={
                    "generator": "SparkSQLGenerator",
                    "status": "GENERATED",
                    "procedure": procedure_name,
                    "translation": translation.to_dict(),
                    "migration_strategy": migration_strategy,
                    "migration_intent": migration_intent,
                },
            )

        # ---------------------------------------------------------
        # Physical source → physical target fallback
        # ---------------------------------------------------------

        if not source_objects or not target_objects:
            return GeneratedArtifact(
                artifact_id=f"{step_id}_spark_sql",
                name=f"{step_name}_spark_sql",
                artifact_type="REVIEW_REQUIRED",
                target_platform="databricks",
                language=target_language,
                code="",
                source_step_id=step_id,
                metadata={
                    "status": "MISSING_SOURCE_OR_TARGET",
                    "migration_strategy": migration_strategy,
                    "migration_intent": migration_intent,
                },
            )

        source = source_objects[0]
        target = target_objects[0]

        code = f"""-- Generated from SSIS migration step: {step_name}
-- Source: {source}
-- Target: {target}

INSERT INTO {target}
SELECT *
FROM {source};
"""

        return GeneratedArtifact(
            artifact_id=f"{step_id}_spark_sql",
            name=f"{step_name}_spark_sql",
            artifact_type="NOTEBOOK_CELL",
            target_platform="databricks",
            language=target_language,
            code=code,
            source_step_id=step_id,
            metadata={
                "generator": "SparkSQLGenerator",
                "status": "GENERATED",
                "migration_strategy": migration_strategy,
            },
        )