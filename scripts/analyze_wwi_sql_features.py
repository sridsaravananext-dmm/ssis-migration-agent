import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlglot.errors import ParseError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ssis_migration.resolver.sql_features import detect_required_features

FILES = [
    ("WideWorldImporters", ROOT / "examples/wwi/database_metadata_wwi.json"),
    ("WideWorldImportersDW", ROOT / "examples/wwi/database_metadata_wwi_dw.json"),
]

feature_counts = Counter()
feature_procedures = defaultdict(list)
parse_failures = []
total = 0
empty_definitions = []

for database, path in FILES:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    procedures = metadata.get("procedures", [])
    print(f"\n{'=' * 72}\n{database}\n{'=' * 72}")
    print(f"Procedures loaded: {len(procedures)}")

    for procedure in procedures:
        total += 1
        schema = procedure.get("schema", "")
        name = procedure.get("name", "")
        label = f"{database}.{schema}.{name}"
        sql = procedure.get("definition")

        if not isinstance(sql, str) or not sql.strip():
            empty_definitions.append(label)
            continue

        try:
            features = detect_required_features(
                sql,
                source_dialect="tsql",
            )
        except Exception as exc:
            parse_failures.append((label, type(exc).__name__, str(exc).splitlines()[0]))
            continue

        for feature in features:
            feature_counts[feature] += 1
            feature_procedures[feature].append(label)

    print("Analysis pass complete.")

print(f"\n{'=' * 72}\nSUMMARY\n{'=' * 72}")
print(f"Total procedures: {total}")
print(f"Procedures with empty definitions: {len(empty_definitions)}")
print(f"Procedures with parsing/detection errors: {len(parse_failures)}")
print("\nDetected feature counts:")
for feature, count in sorted(feature_counts.items()):
    print(f"  {feature}: {count} procedures")
    for procedure in feature_procedures[feature]:
        print(f"    - {procedure}")

if empty_definitions:
    print("\nEmpty definitions:")
    for item in empty_definitions:
        print(f"  - {item}")

if parse_failures:
    print("\nParsing/detection errors:")
    for procedure, error_type, message in parse_failures:
        print(f"  - {procedure}: {error_type}: {message}")
