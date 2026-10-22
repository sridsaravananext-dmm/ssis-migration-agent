"""Analyze a supplied SQL file locally; write a new, evidence-bound report."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ssis_migration.languages.sql import analyze_sql
from ssis_migration.schemas import validate_document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--dialect', choices=['tsql', 'oracle', 'spark', 'databricks'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        path = args.source.resolve(strict=True)
        if not path.is_file():
            raise ValueError('Source must be a regular exported SQL file.')
        data = path.read_bytes()
        report = analyze_sql(data.decode('utf-8-sig'), dialect=args.dialect)
        report['source_file'] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                                 'size_bytes': len(data)}
        validate_document(report, 'sql_analysis')
        if path.read_bytes() != data:
            raise ValueError('Source changed during analysis; repeat with a stable export.')
        # Exclusive creation protects exports and earlier analysis/evidence.
        with args.output.open('x', encoding='utf-8') as output:
            output.write(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
        print(report['status'])
        if report['status'] != 'ANALYZED':
            raise SystemExit(1)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'SQL analysis failed: {exc}\n')


if __name__ == '__main__':
    main()
