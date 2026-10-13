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

        temporal_context = request.get(
            "temporal_context",
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
            "temporal_context": temporal_context,
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
MIGRATION BOUNDARIES — MANDATORY
============================================================

16. The source procedure definitions are authoritative business
    logic to TRANSLATE, not procedures to call at runtime.

17. Do not invoke, execute, or depend on the original source
    stored procedures. Do not use pyodbc, pandas.read_sql,
    cursor-based source procedure execution, or equivalent
    pass-through execution to reuse the old ETL implementation.

18. Ingestion is handled separately. Do not generate source
    ingestion or connectivity code unless the migration step
    explicitly requires it.

19. Translate the supplied source procedure logic into the
    target platform's native transformations, using only
    source tables, historical data, schemas, mappings, and
    dependencies supported by the supplied context.

20. Do not invent Databricks table names, historical versions,
    credentials, connection strings, or column representations.

21. If the source uses temporal queries such as
    FOR SYSTEM_TIME AS OF, determine whether equivalent
    historical data and temporal boundaries are available
    in the supplied context. If they are not, report the
    missing prerequisite in unresolved_items. Do not silently
    substitute current-state tables for historical state.

22. Translate destination procedure logic separately from
    source extraction logic. Preserve staging behavior,
    dimension version closure, insert behavior, lineage
    updates, cutoff updates, and transaction requirements
    where supported by the supplied context.

23. Never claim the migration is complete merely because
    code was generated or passed schema validation.

24. If the supplied context is insufficient for a faithful
    translation, return REVIEW_REQUIRED or UNRESOLVED and
    explain the exact missing information rather than
    generating a misleading implementation.

25. Never treat Delta Lake TIMESTAMP AS OF, VERSION AS OF, or time travel as automatically equivalent to SQL Server FOR SYSTEM_TIME AS OF.

26. Delta Lake time travel represents table snapshots at transaction commit timestamps or versions. It does not, by itself, reproduce row-level system-versioned temporal history at arbitrary business timestamps.

27. When translating FOR SYSTEM_TIME AS OF, require evidence of a compatible historical-data model, including the source row keys, effective/system validity boundaries, and historical versions needed to reconstruct each related table at the requested timestamp. If temporal_context is supplied with has_temporal_queries=True, the ValidFrom/ValidTo columns and archive table pattern provided there constitute the required evidence.

28. If compatible row history is not present in the supplied context (temporal_context is absent or has_temporal_queries is False), do not invent it and do not substitute current-state data or Delta time travel. Return REVIEW_REQUIRED or UNRESOLVED and identify the exact missing historical-data prerequisite.

29. Do not claim that the City Dimension migration is complete until the translated source result has been compared with the original procedure for identical cutoff parameters, including row values, duplicate behavior, and Valid To intervals.

============================================================
IMPORTANT
============================================================

This is a migration task, not a redesign task.

Preserve existing behavior first.

Optimization or modernization must not change semantics unless
explicitly justified and reported.
""".strip()