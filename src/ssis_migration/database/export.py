from __future__ import annotations

import json
from pathlib import Path

from ssis_migration.database.models import DatabaseMetadata
from ssis_migration.database.sqlserver.extractor import (
    SQLServerMetadataExtractor,
)


def export_sqlserver_metadata(
    server: str,
    database: str,
    output_path: str | Path,
    username: str | None = None,
    password: str | None = None,
    trusted_connection: bool = True,
) -> Path:

    output_path = Path(output_path)

    extractor = SQLServerMetadataExtractor(
        server=server,
        database=database,
        username=username,
        password=password,
        trusted_connection=trusted_connection,
    )

    metadata: DatabaseMetadata = extractor.extract()

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            metadata.to_dict(),
            file,
            indent=2,
            ensure_ascii=False,
        )

    return output_path