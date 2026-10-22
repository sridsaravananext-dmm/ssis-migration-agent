"""Explicit file-backed TM1 bundle intake and replayable source analysis."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

from ssis_migration.intake import describe_file
from ssis_migration.ir.normalized import content_digest
from ssis_migration.languages.mdx import analyze_mdx
from ssis_migration.languages.mdx.analysis import mdx_analysis_id
from ssis_migration.schemas import SchemaValidationError, validate_document
from ..base import SourceEvidenceError, UnsupportedSourceError
from .metadata import bind_tm1_metadata, metadata_snapshot, validate_metadata_snapshot
from .metadata.catalog import read_json
from .rules import analyze_rules
from .rules.analysis import rules_analysis_id
from .ti import analyze_ti_sections, ti_analysis_id

COMPACT_FIELDS = ('binding_report_id', 'status', 'bindings', 'metadata_issues', 'runtime_requirements')


def reference_path(parent, reference):
    return Path(os.path.abspath(Path(parent).parent / Path(reference).expanduser()))


def bundle_id(bundle):
    return 'tm1-bundle-' + content_digest({k: v for k, v in bundle.items() if k != 'bundle_id'})


def snapshot(raw, file_id):
    return {'file_id': file_id, 'text': raw.decode('utf-8'),
            'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}


def _bytes(source):
    raw = source['text'].encode('utf-8')
    if source['sha256'] != hashlib.sha256(raw).hexdigest() or source['size_bytes'] != len(raw):
        raise SourceEvidenceError('TM1 preserved file snapshot hash/size is stale.')
    return raw


class BundleReader:
    def __init__(self, source_root):
        self.source_root = source_root
        self.records = {}
        self.snapshots = {}

    def read(self, path, expected_sha=None):
        path = Path(os.path.abspath(Path(path).expanduser()))
        if path.is_symlink() or not path.is_file():
            raise SourceEvidenceError('Missing/non-regular TM1 bundle input: ' + str(path))
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha is not None and digest != expected_sha:
            raise SourceEvidenceError('TM1 bundle input differs from its declared SHA-256: ' + str(path))
        record = describe_file(path, self.source_root)
        if record['platform'] not in {'tm1','unknown'} or path.suffix.lower() in {'.dsx','.mp'}:
            raise UnsupportedSourceError('TM1 bundle cannot override conflicting/excluded source content: '+str(path))
        if record['sha256'] != digest or record['size_bytes'] != len(raw):
            raise SourceEvidenceError('TM1 bundle input changed during inventory: ' + str(path))
        if path in self.snapshots and self.snapshots[path] != raw:
            raise SourceEvidenceError('TM1 input changed between declared uses: ' + str(path))
        self.snapshots[path] = raw
        self.records[record['file_id']] = record
        return raw, record

    def reference(self, parent, entry):
        return self.read(reference_path(parent, entry['path']), entry['sha256'])

    def verify(self):
        for path, raw in self.snapshots.items():
            if path.is_symlink() or path.read_bytes() != raw:
                raise SourceEvidenceError('TM1 bundle input changed during analysis: ' + str(path))


def _file_info(record):
    return {key: record[key] for key in ('path','sha256','size_bytes')}


def analyze_text(language, raw, record):
    text = raw.decode('utf-8-sig')
    if language == 'mdx':
        result = analyze_mdx(text, dialect='tm1', file_id=record['file_id'])
        identity = mdx_analysis_id
    else:
        result = analyze_rules(text, export_format='plain_rux_text', file_id=record['file_id'])
        identity = rules_analysis_id
    result['source_file'] = _file_info(record)
    result['analysis_id'] = identity(result)
    return result


def analyze_ti_manifest(raw, record, read_section):
    document = read_json(raw)
    validate_document(document, 'tm1_ti_manifest')
    sections, originals = {}, {}
    for name, entry in document['sections'].items():
        data, source = read_section(name, entry)
        sections[name] = {'text': data.decode('utf-8-sig'), 'file_id': source['file_id'], 'locator': 'file:/'}
        originals[name] = _file_info(source)
    result = analyze_ti_sections(sections, export_format=document['export_format'],
                                 process_name=document['process_name'], source_export_version=document['source_export_version'])
    result['manifest_file'] = {**_file_info(record), 'file_id': record['file_id']}
    for section in result['sections']:
        section['source_file'] = originals.get(section['name'])
    result['analysis_id'] = ti_analysis_id(result)
    return result


def read_bundle(path, file_record=None):
    reader = BundleReader(Path(os.path.abspath(Path(path).expanduser())).parent)
    raw, record = reader.read(path)
    if len(raw) > 1_048_576:
        raise ValueError('TM1 bundle manifest exceeds 1 MiB.')
    document = read_json(raw)
    validate_document(document, 'tm1_source_bundle')
    if file_record is not None and file_record != record:
        raise SourceEvidenceError('Supplied TM1 source inventory conflicts with current bytes or detection.')
    names = [u['unit_id'] for u in document['units']]
    if len(names) != len(set(names)):
        raise ValueError('TM1 bundle unit IDs must be unique; names are not execution order.')
    metadata = []
    for entry in document['metadata_files']:
        data, source = reader.reference(path, entry)
        metadata.append(metadata_snapshot(data, file_id=source['file_id'], path=source['path']))
    units = []
    for index, entry in enumerate(document['units']):
        data, source = reader.reference(path, entry)
        if entry['language'] == 'tm1_turbointegrator':
            analysis = analyze_ti_manifest(data, source, lambda name, part: reader.reference(source['path'], part))
        else:
            analysis = analyze_text(entry['language'], data, source)
        binding = bind_tm1_metadata(analysis, metadata, source_cube=entry['source_cube'], process_name=entry['process_name'])
        units.append({'unit_id': entry['unit_id'], 'manifest_index': index, 'source_file': snapshot(data, source['file_id']),
                      'analysis': analysis, 'metadata_binding': {key: binding[key] for key in COMPACT_FIELDS}})
    bundle = {'bundle_version': '1.0', 'platform': 'tm1', 'manifest': {**snapshot(raw, record['file_id']), 'document': document},
              'metadata_inputs': metadata, 'units': units}
    bundle['bundle_id'] = bundle_id(bundle)
    reader.verify()
    validate_bundle(bundle, list(reader.records.values()))
    return bundle, list(reader.records.values()), reader


def validate_bundle(bundle, evidence_files):
    """Replay parsers and bindings from preserved bytes, without opening paths."""
    validate_document(bundle, 'tm1_bundle_analysis')
    if bundle['bundle_id'] != bundle_id(bundle):
        raise SchemaValidationError('TM1 source bundle identity is stale.')
    files = {f['file_id']: f for f in evidence_files}
    if len(files) != len(evidence_files):
        raise SchemaValidationError('Duplicate TM1 evidence file IDs.')
    consumed = set()

    def original(source, expected_sha=None):
        raw = _bytes(source)
        record = files.get(source['file_id'])
        if (record is None or record['status'] != 'AVAILABLE' or record['sha256'] != source['sha256']
            or record['size_bytes'] != len(raw) or (expected_sha is not None and source['sha256'] != expected_sha)):
            raise SourceEvidenceError('TM1 source snapshot does not match inventoried/declared input bytes.')
        consumed.add(source['file_id'])
        return raw, record

    raw, primary = original(bundle['manifest'])
    document = read_json(raw)
    if document != bundle['manifest']['document']:
        raise SchemaValidationError('TM1 manifest document differs from its preserved source bytes.')
    if len(document['metadata_files']) != len(bundle['metadata_inputs']):
        raise SchemaValidationError('TM1 metadata inputs were omitted or added.')
    for declaration, meta in zip(document['metadata_files'], bundle['metadata_inputs']):
        validate_metadata_snapshot(meta)
        original(meta['source'], declaration['sha256'])
        expected = reference_path(primary['path'], declaration['path'])
        if Path(meta['source']['path']) != expected:
            raise SourceEvidenceError('TM1 metadata role refers to a different file path.')
    if len(bundle['units']) != len(document['units']) or len({u['unit_id'] for u in bundle['units']}) != len(bundle['units']):
        raise SchemaValidationError('TM1 units must preserve every manifest entry exactly once.')
    for index, (entry, unit) in enumerate(zip(document['units'], bundle['units'])):
        if unit['manifest_index'] != index or unit['unit_id'] != entry['unit_id']:
            raise SchemaValidationError('TM1 manifest unit identity/order is stale.')
        data, record = original(unit['source_file'], entry['sha256'])
        if Path(record['path']) != reference_path(primary['path'], entry['path']):
            raise SourceEvidenceError('TM1 analysis unit refers to a different file path.')
        if entry['language'] == 'tm1_turbointegrator':
            def section_bytes(name, declaration):
                section = next(s for s in unit['analysis']['sections'] if s['name'] == name)
                if section['source'] is None or section['source_file'] is None:
                    raise SourceEvidenceError('A declared TI section cannot be silently omitted.')
                text = section['source']['text'].encode('utf-8')
                if hashlib.sha256(text).hexdigest() != declaration['sha256']:
                    text = b'\xef\xbb\xbf' + text
                evidence = {**section['source'], 'text': text.decode('utf-8'), **section['source_file']}
                _, section_record = original(evidence, declaration['sha256'])
                if Path(section_record['path']) != reference_path(record['path'], declaration['path']):
                    raise SourceEvidenceError('TI section does not match its declared file path.')
                return text, section_record
            expected = analyze_ti_manifest(data, record, section_bytes)
        else:
            expected = analyze_text(entry['language'], data, record)
        if unit['analysis'] != expected:
            raise SchemaValidationError('TM1 unit analysis differs from replay of its original source files.')
        binding = bind_tm1_metadata(expected, bundle['metadata_inputs'], source_cube=entry['source_cube'], process_name=entry['process_name'])
        if unit['metadata_binding'] != {key: binding[key] for key in COMPACT_FIELDS}:
            raise SchemaValidationError('TM1 metadata binding omitted or changed source requirements.')
    if consumed != set(files):
        raise SchemaValidationError('TM1 evidence inventory contains unconsumed or omitted file roles.')
