
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class TemporalHistoryCheck:
    table_name: str
    status: str
    business_key_columns: list[str]
    valid_from_column: str | None
    valid_to_column: str | None
    evidence: list[str]
    missing_requirements: list[str]


def check_temporal_history(
    table_name: str,
    schema: Any,
    business_key_columns: list[str] | None = None,
    valid_from_column: str | None = None,
    valid_to_column: str | None = None,
    history_confirmed: bool = False,
) -> dict[str, Any]:
    """
    Check whether supplied metadata supports row-level temporal reconstruction.

    This is a metadata/prerequisite check, not proof that historical rows
    match SQL Server system-versioned temporal semantics.
    """
    business_key_columns = business_key_columns or []

    if hasattr(schema, "fieldNames"):
        columns = set(schema.fieldNames())
    elif isinstance(schema, dict):
        columns = set(schema.keys())
    elif isinstance(schema, (list, tuple, set)):
        columns = set(schema)
    else:
        columns = set()

    missing = []
    evidence = []

    if not columns:
        missing.append("Table schema metadata was not supplied.")

    if not business_key_columns:
        missing.append("Business key columns have not been identified.")
    else:
        absent_keys = sorted(set(business_key_columns) - columns)
        if absent_keys:
            missing.append(
                f"Business key columns absent from schema: {absent_keys}"
            )
        else:
            evidence.append(
                f"Business key columns present: {business_key_columns}"
            )

    if not valid_from_column:
        missing.append("Row-level Valid From column has not been identified.")
    elif valid_from_column not in columns:
        missing.append(
            f"Valid From column '{valid_from_column}' is absent from schema."
        )
    else:
        evidence.append(
            f"Valid From column present: {valid_from_column}"
        )

    if not valid_to_column:
        missing.append("Row-level Valid To column has not been identified.")
    elif valid_to_column not in columns:
        missing.append(
            f"Valid To column '{valid_to_column}' is absent from schema."
        )
    else:
        evidence.append(f"Valid To column present: {valid_to_column}")

    if not history_confirmed:
        missing.append(
            "Historical-row retention and temporal semantics have not "
            "been confirmed against the source SQL Server temporal tables."
        )
    else:
        evidence.append(
            "Caller confirms historical-row retention; equivalence still "
            "requires comparison against source query results."
        )

    return asdict(
        TemporalHistoryCheck(
            table_name=table_name,
            status="READY_FOR_VALIDATION" if not missing else "UNRESOLVED",
            business_key_columns=business_key_columns,
            valid_from_column=valid_from_column,
            valid_to_column=valid_to_column,
            evidence=evidence,
            missing_requirements=missing,
        )
    )
