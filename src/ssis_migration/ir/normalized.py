"""Versioned source-independent structural graph and integrity checks.

Vendor syntax belongs in adapters. Structural validation never establishes
semantic completeness, execution readiness or migration acceptance.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references


def content_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def normalized_ir_id(document: dict[str, Any]) -> str:
    return 'ir-' + content_digest({k: v for k, v in document.items() if k != 'ir_id'})


def validate_normalized_ir(document: dict[str, Any], *, legacy_ir: dict | None = None) -> None:
    """Validate schema, evidence, identities, graph references and input binding."""
    validate_document(document, 'normalized_ir')
    validate_evidence_references(document)
    files = {item['file_id'] for item in document['evidence_files']}
    if document['source']['file_id'] not in files:
        raise SchemaValidationError('Normalized IR source references an unknown file ID.')
    identities: set[str] = set()
    nodes = {item['node_id'] for item in document['nodes']}
    for collection, key in (('nodes', 'node_id'), ('edges', 'edge_id')):
        for item in document[collection]:
            if item[key] in identities:
                raise SchemaValidationError(f'Duplicate normalized graph identity: {item[key]}')
            identities.add(item[key])
            for evidence in item['evidence']:
                if evidence['file_id'] not in files:
                    raise SchemaValidationError('Normalized graph evidence references an unknown file ID.')
                if legacy_ir is not None:
                    locator = evidence['locator']
                    if not locator.startswith('legacy-ir:/'):
                        raise SchemaValidationError('Normalized graph evidence must identify its legacy-IR input record.')
                    value: Any = legacy_ir
                    try:
                        for part in locator[len('legacy-ir:/'):].split('/'):
                            part = part.replace('~1', '/').replace('~0', '~')
                            if isinstance(value, list):
                                if not part.isascii() or not part.isdecimal() or (part.startswith('0') and part != '0'):
                                    raise ValueError('Invalid JSON pointer array index.')
                                value = value[int(part)]
                            else:
                                value = value[part]
                    except (KeyError, IndexError, ValueError, TypeError) as exc:
                        raise SchemaValidationError(f'Unknown legacy-IR evidence pointer: {locator}') from exc
    for edge in document['edges']:
        for endpoint in ('source_id', 'target_id'):
            value = edge[endpoint]
            if value is not None and value not in nodes:
                raise SchemaValidationError(f'Unknown normalized graph endpoint: {value}')
        if edge['resolution'] == 'RESOLVED' and (edge['source_id'] is None or edge['target_id'] is None):
            raise SchemaValidationError('Resolved graph edges require both endpoints.')
        if edge['resolution'] == 'UNRESOLVED' and not any(
            d['code'] == 'UNRESOLVED_GRAPH_REFERENCE' and d['message'].startswith(edge['edge_id'] + ':')
            for d in document['diagnostics']
        ):
            raise SchemaValidationError('Unresolved graph edges require an explicit diagnostic.')
    if document['status'] != ('BLOCKED' if any(d['severity'] == 'ERROR' for d in document['diagnostics']) else 'REVIEW_REQUIRED'):
        raise SchemaValidationError('Normalized IR status must retain diagnostics and unverified semantics.')
    if legacy_ir is not None and document['provenance']['legacy_ir_sha256'] != content_digest(legacy_ir):
        raise SchemaValidationError('Normalized IR does not match the supplied legacy IR.')
    if document['ir_id'] != normalized_ir_id(document):
        raise SchemaValidationError('Normalized IR content identity does not match its contents.')
