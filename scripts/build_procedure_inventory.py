import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCE_FILE = ROOT / "examples/wwi/database_metadata_wwi.json"
TARGET_FILE = ROOT / "examples/wwi/database_metadata_wwi_dw.json"
OUTPUT_FILE = ROOT / "examples/wwi/procedure_pair_inventory.csv"


def load_metadata(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def procedure_index(metadata):
    return {
        f"{p.get('schema', '')}.{p['name']}": p
        for p in metadata.get("procedures", [])
    }


source = load_metadata(SOURCE_FILE)
target = load_metadata(TARGET_FILE)

source_procs = procedure_index(source)
target_procs = procedure_index(target)

source_updates = {
    name: proc
    for name, proc in source_procs.items()
    if name.startswith("Integration.Get") and name.endswith("Updates")
}

target_migrations = {
    name: proc
    for name, proc in target_procs.items()
    if name.startswith("Integration.MigrateStaged")
    and name.endswith("Data")
}

target_by_suffix = {}
for name, proc in target_migrations.items():
    suffix = name.removeprefix("Integration.MigrateStaged").removesuffix("Data")
    target_by_suffix[suffix] = (name, proc)

rows = []

for source_name, source_proc in sorted(source_updates.items()):
    suffix = source_name.removeprefix("Integration.Get").removesuffix("Updates")
    match = target_by_suffix.get(suffix)

    if match:
        target_name, target_proc = match
        status = "CANDIDATE_NAME_MATCH"
    else:
        target_name, target_proc = "", {}
        status = "NO_NAME_MATCH"

    source_params = source_proc.get("parameters") or []
    target_params = target_proc.get("parameters") or []

    rows.append({
        "source_database": source.get("database", {}).get("name", ""),
        "source_procedure": source_name,
        "source_parameter_count": len(source_params),
        "target_database": target.get("database", {}).get("name", ""),
        "target_procedure": target_name,
        "target_parameter_count": len(target_params),
        "match_status": status,
        "verification_status": "NOT_VERIFIED",
    })

OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "source_database",
    "source_procedure",
    "source_parameter_count",
    "target_database",
    "target_procedure",
    "target_parameter_count",
    "match_status",
    "verification_status",
]

with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"Source update procedures: {len(source_updates)}")
print(f"Destination migration procedures: {len(target_migrations)}")
print(f"Candidate pairs written: {len(rows)}")
print(f"Output: {OUTPUT_FILE}")

for row in rows:
    print(
        f"{row['match_status']:20} "
        f"{row['source_procedure']} -> "
        f"{row['target_procedure'] or '(no match)'}"
    )
