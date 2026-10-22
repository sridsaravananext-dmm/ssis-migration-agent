"""Bind saved TM1 analysis to explicit metadata JSON files, entirely offline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .binding import bind_tm1_metadata, validate_tm1_binding_report
from .catalog import load_tm1_metadata, read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, action='append', required=True)
    parser.add_argument('--source-cube')
    parser.add_argument('--process-name')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        path = args.analysis.expanduser().absolute()
        if path.is_symlink() or not path.is_file():
            raise ValueError('Supply a regular saved TM1 analysis JSON file, not a symlink.')
        raw = path.read_bytes()
        analysis = read_json(raw)
        metadata = [load_tm1_metadata(p) for p in args.metadata]
        report = bind_tm1_metadata(analysis, metadata, source_cube=args.source_cube, process_name=args.process_name)
        validate_tm1_binding_report(report)
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError('TM1 analysis input changed during metadata binding.')
        for snapshot in metadata:
            source = Path(snapshot['source']['path'])
            if source.is_symlink() or source.read_bytes() != snapshot['source']['text'].encode('utf-8'):
                raise ValueError('TM1 metadata input changed during binding.')
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
        print(json.dumps({'status': report['status'], 'binding_count': len(report['bindings']),
                          'unresolved_binding_count': sum(b['status'] != 'MATCHED_DECLARATION' for b in report['bindings']),
                          'semantic_status': report['semantic_status'], 'migration_accepted': report['migration_accepted']}))
        return 0 if report['status'] == 'BOUND_REVIEW_REQUIRED' else 1
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
