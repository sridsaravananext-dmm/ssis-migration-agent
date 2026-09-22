from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConnectionDatabaseMapping:
    connection_reference: str
    metadata_file: str
    database_name: str | None = None
    role: str | None = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass
class ConnectionMappingSet:
    mappings: list[ConnectionDatabaseMapping] = field(
        default_factory=list
    )

    def get(
        self,
        connection_reference: str,
    ) -> ConnectionDatabaseMapping | None:

        for mapping in self.mappings:

            if (
                mapping.connection_reference
                == connection_reference
            ):
                return mapping

        return None

    def to_dict(self) -> dict[str, Any]:

        return {
            "mappings": [
                {
                    "connection_reference": mapping.connection_reference,
                    "metadata_file": mapping.metadata_file,
                    "database_name": mapping.database_name,
                    "role": mapping.role,
                    "metadata": mapping.metadata,
                }
                for mapping in self.mappings
            ]
        }