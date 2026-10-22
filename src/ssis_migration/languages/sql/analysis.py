"""Analyze all SQL statements without execution or semantic-equivalence claims.

Only explicitly selected T-SQL, Oracle SQL, Spark SQL and Databricks SQL are
accepted. PL/SQL packages/blocks and opaque parser fallbacks remain unsupported.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from ssis_migration.schemas import validate_document

_DIALECTS = {'tsql': 'tsql', 't-sql': 'tsql', 'sqlserver': 'tsql',
             'mssql': 'tsql', 'oracle': 'oracle', 'spark': 'spark', 'databricks': 'databricks'}
_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery,
          exp.Insert, exp.Update, exp.Delete, exp.Merge)


def normalize_dialect(dialect: str) -> str:
    actual = _DIALECTS.get(dialect.strip().lower()) if isinstance(dialect, str) else None
    if actual is None:
        raise ValueError('An explicit supported SQL dialect is required; no fallback was selected.')
    return actual


def parameter_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Parameter) and isinstance(node.this, exp.Var):
        return node.this.name or None
    if isinstance(node, exp.Placeholder) and isinstance(node.this, str):
        return node.this or None
    return None


def _location(node: exp.Expression, index: int) -> str:
    parts = []
    current = node
    while current.parent is not None:
        part = current.arg_key
        if current.index is not None:
            part += '/' + str(current.index)
        parts.append(part)
        current = current.parent
    return f'sql:statement/{index}/ast' + ''.join('/' + part for part in reversed(parts))


def _unwrap(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _conjuncts(node: exp.Expression):
    node = _unwrap(node)
    if isinstance(node, exp.And):
        yield from _conjuncts(node.this)
        yield from _conjuncts(node.expression)
    else:
        yield node


def range_candidates(expression: exp.Expression) -> list[dict[str, Any]]:
    """Find positive, conjunctive parameter bounds on the same column/scope.

    Names never establish incremental behavior. OR/NOT branches, unrelated
    columns and parameter names appearing only in SELECT do not create ranges.
    These are candidate filters, not evidence of CDC, SCD or watermark updates.
    """
    candidates = []
    reverse = {'GT': 'LT', 'GTE': 'LTE', 'LT': 'GT', 'LTE': 'GTE'}
    for scope_index, where in enumerate(expression.find_all(exp.Where)):
        columns = defaultdict(list)
        for predicate in _conjuncts(where.this):
            if isinstance(predicate, exp.Between) and isinstance(predicate.this, exp.Column):
                low, high = parameter_name(predicate.args['low']), parameter_name(predicate.args['high'])
                if low and high:
                    columns[predicate.this.sql()].extend([
                        {'operator': 'GTE', 'parameter': low}, {'operator': 'LTE', 'parameter': high}])
            elif isinstance(predicate, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
                left, right = _unwrap(predicate.this), _unwrap(predicate.expression)
                operator = type(predicate).__name__.upper()
                if parameter_name(left) and isinstance(right, exp.Column):
                    left, right, operator = right, left, reverse[operator]
                name = parameter_name(right)
                if isinstance(left, exp.Column) and name:
                    columns[left.sql()].append({'operator': operator, 'parameter': name})
        for column, bounds in columns.items():
            if any(b['operator'] in {'GT', 'GTE'} for b in bounds) and any(b['operator'] in {'LT', 'LTE'} for b in bounds):
                candidates.append({'scope_index': scope_index, 'column': column, 'bounds': bounds,
                                   'classification': 'PARAMETERIZED_RANGE_CANDIDATE'})
    return candidates


def _features(expression: exp.Expression) -> set[str]:
    result = set()
    if any(u.args.get('distinct') is False for u in expression.find_all(exp.Union)):
        result.add('UNION_ALL')
    if any(j.args.get('side') == 'LEFT' for j in expression.find_all(exp.Join)):
        result.add('LEFT_JOIN')
    if next(expression.find_all(exp.Coalesce), None) is not None:
        result.add('COALESCE')
    if any(isinstance(t.args.get('version'), exp.Version) and t.args['version'].args.get('kind') == 'AS OF'
           for t in expression.find_all(exp.Table)):
        result.add('TEMPORAL_TABLE_AS_OF')
    for kind, feature in ((exp.AggFunc, 'AGGREGATION'), (exp.Group, 'GROUP_BY'),
                          (exp.Window, 'WINDOW'), (exp.Distinct, 'DISTINCT'),
                          (exp.Merge, 'MERGE')):
        if next(expression.find_all(kind), None) is not None:
            result.add(feature)
    if range_candidates(expression):
        result.add('PARAMETERIZED_RANGE')
    return result


def _tables(expression: exp.Expression, dialect: str, index: int) -> list[dict]:
    cte_refs = set()
    # Oracle folds unquoted identifiers to uppercase by language definition.
    # Other engines can depend on unspecified case/collation configuration.
    scoped = normalize_identifiers(expression.copy(), dialect=dialect) if dialect == 'oracle' else expression
    for scope in traverse_scope(scoped):
        for table in scope.tables:
            if isinstance(scope.sources.get(table.alias_or_name), Scope):
                cte_refs.add(_location(table, index))
    writes = set()
    for node in expression.walk():
        target = node.this if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge)) else None
        if isinstance(node, exp.Select) and node.args.get('into') is not None:
            target = node.args['into'].this
        if isinstance(target, exp.Schema):
            target = target.this
        if isinstance(target, exp.Table):
            writes.add(id(target))
    result = []
    for table in expression.find_all(exp.Table):
        reference = table.copy()
        reference.set('alias', None)
        role = 'WRITE' if id(table) in writes else 'CTE_REFERENCE' if _location(table, index) in cte_refs else 'READ'
        result.append({'reference': reference.sql(dialect=dialect), 'role': role,
                       'resolution': 'DECLARED_ONLY', 'locator': _location(table, index)})
    return result


@dataclass
class SQLDocument:
    expressions: list[exp.Expression]
    report: dict[str, Any]

    @property
    def supported(self) -> bool:
        return self.report['status'] == 'ANALYZED'


def parse_sql(sql: str, *, dialect: str) -> SQLDocument:
    """Parse the entire input, preserving each nonempty statement in order."""
    try:
        actual = normalize_dialect(dialect)
    except ValueError:
        actual = None
    report = {'analysis_version': '1.0', 'dialect': actual,
              'source_sha256': hashlib.sha256(sql.encode()).hexdigest() if isinstance(sql, str) else None,
              'status': 'UNRESOLVED', 'scope': 'SQL_AST_STRUCTURE', 'semantic_status': 'NOT_VERIFIED',
              'source_file': None, 'statements': [], 'diagnostics': []}
    expressions = []

    def diagnostic(code, message, index=None):
        report['diagnostics'].append({'code': code, 'severity': 'WARNING', 'message': message,
                                      'statement_index': index,
                                      'locator': f'sql:statement/{index}/ast' if index is not None else 'sql:input'})
    if actual is None:
        diagnostic('UNKNOWN_SQL_DIALECT', 'An explicit supported SQL dialect is required; no fallback was selected.')
    elif not isinstance(sql, str) or not sql.strip():
        diagnostic('EMPTY_SQL', 'SQL input must be nonempty text.')
    else:
        try:
            # SQLGlot can represent trailing comments as Semicolon nodes. They
            # carry no executable statement and must not hide real batch members.
            expressions = [x for x in sqlglot.parse(sql, read=actual, error_level=sqlglot.ErrorLevel.RAISE)
                           if x is not None and not isinstance(x, exp.Semicolon)]
            if not expressions:
                diagnostic('EMPTY_SQL', 'No SQL statements were supplied.')
            else:
                for i, expression in enumerate(expressions):
                    opaque = not isinstance(expression, _ROOTS) or any(
                        isinstance(n, (exp.Command, exp.Anonymous, exp.SessionParameter))
                        or (isinstance(n, exp.Parameter) and not isinstance(n.this, exp.Var))
                        for n in expression.walk()) or any(
                        not select.expressions for select in expression.find_all(exp.Select))
                    if opaque:
                        diagnostic('UNSUPPORTED_SQL_CONSTRUCT',
                                   f'Statement {i} contains incomplete, procedural, opaque or unresolved function syntax ({type(expression).__name__}).', i)
                    parameters = [{'name': parameter_name(n), 'locator': _location(n, i)}
                                  for n in expression.walk() if isinstance(n, (exp.Parameter, exp.Placeholder))]
                    report['statements'].append({
                        'statement_index': i, 'node_type': type(expression).__name__,
                        'status': 'UNSUPPORTED' if opaque else 'AST_PARSED',
                        'features': sorted(_features(expression)), 'parameters': parameters,
                        'tables': _tables(expression, actual, i),
                        'range_candidates': range_candidates(expression), 'locator': _location(expression, i),
                    })
                report['status'] = 'UNSUPPORTED' if report['diagnostics'] else 'ANALYZED'
        except (sqlglot.errors.SqlglotError, ValueError, TypeError) as exc:
            expressions = []
            report['statements'] = []
            report['status'] = 'UNRESOLVED'
            diagnostic('SQL_PARSE_ERROR', f'{type(exc).__name__}: {exc}')
    validate_document(report, 'sql_analysis')
    return SQLDocument(expressions, report)


def analyze_sql(sql: str, *, dialect: str) -> dict[str, Any]:
    """Return the versioned JSON-compatible report without executing SQL."""
    return parse_sql(sql, dialect=dialect).report
