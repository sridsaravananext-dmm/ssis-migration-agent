"""Analyze a supplied MDX export offline; never generate or execute workloads."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ssis_migration.intake.inventory import describe_file
from ssis_migration.languages.mdx import analyze_mdx, mdx_analysis_id, validate_mdx_analysis


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--dialect', required=True, choices=['tm1'])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        path = args.source.expanduser().absolute()
        if path.is_symlink() or not path.is_file():
            raise ValueError('Supply a regular exported MDX file, not a symlink.')
        data = path.read_bytes()
        file_record = describe_file(path)
        if file_record['sha256'] != hashlib.sha256(data).hexdigest():
            raise ValueError('MDX export changed during inventory.')
        report = analyze_mdx(data.decode('utf-8-sig'), dialect=args.dialect, file_id=file_record['file_id'])
        report['source_file'] = {'path': str(path), 'sha256': file_record['sha256'], 'size_bytes': len(data)}
        report['analysis_id'] = mdx_analysis_id(report)
        validate_mdx_analysis(report)
        if path.read_bytes() != data:
            raise ValueError('MDX export changed during analysis.')
        with args.output.open('x', encoding='utf-8') as output:
            json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
            output.write('\n')
        print(json.dumps({'status': report['status'], 'semantic_status': report['semantic_status'],
                          'missing_dependency_count': len(report['dependencies'])}))
        return 0 if report['status'] == 'STRUCTURED' else 1
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
