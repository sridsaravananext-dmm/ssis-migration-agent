from __future__ import annotations

from typing import Any


class AIMigrationPlanner:
    """
    Converts deterministic migration context into a structured,
    provider-independent request for the AI migration agent.

    No LLM calls are made here.
    """

    def build_request(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:

        migration = context.get(
            "migration",
            {},
        )

        source = context.get(
            "source",
            {},
        )

        workflow = context.get(
            "workflow",
            {},
        )

        destination = context.get(
            "destination",
            {},
        )

        requirements = context.get(
            "requirements",
            {},
        )

        evidence = context.get(
            "evidence",
            {},
        )

        strategy = migration.get(
            "strategy",
            {},
        )

        return {
            "agent": {
                "name": "data_migration_agent",
                "version": "1.0",
                "mode": "AI_ASSISTED",
            },

            "task": {
                "type": "DATA_MIGRATION",
                "step_id": migration.get(
                    "step_id"
                ),
                "step_name": migration.get(
                    "step_name"
                ),
                "source_platform": migration.get(
                    "source_platform"
                ),
                "target_platform": migration.get(
                    "target_platform"
                ),
                "target_language": migration.get(
                    "target_language"
                ),
            },

            "migration_strategy": strategy,

            "source_context": source,

            "workflow_context": workflow,

            "destination_context": destination,

            "requirements": requirements,

            "evidence": evidence,

            "instructions": [
                "Analyze the supplied migration context.",
                "Preserve the behavior represented by the source semantics.",
                "Preserve the behavior represented by the destination semantics.",
                "Preserve workflow and data-flow mappings.",
                "Preserve incremental behavior.",
                "Preserve required lookup, key, and loading behavior.",
                "Preserve lineage behavior where represented in the supplied context.",
                "Preserve cutoff or watermark behavior where represented in the supplied context.",
                "Do not invent unsupported business logic.",
                "Explicitly report assumptions.",
                "Explicitly report unresolved items.",
                "Return structured migration output.",
            ],
        }