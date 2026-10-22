from __future__ import annotations

import copy
import hashlib

import pytest

from ssis_migration.languages.sql import analyze_sql, parse_sql
from ssis_migration.schemas import SchemaValidationError, validate_document
from ssis_migration.resolver.sql_features import detect_required_features


def features(sql, dialect='tsql'):
    report = analyze_sql(sql, dialect=dialect)
    return {f for s in report['statements'] for f in s['features']}


def test_all_batch_statements_are_analyzed_in_order():
    sql = 'SELECT 1; SELECT COALESCE(x, 0) FROM t LEFT JOIN u ON t.id=u.id'
    report = analyze_sql(sql, dialect='tsql')
    assert report['status'] == 'ANALYZED'
    assert len(report['statements']) == 2
    assert report['statements'][1]['features'] == ['COALESCE', 'LEFT_JOIN']
    assert report['source_sha256'] == hashlib.sha256(sql.encode()).hexdigest()
    assert report['semantic_status'] == 'NOT_VERIFIED'
    assert report['scope'] == 'SQL_AST_STRUCTURE'
    assert detect_required_features(sql, source_dialect='tsql') == {'COALESCE', 'LEFT_JOIN'}


@pytest.mark.parametrize('sql', ['', ' ', '-- SELECT * FROM t', '/* statement */;', '; ;'])
def test_empty_or_comment_only_inputs_do_not_pass(sql):
    report = analyze_sql(sql, dialect='tsql')
    assert report['status'] == 'UNRESOLVED'
    assert report['statements'] == []


def test_trailing_comments_do_not_create_an_executable_statement():
    report = analyze_sql('SELECT 1; -- trailing comment', dialect='tsql')
    assert report['status'] == 'ANALYZED'
    assert len(report['statements']) == 1


@pytest.mark.parametrize('dialect', [None, '', 'unknown', 'mdx', 'tm1', 'plsql'])
def test_explicit_supported_dialect_required(dialect):
    report = analyze_sql('SELECT 1', dialect=dialect)
    assert report['status'] == 'UNRESOLVED'
    assert report['dialect'] is None
    assert report['diagnostics'][0]['code'] == 'UNKNOWN_SQL_DIALECT'


def test_invalid_later_statement_is_not_hidden_by_a_valid_first_statement():
    report = analyze_sql('SELECT 1; SELECT FROM', dialect='tsql')
    assert report['status'] == 'UNRESOLVED'
    assert report['statements'] == []
    assert any(d['code'] == 'SQL_PARSE_ERROR' for d in report['diagnostics'])


@pytest.mark.parametrize('sql,dialect', [
    ('EXEC dbo.run_process', 'tsql'),
    ('frobnicate target', 'tsql'),
    ('CREATE OR REPLACE PACKAGE p AS PROCEDURE run_job; END;', 'oracle'),
    ('CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;', 'oracle'),
    ('BEGIN NULL; END;', 'oracle'),
    ('SELECT dbo.user_function(id) FROM t', 'tsql'),
])
def test_procedural_or_opaque_syntax_never_claims_supported_analysis(sql, dialect):
    report = analyze_sql(sql, dialect=dialect)
    assert report['status'] in {'UNSUPPORTED', 'UNRESOLVED'}
    assert report['diagnostics']


def test_cte_names_are_distinguished_from_physical_source_tables():
    sql = 'WITH local_cte AS (SELECT id FROM dbo.source_table) SELECT * FROM local_cte'
    tables = analyze_sql(sql, dialect='tsql')['statements'][0]['tables']
    assert {t['reference']: t['role'] for t in tables} == {
        'local_cte': 'CTE_REFERENCE', 'dbo.source_table': 'READ'}
    assert all(t['resolution'] == 'DECLARED_ONLY' for t in tables)


def test_scoped_cte_name_does_not_hide_a_qualified_physical_table():
    sql = 'WITH same AS (SELECT id FROM dbo.source) SELECT a.id FROM same a JOIN dbo.same b ON a.id=b.id'
    tables = analyze_sql(sql, dialect='tsql')['statements'][0]['tables']
    assert {t['reference']: t['role'] for t in tables} == {
        'same': 'CTE_REFERENCE', 'dbo.same': 'READ', 'dbo.source': 'READ'}


@pytest.mark.parametrize('sql,target,source', [
    ('INSERT INTO dbo.target (id) SELECT id FROM dbo.source', 'dbo.target (id)', 'dbo.source'),
    ('UPDATE dbo.target SET id = 1 WHERE id > @lower', 'dbo.target', None),
    ('DELETE FROM dbo.target WHERE id > @lower', 'dbo.target', None),
    ('MERGE INTO dbo.target t USING dbo.source s ON t.id=s.id WHEN MATCHED THEN UPDATE SET t.id=s.id', 'dbo.target', 'dbo.source'),
])
def test_dml_read_and_write_roles_are_structural(sql, target, source):
    report = analyze_sql(sql, dialect='tsql')
    assert report['status'] == 'ANALYZED', report
    tables = report['statements'][0]['tables']
    assert any(t['role'] == 'WRITE' and t['reference'].startswith('dbo.target') for t in tables)
    if source:
        assert any(t['role'] == 'READ' and t['reference'] == source for t in tables)


@pytest.mark.parametrize('sql', [
    'SELECT @LastCutoff, @NewCutoff FROM t',
    "SELECT '@LastCutoff @NewCutoff' FROM t",
    'SELECT * FROM t WHERE a > @LastCutoff',
    'SELECT * FROM t WHERE a > @LastCutoff AND b <= @NewCutoff',
    'SELECT * FROM t WHERE a > @LastCutoff OR a <= @NewCutoff',
    'SELECT * FROM t WHERE NOT (a > @LastCutoff AND a <= @NewCutoff)',
    'SELECT * FROM t WHERE a > @LastCutoff UNION ALL SELECT * FROM t WHERE a <= @NewCutoff',
    'SELECT * FROM t WHERE a > @LastCutoff AND EXISTS (SELECT 1 FROM q WHERE a <= @NewCutoff)',
])
def test_names_or_different_predicate_scopes_do_not_invent_incremental_behavior(sql):
    assert 'PARAMETERIZED_RANGE' not in features(sql)
    assert 'INCREMENTAL_CUTOFF' not in detect_required_features(sql, source_dialect='tsql')


@pytest.mark.parametrize('sql,dialect', [
    ('SELECT * FROM t WHERE changed_at > @start_value AND changed_at <= @end_value', 'tsql'),
    ('SELECT * FROM t WHERE @start_value < changed_at AND @end_value >= changed_at', 'tsql'),
    ('SELECT * FROM t WHERE (changed_at >= :lower_bound AND changed_at < :upper_bound)', 'oracle'),
    ('SELECT * FROM t WHERE changed_at BETWEEN :lower_bound AND :upper_bound', 'oracle'),
    ('SELECT * FROM t WHERE changed_at > ${start} AND changed_at <= ${end}', 'databricks'),
])
def test_parameterized_range_candidates_use_ast_bounds_with_arbitrary_names(sql, dialect):
    report = analyze_sql(sql, dialect=dialect)
    assert report['status'] == 'ANALYZED', report
    statement = report['statements'][0]
    assert 'PARAMETERIZED_RANGE' in statement['features']
    candidate = statement['range_candidates'][0]
    assert candidate['column'] == 'changed_at'
    assert candidate['classification'] == 'PARAMETERIZED_RANGE_CANDIDATE'
    assert report['semantic_status'] == 'NOT_VERIFIED'


def test_oracle_relational_sql_reuses_the_dialect_parser():
    report = analyze_sql('SELECT NVL(amount, 0) FROM account_entries WHERE changed_at > :lo AND changed_at <= :hi', dialect='oracle')
    assert report['status'] == 'ANALYZED', report
    assert {'COALESCE', 'PARAMETERIZED_RANGE'} <= set(report['statements'][0]['features'])
    assert {p['name'] for p in report['statements'][0]['parameters']} == {'lo', 'hi'}


def test_grouping_window_and_distinct_are_syntactic_features_only():
    report = analyze_sql('SELECT DISTINCT id, SUM(amount) OVER (PARTITION BY id) AS total FROM t', dialect='tsql')
    assert {'DISTINCT', 'AGGREGATION', 'WINDOW'} <= set(report['statements'][0]['features'])
    assert report['semantic_status'] == 'NOT_VERIFIED'


def test_reports_validate_against_packaged_versioned_schema():
    report = analyze_sql('SELECT 1', dialect='tsql')
    validate_document(report, 'sql_analysis')
    report = copy.deepcopy(report)
    report['analysis_version'] = 'future'
    with pytest.raises(SchemaValidationError):
        validate_document(report, 'sql_analysis')


def test_language_analysis_never_executes_supplied_text(tmp_path):
    marker = tmp_path / 'executed'
    sql = f"SELECT 'open({marker}).write(executed)' AS text_value"
    assert analyze_sql(sql, dialect='tsql')['status'] == 'ANALYZED'
    assert not marker.exists()


def test_unsupported_features_are_not_silently_reported_as_empty():
    with pytest.raises(ValueError):
        detect_required_features('EXEC dbo.p', source_dialect='tsql')


def test_bare_select_without_projection_is_not_supported():
    assert analyze_sql('SELECT', dialect='tsql')['status'] != 'ANALYZED'


def test_cli_writes_a_bound_report_and_protects_existing_files(tmp_path):
    import json
    import subprocess
    import sys
    source = tmp_path / 'export.sql'
    data = b'\xef\xbb\xbfSELECT id FROM t\r\nWHERE changed_at > @a AND changed_at <= @b;'
    source.write_bytes(data)
    target = tmp_path / 'analysis.json'
    command = [sys.executable, '-m', 'ssis_migration.languages.sql', '--source', str(source),
               '--dialect', 'tsql', '--output', str(target)]
    proc = subprocess.run(command, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(target.read_text())
    assert report['source_file']['sha256'] == hashlib.sha256(data).hexdigest()
    assert report['source_sha256'] == hashlib.sha256(data.decode('utf-8-sig').encode()).hexdigest()
    assert report['source_file']['size_bytes'] == len(data)
    original = target.read_bytes()
    proc = subprocess.run(command, capture_output=True, text=True)
    assert proc.returncode == 2
    assert target.read_bytes() == original
    assert source.read_bytes() == data


def test_cli_reports_unsupported_procedures_without_generating_code(tmp_path):
    import json
    import subprocess
    import sys
    source = tmp_path / 'package.sql'
    source.write_text('CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;')
    target = tmp_path / 'report.json'
    proc = subprocess.run([sys.executable, '-m', 'ssis_migration.languages.sql', '--source', str(source),
                           '--dialect', 'oracle', '--output', str(target)], capture_output=True, text=True)
    assert proc.returncode == 1, proc.stderr
    assert json.loads(target.read_text())['status'] in {'UNSUPPORTED', 'UNRESOLVED'}
    assert set(p.name for p in tmp_path.iterdir()) == {'package.sql', 'report.json'}


def test_oracle_cte_resolution_respects_unquoted_identifier_folding():
    report = analyze_sql('WITH LOCAL_CTE AS (SELECT id FROM source_table) SELECT * FROM local_cte', dialect='oracle')
    assert report['status'] == 'ANALYZED', report
    assert any(t['reference'] == 'local_cte' and t['role'] == 'CTE_REFERENCE' for t in report['statements'][0]['tables'])


def test_select_into_is_recorded_as_a_write_not_just_a_source_read():
    report = analyze_sql('SELECT id INTO dbo.new_target FROM dbo.source_table', dialect='tsql')
    assert report['status'] == 'ANALYZED', report
    assert {t['reference']: t['role'] for t in report['statements'][0]['tables']} == {
        'dbo.new_target': 'WRITE', 'dbo.source_table': 'READ'}


def test_sql_server_session_state_is_not_rewritten_as_an_ordinary_parameter():
    report = analyze_sql('SELECT @@ROWCOUNT', dialect='tsql')
    assert report['status'] == 'UNSUPPORTED'
