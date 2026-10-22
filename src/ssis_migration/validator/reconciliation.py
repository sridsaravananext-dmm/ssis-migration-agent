"""Compare supplied observations offline; never run vendor or target workloads.

Bindings and provenance are operator attestations, not authenticated captures.
Typed exact comparison preserves duplicate counts, nulls and optional row order.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

from ssis_migration.metadata.json_io import read_json
from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import validate_document


def utc_timestamp(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)',value):
        raise ValueError('Timestamps require explicit UTC and at most microsecond precision.')
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def cell(value,column):
    if value is None:
        if not column['nullable']: raise ValueError('Null in nonnullable column: '+column['name'])
        return ['NULL']
    kind = column['type']
    if kind == 'INT64':
        if type(value) is not int or not -(1<<63) <= value < (1<<63): raise ValueError('INT64 values must be exact signed 64-bit integers.')
        return [kind,value]
    if kind == 'BOOLEAN':
        if type(value) is not bool: raise ValueError('BOOLEAN values must be JSON booleans.')
        return [kind,value]
    if not isinstance(value,str): raise ValueError(kind+' values must be explicit strings.')
    if kind == 'DECIMAL':
        if not re.fullmatch(r'[+-]?[0-9]{1,38}(?:\.[0-9]{1,38})?',value):
            raise ValueError('DECIMAL requires an exact decimal string with bounded precision, without exponents.')
        number = Decimal(value)
        if len(number.as_tuple().digits) > 38: raise ValueError('DECIMAL precision exceeds 38 digits.')
        rational = Fraction(number)
        return [kind,rational.numerator,rational.denominator]
    if kind == 'DATE':
        if date.fromisoformat(value).isoformat() != value: raise ValueError('DATE values require ISO YYYY-MM-DD.')
    elif kind == 'TIMESTAMP_UTC': value = utc_timestamp(value)
    elif kind != 'STRING': raise ValueError('Unsupported reconciliation data type.')
    return [kind,value]


def validate_observation(document):
    validate_document(document,'data_observation')
    content_digest(document)  # Refuse non-finite JSON and invalid Unicode.
    utc_timestamp(document['provenance']['captured_at'])
    names = [c['name'].casefold() for c in document['columns']]
    if len(set(names)) != len(names): raise ValueError('Observation column names must be unique, including case folding.')
    kind = document['provenance']['kind']
    if (document['side'] == 'SOURCE' and kind == 'TARGET_ENGINE') or (document['side'] == 'TARGET' and kind == 'SOURCE_ENGINE'):
        raise ValueError('Observation engine provenance conflicts with its side.')
    rows = []
    for row in document['rows']:
        if len(row) != len(document['columns']): raise ValueError('Observation row width does not match its declared columns.')
        rows.append(json.dumps([cell(value,col) for value,col in zip(row,document['columns'])],ensure_ascii=False,separators=(',',':')))
    return rows


def read_observation(path):
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file() or path.stat().st_size > 8*1048576:
        raise ValueError('Supply a regular observation file up to 8 MiB without symlink ancestors.')
    raw = path.read_bytes();document = read_json(raw)
    validate_observation(document)
    if path.read_bytes() != raw: raise ValueError('Observation changed during loading.')
    return document, {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw)}


def reconcile(source,target):
    left = validate_observation(source);right = validate_observation(target)
    if source['side'] != 'SOURCE' or target['side'] != 'TARGET':
        raise ValueError('Reconciliation requires a source observation and a target observation.')
    disagreements = [key for key in ('source_platform','case_id','unit_id','binding','comparison_mode','columns') if source[key] != target[key]]
    eligible = source['provenance']['kind']=='SOURCE_ENGINE' and target['provenance']['kind']=='TARGET_ENGINE'
    counts = None
    if not disagreements:
        missing = Counter(left)-Counter(right);extra = Counter(right)-Counter(left)
        matched = left == right if source['comparison_mode']=='ORDERED' else not missing and not extra
        counts = {'source_rows':len(left),'target_rows':len(right),'missing_rows':sum(missing.values()),
                  'extra_rows':sum(extra.values()),'order_matches':left==right if source['comparison_mode']=='ORDERED' else None}
        status = 'MATCH' if matched else 'MISMATCH'
    else: status = 'INCOMPARABLE'
    return {'reconciliation_version':'1.0','status':status,'case_id':source['case_id'],'unit_id':source['unit_id'],
            'source_platform':source['source_platform'],'binding':source['binding'],
            'observation_digests':{'source':content_digest(source),'target':content_digest(target)},
            'comparison_mode':source['comparison_mode'],'incomparable_fields':disagreements,'counts':counts,
            'evidence_scope':'OPERATOR_ATTESTED_ENGINE_OBSERVATIONS' if eligible else 'SYNTHETIC_FIXTURES_ONLY',
            'source_equivalence':'MATCHED_OBSERVED_CASE_ONLY' if eligible and status=='MATCH' else 'NOT_VERIFIED',
            'migration_accepted':False,
            'limitations':['Capture provenance and bindings are operator-supplied and not authenticated.',
                           'A matching observed case does not establish weighted behavior coverage or migration acceptance.']}


def validate_engine_reports(reports, *, report_root, package):
    """Replay native acceptance gate comparisons from contained original captures."""
    root = Path(report_root).resolve(strict=True)

    def supplied(reference):
        path = root / reference['path']
        if not path.resolve().is_relative_to(root) or not path.is_file() or any(p.is_symlink() for p in (path,*path.parents)):
            raise ValueError('Observation evidence must remain inside the evidence report folder without symlinks.')
        if path.stat().st_size > 8*1048576: raise ValueError('Observation evidence exceeds the 8 MiB limit.')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != reference['sha256']: raise ValueError('Observation evidence hash changed.')
        return read_json(raw)

    covered = set()
    for reference in reports:
        document = supplied(reference)
        if 'observation_files' not in document: raise ValueError('Engine evidence needs original source and target observation files.')
        files = document['observation_files']
        if set(files) != {'source','target'}: raise ValueError('Engine evidence requires both original captures.')
        source = supplied(files['source']); target = supplied(files['target'])
        expected = reconcile(source,target)
        if {k:v for k,v in document.items() if k!='observation_files'} != expected:
            raise ValueError('Observation reconciliation report does not replay from its original captures.')
        if expected['status'] != 'MATCH' or expected['evidence_scope'] != 'OPERATOR_ATTESTED_ENGINE_OBSERVATIONS':
            raise ValueError('Synthetic or mismatching observations cannot satisfy the native source-equivalence gate.')
        binding = expected['binding']
        if any(binding[key] != value for key,value in package['observation_binding'].items()):
            raise ValueError('Observation capture is bound to a different source, contract or artifact set.')
        if expected['source_platform'] != package['source_platform'] or expected['unit_id'] not in package['unit_ids']:
            raise ValueError('Observation capture references an unknown native source unit.')
        covered.add(expected['unit_id'])
    if covered != set(package['unit_ids']):
        raise ValueError('Independent source and target observations must cover every native source unit.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-observation',type=Path,required=True)
    parser.add_argument('--target-observation',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    try:
        source, sref = read_observation(args.source_observation)
        target, tref = read_observation(args.target_observation)
        report = reconcile(source,target);report['observation_files'] = {'source':sref,'target':tref}
        with args.output.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2,ensure_ascii=False,allow_nan=False)
        print(report['status'])
        if report['status'] != 'MATCH': raise SystemExit(1)
    except (ValueError,OSError,TypeError,KeyError) as exc:
        parser.exit(2,'Reconciliation failed: '+str(exc)+'\n')


if __name__ == '__main__': main()
