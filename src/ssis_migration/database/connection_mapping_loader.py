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