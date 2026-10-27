"""Bounded XML evidence handling shared by file-only vendor adapters.

This module knows XML and neutral graph records, never vendor semantics. Each
adapter classifies elements and resolves only relationships its format declares.
"""
from __future__ import annotations

import copy
import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ssis_migration.intake import describe_file
from ssis_migration.ir.normalized import content_digest, normalized_ir_id, validate_normalized_ir
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from .base import SourceAdapterResult, SourceEvidenceError, UnsupportedSourceError

MAX_BYTES = 16 * 1024 * 1024
MAX_ELEMENTS = 10000


def _decode_xml(data):
    """Honor bounded, explicit XML encodings without guessing or rewriting bytes."""
    declaration = re.match(br'^(?:\xef\xbb\xbf)?\s*<\?xml\b[^?]*\?>', data, re.I)
    encoding = re.search(br'\bencoding\s*=\s*([\'"])([^\'"]+)\1', declaration[0], re.I) if declaration else None
    codecs = {b'utf-8': 'utf-8', b'utf8': 'utf-8', b'windows-1252': 'windows-1252'}
    codec = codecs.get(encoding[2].lower()) if encoding else 'utf-8'
    if codec is None:
        raise UnsupportedSourceError('XML encoding is unsupported; supply UTF-8 or explicitly declared Windows-1252.')
    if data.startswith(b'\xef\xbb\xbf') and codec != 'utf-8':
        raise UnsupportedSourceError('XML encoding declaration conflicts with its UTF-8 byte-order mark.')
    try:
        return data.decode(codec), codec
    except UnicodeError as exc:
        raise UnsupportedSourceError('XML bytes do not match their supported declared encoding.') from exc


def local(tag):
    return tag.rsplit('}', 1)[-1]


def snapshot_bytes(snapshot, record):
    """Reconstruct exact XML evidence independently of the original file path."""
    if not isinstance(snapshot, dict):
        raise SchemaValidationError('XML source snapshot must be an object.')
    encoding = snapshot.get('encoding', 'utf-8')
    if encoding not in ('utf-8', 'windows-1252'):
        raise SchemaValidationError('XML source snapshot encoding is unsupported.')
    try:
        data = snapshot['text'].encode(encoding)
    except (UnicodeError, KeyError, TypeError, AttributeError) as exc:
        raise SchemaValidationError('XML source snapshot cannot reproduce its original bytes.') from exc
    if (hashlib.sha256(data).hexdigest() != record['sha256'] or len(data) != record['size_bytes']
            or snapshot.get('sha256') != record['sha256']):
        raise SchemaValidationError('XML source snapshot does not match its evidence hash and size.')
    return data


class ExportGraph:
    def __init__(self, platform, export_format, record, text, version, encoding='utf-8'):
        self.record, self.text = copy.deepcopy(record), text
        self.encoding = encoding
        self.source = {'platform': platform, 'format': export_format, 'export_version': version,
                       'file_id': record['file_id']}
        self.nodes, self.edges, self.diagnostics = [], [], []
        self.elements, self.ids, self.paths, self.parents = [], {}, {}, {}

    def evidence(self, path, rule='export_xml_structure_only'):
        return [{'file_id': self.record['file_id'], 'locator': 'xml:' + path, 'rule': rule}]

    def diagnostic(self, code, message, path, severity='WARNING'):
        self.diagnostics.append({'code': code, 'severity': severity, 'message': message,
                                 'file_ids': [self.record['file_id']], 'evidence': self.evidence(path)})

    def node(self, path, kind, operation, name=None, attributes=None):
        identity = 'node-' + content_digest([self.record['file_id'], path])[:24]
        self.nodes.append({'node_id': identity, 'kind': kind, 'operation': operation, 'name': name,
                           'source_ref': path, 'semantic_status': 'NOT_VERIFIED',
                           'attributes': copy.deepcopy(attributes or {}), 'extensions': {},
                           'evidence': self.evidence(path)})
        return identity

    def edge(self, kind, start, end, path, attributes=None):
        edge = {'kind': kind, 'source_id': start, 'target_id': end,
                'resolution': 'RESOLVED' if start is not None and end is not None else 'UNRESOLVED',
                'attributes': copy.deepcopy(attributes or {}), 'extensions': {}, 'evidence': self.evidence(path)}
        edge['edge_id'] = 'edge-' + content_digest([len(self.edges), edge])[:24]
        self.edges.append(edge)
        if edge['resolution'] == 'UNRESOLVED':
            self.diagnostic('UNRESOLVED_GRAPH_REFERENCE', edge['edge_id'] + ': Declared reference is missing or ambiguous; no definition was invented.', path, 'ERROR')

    def scope(self, element, tag):
        while element is not None:
            if local(element.tag).upper() == tag.upper():
                return element
            element = self.parents.get(element)
        return None

    def finish(self, profile, *, analysis_metadata=None):
        snapshot = {'text': self.text, 'sha256': self.record['sha256']}
        # Keep historical UTF-8 snapshots/graphs byte-for-byte compatible.
        if self.encoding != 'utf-8':
            snapshot['encoding'] = self.encoding
        analysis = {'analysis_version': '1.0', 'profile': profile, 'source': self.source,
                    'source_snapshot': snapshot,
                    'semantic_status': 'NOT_VERIFIED'}
        if analysis_metadata is not None:
            analysis['metadata'] = copy.deepcopy(analysis_metadata)
        graph = {'ir_version': '2.0', 'ir_id': '', 'source': copy.deepcopy(self.source),
                 'evidence_files': [self.record], 'nodes': self.nodes, 'edges': self.edges,
                 'diagnostics': self.diagnostics, 'semantic_status': 'NOT_VERIFIED',
                 'status': 'BLOCKED' if any(d['severity'] == 'ERROR' for d in self.diagnostics) else 'REVIEW_REQUIRED',
                 'capability_gaps': ['vendor_runtime_semantics', 'target_translation', 'source_equivalence'],
                 'provenance': {'builder': profile, 'version': '1.0', 'source_analysis_sha256': content_digest(analysis),
                                'evidence_scope': 'SOURCE_ANALYSIS',
                                'limitations': ['Structural extraction does not prove execution or semantic equivalence.']},
                 'extensions': {'source_analysis': analysis}}
        graph['ir_id'] = normalized_ir_id(graph)
        validate_normalized_ir(graph)
        return graph


class XMLExportAdapter:
    """Subclasses supply signature, element classification and declared references."""
    platform = ''
    export_format = ''
    roots = frozenset()

    def classify(self, element):
        raise NotImplementedError

    def relationships(self, graph):
        pass

    def export_version(self, root):
        return root.get('version') or root.get('VERSION')

    def _build(self, data, record):
        if len(data) > MAX_BYTES:
            raise UnsupportedSourceError('XML export exceeds the 16 MiB offline analysis limit.')
        # PowerCenter commonly declares an external DTD. ElementTree does not
        # retrieve it. Internal subsets/entities are rejected before parsing.
        from ssis_migration.intake.inventory import has_unsafe_xml_declarations
        if has_unsafe_xml_declarations(data):
            raise UnsupportedSourceError('XML entities and internal DTD subsets are unsupported.')
        text, encoding = _decode_xml(data)
        try:
            root = ET.fromstring(text.removeprefix('\ufeff'))
        except (ET.ParseError, RecursionError) as exc:
            raise UnsupportedSourceError('Invalid XML export: ' + str(exc)) from exc
        if local(root.tag).upper() not in self.roots:
            raise UnsupportedSourceError(f'{self.platform} adapter does not support XML root {local(root.tag)!r}.')
        graph = ExportGraph(self.platform, self.export_format, record, text, self.export_version(root), encoding)

        def visit(element, path, parent=None, depth=0):
            if len(graph.elements) >= MAX_ELEMENTS or depth > 128:
                raise UnsupportedSourceError('XML export exceeds the element/depth analysis limits.')
            graph.elements.append(element); graph.paths[element] = path; graph.parents[element] = parent
            kind, operation, attributes, supported = self.classify(element)
            attributes = {**attributes, 'export_attributes': dict(element.attrib),
                          'export_text': element.text or '', 'export_tail': element.tail or '',
                          'structure_supported': supported}
            if parent is None:
                kind = 'TASK'; attributes.update(analysis_unit=True, analysis_status='STRUCTURED')
            identity = graph.node(path, kind, operation, element.get('NAME') or element.get('name'), attributes)
            graph.ids[element] = identity
            if parent is not None:
                graph.edge('CONTAINS', graph.ids[parent], identity, path)
            if not supported:
                graph.diagnostic('UNSUPPORTED_CONSTRUCT', f'{local(element.tag)} is retained without an implemented semantic interpretation.', path, 'ERROR')
            counts = {}
            for child in element:
                tag = local(child.tag); counts[tag] = counts.get(tag, 0) + 1
                visit(child, path + '/' + tag + '[' + str(counts[tag]) + ']', element, depth+1)

        visit(root, '/' + local(root.tag) + '[1]')
        graph.diagnostic('EXPORT_VERSION_SEMANTICS_UNVERIFIED',
                         f'Export version {graph.source["export_version"]!r} is retained; this versioned structural profile does not certify vendor execution semantics.', graph.paths[root])
        self.relationships(graph)
        return graph.finish(self.export_format + '_structure_v1')

    def analyze(self, source_path, *, procedures_path=None, file_record=None):
        if procedures_path is not None:
            raise UnsupportedSourceError('Native XML exports do not accept SSIS procedure arguments.')
        path = Path(source_path).expanduser().absolute()
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise SourceEvidenceError('Supply a regular XML export file without symlink ancestors.')
        if path.stat().st_size > MAX_BYTES:
            raise UnsupportedSourceError('XML export exceeds the 16 MiB offline analysis limit.')
        record = describe_file(path)
        data = path.read_bytes()
        if record['sha256'] != hashlib.sha256(data).hexdigest():
            raise SourceEvidenceError('XML export evidence does not match the supplied file.')
        if file_record is not None:
            # Intake may use a different source root or an explicitly confirmed
            # role. Preserve that inventory identity while checking actual bytes.
            fields = ('path','sha256','size_bytes','platform','format','export_version','status')
            if not isinstance(file_record,dict) or any(file_record.get(k)!=record[k] for k in fields):
                raise SourceEvidenceError('XML export inventory does not match the supplied file.')
            record = copy.deepcopy(file_record)
        graph = self._build(data, record)
        envelope = {'envelope_version': '2.0', 'source': copy.deepcopy(graph['source']),
                    'evidence_files': copy.deepcopy(graph['evidence_files']), 'diagnostics': copy.deepcopy(graph['diagnostics']),
                    'status': graph['status'], 'native_analysis': copy.deepcopy(graph['extensions']['source_analysis'])}
        validate_document(envelope, 'source_envelope_v2'); validate_evidence_references(envelope)
        if hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise SourceEvidenceError('XML export changed during analysis.')
        return SourceAdapterResult(parsed={'name': path.stem}, package_ir=None,
                                   analysis={'platform': self.platform, 'status': graph['status'], 'semantic_status': 'NOT_VERIFIED'},
                                   source_envelope=envelope, normalized_ir=graph)


def validate_export_graph(graph):
    """Reparse preserved original bytes and require an exact neutral graph replay."""
    from .registry import get_adapter
    validate_normalized_ir(graph)
    adapter = get_adapter(graph['source']['platform'])
    if not isinstance(adapter, XMLExportAdapter):
        raise SchemaValidationError('An XML structural graph needs its registered XML adapter.')
    native_profile = getattr(adapter, 'native_profile', None)
    if native_profile is not None and graph['extensions']['source_analysis'].get('profile') == native_profile:
        adapter.validate_native_graph(graph)
        return
    bundle_profile = getattr(adapter, 'bundle_profile', None)
    if bundle_profile is not None and graph['extensions']['source_analysis'].get('profile') == bundle_profile:
        adapter.validate_bundle_graph(graph)
        return
    if len(graph['evidence_files']) != 1:
        raise SchemaValidationError('An XML structural graph needs its registered adapter and one source file.')
    record = graph['evidence_files'][0]
    data = snapshot_bytes(graph['extensions']['source_analysis']['source_snapshot'], record)
    if graph != adapter._build(data, record):
        raise SchemaValidationError('XML graph does not replay from its original source evidence.')
