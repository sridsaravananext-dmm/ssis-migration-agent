"""Traceable draft sections. Pending source behavior is never counted as implemented."""
from __future__ import annotations

import hashlib
import json
import re

from ssis_migration.mapping import ConversionGap, translate_sql
from ssis_migration.sql_text import QUALIFIED, mask_sql, procedure_calls


def pending_operation(source_id, reason_codes, *, connection=None, sql=None,
                      properties=None, procedures=None, kind='IMPLEMENTATION', operation_id=None):
    pending_id = 'pending_' + hashlib.sha256((source_id + '\0' + kind).encode()).hexdigest()[:16]
    properties = properties or {}
    item = {'pending_id': pending_id, 'source_id': source_id, 'kind': kind,
        'reason_codes': sorted(set(reason_codes)), 'connection_reference': connection,
        'original_sql': sql, 'procedures': procedures or [],
        'parameter_bindings': properties.get('parameter_bindings', []),
        'result_bindings': properties.get('result_bindings', []),
        'result_type': properties.get('ResultType'),
        'source_variable': properties.get('SqlCommandVariable'),
        'source_access_mode': properties.get('AccessMode'),
        'target_object': properties.get('OpenRowset'),
        'target_proposals': properties.get('_target_proposals', []),
        'replacement': 'Supply missing inputs and regenerate this source step; review bindings before implementing it.'}
    lines = [f'# BEGIN PENDING {pending_id}']
    for label, value in [('Source', source_id), ('Connection', connection),
                         ('Reasons', item['reason_codes']), ('Procedures', item['procedures']),
                         ('Parameter bindings', item['parameter_bindings']),
                         ('Result bindings', item['result_bindings']),
                         ('Result type', item['result_type']),
                         ('Source variable', item['source_variable']),
                         ('Source access mode', item['source_access_mode']),
                         ('Target object', item['target_object']),
                         ('Target proposals', item['target_proposals'])]:
        if value is not None and value != []:
            lines.append('# ' + label + ': ' + json.dumps(value, ensure_ascii=True))
    if sql:
        if item['source_variable']:
            lines.append('# Variable-backed source: the variable controls SQL; captured command text below is reference only.')
        lines.append('# Original SQL (reference only):')
        lines.extend('# ' + line for line in sql.splitlines())
    lines.extend([f'# END PENDING {pending_id}',
        f'raise RuntimeError({("Pending source implementation " + pending_id + "; see pending_items.json.")!r})'])
    return {'operation_id': operation_id or source_id, 'operation_type': 'REVIEW_ONLY',
        'order': 0, 'code': '\n'.join(lines), 'pending_item': item}


def procedure_references(sql, connection, context):
    from ssis_migration.dependencies import ProcedureResolver
    resolver = ProcedureResolver(context)
    records, bodies = [], {}
    for name in procedure_calls(sql):
        record = {'name': name, 'definition_status': 'MISSING', 'connection_reference': connection}
        try:
            body, scope, key = resolver.resolve(name, connection)
            record.update(definition_status='AVAILABLE', resolved_scope=scope,
                definition_sha256=hashlib.sha256(body.encode()).hexdigest())
            bodies[name] = body
        except ConversionGap as exc:
            record.update(definition_status='MISSING' if exc.code == 'MISSING_PROCEDURE_DEFINITION' else 'UNRESOLVED',
                reason_code=exc.code)
        records.append(record)
    return records, bodies


def translate_parameterless_procedure(sql, definition, connection, config, properties):
    """Only inline an argument-free call to one parameterless relational statement."""
    from ssis_migration.procedure_file_loader import CREATE
    if not re.fullmatch(rf'\s*EXEC(?:UTE)?\s+{QUALIFIED}\s*;?\s*', mask_sql(sql), re.I):
        raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED', 'Procedure arguments, return values, and mixed SQL require explicit translation.')
    if properties.get('parameter_bindings') or properties.get('result_bindings') or str(properties.get('ResultType', '')) not in {'', '0', 'ResultSetType_None', 'None'}:
        raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Procedure parameter/result bindings still require translation.')
    clean = mask_sql(definition)
    header = CREATE.match(clean.lstrip())
    offset = len(clean) - len(clean.lstrip())
    if not header:
        raise ConversionGap('PROCEDURE_TRANSLATION_REQUIRED', 'A complete procedure definition is required.')
    tail = clean[offset + header.end():]
    start = re.match(r'\s+AS\b', tail, re.I)
    if not start:
        raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED', 'Procedure declarations or execution options require explicit translation.')
    body = definition[offset + header.end() + start.end():].strip()
    masked_body = mask_sql(body).strip()
    wrapper = re.fullmatch(r'BEGIN\b([\s\S]*)\bEND\s*;?', masked_body, re.I)
    if wrapper:
        # Offsets come from the unstripped mask, preserving leading comments.
        masked = mask_sql(body)
        match = re.fullmatch(r'\s*BEGIN\b([\s\S]*)\bEND\s*;?\s*', masked, re.I)
        body = body[match.start(1):match.end(1)].strip()
    return translate_sql(body, connection, config)
