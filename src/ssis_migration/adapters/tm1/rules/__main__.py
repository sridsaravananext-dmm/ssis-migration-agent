"""Analyze a plain TM1 .rux export offline; never evaluate source rules/feeders."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ssis_migration.intake.inventory import describe_file
from .analysis import analyze_rules, rules_analysis_id, validate_rules_analysis


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--format',required=True,choices=['plain-rux-text'])
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        path=args.source.expanduser().absolute()
        if path.is_symlink() or not path.is_file(): raise ValueError('Supply a regular exported rules file, not a symlink.')
        data=path.read_bytes(); record=describe_file(path)
        if record['sha256']!=hashlib.sha256(data).hexdigest(): raise ValueError('Rules export changed during inventory.')
        report=analyze_rules(data.decode('utf-8-sig'),export_format='plain_rux_text',file_id=record['file_id'])
        report['source_file']={'path':str(path),'sha256':record['sha256'],'size_bytes':len(data)}
        report['analysis_id']=rules_analysis_id(report)
        validate_rules_analysis(report)
        if path.read_bytes()!=data: raise ValueError('Rules export changed during analysis.')
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(report,stream,indent=2,ensure_ascii=False,allow_nan=False); stream.write('\n')
        print(json.dumps({'status':report['status'],'semantic_status':report['semantic_status'],
                          'statement_count':len(report['statements']),'feeder_count':len(report['feeder_relations']),
                          'missing_dependency_count':len(report['dependencies'])}))
        return 0 if report['status']=='STRUCTURED' else 1
    except (OSError,ValueError) as exc: parser.exit(2,str(exc)+'\n')


if __name__=='__main__': raise SystemExit(main())
