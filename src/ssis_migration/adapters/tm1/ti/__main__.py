"""Analyze explicitly mapped TI section files offline; no source execution."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ssis_migration.intake.inventory import describe_file
from ssis_migration.schemas import validate_document
from .analysis import analyze_ti_sections, ti_analysis_id, validate_ti_analysis


def _read_json(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate TI manifest key: '+key)
            result[key]=value
        return result
    def nonfinite(value):raise ValueError('Non-finite TI manifest value: '+value)
    return json.loads(data.decode('utf-8-sig'),object_pairs_hook=unique,parse_constant=nonfinite)


def analyze_manifest(path: str | Path):
    """Read, inventory and verify exactly the files explicitly mapped by roles."""
    path=Path(path).expanduser().absolute()
    if path.is_symlink() or not path.is_file():raise ValueError('Supply a regular TI section manifest, not a symlink.')
    raw=path.read_bytes();manifest=_read_json(raw);validate_document(manifest,'tm1_ti_manifest')
    manifest_record=describe_file(path)
    if manifest_record['sha256']!=hashlib.sha256(raw).hexdigest():raise ValueError('TI manifest changed during inventory.')
    sections={};files={};snapshots=[(path,raw)]
    for name,entry in manifest['sections'].items():
        supplied=Path(entry['path']).expanduser()
        source=supplied if supplied.is_absolute() else path.parent/supplied
        source=source.absolute()
        if source.is_symlink() or not source.is_file():raise ValueError('Missing/non-regular TI section source: '+str(source))
        data=source.read_bytes()
        if hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('TI section does not match its supplied SHA-256: '+name)
        record=describe_file(source)
        if record['sha256']!=entry['sha256']:raise ValueError('TI section changed during inventory: '+name)
        sections[name]={'text':data.decode('utf-8-sig'),'file_id':record['file_id'],'locator':'file:/'}
        files[name]={'path':str(source),'sha256':record['sha256'],'size_bytes':len(data)}
        snapshots.append((source,data))
    report=analyze_ti_sections(sections,export_format=manifest['export_format'],process_name=manifest['process_name'],
                              source_export_version=manifest['source_export_version'])
    report['manifest_file']={'path':str(path),'sha256':manifest_record['sha256'],'size_bytes':len(raw),'file_id':manifest_record['file_id']}
    for section in report['sections']:
        if section['name'] in files:section['source_file']=files[section['name']]
    report['analysis_id']=ti_analysis_id(report);validate_ti_analysis(report)
    for source,original in snapshots:
        if source.is_symlink() or source.read_bytes()!=original:raise ValueError('TI manifest/section changed during analysis: '+str(source))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        report=analyze_manifest(args.manifest)
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(report,stream,indent=2,ensure_ascii=False,allow_nan=False);stream.write('\n')
        print(json.dumps({'status':report['status'],'section_count':len(report['sections']),
                          'missing_sections':report['missing_sections'],'effect_count':len(report['effects']),
                          'semantic_status':report['semantic_status']}))
        return 0 if report['status']=='STRUCTURED' else 1
    except (OSError,ValueError) as exc:parser.exit(2,str(exc)+'\n')


if __name__=='__main__':raise SystemExit(main())
