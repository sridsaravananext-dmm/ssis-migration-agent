"""Bounded ODI XML compatibility inventory, separate from behavior/IR analysis.

Field values and encryption keys are never included. The unchanged source bytes
remain necessary for replay; this document cannot be used as migration evidence
of semantic implementation, dependency completeness or source equivalence.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ssis_migration.intake.inventory import has_unsafe_xml_declarations
from ssis_migration.schemas import SchemaValidationError, validate_document
from ..base import SourceEvidenceError, UnsupportedSourceError
from ..xml_export import MAX_BYTES, MAX_ELEMENTS, _decode_xml, local
from .adapter import ODIAdapter

PROFILE = 'odi_export_compatibility_v1'
MAX_PREFLIGHT_ELEMENTS = 100_000
MAX_DEPTH = 128
MAX_DISTINCT_VALUES = 4096
_ADMIN_ATTRIBUTES = ('RepositoryVersion', 'OdiVersion', 'IsSmartExportFile')
_SUPPORTED_TAGS = {'SUNOPSISEXPORT', 'ADMIN', 'EXPORTSUMMARY', 'NULL', 'OBJECT', 'FIELD'}


def _read_source(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise SourceEvidenceError('Supply a regular ODI XML export without symlink ancestors.')
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise UnsupportedSourceError('ODI compatibility preflight exceeds its 16 MiB byte limit.')
    return data


def _decode_inventory_xml(data):
    """Original ODI byte declarations; does not extend legacy XML IR support."""
    declaration = re.match(br'^(?:\xef\xbb\xbf)?\s*<\?xml\b[^?]*\?>', data, re.I)
    declared = re.search(br'\bencoding\s*=\s*([\'\"])([^\'\"]+)\1', declaration[0], re.I) if declaration else None
    if declared and declared[2].lower() == b'iso-8859-1':
        if data.startswith(b'\xef\xbb\xbf'):
            raise UnsupportedSourceError('ODI XML encoding conflicts with its byte-order mark.')
        return data.decode('iso-8859-1'), 'iso-8859-1'
    return _decode_xml(data)


def inspect_export(source_path):
    """Count original declarations without interpreting fields or expanding IR.

    Uses iterparse with completed elements detached. A larger preflight element
    budget does not change the smaller shared structural parser budget.
    """
    path = Path(source_path).expanduser().absolute()
    data = _read_source(path)
    if has_unsafe_xml_declarations(data):
        raise UnsupportedSourceError('ODI XML entities and internal DTD subsets are unsupported.')
    # Oracle's original explicitly declares ISO-8859-1, which the structural
    # parser does not accept. Inventory it without extending that parser or
    # rewriting the original declaration. Other encodings use its exact rules.
    _, encoding = _decode_inventory_xml(data)
    classes = Counter(); tags = Counter(); field_types = Counter()
    first_classes = {}; first_tags = {}; first_types = {}
    admins = []; encryption = []; stack = []
    element_count = 0; maximum_depth = 0
    root_versions = {}
    try:
        for event, element in ET.iterparse(io.BytesIO(data), events=('start', 'end')):
            if event == 'end':
                stack.pop()
                if stack:
                    stack[-1]['element'].remove(element)
                element.clear()
                continue
            tag = local(element.tag)
            if stack:
                counts = stack[-1]['counts']; counts[tag] += 1
                locator = stack[-1]['locator'] + '/' + tag + '[' + str(counts[tag]) + ']'
            else:
                if tag.upper() not in ODIAdapter.roots:
                    raise UnsupportedSourceError('ODI compatibility preflight requires a SunopsisExport root.')
                locator = '/' + tag + '[1]'
                root_versions = {key: element.attrib[key] for key in ('version', 'VERSION') if key in element.attrib}
            stack.append({'element': element, 'locator': locator, 'counts': Counter()})
            element_count += 1; maximum_depth = max(maximum_depth, len(stack))
            if element_count > MAX_PREFLIGHT_ELEMENTS or len(stack) > MAX_DEPTH:
                raise UnsupportedSourceError('ODI compatibility preflight element/depth limit exceeded; no partial report was produced.')
            tags[tag] += 1; first_tags.setdefault(tag, 'xml:' + locator)
            if tag.upper() == 'OBJECT':
                name = element.get('class')
                classes[name] += 1; first_classes.setdefault(name, 'xml:' + locator)
            elif tag.upper() == 'FIELD':
                name = element.get('type')
                field_types[name] += 1; first_types.setdefault(name, 'xml:' + locator)
            elif tag.upper() == 'ADMIN':
                admins.append({'locator': 'xml:' + locator,
                               'declarations': {key: element.attrib[key] for key in _ADMIN_ATTRIBUTES if key in element.attrib}})
            elif tag.upper() == 'ENCRYPTION':
                encryption.append({'locator': 'xml:' + locator,
                                   'contains_cipher_text': element.get('containsCipherText')})
            if max(len(classes), len(tags), len(field_types), len(admins), len(encryption)) > MAX_DISTINCT_VALUES:
                raise UnsupportedSourceError('ODI compatibility preflight distinct-declaration limit exceeded.')
    except ET.ParseError as exc:
        raise UnsupportedSourceError('ODI compatibility preflight requires well-formed XML.') from exc

    def class_supported(name):
        # Match ODIAdapter.object_class exactly, including its qualified prefix.
        value = name or ''
        if value.startswith('com.sunopsis.dwg.dbobj.'):
            value = value.rsplit('.', 1)[-1]
        return value in ODIAdapter.classes

    blockers = []
    if element_count > MAX_ELEMENTS: blockers.append('ELEMENT_LIMIT_EXCEEDED')
    if encoding not in ('utf-8', 'windows-1252'): blockers.append('UNSUPPORTED_STRUCTURAL_ENCODING')
    unsupported_classes = sum(count for name, count in classes.items() if not class_supported(name))
    unsupported_tags = sum(count for name, count in tags.items() if name.upper() not in _SUPPORTED_TAGS)
    actions = []
    if 'ELEMENT_LIMIT_EXCEEDED' in blockers:
        actions.append(f'This export has {element_count:,} elements; the structural IR parser limit is {MAX_ELEMENTS:,}. A larger ODI-specific structural profile is required before IR/contracts can be built.')
    if 'UNSUPPORTED_STRUCTURAL_ENCODING' in blockers:
        actions.append('The original explicitly declares ISO-8859-1; the structural parser accepts UTF-8 or explicitly declared Windows-1252. Add and validate original-byte encoding support before IR/contracts can be built; do not relabel the source bytes.')
    if unsupported_classes or unsupported_tags:
        actions.append('Implement and validate the unsupported ODI classes/tags from real source evidence; retain all unknown behavior as blockers.')
    actions.extend([
        'Supply complete model/topology/context and Knowledge Module dependencies. Dependency completeness has not been analyzed by this inventory.',
        'Validate version-specific mapping, command, transaction and error behavior before Databricks generation.',
        'Supply independent source execution/input/output evidence for execution and equivalence gates.',
    ])
    report = {
        'report_version': '1.0', 'profile': PROFILE, 'scope': 'EXPORT_COMPATIBILITY_ONLY',
        'source': {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)},
        'format': 'odi_xml', 'encoding': encoding,
        'version_declarations': {'root': root_versions, 'admin': admins},
        'encryption_declarations': encryption,
        'inventory': {
            'element_count': element_count, 'maximum_depth': maximum_depth,
            'object_count': sum(classes.values()), 'field_count': sum(field_types.values()),
            'object_classes': [{'class_name': name, 'count': count, 'structure_supported': class_supported(name),
                                'representative_locator': first_classes[name]}
                               for name, count in sorted(classes.items(), key=lambda item: item[0] or '')],
            'element_tags': [{'tag': name, 'count': count, 'structure_supported': name.upper() in _SUPPORTED_TAGS,
                              'representative_locator': first_tags[name]} for name, count in sorted(tags.items())],
            'field_types': [{'declared_type': name, 'count': count, 'representative_locator': first_types[name]}
                            for name, count in sorted(field_types.items(), key=lambda item: item[0] or '')],
        },
        'structural_parser': {'profile': 'odi_xml_structure_v1',
                              'eligibility': 'BLOCKED' if blockers else 'WITHIN_PREFLIGHT_LIMITS',
                              'blockers': blockers,
                              'max_bytes': MAX_BYTES, 'max_elements': MAX_ELEMENTS, 'max_depth': MAX_DEPTH,
                              'unsupported_object_count': unsupported_classes,
                              'unsupported_element_count': unsupported_tags,
                              'dependency_status': 'NOT_ANALYZED'},
        'preflight_limits': {'max_bytes': MAX_BYTES, 'max_elements': MAX_PREFLIGHT_ELEMENTS,
                             'max_depth': MAX_DEPTH, 'max_distinct_values': MAX_DISTINCT_VALUES},
        'status': 'BLOCKED', 'semantic_status': 'NOT_VERIFIED', 'execution_status': 'NOT_RUN',
        'migration_accepted': False, 'required_actions': actions,
    }
    validate_document(report, 'odi_export_compatibility')
    if _read_source(path) != data:
        raise SourceEvidenceError('ODI source changed during compatibility preflight.')
    return report


def validate_preflight(report, source_path):
    """Recompute from unchanged bytes; original source may have been relocated.

    Source path is a historical label, not an identity or an attestation. No
    source field values or source snapshot are embedded in the report.
    """
    validate_document(report, 'odi_export_compatibility')
    expected = inspect_export(source_path)
    expected['source']['path'] = report['source']['path']
    if report != expected:
        raise SchemaValidationError('ODI compatibility report does not replay from its original source bytes.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only ODI XML compatibility inventory; no migration or workload execution.')
    parser.add_argument('--source', required=True, help='Extracted native Sunopsis XML export')
    parser.add_argument('--output', required=True, help='New JSON report path (existing files are not overwritten)')
    args = parser.parse_args(argv)
    try:
        report = inspect_export(args.source)
        with Path(args.output).expanduser().open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')
    print('ODI compatibility report: ' + report['structural_parser']['eligibility'] + '; migration BLOCKED. ' + args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
