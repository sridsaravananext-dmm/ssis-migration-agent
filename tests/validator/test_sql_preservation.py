from __future__ import annotations

import pytest

from ssis_migration.validator.sql_semantic import SQLSemanticValidator
from ssis_migration.validator.readiness import summarize_readiness
from ssis_migration.resolver.sqlglot_resolver import SQLGlotResolver, SQLGlotResolution
from ssis_migration.resolver.resolution_engine import SQLResolutionEngine


def validate(source, target, **kwargs):
    return SQLSemanticValidator().validate(source, target, source_dialect=kwargs.pop('source_dialect', 'tsql'),
                                           target_dialect=kwargs.pop('target_dialect', 'databricks'), **kwargs)


@pytest.mark.parametrize('source,target,check', [
    ('SELECT a FROM t', 'SELECT b FROM t', 'projection_expressions'),
    ('SELECT id FROM t WHERE enabled=1', 'SELECT id FROM t WHERE enabled=0', 'filter_predicates'),
    ('SELECT a.id FROM a LEFT JOIN b ON a.id=b.id', 'SELECT a.id FROM a LEFT JOIN b ON a.id=b.other_id', 'join_semantics'),
    ('SELECT SUM(x) FROM t GROUP BY a', 'SELECT SUM(x) FROM t GROUP BY b', 'grouping_and_windows'),
    ('SELECT id FROM t ORDER BY id', 'SELECT id FROM t', 'relational_structure'),
    ('SELECT a AS output FROM t', 'SELECT a AS renamed FROM t', 'projection_expressions'),
    ('SELECT id FROM t; SELECT id FROM u WHERE enabled=1', 'SELECT id FROM t; SELECT id FROM u WHERE enabled=0', 'filter_predicates'),
    ('SELECT id FROM t; SELECT id FROM u', 'SELECT id FROM t', 'statement_count'),
    ('UPDATE t SET amount=amount+1 WHERE id=1', 'UPDATE t SET amount=amount+2 WHERE id=1', 'relational_structure'),
    ('SELECT COUNT(*) FROM t', 'SELECT COUNT(*) FROM t JOIN t t2 ON t.id=t2.id', 'source_tables'),
])
def test_changed_behavior_cannot_pass_equal_projection_or_table_counts(source, target, check):
    result = validate(source, target)
    assert result.status == 'FAIL', result.to_dict()
    assert result.checks[check] is False


def test_complete_batch_with_matching_statements_passes_structural_checks_only():
    result = validate('SELECT id FROM t; SELECT id FROM u', 'SELECT id FROM t; SELECT id FROM u')
    assert result.status == 'PASS', result.failures
    assert len(result.source_analysis['statements']) == 2
    assert result.to_dict()['semantic_equivalence'] == 'NOT_VERIFIED'
    readiness = summarize_readiness([result.to_dict()], generated_artifact_count=1)
    assert not readiness['acceptance']['accepted']
    assert readiness['coverage']['semantic']['verified_percent'] is None


def test_invalid_second_statement_is_unresolved():
    result = validate('SELECT 1; SELECT 2', 'SELECT 1; SELECT FROM')
    assert result.status == 'UNRESOLVED'


@pytest.mark.parametrize('sql', ['EXEC dbo.p', 'nonsense statement', 'CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;'])
def test_identical_opaque_inputs_do_not_pass_vacuous_checks(sql):
    result = validate(sql, sql, source_dialect='oracle' if sql.startswith('CREATE') else 'tsql')
    assert result.status != 'PASS'
    assert result.failures


def test_unknown_required_feature_cannot_be_ignored():
    result = validate('SELECT 1', 'SELECT 1', required_features={'CRITICAL_BUSINESS_RULE'})
    assert result.status == 'FAIL'
    assert result.checks['required_features_known'] is False


@pytest.mark.parametrize('requirement,check', [('LEFT_JOIN', 'required_left_join'), ('COALESCE', 'required_coalesce'), ('INCREMENTAL_CUTOFF', 'incremental_cutoff')])
def test_absent_required_feature_does_not_pass_when_missing_on_both_sides(requirement, check):
    result = validate('SELECT id FROM t', 'SELECT id FROM t', required_features={requirement})
    assert result.status == 'FAIL'
    assert not result.checks[check]


def test_arbitrary_watermark_names_and_all_union_scopes_are_checked():
    source = '''SELECT id FROM a WHERE modified_at > @start_key AND modified_at <= @end_key
UNION ALL SELECT id FROM b WHERE modified_at > @start_key AND modified_at <= @end_key'''
    target = source.replace('@start_key', '${start_key}').replace('@end_key', '${end_key}')
    assert validate(source, target, required_features={'INCREMENTAL_CUTOFF'}).status == 'PASS'
    changed = target.replace('modified_at <= ${end_key}', 'modified_at < ${end_key}', 2)
    result = validate(source, changed, required_features={'INCREMENTAL_CUTOFF'})
    assert result.status == 'FAIL'
    assert result.checks['incremental_cutoff'] is False


def test_change_in_second_union_branch_is_not_hidden_by_first_branch():
    source = 'SELECT id FROM a WHERE id > @low AND id <= @high UNION ALL SELECT id FROM b WHERE id > @low AND id <= @high'
    target = source.replace('@low', '${low}').replace('@high', '${high}')
    first, second = target.split('UNION ALL')
    target = first + 'UNION ALL' + second.replace('<= ${high}', '< ${high}')
    assert not validate(source, target, required_features={'INCREMENTAL_CUTOFF'}).checks['incremental_cutoff']


@pytest.mark.parametrize('target', [
    'SELECT id FROM t WHERE id > ${lo} AND id <= ${hi}',
    'SELECT id FROM t WHERE id > :lo AND id <= :hi',
])
def test_named_parameter_syntax_is_normalized_without_verifying_runtime_bindings(target):
    source = 'SELECT id FROM t WHERE id > @lo AND id <= @hi'
    result = validate(source, target)
    assert result.status == 'PASS', result.to_dict()
    assert result.to_dict()['semantic_equivalence'] == 'NOT_VERIFIED'


def test_oracle_relational_conversion_is_structural_only():
    result = SQLResolutionEngine().resolve('SELECT NVL(amount, 0) FROM account_entries WHERE changed_at > :lo AND changed_at <= :hi',
                                           source_dialect='oracle', target_dialect='databricks', required_features={'COALESCE', 'INCREMENTAL_CUTOFF'})
    assert result.status == 'RESOLVED', result.to_dict()
    assert result.validation['semantic_equivalence'] == 'NOT_VERIFIED'
    assert result.validation['source_analysis']['dialect'] == 'oracle'


def test_mixed_union_and_union_all_preserved_without_requiring_all_unions_to_be_all():
    sql = 'SELECT id FROM a UNION ALL SELECT id FROM b UNION SELECT id FROM c'
    assert validate(sql, sql, required_features={'UNION_ALL'}).status == 'PASS'


def test_sql_server_temporal_is_not_delta_time_travel():
    source = 'SELECT id FROM t FOR SYSTEM_TIME AS OF @moment'
    target = 'SELECT id FROM t TIMESTAMP AS OF ${moment}'
    result = validate(source, target, required_features={'TEMPORAL_TABLE_AS_OF'})
    assert result.status == 'REVIEW_REQUIRED'
    assert 'historical-data' in ' '.join(result.failures)
    resolution = SQLGlotResolver().resolve(source, source_dialect='tsql', target_dialect='databricks')
    assert resolution.status == 'REVIEW_REQUIRED'
    assert resolution.target_sql is None


@pytest.mark.parametrize('sql', ['', '-- comments', 'EXEC dbo.p', 'unsupported raw_command', 'SELECT 1; SELECT 2'])
def test_low_level_resolver_never_promotes_empty_opaque_or_multi_statement_input(sql):
    result = SQLGlotResolver().resolve(sql, source_dialect='tsql', target_dialect='databricks')
    assert result.status != 'RESOLVED'
    assert result.target_sql is None
    assert result.source_analysis


def test_low_level_resolver_accepts_trailing_comment_without_empty_artifacts():
    result = SQLGlotResolver().resolve('SELECT 1; -- comment', source_dialect='tsql', target_dialect='databricks')
    assert result.status == 'RESOLVED', result.error
    assert len(result.target_analysis['statements']) == 1


def test_resolution_engine_rejects_a_converter_that_returns_changed_sql():
    class ChangedSQL:
        def resolve(self, sql, **kwargs):
            return SQLGlotResolution('RESOLVED', sql, 'SELECT id FROM t WHERE enabled=0', 'tsql', 'databricks')
    result = SQLResolutionEngine(resolver=ChangedSQL()).resolve('SELECT id FROM t WHERE enabled=1', source_dialect='tsql', target_dialect='databricks')
    assert result.status == 'REVIEW_REQUIRED'
    assert result.validation['checks']['filter_predicates'] is False


def test_resolution_engine_retains_blocked_source_evidence():
    result = SQLResolutionEngine().resolve('EXEC dbo.p', source_dialect='tsql', target_dialect='databricks')
    assert result.status == 'REVIEW_REQUIRED'
    assert result.validation['source_analysis']['status'] == 'UNSUPPORTED'
    assert result.validation['semantic_equivalence'] == 'NOT_VERIFIED'
