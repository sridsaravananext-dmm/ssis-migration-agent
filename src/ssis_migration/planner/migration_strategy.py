from __future__ import annotations

from typing import Any


def determine_migration_strategy(
    migration_intent: dict[str, Any],
) -> dict[str, Any]:

    reasons: list[str] = []
    requires_ai = False

    source_procedures = migration_intent.get(
        "source_procedures",
        [],
    )

    for source in source_procedures:

        if source.get("has_cursor"):
            reasons.append("CURSOR")

        if source.get("has_loop"):
            reasons.append("LOOP")

        if source.get("temporary_tables"):
            reasons.append("TEMP_TABLE")

        if source.get("has_point_in_time_lookup"):
            reasons.append("POINT_IN_TIME_LOOKUP")

        if "UNION" in source.get(
            "relational_features",
            [],
        ):
            reasons.append("UNION")

        if source.get("has_temporal_logic"):
            reasons.append("TEMPORAL")

    # ---------------------------------------------------------
    # Deterministic patterns
    # ---------------------------------------------------------

    deterministic_patterns = {
        "FULL_SNAPSHOT_SIMPLE",
        "FULL_SNAPSHOT_RELATIONAL",
        "INCREMENTAL_SIMPLE",
        "INCREMENTAL_RELATIONAL",
    }

    complex_patterns = {
        "TEMPORAL",
        "INCREMENTAL_COMPOSITE",
        "FULL_SNAPSHOT_COMPOSITE",
    }

    patterns = {
        source.get("pattern")
        for source in source_procedures
        if source.get("pattern")
    }

    if patterns & complex_patterns:
        requires_ai = True

    # ---------------------------------------------------------
    # Strategy
    # ---------------------------------------------------------

    if requires_ai:
        strategy = "AI_ASSISTED"

    elif patterns <= deterministic_patterns:
        strategy = "DETERMINISTIC"

    else:
        strategy = "REVIEW_REQUIRED"

    return {
        "strategy": strategy,
        "requires_ai": requires_ai,
        "complexity_signals": sorted(
            set(reasons)
        ),
        "patterns": sorted(patterns),
    }
