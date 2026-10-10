
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ssis_migration.database.export import export_sqlserver_metadata


SERVER = "IN-1N461302K1"

DATABASES = {
    "WideWorldImporters": (
        PROJECT_ROOT / "examples" / "wwi" / "database_metadata_wwi.json"
    ),
    "WideWorldImportersDW": (
        PROJECT_ROOT / "examples" / "wwi" / "database_metadata_wwi_dw.json"
    ),
}


def main() -> None:
    for database, output_path in DATABASES.items():
        print(f"\nExporting database: {database}")

        export_sqlserver_metadata(
            server=SERVER,
            database=database,
            output_path=output_path,
            trusted_connection=True,
        )

        # Read back the exported JSON and verify the results.
        metadata = json.loads(
            output_path.read_text(encoding="utf-8")
        )

        procedures = metadata.get("procedures", [])
        definitions_available = sum(
            isinstance(p.get("definition"), str)
            and bool(p["definition"].strip())
            for p in procedures
        )
        definitions_missing = len(procedures) - definitions_available

        print(f"Output file: {output_path}")
        print(f"Procedures exported: {len(procedures)}")
        print(f"Definitions available: {definitions_available}")
        print(f"Definitions missing: {definitions_missing}")

        if definitions_missing:
            print("WARNING: Some procedure definitions are missing.")
            for procedure in procedures:
                if not procedure.get("definition"):
                    print(
                        "  - "
                        f"{procedure.get('schema')}"
                        f".{procedure.get('name')}"
                    )

    print("\nMetadata export finished.")


if __name__ == "__main__":
    main()