"""Explicit overrides and visible catalog/schema naming proposals."""
from __future__ import annotations
import sqlglot
from sqlglot import exp, ErrorLevel
from ssis_migration.sql_text import normalize_name
import re


class ConversionGap(ValueError):
    def __init__(self, code, message, evidence=None):
        super().__init__(message)
        self.code, self.evidence = code, evidence


def target_table(value):
    target = value.get('target') if isinstance(value, dict) else value
    if not isinstance(target, str):
        raise ValueError('Object mapping requires a target string')
    try:
        table = sqlglot.parse_one(target, read='databricks', into=exp.Table)
    except Exception as exc:
        raise ValueError(f'Invalid Databricks table mapping: {target}') from exc
    if len(table.parts) != 3 or table.args.get('alias') or any(not isinstance(p, exp.Identifier) for p in table.parts):
        raise ValueError('Mapped tables must be fully qualified catalog.schema.table identifiers')
    return exp.Table(this=exp.to_identifier(table.name, quoted=True),
                     db=exp.to_identifier(table.db, quoted=True),
                     catalog=exp.to_identifier(table.catalog, quoted=True))


def resolve_mapping(name, connection, mappings, default_schema='dbo'):
    key = normalize_name(name, default_schema)
    scoped = f'{connection}|{key}' if connection else None
    normalized = {}
    for source, target in mappings.items():
        prefix, separator, obj = source.rpartition('|')
        canonical = f'{prefix}|{normalize_name(obj, default_schema)}' if separator else normalize_name(source, default_schema)
        if canonical in normalized:
            raise ValueError(f'Duplicate normalized object mapping: {canonical}')
        normalized[canonical] = (source, target)
    selected = scoped if scoped in normalized else key
    if selected not in normalized:
        raise ConversionGap('OBJECT_MAPPING_REQUIRED', f'Provide an explicit target for {name} on connection {connection or "unspecified"}.', key)
    source, value = normalized[selected]
    return target_table(value), source


def location_identifier(value, label):
    """Configuration accepts a single name, never a SQL expression or description."""
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', value):
        raise ValueError(f'{label} must be a single identifier, for example develop_dev_silver; omit explanatory text')
    return value


def resolve_target(name, connection, config, *, role):
    """An override wins; otherwise propose catalog.schema.lowercase_table."""
    try:
        mapped, key = resolve_mapping(name, connection, config.object_mappings, config.default_schema)
        value = config.object_mappings[key]
        resolution = 'EXPLICIT'
        layer = value.get('layer') if isinstance(value, dict) else None
    except ConversionGap as exc:
        if exc.code != 'OBJECT_MAPPING_REQUIRED': raise
        try:
            source = sqlglot.parse_one(name, read='tsql', into=exp.Table)
        except Exception as error:
            raise ConversionGap('IDENTIFIER_MAPPING_REVIEW', 'Cannot identify a table name for the naming convention.', name) from error
        if not 1 <= len(source.parts) <= 3 or source.args.get('alias') or any(not isinstance(p, exp.Identifier) or '.' in p.name for p in source.parts):
            raise ConversionGap('IDENTIFIER_MAPPING_REVIEW', 'Source needs an explicit unambiguous object override.', name)
        catalog = location_identifier(config.catalog, 'Target catalog')
        schema = location_identifier(config.target_schema, 'Target schema') if config.target_schema else '__pending_target_schema__'
        mapped = exp.Table(this=exp.to_identifier(source.name.casefold(), quoted=True),
            db=exp.to_identifier(schema, quoted=True), catalog=exp.to_identifier(catalog, quoted=True))
        key, layer = None, None
        resolution = 'CONVENTION' if config.target_schema else 'PLACEHOLDER'
    return mapped, {'source': name, 'connection_reference': connection, 'mapping_key': key,
        'target': mapped.sql(dialect='databricks'), 'role': role, 'resolution': resolution, 'layer': layer}


def translate_sql(sql, connection, config, *, query_only=False):
    """Translate a bounded relational subset and map every physical table."""
    from ssis_migration.sql_text import mask_sql
    import re
    clean = mask_sql(sql)
    if re.search(r'@[\w@]|\?|(?<!:):[A-Za-z_]|\b(?:EXEC(?:UTE)?|DECLARE|CURSOR|WHILE|BEGIN|COMMIT|ROLLBACK|TRY|CATCH)\b', clean, re.I):
        raise ConversionGap('SQL_SEMANTICS_UNSUPPORTED', 'Procedural SQL, parameters, and dynamic execution require explicit translation.')
    try:
        statements = [s for s in sqlglot.parse(sql, read='tsql', error_level=ErrorLevel.RAISE) if s]
    except Exception as exc:
        raise ConversionGap('SQL_PARSE_FAILED', str(exc)) from exc
    if len(statements) != 1:
        raise ConversionGap('SQL_SEMANTICS_UNSUPPORTED', 'Expected one complete relational SQL statement.')
    expression = statements[0]
    allowed = (exp.Select, exp.Union) if query_only else (exp.Select, exp.Union, exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.TruncateTable)
    if not isinstance(expression, allowed):
        raise ConversionGap('SQL_SEMANTICS_UNSUPPORTED', f'Unsupported statement: {type(expression).__name__}')
    for node in expression.walk():
        if isinstance(node, (exp.Command, exp.Anonymous, exp.Into, exp.Parameter, exp.Placeholder)):
            raise ConversionGap('SQL_SEMANTICS_UNSUPPORTED', f'Unsupported SQL construct: {type(node).__name__}')
        if isinstance(node, exp.CTE) and node.parent and node.parent.args.get('recursive'):
            raise ConversionGap('SQL_SEMANTICS_UNSUPPORTED', 'Recursive CTE translation requires review.')
        if isinstance(node, exp.Table) and (node.args.get('version') or node.args.get('hints') or node.args.get('pivots')):
            raise ConversionGap('SQL_SEMANTICS_UNSUPPORTED', 'Temporal/hinted/pivoted table access requires review.')
    # Scope resolution distinguishes CTE aliases from physical objects.
    from sqlglot.optimizer.scope import traverse_scope, Scope
    aliases = set()
    for scope in traverse_scope(expression):
        for table in scope.tables:
            if isinstance(scope.sources.get(table.alias_or_name), Scope): aliases.add(id(table))
    if any(c.db or c.catalog for c in expression.find_all(exp.Column)):
        raise ConversionGap('SQL_QUALIFIED_COLUMN_REVIEW', 'Database-qualified columns need explicit qualifier translation.')
    used = []
    for table in list(expression.find_all(exp.Table)):
        if id(table) in aliases: continue
        if any(not isinstance(p, exp.Identifier) for p in table.parts):
            raise ConversionGap('SQL_SEMANTICS_UNSUPPORTED', 'Table-valued functions require explicit translation.')
        if any('.' in p.name for p in table.parts):
            raise ConversionGap('IDENTIFIER_MAPPING_REVIEW', 'Source identifiers containing dots require an unambiguous mapping representation.')
        source = '.'.join(p.name for p in table.parts)
        role = 'source' if isinstance(table.parent, (exp.From, exp.Join)) else 'target'
        mapped, use = resolve_target(source, connection, config, role=role)
        if table.args.get('alias'): mapped.set('alias', table.args['alias'].copy())
        elif table.name != mapped.name and any(c.table == table.name for c in expression.find_all(exp.Column)):
            if role != 'source':
                raise ConversionGap('SQL_QUALIFIED_COLUMN_REVIEW', 'Target-qualified columns require explicit alias review.')
            mapped.set('alias', exp.TableAlias(this=exp.to_identifier(table.name, quoted=True)))
        table.replace(mapped)
        used.append(use)
    try:
        result = expression.sql(dialect='databricks', unsupported_level=ErrorLevel.RAISE)
        sqlglot.parse_one(result, read='databricks', error_level=ErrorLevel.RAISE)
    except Exception as exc:
        raise ConversionGap('SQL_TRANSLATION_FAILED', str(exc)) from exc
    return result, used
