from __future__ import annotations

from typing import Any


def analyze_column_mappings(
    data_flow: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Convert parser-produced SSIS column mappings into a
    normalized deterministic lineage representation.

    No semantic inference is performed.
    """

    results: list[dict[str, Any]] = []

    for mapping in data_flow.get("column_mappings", []):
        source_lineage_id = mapping.get("source_lineage_id")

        destination_component = mapping.get(
            "destination_component"
        )

        destination_column = mapping.get(
            "destination_column"
        )

        source_column = None

        # The parser already gives us the SSIS lineage reference.
        # Extract the final column name from it.
        if source_lineage_id:
            marker = "].Columns["
            if marker in source_lineage_id:
                source_column = (
                    source_lineage_id
                    .split(marker, 1)[1]
                    .rstrip("]")
                )

        results.append(
            {
                "source_column": source_column,
                "source_lineage_id": source_lineage_id,

                "destination_component": destination_component,
                "destination_column": destination_column,

                "destination_column_ref": mapping.get(
                    "destination_column_ref"
                ),

                "external_metadata_column_id": mapping.get(
                    "external_metadata_column_id"
                ),

                "mapping_type": (
                    "DIRECT"
                    if source_column
                    and destination_column
                    else "UNKNOWN"
                ),

                "evidence": mapping,
            }
        )

    return results