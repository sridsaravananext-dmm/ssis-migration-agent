from __future__ import annotations

import json
from typing import Any


class AIMigrationPromptBuilder:
    """
    Converts a structured AIMigrationRequest into a deterministic,
    provider-independent LLM prompt.

    No LLM calls are made here.
    """

    def build(
        self,
        request: dict[str, Any],
    ) -> str:

        task = request.get(
            "task",
            {},
        )

        strategy = request.get(
            "migration_strategy",
            {},
        )

        source = request.get(
            "source_context",
            {},
        )

        workflow = request.get(
            "workflow_context",
            {},
        )

        destination = request.get(
            "destination_context",
            {},
        )

        requirements = request.get(
            "requirements",
            {},
        )

        evidence = request.get(
            "evidence",
            {},
        )

        instructions = request.get(
            "instructions",
            [],
        )

        context = {
            "migration": task,
            "strategy": strategy,
            "source": source,
            "workflow": workflow,
            "destination": destination,
            "requirements": requirements,
            "evidence": evidence,
        }

        serialized_context = json.dumps(
            context,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

        instruction_text = "\n".join(
            f"{index}. {instruction}"
            for index, instruction in enumerate(
                instructions,
                start=1,
            )
        )

        target_language = (
            task.get("target_language")
            or "target-language"
        )

        target_platform = (
            task.get("target_platform")
            or "target-platform"
        )

        source_platform = (
            task.get("source_platform")
            or "source-platform"
        )

        return f"""
You are an expert data migration engineer.

Your task is to migrate ONE logical migration step.

Source platform:
{source_platform}

Target platform:
{target_platform}

Target language:
{target_language}

You must use ONLY the supplied migration context.

Do not invent business logic.

Do not assume behavior that is not supported by the supplied
source semantics, destination semantics, workflow metadata,
mappings, transformations, database context, or migration
strategy.

============================================================
MIGRATION CONTEXT
============================================================

{serialized_context}

============================================================
MIGRATION INSTRUCTIONS
============================================================

{instruction_text}

============================================================
REQUIRED OUTPUT
============================================================

Return ONLY valid JSON.

The JSON must have exactly this logical structure:

{{
  "status": "GENERATED | REVIEW_REQUIRED | UNRESOLVED",
  "language": "{target_language}",
  "code": "<executable migration code>",
  "assumptions": [],
  "unresolved_items": [],
  "validation_requirements": [],
  "metadata": {{}}
}}

============================================================
CODE REQUIREMENTS
============================================================

1. Generate executable code for the supplied target platform
   and target language.

2. Preserve source extraction semantics.

3. Preserve incremental or watermark behavior where represented.

4. Preserve workflow and data-flow mappings.

5. Preserve destination loading behavior.

6. Preserve required lookups, key behavior, and other target
   semantics represented in the supplied context.

7. Preserve lineage behavior where represented in the supplied
   destination logic.

8. Preserve cutoff or watermark behavior where represented in
   the supplied destination logic.

9. Do not silently discard source logic.

10. Do not silently simplify UNION, JOIN, MERGE, lookup,
    filtering, aggregation, or incremental logic.

11. If behavior cannot be safely translated, report it in
    "unresolved_items".

12. If an assumption is necessary, report it explicitly in
    "assumptions".

13. Include concrete validation requirements in
    "validation_requirements".

14. Never claim that behavior has been preserved unless the
    supplied migration context supports that conclusion.

15. The generated code must be self-contained for the supplied
    migration step.

============================================================
IMPORTANT
============================================================

This is a migration task, not a redesign task.

Preserve existing behavior first.

Optimization or modernization must not change semantics unless
explicitly justified and reported.
""".strip()