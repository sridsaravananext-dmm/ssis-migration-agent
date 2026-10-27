"""Original Oracle declarations and hostile review artifacts; no new workload."""
from collections import Counter, defaultdict
import copy
import hashlib
from pathlib import Path

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.base import UnsupportedSourceError
from ssis_migration.adapters.export_pipeline import run_export_review, validate_export_review
from ssis_migration.adapters.odi import commands, expressions, behavior_v2
from ssis_migration.adapters.odi.behavior import MappingIndex, PROFILE, LATEST_PROFILE, analyze_behavior, validate_behavior_analysis
from ssis_migration.generator.odi.expressions import build_candidates, validate_candidates
from ssis_migration.ir.normalized import content_digest
from ssis_migration.migration.native_review import build_native_review_plan
from ssis_migration.schemas import SchemaValidationError, validate_document
from ssis_migration.ui import extract_bundle

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / 'examples/public/oracle_odi_etl_offload/upstream/CloudAtCustomerODIETLOffload/ODISmartExport_ETLOffload_BigDataCloud@Customer.zip'


@pytest.fixture(scope='module')
def result(tmp_path_factory):
    folder = extract_bundle(ARCHIVE.read_bytes(), tmp_path_factory.mktemp('odi-expressions') / 'source')
    original, = folder.iterdir()
    return get_adapter('odi').analyze(original)


@pytest.fixture(scope='module')
def index(result):
    return MappingIndex(result.normalized_ir)


@pytest.fixture(scope='module')
def analysis(result):
    return analyze_behavior(result.normalized_ir)


@pytest.fixture(scope='module')
def bundle(result, analysis):
    plan, contracts = build_native_review_plan(result.normalized_ir, result.parsed['name'], behavior_analysis=analysis)
    candidates = build_candidates(result.normalized_ir, analysis, contracts)
    return plan, contracts, candidates


def syntax_entries(analysis):
    return [e['native_syntax'] for r in analysis['requirements'] if r['kind'] == 'ODI_MAPPING' for e in r['declarations']['expressions']]


def test_new_profile_accounts_for_every_expression_and_command_without_changing_ir(result, analysis):
    assert analysis['profile'] == LATEST_PROFILE
    assert len(analysis['requirements']) == 247
    assert Counter(r['kind'] for r in analysis['requirements']) == {
        'ODI_MAPPING': 7, 'ODI_EXPORT_REVIEW': 1, 'ODI_PROCEDURE_KM': 4,
        'ODI_PROCEDURE_KM_COMMAND': 80, 'ODI_PROCEDURE_KM_OPTION': 20,
        'ODI_PROCEDURE_KM_OPTION_ASSOCIATION': 135,
    }
    syntax = syntax_entries(analysis)
    assert Counter(e['status'] for e in syntax) == {'PARSED': 41, 'EMPTY_DECLARATION': 37, 'TEXT_METADATA_DECLARATION': 7, 'REVIEW_REQUIRED': 4}
    assert len({e['expression_node_id'] for e in syntax}) == 89
    assert all(e['semantic_status'] == 'NOT_VERIFIED' for e in syntax)
    assert result.normalized_ir['ir_id'] == analysis['ir_id']
    assert result.normalized_ir['extensions']['source_analysis']['metadata']['element_count'] == 41411


def test_source_native_text_is_never_sent_to_sql_parser_or_executed(index, monkeypatch):
    import sqlglot
    def forbidden(*args, **kwargs): raise AssertionError('Source native expression/KM text reached SQL parsing.')
    monkeypatch.setattr(sqlglot, 'parse', forbidden)
    results = [expressions.analyze_expression(index, n) for n in index.classes['SnpMapExpr']]
    assert sum(r['status'] == 'PARSED' for r in results) == 41
    assert len(commands.command_requirements(index)) == 239


def test_databricks_fragments_preserve_bindings_and_leave_dates_windows_and_empty_text_untranslated(analysis, bundle):
    _, contracts, candidates = bundle
    assert candidates['candidate_count'] == 41 and len(candidates['entries']) == 89
    source = {e['expression_node_id']: e for e in syntax_entries(analysis)}
    code = [e['databricks_sql_expression'] for e in candidates['entries'] if e['status'] == 'CANDIDATE']
    assert 'SUM(`ORD_HIVE`.`AMOUNT`)' in code
    assert 'YEAR(`ORD_HIVE`.`ORDER_DATE`)' in code
    assert '`ORD_HIVE`.`PRODUCT_ID` = `PD_HIVE`.`PRODUCT_ID`' in code
    for entry in candidates['entries']:
        original = source[entry['expression_node_id']]
        assert entry['logical_bindings'] == original['bindings']
        assert all(b['validity_declaration'] == '0' for b in original['bindings'])
        if original['status'] != 'PARSED':
            assert entry['databricks_sql_expression'] is None and entry['validation_status'] == 'NOT_GENERATED'
    assert not candidates['generated_notebooks'] and not candidates['migration_accepted']
    assert candidates['execution_status'] == 'NOT_RUN'
    assert contracts[0]['target_capabilities']['generation_languages'] == []
    assert all(o['verification'] == 'NOT_VERIFIED' for o in contracts[0]['required_behavior']['obligations'])


def test_command_text_flags_options_and_disagreeing_module_ids_remain_declared(analysis, index):
    steps = [r['declarations'] for r in analysis['requirements'] if r['kind'] == 'ODI_PROCEDURE_KM_COMMAND']
    sides = [s for step in steps for s in step['sides'].values()]
    assert len(steps) == 80 and all(s['effective_command'] is None and s['execution_status'] == 'NOT_RUN' for s in steps)
    assert Counter(s['text_binding']['resolution'] for s in sides) == {'ONE_DECLARED_RECORD': 58, 'UNBOUND_DECLARATION': 102}
    assert {s['language_declaration'] for s in sides} == {'null'}
    records = [r for s in sides for r in s['text_binding']['records']]
    assert Counter(r['contains_template_markers'] for r in records) == {True: 55, False: 3}
    for record in records:
        assert record['encryption_declaration'] == 'null'
        for name, text in record['fields'].items():
            if isinstance(text, str): assert record['text_sha256_utf8'][name] == hashlib.sha256(text.encode()).hexdigest()
    associations = [r['declarations'] for r in analysis['requirements'] if r['kind'] == 'ODI_PROCEDURE_KM_OPTION_ASSOCIATION']
    assert len(associations) == 135 and all(a['resolution'] == 'UNRESOLVED' and a['effective_binding'] is None for a in associations)
    assert {a['declared_module_identity'] for a in associations} == {'139', '329', '18161'}
    assert all(not a['module_identity_candidate_node_ids'] for a in associations)
    assert sum(len(step['option_associations']) for step in steps) == 135
    assert all(b['resolution'] == 'UNRESOLVED' for step in steps for b in step['option_associations'])
    assert all(r['declarations']['effective_value'] is None for r in analysis['requirements'] if r['kind'] == 'ODI_PROCEDURE_KM_OPTION')


def altered_input(index, node, field_name, replacement):
    """Fault injection into a copy of an original declaration; no source file."""
    cloned = copy.copy(index); cloned.nodes = dict(index.nodes)
    cloned.classes = defaultdict(list, {key: list(nodes) for key, nodes in index.classes.items()})
    changed = copy.deepcopy(node)
    next(f for f in changed['attributes']['declared_fields'] if f['name'] == field_name)['value'] = replacement
    cloned.nodes[node['node_id']] = changed
    cls = node['attributes']['object_class']
    cloned.classes[cls] = [changed if n['node_id'] == node['node_id'] else n for n in cloned.classes[cls]]
    return cloned, changed


@pytest.mark.parametrize('change', ['split_identifier', 'other_text', 'unparsed_flag', 'unknown_placeholder', 'extra_statement', 'changed_reference_name'])
def test_parser_rejects_disagreeing_or_unsafe_native_declarations_without_selecting_a_winner(index, change):
    node = next(n for n in index.classes['SnpMapExpr'] if next(f['value'] for f in n['attributes']['declared_fields'] if f['name'] == 'Txt') == 'CUS.CUSTOMER_ID')
    field_name, replacement = {
        'split_identifier': ('Txt', 'CUS.CUSTOMER_ ID'), 'other_text': ('Text', 'DIFFERENT'),
        'unparsed_flag': ('IsParsed', '0'), 'unknown_placeholder': ('ParsedTxt', '@{R99}'),
        'extra_statement': ('ParsedTxt', '@{R0}; SELECT 1'), 'changed_reference_name': ('Txt', 'CUS.UNDECLARED'),
    }[change]
    modified, expression = altered_input(index, node, field_name, replacement)
    syntax = expressions.analyze_expression(modified, expression)
    assert syntax['status'] == 'REVIEW_REQUIRED' and syntax['ast'] is None
    assert syntax['source_fields'][field_name] == replacement and syntax['unresolved_items']


@pytest.mark.parametrize('limit,low', [('MAX_TEXT_CHARS', 1), ('MAX_REFERENCES', 0)])
def test_expression_limits_stop_processing_original_declarations(index, monkeypatch, limit, low):
    monkeypatch.setattr(expressions, limit, low)
    node = next(n for n in index.classes['SnpMapExpr'] if next(f['value'] for f in n['attributes']['declared_fields'] if f['name'] == 'Txt') == 'CUS.CUSTOMER_ID')
    with pytest.raises(UnsupportedSourceError): expressions.analyze_expression(index, node)


@pytest.mark.parametrize('module,limit,low', [(commands, 'MAX_COMMANDS', 79), (behavior_v2, 'MAX_EXPRESSIONS', 88)])
def test_latest_analysis_caps_fail_before_publishing_source_review(result, tmp_path, monkeypatch, module, limit, low):
    monkeypatch.setattr(module, limit, low)
    output = tmp_path / 'output'
    with pytest.raises(UnsupportedSourceError): run_export_review(result.source_envelope['evidence_files'][0]['path'], output, 'odi')
    assert not output.exists()


def test_frozen_version_one_reviews_remain_replayable(result):
    graph = result.normalized_ir
    old = analyze_behavior(graph, profile=PROFILE)
    plan, contracts = build_native_review_plan(graph, result.parsed['name'], behavior_analysis=old)
    assert len(old['requirements']) == 8
    validate_export_review(result.source_envelope, graph, plan, contracts)
    with pytest.raises(SchemaValidationError): build_candidates(graph, old, contracts)
    with pytest.raises(SchemaValidationError): analyze_behavior(graph, profile='unknown')


def test_forged_command_flags_cannot_be_authorized_by_rehashed_requirements(result, analysis):
    changed = copy.deepcopy(analysis)
    step = next(r for r in changed['requirements'] if r['kind'] == 'ODI_PROCEDURE_KM_COMMAND')
    step['declarations']['effective_command'] = 'invented command'
    step['requirement_id'] = 'requirement-' + content_digest([LATEST_PROFILE, {k: v for k, v in step.items() if k != 'requirement_id'}])[:24]
    with pytest.raises(SchemaValidationError): validate_behavior_analysis(changed, result.normalized_ir)


def test_candidate_replay_rejects_valid_sql_with_wrong_source_binding(result, analysis, bundle):
    plan, contracts, candidates = bundle
    changed = copy.deepcopy(candidates)
    entry = next(e for e in changed['entries'] if e['status'] == 'CANDIDATE')
    entry['databricks_sql_expression'] = '`invented`.`column`'
    with pytest.raises(SchemaValidationError):
        validate_candidates(changed, result.source_envelope, result.normalized_ir, plan, contracts, analysis)


def test_candidate_schema_rejects_semantic_promotion_and_code_for_untranslated_forms(bundle):
    _, _, candidates = bundle
    changed = copy.deepcopy(candidates); changed['semantic_status'] = 'VERIFIED'
    with pytest.raises(SchemaValidationError): validate_document(changed, 'odi_expression_candidates')
    changed = copy.deepcopy(candidates)
    entry = next(e for e in changed['entries'] if e['status'] == 'NO_CANDIDATE'); entry['databricks_sql_expression'] = '1'
    with pytest.raises(SchemaValidationError): validate_document(changed, 'odi_expression_candidates')
