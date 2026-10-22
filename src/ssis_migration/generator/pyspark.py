from __future__ import annotations

from typing import Any

from .base import MigrationGenerator
from .models import GeneratedArtifact


class PySparkGenerator(MigrationGenerator):

    def generate(
        self,
        migration_step: dict[str, Any],
        target_language: str,
    ) -> GeneratedArtifact:

        from ssis_migration.migration import prepare_generation_step

        migration_step = prepare_generation_step(migration_step)
        contract = migration_step.get("metadata", {}).get("migration_contract")
        if contract and contract["implementation_strategy"]["decision"] == "UNSUPPORTED":
            raise ValueError("Unsupported contract cannot generate target code.")

        step_id = migration_step["step_id"]
        step_name = migration_step["name"]

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

        data_flows = migration_intent.get(
            "data_flows",
            [],
        )

        source_procedure = (
            source_procedures[0]
            if source_procedures
            else {}
        )

        destination_procedure = (
            destination_procedures[0]
            if destination_procedures
            else {}
        )

        source_definition = source_procedure.get(
            "definition"
        )

        destination_definition = (
            metadata
            .get("destination_procedure_semantics", [{}])[0]
            .get("definition")
        )

        code = self._build_ai_artifact(
            migration_step=migration_step,
            source_procedure=source_procedure,
            destination_procedure=destination_procedure,
            source_definition=source_definition,
            destination_definition=destination_definition,
            migration_strategy=migration_strategy,
        )

        return GeneratedArtifact(
            artifact_id=f"{step_id}_pyspark",
            name=f"{step_name}_pyspark",
            artifact_type="AI_MIGRATION_ARTIFACT",
            target_platform="databricks",
            language=target_language,
            code=code,
            source_step_id=step_id,
            metadata={
                "status": "AI_REQUIRED",

                "migration_strategy": migration_strategy,

                "migration_intent": migration_intent,

                "source_procedure": source_procedure,

                "source_procedure_definition": (
                    source_definition
                ),

                "destination_procedure": (
                    destination_procedure
                ),

                "destination_procedure_definition": (
                    destination_definition
                ),

                "control_flow_operations": (
                    migration_intent.get(
                        "control_flow_operations",
                        [],
                    )
                ),

                "data_flows": data_flows,

                "source_objects": migration_step.get(
                    "source_objects",
                    [],
                ),

                "target_objects": migration_step.get(
                    "target_objects",
                    [],
                ),

                "transformations": migration_step.get(
                    "transformations",
                    [],
                ),
            },
        )

    @staticmethod
    def _build_ai_artifact(
        migration_step: dict[str, Any],
        source_procedure: dict[str, Any],
        destination_procedure: dict[str, Any],
        source_definition: str | None,
        destination_definition: str | None,
        migration_strategy: dict[str, Any],
    ) -> str:

        step_name = migration_step.get(
            "name",
            "Unnamed migration step",
        )

        source_name = source_procedure.get(
            "procedure",
            "UNKNOWN",
        )

        destination_name = destination_procedure.get(
            "procedure",
            "UNKNOWN",
        )

        strategy = migration_strategy.get(
            "strategy",
            "UNKNOWN",
        )

        pattern = migration_strategy.get(
            "patterns",
            [],
        )

        complexity = migration_strategy.get(
            "complexity_signals",
            [],
        )

        return f'''# AI_REQUIRED
#
# ============================================================
# SSIS -> Databricks AI Migration Artifact
# ============================================================
#
# Migration step:
# {step_name}
#
# Target:
# Databricks PySpark
#
# Migration strategy:
# {strategy}
#
# Migration patterns:
# {pattern}
#
# Complexity signals:
# {complexity}
#
# ------------------------------------------------------------
# SOURCE PROCEDURE
# ------------------------------------------------------------
#
# {source_name}
#
# The complete source procedure is preserved in artifact
# metadata under:
#
#   source_procedure_definition
#
# The AI migration agent must preserve its semantics.
#
# ------------------------------------------------------------
# DESTINATION PROCEDURE
# ------------------------------------------------------------
#
# {destination_name}
#
# The complete destination procedure is preserved in
# artifact metadata under:
#
#   destination_procedure_definition
#
# The AI migration agent must analyze its target-side
# semantics before generating the Databricks implementation.
#
# ------------------------------------------------------------
# MIGRATION REQUIREMENT
# ------------------------------------------------------------
#
# Generate executable Databricks PySpark logic equivalent to
# the original SSIS workflow.
#
# The implementation must account for:
#
# 1. Source extraction semantics
# 2. Incremental cutoff handling
# 3. SSIS data-flow mappings
# 4. Staging behavior
# 5. Destination transformation semantics
# 6. Dimension/surrogate-key lookups
# 7. Target fact/dimension loading
# 8. Lineage behavior
# 9. ETL cutoff behavior
# 10. Transaction / consistency semantics
#
# Do not invent business logic that is not supported by the
# supplied source SQL, destination SQL, SSIS metadata, or
# database metadata.
#
# This artifact is an AI input contract, not executable code.
'''
