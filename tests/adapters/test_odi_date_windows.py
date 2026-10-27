"""Date/window declarations from Oracle's original export, without new data."""
from collections import Counter, defaultdict
import copy
from pathlib import Path

import pytest
import sqlglot

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.odi import expressions_v2
from ssis_migration.adapters.odi.behavior import MappingIndex, analyze_behavior, validate_behavior_analysis
from ssis_migration.adapters.odi.behavior_v3 import PROFILE
from ssis_migration.generator.odi.expressions_v2 import build_candidates, validate_candidates, _check_window
from ssis_migration.ir.normalized import content_digest
from ssis_migration.migration.native_review import build_native_review_plan
from ssis_migration.schemas import SchemaValidationError, validate_document
from ssis_migration.ui import extract_bundle

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / 'examples/public/oracle_odi_etl_offload/upstream/CloudAtCustomerODIETLOffload/ODISmartExport_ETLOffload_BigDataCloud@Customer.zip'


@pytest.fixture(scope='module')
def result(tmp_path_factory):
    folder = extract_bundle(ARCHIVE.read_bytes(), tmp_path_factory.mktemp('odi-windows') / 'source')
    source, = folder.iterdir()
    return get_adapter('odi').analyze(source)


@pytest.fixture(scope='module')
def analysis(result):
    return analyze_behavior(result.normalized_ir, profile=PROFILE)


@pytest.fixture(scope='module')
def syntax(analysis):
    return [e['native_syntax'] for r in analysis['requirements'] if r['kind'] == 'ODI_MAPPING' for e in r['declarations']['expressions']]


@pytest.fixture(scope='module')
def bundle(result, analysis):
    plan, contracts = build_native_review_plan(result.normalized_ir, result.parsed['name'], behavior_analysis=analysis)
    return plan, contracts, build_candidates(result.normalized_ir, analysis, contracts)


def test_all_original_expressions_remain_accounted_and_unspecified_window_fields_stay_unspecified(analysis, syntax):
    assert analysis['profile'] == PROFILE and len(analysis['requirements']) == 247
    assert Counter(s['status'] for s in syntax) == {'PARSED': 45, 'EMPTY_DECLARATION': 37, 'TEXT_METADATA_DECLARATION': 7}
    windows = [s['ast'] for s in syntax if s['ast'] and s['ast']['kind'] == 'WINDOW']
    assert {w['function'] for w in windows} == {'LAG', 'MAX', 'SUM'}
    assert all(w['offset_literal'] is None and w['default_expression'] is None for w in windows)
    assert all(item['direction'] is None and item['null_order'] is None for w in windows for item in w['order_by'])
    maximum = next(w for w in windows if w['function'] == 'MAX')
    assert maximum['frame'] == {'unit': 'ROWS', 'syntax_form': 'SINGLE_BOUND', 'start': {'kind': 'INTEGER_LITERAL', 'token': '3'}, 'start_direction': 'PRECEDING', 'end': None}
    assert all(w['frame'] is None for w in windows if w['function'] != 'MAX')
    assert all(s['semantic_status'] == 'NOT_VERIFIED' for s in syntax)


def test_date_start_length_and_format_are_literal_source_tokens_with_no_date_sql(syntax, bundle):
    _, _, candidates = bundle
    source = next(s for s in syntax if s['ast'] and s['ast']['kind'] == 'DATE_CONVERSION')
    assert source['ast']['substring']['start']['token'] == '0'
    assert source['ast']['substring']['length']['token'] == '10'
    assert source['ast']['format']['token'] == "'YYYY-MM-DD'"
    entry = next(e for e in candidates['entries'] if e['expression_node_id'] == source['expression_node_id'])
    assert entry['databricks_sql_expression'] is None and entry['status'] == 'NO_CANDIDATE'
    assert any('date SQL' in item or 'Date SQL' in item for item in entry['unresolved_items'])
    assert any('invalid-input' in item for item in entry['unresolved_items'])


def test_window_candidates_preserve_single_bounds_and_omissions_without_enabling_execution(syntax, bundle):
    _, contracts, candidates = bundle
    assert candidates['candidate_count'] == 44 and len(candidates['entries']) == 89
    windows = {s['expression_node_id']: s for s in syntax if s['ast'] and s['ast']['kind'] == 'WINDOW'}
    code = [e['databricks_sql_expression'] for e in candidates['entries'] if e['expression_node_id'] in windows]
    assert len(code) == 3 and all(text for text in code)
    assert any(text.endswith(' ROWS 3 PRECEDING)') for text in code)
    assert not any('CURRENT ROW' in text or 'BETWEEN' in text or 'NULLS' in text or ' ASC' in text or ' DESC' in text for text in code)
    assert 'LAG(`AGGREGATE`.`AMT`) OVER (PARTITION BY `AGGREGATE`.`REGION`, `AGGREGATE`.`FAMILY` ORDER BY `AGGREGATE`.`YR`)' in code
    assert candidates['generated_notebooks'] == [] and candidates['execution_status'] == 'NOT_RUN'
    assert not candidates['migration_accepted'] and contracts[0]['target_capabilities']['generation_languages'] == []


def test_native_date_windows_do_not_call_a_sql_parser(result, syntax, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Native date/window source reached SQL parsing.')
    monkeypatch.setattr(sqlglot, 'parse', forbidden)
    index = MappingIndex(result.normalized_ir)
    nodes = {s['expression_node_id'] for s in syntax if s['ast'] and s['ast']['kind'] in ('WINDOW', 'DATE_CONVERSION')}
    assert all(expressions_v2.analyze_expression(index, index.nodes[node])['status'] == 'PARSED' for node in nodes)


def changed_expression(result, source, replacements):
    """Corrupt a copy of actual typed declarations, without authoring a source."""
    index = MappingIndex(result.normalized_ir); original = index.nodes[source['expression_node_id']]
    node = copy.deepcopy(original)
    for field in node['attributes']['declared_fields']:
        if field['name'] in replacements: field['value'] = replacements[field['name']]
    index.nodes[node['node_id']] = node
    index.classes = defaultdict(list, {k: [node if n['node_id'] == node['node_id'] else n for n in nodes] for k, nodes in index.classes.items()})
    return index, node


@pytest.mark.parametrize('change', ['mismatched_frame', 'added_direction', 'following_bound', 'extra_statement', 'literal_placeholder'])
def test_extended_parser_rejects_disagreements_and_unsupported_changes_without_prefix_translation(result, syntax, change):
    source = next(s for s in syntax if s['ast'] and s['ast']['kind'] == ('DATE_CONVERSION' if change == 'literal_placeholder' else 'WINDOW')
                  and (change == 'literal_placeholder' or s['ast']['function'] == 'MAX'))
    text = source['source_fields']['Txt']; parsed = source['source_fields']['ParsedTxt']
    changes = {
        'mismatched_frame': {'ParsedTxt': parsed.replace('3 PRECEDING', '4 PRECEDING')},
        'added_direction': {'ParsedTxt': parsed.replace('ROWS', 'ASC ROWS'), 'Txt': text.replace('ROWS', 'ASC ROWS')},
        'following_bound': {'ParsedTxt': parsed.replace('PRECEDING', 'FOLLOWING'), 'Txt': text.replace('PRECEDING', 'FOLLOWING')},
        'extra_statement': {'ParsedTxt': parsed + '; SELECT 1'},
        'literal_placeholder': {'ParsedTxt': parsed.replace("'YYYY-MM-DD'", "'@{R0}'"), 'Txt': text.replace("'YYYY-MM-DD'", "'ORD.ORDER_DATE'")},
    }[change]
    index, node = changed_expression(result, source, changes)
    reviewed = expressions_v2.analyze_expression(index, node)
    assert reviewed['status'] == 'REVIEW_REQUIRED' and reviewed['ast'] is None
    assert reviewed['unresolved_items']


@pytest.mark.parametrize('limit,low,kind', [('MAX_FRAME_BOUND', 2, 'WINDOW'), ('MAX_WINDOW_KEYS', 1, 'WINDOW'), ('MAX_FORMAT_TOKEN', 5, 'DATE_CONVERSION')])
def test_new_grammar_limits_retain_real_source_records_for_review(result, syntax, monkeypatch, limit, low, kind):
    source = next(s for s in syntax if s['ast'] and s['ast']['kind'] == kind and (kind != 'WINDOW' or s['ast']['function'] == 'MAX'))
    monkeypatch.setattr(expressions_v2, limit, low)
    index = MappingIndex(result.normalized_ir)
    reviewed = expressions_v2.analyze_expression(index, index.nodes[source['expression_node_id']])
    assert reviewed['status'] == 'REVIEW_REQUIRED' and reviewed['ast'] is None
    assert reviewed['source_fields'] == source['source_fields']


@pytest.mark.parametrize('replacement', ['ROWS 4 PRECEDING', 'ROWS BETWEEN 3 PRECEDING AND CURRENT ROW'])
def test_independent_target_check_rejects_changed_or_invented_frame_boundaries(syntax, bundle, replacement):
    _, _, candidates = bundle
    source = next(s for s in syntax if s['ast'] and s['ast']['kind'] == 'WINDOW' and s['ast']['function'] == 'MAX')
    entry = next(e for e in candidates['entries'] if e['expression_node_id'] == source['expression_node_id'])
    code = entry['databricks_sql_expression'].replace('ROWS 3 PRECEDING', replacement)
    target = sqlglot.parse_one('SELECT ' + code, read='databricks').expressions[0]
    with pytest.raises(SchemaValidationError): _check_window(target, source['ast'], source['bindings'])


def test_source_replay_rejects_self_rehashed_date_index_rewrite(result, analysis):
    changed = copy.deepcopy(analysis)
    mapping = next(r for r in changed['requirements'] if r['kind'] == 'ODI_MAPPING' and any(e['native_syntax']['ast'] and e['native_syntax']['ast']['kind'] == 'DATE_CONVERSION' for e in r['declarations']['expressions']))
    expression = next(e for e in mapping['declarations']['expressions'] if e['native_syntax']['ast'] and e['native_syntax']['ast']['kind'] == 'DATE_CONVERSION')
    expression['native_syntax']['ast']['substring']['start']['token'] = '1'
    mapping['requirement_id'] = 'requirement-' + content_digest([PROFILE, {k: v for k, v in mapping.items() if k != 'requirement_id'}])[:24]
    with pytest.raises(SchemaValidationError): validate_behavior_analysis(changed, result.normalized_ir)


def test_candidate_replay_rejects_an_implicit_frame_expansion_even_when_sql_is_valid(result, analysis, bundle):
    plan, contracts, candidates = bundle
    changed = copy.deepcopy(candidates)
    entry = next(e for e in changed['entries'] if e['databricks_sql_expression'] and 'ROWS 3 PRECEDING' in e['databricks_sql_expression'])
    entry['databricks_sql_expression'] = entry['databricks_sql_expression'].replace('ROWS 3 PRECEDING', 'ROWS BETWEEN 3 PRECEDING AND CURRENT ROW')
    with pytest.raises(SchemaValidationError):
        validate_candidates(changed, result.source_envelope, result.normalized_ir, plan, contracts, analysis)


def test_schema_does_not_allow_promoting_window_review_to_semantic_verification(bundle):
    _, _, candidates = bundle
    changed = copy.deepcopy(candidates); changed['semantic_status'] = 'VERIFIED'
    with pytest.raises(SchemaValidationError): validate_document(changed, 'odi_expression_candidates_v2')
