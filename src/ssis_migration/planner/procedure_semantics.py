from __future__ import annotations

from typing import Any

from ssis_migration.analyzer.sql_semantic import analyze_sql


def analyze_procedure(
    procedure: dict[str, Any],
    known_objects: set[str] | None = None,
) -> dict[str, Any]:

    definition = procedure.get("definition") or ""

    semantic = analyze_sql(definition)

    referenced_objects = semantic[
        "referenced_objects"
    ]

    if known_objects is not None:

        normalized_known_objects = {
            _normalize_object_name(name)
            for name in known_objects
        }

        referenced_objects = [
            reference
            for reference in referenced_objects
            if _normalize_object_name(reference)
            in normalized_known_objects
        ]

    return {
        "schema": procedure.get("schema"),
        "name": procedure.get("name"),
        "object_type": procedure.get(
            "object_type",
            "PROCEDURE",
        ),
        "classification": semantic[
            "classification"
        ],
        "features": semantic["features"],
        "procedural_features": semantic[
            "procedural_features"
        ],
        "relational_features": semantic[
            "relational_features"
        ],
        "referenced_objects": referenced_objects,
        "stored_procedures": semantic[
            "stored_procedures"
        ],
        "parameters": procedure.get(
            "parameters",
            [],
        ),
        "definition": definition,
    }


def _normalize_object_name(
    value: str,
) -> str:

    return (
        value
        .strip()
        .strip("[]")
        .replace("].[", ".")
        .replace("[", "")
        .replace("]", "")
        .lower()
    )