from ssis_migration.planner.migration_strategy import (
    determine_migration_strategy,
)


def test_temporal_is_ai_assisted():
    intent = {
        "source_procedures": [
            {
                "pattern": "TEMPORAL",
                "has_cursor": True,
                "has_loop": True,
                "temporary_tables": ["RowsToCloseOff"],
                "has_temporal_logic": True,
            }
        ]
    }

    result = determine_migration_strategy(intent)

    assert result["strategy"] == "AI_ASSISTED"
    assert result["requires_ai"] is True


def test_incremental_relational_is_deterministic():
    intent = {
        "source_procedures": [
            {
                "pattern": "INCREMENTAL_RELATIONAL",
                "has_cutoff_parameters": True,
                "has_cutoff_filter": True,
                "relational_features": ["JOIN"],
            }
        ]
    }

    result = determine_migration_strategy(intent)

    assert result["strategy"] == "DETERMINISTIC"
    assert result["requires_ai"] is False


def test_full_snapshot_relational_is_deterministic():
    intent = {
        "source_procedures": [
            {
                "pattern": "FULL_SNAPSHOT_RELATIONAL",
                "relational_features": ["JOIN"],
            }
        ]
    }

    result = determine_migration_strategy(intent)

    assert result["strategy"] == "DETERMINISTIC"
    assert result["requires_ai"] is False


def test_incremental_composite_is_ai_assisted():
    intent = {
        "source_procedures": [
            {
                "pattern": "INCREMENTAL_COMPOSITE",
                "relational_features": ["UNION"],
            }
        ]
    }

    result = determine_migration_strategy(intent)

    assert result["strategy"] == "AI_ASSISTED"
    assert result["requires_ai"] is True


def test_union_alone_does_not_trigger_ai():
    intent = {
        "source_procedures": [
            {
                "pattern": "INCREMENTAL_RELATIONAL",
                "relational_features": ["UNION"],
            }
        ]
    }

    result = determine_migration_strategy(intent)

    assert result["strategy"] == "DETERMINISTIC"
    assert result["requires_ai"] is False


def test_unknown_pattern_requires_review():
    intent = {
        "source_procedures": [
            {
                "pattern": "UNKNOWN_PATTERN",
            }
        ]
    }

    result = determine_migration_strategy(intent)

    assert result["strategy"] == "REVIEW_REQUIRED"
    assert result["requires_ai"] is False


if __name__ == "__main__":
    tests = [
        test_temporal_is_ai_assisted,
        test_incremental_relational_is_deterministic,
        test_full_snapshot_relational_is_deterministic,
        test_incremental_composite_is_ai_assisted,
        test_union_alone_does_not_trigger_ai,
        test_unknown_pattern_requires_review,
    ]

    for test in tests:
        test()
        print(f"PASS: {test.__name__}")

    print(f"\n{len(tests)} tests passed.")