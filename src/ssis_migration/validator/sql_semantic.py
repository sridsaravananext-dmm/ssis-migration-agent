from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from ssis_migration.languages.sql import parse_sql, parameter_name, range_candidates


@dataclass
class SQLSemanticValidation:
    status: str
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    source_analysis: dict[str, Any] = field(default_factory=dict)
    target_analysis: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'status': self.status, 'checks': dict(self.checks), 'failures': list(self.failures),
            'scope': 'SQL_AST_STRUCTURE', 'semantic_equivalence': 'NOT_VERIFIED',
            'source_analysis': self.source_analysis, 'target_analysis': self.target_analysis,
        }


def _signature(value: Any) -> Any:
    """Compare normalized AST arguments, excluding locations and comments."""
    if isinstance(value, exp.Expression):
        name = parameter_name(value)
        if name is not None:
            # Named binding syntax differs across dialects (@x, :x, ${x}).
            return ('named_parameter', name)
        return (value.key, tuple((k, _signature(v)) for k, v in sorted(value.args.items()) if v is not None))
    if isinstance(value, list):
        return tuple(_signature(v) for v in value)
    return value


class SQLSemanticValidator:
    """Conservative structural comparison of every supplied SQL statement.

    PASS describes these checks only. Different but equivalent ASTs can require
    review; matching ASTs cannot prove collation, types, bindings or data parity.
    """

    def validate(self, source_sql: str, target_sql: str, *, source_dialect: str,
                 target_dialect: str, required_features: set[str] | None = None) -> SQLSemanticValidation:
        required_features = required_features or set()
        source_doc = parse_sql(source_sql, dialect=source_dialect)
        target_doc = parse_sql(target_sql, dialect=target_dialect)
        result = SQLSemanticValidation('UNRESOLVED', source_analysis=source_doc.report,
                                       target_analysis=target_doc.report)
        if not source_doc.supported or not target_doc.supported:
            result.status = 'REVIEW_REQUIRED' if 'UNSUPPORTED' in {source_doc.report['status'], target_doc.report['status']} else 'UNRESOLVED'
            result.failures = [f'{label}: {d["message"]}' for label, doc in (('source', source_doc), ('target', target_doc))
                               for d in doc.report['diagnostics']]
            return result

        def check(name: str, condition: bool, message: str):
            result.checks[name] = bool(condition)
            if not condition:
                result.failures.append(message)

        known = {'UNION_ALL', 'LEFT_JOIN', 'COALESCE', 'INCREMENTAL_CUTOFF', 'TEMPORAL_TABLE_AS_OF'}
        check('required_features_known', not required_features - known,
              f'Unsupported required SQL features: {sorted(required_features - known)}')
        check('statement_count', len(source_doc.expressions) == len(target_doc.expressions),
              'SQL statement count changed; every nonempty statement must be preserved.')
        source_features = {f for s in source_doc.report['statements'] for f in s['features']}
        # SQL Server temporal/Oracle flashback reads cannot be replaced by Delta
        # table-version reads without historical source data and a strategy.
        if 'TEMPORAL_TABLE_AS_OF' in source_features and source_doc.report['dialect'] not in {'spark', 'databricks'}:
            result.status = 'REVIEW_REQUIRED'
            result.failures.append('Source temporal/flashback semantics require an explicit historical-data strategy; Delta time travel is not an equivalent rewrite.')
            return result
        try:
            target_read = target_doc.report['dialect']
            expected_sql = ';\n'.join(x.sql(dialect=target_read, unsupported_level=sqlglot.ErrorLevel.RAISE)
                                     for x in source_doc.expressions)
            expected_doc = parse_sql(expected_sql, dialect=target_read)
            if not expected_doc.supported:
                result.status = 'REVIEW_REQUIRED'
                result.failures.append('Source AST could not be rendered as fully supported target SQL.')
                return result
        except (sqlglot.errors.SqlglotError, ValueError) as exc:
            result.status = 'REVIEW_REQUIRED'
            result.failures.append(f'Target SQL structural normalization failed: {exc}')
            return result

        expected, actual = expected_doc.expressions, target_doc.expressions

        def all_nodes(roots, kind):
            return [node for root in roots for node in root.find_all(kind)]

        def signatures(roots, kind):
            return [_signature(n) for n in all_nodes(roots, kind)]

        check('projection_structure', [len(s.expressions) for s in all_nodes(expected, exp.Select)] ==
              [len(s.expressions) for s in all_nodes(actual, exp.Select)], 'SELECT projection counts changed.')
        check('projection_expressions', [_signature(s.expressions) for s in all_nodes(expected, exp.Select)] ==
              [_signature(s.expressions) for s in all_nodes(actual, exp.Select)], 'SELECT expressions, aliases or output ordering changed.')
        expected_unions, actual_unions = all_nodes(expected, exp.Union), all_nodes(actual, exp.Union)
        flags = lambda unions: [u.args.get('distinct') is False for u in unions]
        check('union_count', len(expected_unions) == len(actual_unions), 'UNION count changed.')
        check('union_all_semantics', flags(expected_unions) == flags(actual_unions), 'UNION ALL structure changed.')
        check('source_tables', signatures(expected, exp.Table) == signatures(actual, exp.Table),
              'Table references, aliases, versions or occurrence counts changed.')
        check('filter_predicates', signatures(expected, (exp.Where, exp.Having, exp.Qualify)) ==
              signatures(actual, (exp.Where, exp.Having, exp.Qualify)), 'Filter expressions or predicate scopes changed.')
        check('join_semantics', signatures(expected, exp.Join) == signatures(actual, exp.Join),
              'Join sides, conditions or ordering changed.')
        check('grouping_and_windows', signatures(expected, (exp.Group, exp.Window)) ==
              signatures(actual, (exp.Group, exp.Window)), 'Grouping or window structure changed.')
        check('relational_structure', [_signature(x) for x in expected] == [_signature(x) for x in actual],
              'Complete SQL AST structure differs from the supported dialect rendering.')
        if 'UNION_ALL' in required_features:
            check('required_union_all', any(flags(expected_unions)) and flags(expected_unions) == flags(actual_unions),
                  'Required UNION ALL structure was not preserved.')
        if 'LEFT_JOIN' in required_features:
            left = lambda roots: [_signature(j) for j in all_nodes(roots, exp.Join) if j.args.get('side') == 'LEFT']
            check('required_left_join', bool(left(expected)) and left(expected) == left(actual),
                  'Required LEFT JOIN conditions were not preserved.')
        if 'COALESCE' in required_features:
            check('required_coalesce', bool(all_nodes(expected, exp.Coalesce)) and
                  signatures(expected, exp.Coalesce) == signatures(actual, exp.Coalesce),
                  'Required COALESCE expressions were not preserved.')
        if 'INCREMENTAL_CUTOFF' in required_features:
            # Preserve every range in every statement/query scope; there are no
            # customer-specific columns, aliases or parameter names here.
            ranges = lambda roots: [range_candidates(x) for x in roots]
            check('incremental_cutoff', any(ranges(expected)) and ranges(expected) == ranges(actual),
                  'Parameterized range predicates were absent or changed; incremental behavior remains unverified.')
        if 'TEMPORAL_TABLE_AS_OF' in required_features:
            check('required_temporal_table_as_of', 'TEMPORAL_TABLE_AS_OF' in source_features and
                  signatures(expected, exp.Version) == signatures(actual, exp.Version),
                  'Required temporal read structure was not preserved.')
        result.status = 'FAIL' if result.failures else 'PASS'
        return result
