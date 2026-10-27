"""Port-default analysis on unchanged exports and hostile review artifacts."""
import copy
from collections import Counter
from pathlib import Path

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.export_pipeline import validate_export_review
from ssis_migration.adapters.informatica.behavior import PROFILE, LATEST_PROFILE, analyze_behavior, validate_behavior_analysis
from ssis_migration.adapters.informatica.defaults import analyze_port_default
from ssis_migration.adapters.informatica.expressions import analyze_expression
from ssis_migration.ir.normalized import content_digest
from ssis_migration.migrate import run_migration
from ssis_migration.migration.native_review import build_native_review_plan
from ssis_migration.schemas import SchemaValidationError

SOURCES = Path(__file__).resolve().parents[2] / 'examples/public/powercenter_scd1/upstream/SCD1'


@pytest.fixture(scope='module')
def source():
    return get_adapter('powercenter').analyze_bundle(SOURCES/'w_scd1.XML', dependency_files=[SOURCES/'m_scd1.XML'])


@pytest.fixture(scope='module')
def analysis(source):
    return analyze_behavior(source.normalized_ir)


def port(source, name):
    return next(n for n in source.normalized_ir['nodes'] if n['operation'] == 'PORT' and n['attributes']['export_attributes'].get('NAME') == name)


def defaults(analysis):
    return [d for r in analysis['requirements'] if r['kind'] == 'TRANSFORMATION' for d in r['declarations']['port_defaults']]


def test_latest_profile_adds_only_declared_nonempty_default_requirements(source, analysis):
    before = copy.deepcopy(source.normalized_ir)
    validate_behavior_analysis(analysis, source.normalized_ir)
    assert source.normalized_ir == before
    assert analysis['profile'] == LATEST_PROFILE
    assert len(analysis['requirements']) == 33
    assert Counter(d['default']['declaration_state'] for d in defaults(analysis)) == {'EMPTY': 37, 'EXPRESSION': 2}
    assert len([r for r in analysis['requirements'] if r['kind'] == 'PORT_DEFAULT']) == 2
    assert all(d['default']['effective_default_value'] is None and d['default']['runtime_outcome'] is None for d in defaults(analysis))


def test_empty_and_missing_default_metadata_never_supply_a_fallback(source):
    field = port(source, 'lkp_DIM_KEY')
    attributes = copy.deepcopy(field['attributes']['export_attributes'])
    assert attributes['DEFAULTVALUE'] == ''
    empty = analyze_port_default(attributes, {})
    del attributes['DEFAULTVALUE']  # Hostile loss of exported review metadata.
    absent = analyze_port_default(attributes, {})
    assert empty['declaration_state'] == 'EMPTY' and empty['raw_text'] == ''
    assert absent['declaration_state'] == 'ABSENT' and absent['raw_text'] is None
    assert empty['expression'] is None and absent['expression'] is None
    assert empty['effective_default_value'] is None and absent['effective_default_value'] is None


def test_both_original_error_literals_preserve_context_message_and_unverified_outcome(source, analysis):
    requirements = [r for r in analysis['requirements'] if r['kind'] == 'PORT_DEFAULT']
    assert {r['declarations']['port_name'] for r in requirements} == {'NEXTVAL', 'CURRVAL'}
    for item in requirements:
        declaration = item['declarations']; default = declaration['default']
        original = port(source, declaration['port_name'])
        assert item['subject_node_id'] == original['node_id']
        assert default['raw_text'] == original['attributes']['export_attributes']['DEFAULTVALUE']
        assert default['expression']['status'] == 'PARSED'
        assert default['expression']['ast'] == {'kind': 'ERROR_EXPRESSION', 'message': {'kind': 'STRING_LITERAL', 'value': 'transformation error'}}
        assert default['semantic_status'] == 'NOT_VERIFIED' and default['runtime_outcome'] is None
        assert original['evidence'][0] in item['evidence']
        assert any('recovery' in text for text in item['preservation_requirements'])
    assert {r['declarations']['port_name']: len(r['declarations']['outgoing_column_link_ids']) for r in requirements} == {'NEXTVAL': 1, 'CURRVAL': 0}


def test_truncated_real_error_metadata_stays_unresolved_without_repair(source):
    attributes = copy.deepcopy(port(source, 'NEXTVAL')['attributes']['export_attributes'])
    attributes['DEFAULTVALUE'] = attributes['DEFAULTVALUE'][:-1]
    result = analyze_port_default(attributes, {})
    assert result['raw_text'] == attributes['DEFAULTVALUE']
    assert result['expression']['status'] == 'REVIEW_REQUIRED'
    assert result['expression']['ast'] is None and result['unresolved_items']


def test_version_one_syntax_analysis_and_recorded_contract_profile_stay_replayable(source):
    text = port(source, 'NEXTVAL')['attributes']['export_attributes']['DEFAULTVALUE']
    assert analyze_expression(text, {})['status'] == 'REVIEW_REQUIRED'
    legacy = analyze_behavior(source.normalized_ir, profile=PROFILE)
    assert legacy['profile'] == PROFILE and len(legacy['requirements']) == 31
    assert not any('port_defaults' in r['declarations'] for r in legacy['requirements'])
    plan, contracts = build_native_review_plan(source.normalized_ir, 'w_scd1', behavior_analysis=legacy)
    validate_export_review(source.source_envelope, source.normalized_ir, plan, contracts)
    validate_export_review(source.source_envelope, source.normalized_ir, plan, contracts, behavior_analysis=legacy)


def test_all_default_requirements_enter_contracts_without_verification_credit(source, analysis):
    plan, contracts = build_native_review_plan(source.normalized_ir, 'w_scd1', behavior_analysis=analysis)
    obligations = [o for c in contracts for o in c['required_behavior']['obligations']]
    assert len([o for o in obligations if o['kind'].startswith('normalized_')]) == 702
    preserved = [o for o in obligations if o['kind'] == 'source_requirement']
    assert len(preserved) == 33
    error_defaults = [o for o in preserved if o['source_value']['kind'] == 'PORT_DEFAULT']
    assert len(error_defaults) == 2
    assert all(o['verification'] == 'NOT_VERIFIED' and o['critical'] is None and o['weight'] is None for o in error_defaults)
    validate_export_review(source.source_envelope, source.normalized_ir, plan, contracts, behavior_analysis=analysis)


@pytest.mark.parametrize('change', ['omit_error', 'message', 'invent_null', 'invent_abort', 'downgrade_profile'])
def test_rebuilt_contracts_cannot_authorize_altered_port_defaults(source, analysis, change):
    altered = copy.deepcopy(analysis)
    error = next(r for r in altered['requirements'] if r['kind'] == 'PORT_DEFAULT')
    if change == 'omit_error':altered['requirements'].remove(error)
    elif change == 'message':error['declarations']['default']['expression']['ast']['message']['value'] = ''
    elif change == 'invent_null':next(d for d in defaults(altered) if d['default']['declaration_state'] == 'EMPTY')['default']['expression'] = {'ast': None}
    elif change == 'invent_abort':error['declarations']['default']['runtime_outcome'] = 'ABORT'
    else:altered['profile'] = PROFILE
    # Recompute requirement and plan/contract hashes; original evidence must win.
    for r in altered['requirements']:
        r['requirement_id'] = 'requirement-' + content_digest([altered['profile'], {k:v for k,v in r.items() if k != 'requirement_id'}])[:24]
    plan, contracts = build_native_review_plan(source.normalized_ir, 'w_scd1', behavior_analysis=altered)
    with pytest.raises(SchemaValidationError):
        validate_export_review(source.source_envelope, source.normalized_ir, plan, contracts)
    with pytest.raises(SchemaValidationError):
        validate_behavior_analysis(altered, source.normalized_ir)


def test_unknown_recorded_profile_does_not_fall_back_to_latest(source, analysis):
    plan, contracts = build_native_review_plan(source.normalized_ir, 'w_scd1', behavior_analysis=analysis)
    plan['metadata']['behavior_analysis_binding']['profile'] = 'unrecognized_profile'
    with pytest.raises(SchemaValidationError, match='Unsupported PowerCenter behavior profile'):
        validate_export_review(source.source_envelope, source.normalized_ir, plan, contracts)


def test_new_reviews_publish_version_two_but_remain_blocked(tmp_path):
    report = run_migration(str(SOURCES/'w_scd1.XML'), str(tmp_path), dependency_files=[str(SOURCES/'m_scd1.XML')])
    assert report['behavior_analysis']['profile'] == LATEST_PROFILE
    assert report['behavior_analysis']['requirement_count'] == 33
    assert report['status'] == 'BLOCKED' and report['migration_accepted'] is False
    assert report['coverage']['semantic']['verified_percent'] is None
    assert report['execution_status'] == 'NOT_RUN' and not list(tmp_path.rglob('*.ipynb'))
