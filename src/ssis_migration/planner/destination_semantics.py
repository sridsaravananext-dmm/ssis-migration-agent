from __future__ import annotations

import re
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

    migration_operations = _extract_migration_operations(
        definition
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
        "migration_operations": migration_operations,
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

    name = (
        procedure_name
        .lower()
        .replace("_", "")
        .replace(" ", "")
    )

    if (
        "getlastetlcutoff" in name
        or "getlineage" in name
        or name.startswith("ensure")
        or name.startswith("populate")
    ):
        return "ORCHESTRATION"

    if (
        "migrate" in name
        or "load" in name
    ):
        return "DATA_MIGRATION"

    if target_objects:
        return "DATA_MIGRATION"

    return "UTILITY"


def _extract_migration_operations(
    sql: str,
) -> list[dict[str, Any]]:

    operations: list[dict[str, Any]] = []

    if not sql:
        return operations

    normalized = sql.upper()

    # ---------------------------------------------------------
    # 1. Dimension / surrogate-key resolution
    # ---------------------------------------------------------

    if (
        re.search(
            r"\bUPDATE\s+\w+\s+SET\b",
            sql,
            re.IGNORECASE,
        )
        and re.search(
            r"\bVALID\s+FROM\b",
            sql,
            re.IGNORECASE,
        )
        and re.search(
            r"\bVALID\s+TO\b",
            sql,
            re.IGNORECASE,
        )
    ):

        targets = _extract_update_target_tables(sql)

        if targets:

            operations.append(
                {
                    "operation":
                        "RESOLVE_DIMENSION_KEYS",

                    "type":
                        "LOOKUP_UPDATE",

                    "description":
                        "Resolve surrogate dimension keys from staging records using validity ranges.",

                    "targets":
                        targets,

                    "features": [
                        "VALID_FROM",
                        "VALID_TO",
                        "TOP_1",
                        "COALESCE",
                    ],
                }
            )

    # ---------------------------------------------------------
    # 2. MERGE target
    # ---------------------------------------------------------

    merge_match = re.search(
        r"\bMERGE\s+"
        r"(?:INTO\s+)?"
        r"(?P<target>"
        r"(?:\[[^\]]+\]|\w+)"
        r"\s*\.\s*"
        r"(?:\[[^\]]+\]|\w+)"
        r")",
        sql,
        re.IGNORECASE,
    )

    if merge_match:

        target = _normalize_sql_object(
            merge_match.group("target")
        )

        operations.append(
            {
                "operation":
                    "MERGE_TARGET",

                "type":
                    "MERGE",

                "description":
                    "Merge staged records into the target fact or dimension table.",

                "target":
                    target,

                "features": [
                    "WHEN_MATCHED",
                    "WHEN_NOT_MATCHED",
                    "UPDATE",
                    "INSERT",
                ],
            }
        )

    else:

        # -----------------------------------------------------
        # 3. DELETE affected target records
        # -----------------------------------------------------

        delete_match = re.search(
            r"\bDELETE\s+\w+\s+"
            r"FROM\s+"
            r"(?P<target>"
            r"(?:\[[^\]]+\]|\w+)"
            r"\s*\.\s*"
            r"(?:\[[^\]]+\]|\w+)"
            r")",
            sql,
            re.IGNORECASE,
        )

        if delete_match:

            target = _normalize_sql_object(
                delete_match.group("target")
            )

            operations.append(
                {
                    "operation":
                        "DELETE_EXISTING_TARGET",

                    "type":
                        "DELETE",

                    "description":
                        "Remove existing target records affected by the incoming staging records.",

                    "target":
                        target,

                    "features": [
                        "DELETE",
                        "STAGING_KEY_FILTER",
                    ],
                }
            )

        # -----------------------------------------------------
        # 4. TRUNCATE snapshot target
        # -----------------------------------------------------

        truncate_match = re.search(
            r"\bTRUNCATE\s+TABLE\s+"
            r"(?P<target>"
            r"(?:\[[^\]]+\]|\w+)"
            r"\s*\.\s*"
            r"(?:\[[^\]]+\]|\w+)"
            r")",
            sql,
            re.IGNORECASE,
        )

        if truncate_match:

            target = _normalize_sql_object(
                truncate_match.group("target")
            )

            operations.append(
                {
                    "operation":
                        "TRUNCATE_TARGET",

                    "type":
                        "TRUNCATE",

                    "description":
                        "Clear the complete target table before loading the current snapshot.",

                    "target":
                        target,

                    "features": [
                        "TRUNCATE",
                        "FULL_SNAPSHOT",
                    ],
                }
            )

    # ---------------------------------------------------------
    # 5. INSERT into target
    # ---------------------------------------------------------

    insert_matches = re.finditer(
        r"\bINSERT\s+"
        r"(?:INTO\s+)?"
        r"(?P<target>"
        r"(?:\[[^\]]+\]|\w+)"
        r"\s*\.\s*"
        r"(?:\[[^\]]+\]|\w+)"
        r")",
        sql,
        re.IGNORECASE,
    )

    inserted_targets = []

    for match in insert_matches:

        target = _normalize_sql_object(
            match.group("target")
        )

        if target not in inserted_targets:

            inserted_targets.append(target)

            operations.append(
                {
                    "operation":
                        "INSERT_TARGET",

                    "type":
                        "INSERT",

                    "description":
                        "Insert staged records into the target table.",

                    "target":
                        target,

                    "features": [
                        "INSERT",
                        "SELECT_FROM_STAGING",
                    ],
                }
            )

    # ---------------------------------------------------------
    # 6. Lineage completion
    # ---------------------------------------------------------

    if re.search(
        r"\bUPDATE\s+"
        r"(?:\[?Integration\]?\s*\.\s*)?"
        r"\[?Lineage\]?",
        sql,
        re.IGNORECASE,
    ):

        operations.append(
            {
                "operation":
                    "COMPLETE_LINEAGE",

                "type":
                    "UPDATE",

                "description":
                    "Mark the current lineage record as successfully completed.",

                "target":
                    "Integration.Lineage",

                "features": [
                    "DATA_LOAD_COMPLETED",
                    "WAS_SUCCESSFUL",
                    "LINEAGE_KEY",
                ],
            }
        )

    # ---------------------------------------------------------
    # 7. ETL cutoff update
    # ---------------------------------------------------------

    if re.search(
        r"\bUPDATE\s+"
        r"(?:\[?Integration\]?\s*\.\s*)?"
        r"\[?ETL\s+Cutoff\]?",
        sql,
        re.IGNORECASE,
    ):

        operations.append(
            {
                "operation":
                    "UPDATE_ETL_CUTOFF",

                "type":
                    "UPDATE",

                "description":
                    "Advance the ETL cutoff using the source-system cutoff associated with the lineage key.",

                "target":
                    "Integration.ETL Cutoff",

                "features": [
                    "SOURCE_SYSTEM_CUTOFF_TIME",
                    "LINEAGE_KEY",
                ],
            }
        )

    return operations

def _contains_dimension_lookup(
    sql: str,
) -> bool:

    return bool(
        re.search(
            r"\bUPDATE\s+\w+\s+SET\b.*?"
            r"\bVALID\s+FROM\b.*?"
            r"\bVALID\s+TO\b",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )


def _extract_update_target_tables(
    sql: str,
) -> list[str]:

    targets = []

    # Dimension tables participating in point-in-time
    # surrogate-key resolution.
    pattern = re.compile(
        r"\bFROM\s+"
        r"(?P<object>"
        r"(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*\.\s*"
        r"(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)"
        r")",
        flags=re.IGNORECASE,
    )

    for match in pattern.finditer(sql):

        object_name = _normalize_object_name(
            match.group("object")
        )

        if not object_name:
            continue

        if object_name.lower().startswith(
            "dimension."
        ):
            if object_name not in targets:
                targets.append(object_name)

    return targets

def _extract_merge_features(
    sql: str,
) -> list[str]:

    features = []

    if re.search(
        r"\bWHEN\s+MATCHED\b",
        sql,
        flags=re.IGNORECASE,
    ):
        features.append("WHEN_MATCHED")

    if re.search(
        r"\bWHEN\s+NOT\s+MATCHED\b",
        sql,
        flags=re.IGNORECASE,
    ):
        features.append("WHEN_NOT_MATCHED")

    if re.search(
        r"\bUPDATE\s+SET\b",
        sql,
        flags=re.IGNORECASE,
    ):
        features.append("UPDATE")

    if re.search(
        r"\bINSERT\s*\(",
        sql,
        flags=re.IGNORECASE,
    ):
        features.append("INSERT")

    return features


def _updates_lineage(
    sql: str,
) -> bool:

    return bool(
        re.search(
            r"\bUPDATE\s+"
            r"(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)"
            r"\s*\.\s*"
            r"(?:\[Lineage\]|Lineage)\b",
            sql,
            flags=re.IGNORECASE,
        )
    )


def _updates_etl_cutoff(
    sql: str,
) -> bool:

    return bool(
        re.search(
            r"\bUPDATE\s+"
            r"(?:Integration\s*\.\s*)?"
            r"(?:\[ETL\s+Cutoff\]|ETL\s+Cutoff)",
            sql,
            flags=re.IGNORECASE,
        )
    )


def _normalize_object_name(
    value: str,
) -> str:

    return (
        value
        .replace("[", "")
        .replace("]", "")
        .replace(" ", " ")
        .strip()
    )

def _normalize_sql_object(
    value: str,
) -> str:

    value = value.strip()

    value = re.sub(
        r"\[([^\]]+)\]",
        r"\1",
        value,
    )

    value = re.sub(
        r"\s*\.\s*",
        ".",
        value,
    )

    return value