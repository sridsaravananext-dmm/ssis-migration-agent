import sys
from pathlib import Path

# Adjust this path on the Windows machine where the repository exists.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(
    0,
    str(PROJECT_ROOT / "src"),
)

from ssis_migration.database.export import (
    export_sqlserver_metadata,
)


output = export_sqlserver_metadata(
    server="IN-1N461302K1",
    database="WideWorldImportersDW",
    output_path=PROJECT_ROOT / "examples" / "wwi" / "database_metadata.json",
    trusted_connection=True,
)

print(f"Metadata exported to: {output}")