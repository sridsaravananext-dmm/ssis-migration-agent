from __future__ import annotations

import json
from pathlib import Path

from .connection_mapping import (
    ConnectionDatabaseMapping,
    ConnectionMappingSet,
)


def load_connection_mapping(
    file_path: str | Path,
) -> ConnectionMappingSet:

    file_path = Path(file_path)

    with file_path.open(
        "r",
        encoding="utf-8",
    ) as file:

        data = json.load(file)

    validate_connection_mapping(data)

    mappings = []

    for item in data.get(
        "mappings",
        [],
    ):

        mappings.append(
            ConnectionDatabaseMapping(
                connection_reference=item[
                    "connection_reference"
                ],
                metadata_file=item[
                    "metadata_file"
                ],
                database_name=item.get(
                    "database_name"
                ),
                role=item.get(
                    "role"
                ),
                metadata=item.get(
                    "metadata",
                    {},
                ),
            )
        )

    return ConnectionMappingSet(
        mappings=mappings
    )

def validate_connection_mapping(data):
    if not isinstance(data, dict) or not isinstance(data.get("mappings"), list):
        raise ValueError("Connection mapping must be an object with a mappings list")
    seen = set()
    for item in data["mappings"]:
        if not isinstance(item, dict):
            raise ValueError("Each mapping must be an object")
        for key in ("connection_reference", "metadata_file"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"Mapping requires non-empty {key}")
        if item["connection_reference"] in seen:
            raise ValueError("Duplicate connection reference in mapping")
        seen.add(item["connection_reference"])
