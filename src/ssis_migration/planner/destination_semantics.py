from __future__ import annotations

from typing import Any

from ssis_migration.analyzer.sql_semantic import analyze_sql


def analyze_destination_procedure(
    procedure: dict[str, Any],
) -> dict[str, Any]:
    definition = procedure.get("definition") or ""

    semantic = analyze_sql(definition)

    target_objects = _extract_target_objects(
        semantic.get("referenced_objects", [])
    )

    procedure_role = _classify_procedure_role(
        procedure_name=procedure.get("name") or "",
        target_objects=target_objects,
        semantic=semantic,
    )

    return {
        "schema": procedure.get("schema"),
        "name": procedure.get("name"),
        "object_type": procedure.get("object_type"),
        "classification": semantic.get("classification"),
        "features": semantic.get("features", []),
        "referenced_objects": semantic.get("referenced_objects", []),
        "stored_procedures": semantic.get("stored_procedures", []),
        "target_objects": target_objects,
        "procedure_role": procedure_role,
        "definition": definition,
    }


def _extract_target_objects(
    referenced_objects: list[str],
) -> list[str]:
    targets = []

    for obj in referenced_objects:
        normalized = (
            obj.replace("[", "")
            .replace("]", "")
            .strip()
        )

        if not normalized or normalized in targets:
            continue

        targets.append(normalized)

    return targets

def _classify_procedure_role(
    procedure_name: str,
    target_objects: list[str],
    semantic: dict[str, Any],
) -> str:
    name = procedure_name.lower().replace("_", "").replace(" ", "")

    # Procedures used for ETL/package orchestration.
    if (
        "getlastetlcutoff" in name
        or "getlineage" in name
        or name.startswith("ensure")
        or name.startswith("populate")
    ):
        return "ORCHESTRATION"

    # Procedures that perform actual data migration.
    if (
        "migrate" in name
        or "load" in name
    ):
        return "DATA_MIGRATION"

    if target_objects:
        return "DATA_MIGRATION"

    return "UTILITY"