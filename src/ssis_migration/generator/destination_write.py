"""Mapped native INSERTs into existing tables; no inferred overwrite or merge policy."""
from __future__ import annotations

import hashlib
from ssis_migration.mapping import ConversionGap


def destination_columns(component, columns, lineage):
    if len(component.inputs) != 1:
        raise ConversionGap('DESTINATION_MAPPING_REQUIRED','Destination needs one explicit input.')
    port=component.inputs[0]
    external={}
    for col in port.get('external_columns', []):
        if not col.get('ref_id') or col['ref_id'] in external:
            raise ConversionGap('DESTINATION_MAPPING_REQUIRED','External column identities must be unique.')
        external[col['ref_id']]=col
    result=[]
    for col in port.get('columns', []):
        external_id=col.get('external_metadata_column_id') or col.get('attributes',{}).get('externalMetadataColumnId')
        target=external.get(external_id)
        source=lineage.get(str(col.get('lineage_id')))
        if not target or not target.get('name') or not source or source not in columns:
            raise ConversionGap('DESTINATION_MAPPING_REQUIRED','Resolve each source lineage and external target column explicitly.')
        dtype=str(target.get('data_type') or '').casefold()
        expected={'i1':'tinyint','ui1':'smallint','i2':'smallint','i4':'int','i8':'bigint','bool':'boolean',
            'r4':'float','r8':'double','wstr':'string','str':'string','bytes':'binary','dbdate':'date',
            'dbtimestamp':'timestamp','dbtimestamp2':'timestamp','date':'timestamp'}.get(dtype)
        if dtype in {'numeric','decimal'}:
            precision,scale=target.get('precision'),target.get('scale')
            if precision is None or scale is None or not str(precision).isdigit() or not str(scale).isdigit():
                raise ConversionGap('DESTINATION_TYPE_REVIEW_REQUIRED','Decimal precision/scale must be captured.')
            if not 1<=int(precision)<=38 or not 0<=int(scale)<=int(precision):
                raise ConversionGap('DESTINATION_TYPE_REVIEW_REQUIRED','Decimal precision/scale is outside the supported target range.')
            expected=f'decimal({int(precision)},{int(scale)})'
        if not expected:
            raise ConversionGap('DESTINATION_TYPE_REVIEW_REQUIRED',f'Unsupported captured destination type {dtype}.')
        if dtype == 'str':
            raise ConversionGap('DESTINATION_CODEPAGE_REVIEW_REQUIRED','ANSI strings require an explicit code-page conversion design.')
        length=target.get('length')
        if dtype == 'wstr' and (length is None or not str(length).isdigit() or int(length)<1):
            raise ConversionGap('DESTINATION_TYPE_REVIEW_REQUIRED','Captured string lengths must be positive integers.')
        result.append({'source':source,'target':target['name'],'expected_type':expected,
            'utf16_units':int(length) if dtype=='wstr' and length is not None else None})
    if not result or len({c['target'].casefold() for c in result}) != len(result):
        raise ConversionGap('DESTINATION_MAPPING_REQUIRED','Expected nonempty, unique target column mappings.')
    return result


def append_code(component, source, columns, lineage, mapped):
    pairs=destination_columns(component,columns,lineage)
    target=mapped.sql(dialect='databricks')
    token=hashlib.sha256(component.ref_id.encode()).hexdigest()[:12]
    frame='_ssis_write_'+token
    code=[f'if not spark.catalog.tableExists({target!r}):',
        "    raise RuntimeError('Mapped destination table must already exist; supply reviewed DDL.')",
        f'_ssis_target_order = list(spark.table({target!r}).schema.fields)',
        '_ssis_target_fields = {f.name.casefold(): f for f in _ssis_target_order}',
        f'_ssis_source_fields = {{f.name: f for f in {source}.schema.fields}}',
        f'_ssis_column_pairs = {pairs!r}',
        "if len(_ssis_target_fields) != len(_ssis_column_pairs) or set(_ssis_target_fields) != {p['target'].casefold() for p in _ssis_column_pairs}:",
        "    raise RuntimeError('Destination columns differ from captured mappings; review defaults, identity, and extra columns.')",
        '_ssis_projections = {}',
        'for _ssis_pair in _ssis_column_pairs:',
        "    _ssis_source_field = _ssis_source_fields[_ssis_pair['source']]",
        "    _ssis_target_field = _ssis_target_fields[_ssis_pair['target'].casefold()]",
        "    if _ssis_source_field.dataType.simpleString() != _ssis_pair['expected_type'] or _ssis_target_field.dataType.simpleString() != _ssis_pair['expected_type']:",
        "        raise RuntimeError('Destination/source types differ from captured SSIS metadata; review explicit conversion.')",
        "    _ssis_column = F.col('`' + _ssis_pair['source'].replace('`', '``') + '`')",
        '    _ssis_invalid = F.lit(False)',
        '    if not _ssis_target_field.nullable:',
        '        _ssis_invalid = _ssis_invalid | _ssis_column.isNull()',
        "    if _ssis_pair['utf16_units'] is not None:",
        "        _ssis_invalid = _ssis_invalid | (F.length(F.encode(_ssis_column, 'UTF-16LE')) > F.lit(2 * _ssis_pair['utf16_units']))",
        "    _ssis_checked = F.when(_ssis_invalid, F.raise_error('SSIS destination NULL/string-length check failed')).otherwise(_ssis_column)",
        "    _ssis_projections[_ssis_pair['target'].casefold()] = _ssis_checked.alias(_ssis_target_field.name)",
        f'{frame} = {source}.select(*[_ssis_projections[f.name.casefold()] for f in _ssis_target_order])',
        f'{frame}.write.mode("append").insertInto({target!r})']
    return '\n'.join(code),pairs
