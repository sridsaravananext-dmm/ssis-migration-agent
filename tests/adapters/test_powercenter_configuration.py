"""Scoped configuration on original exports and hostile review metadata only."""
import copy
import json
from pathlib import Path

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.base import UnsupportedSourceError
from ssis_migration.adapters.export_pipeline import validate_export_review
from ssis_migration.adapters.informatica.behavior import PROFILE, DEFAULTS_PROFILE, LATEST_PROFILE, analyze_behavior, validate_behavior_analysis
from ssis_migration.adapters.informatica.behavior_v3 import ConfigurationBehaviorAnalysis
from ssis_migration.adapters.informatica.configuration import configuration_context
from ssis_migration.ir.normalized import content_digest
from ssis_migration.migrate import run_migration
from ssis_migration.migration.native_review import build_native_review_plan
from ssis_migration.schemas import SchemaValidationError

SOURCES = Path(__file__).resolve().parents[2] / 'examples/public/powercenter_scd1/upstream/SCD1'


@pytest.fixture(scope='module')
def original():
    return get_adapter('powercenter').analyze_bundle(SOURCES/'w_scd1.XML', dependency_files=[SOURCES/'m_scd1.XML'])


@pytest.fixture(scope='module')
def analysis(original):
    return analyze_behavior(original.normalized_ir)


def session_context(document):
    return next(r for r in document['requirements'] if r['kind'] == 'SESSION')['declarations']['configuration_contributions']


def connections(document):
    return [r for r in document['requirements'] if r['kind'] == 'CONNECTION_BINDING']


def test_configuration_selection_is_explicit_and_effective_values_remain_unverified(original, analysis):
    before = copy.deepcopy(original.normalized_ir)
    validate_behavior_analysis(analysis, original.normalized_ir)
    assert original.normalized_ir == before
    assert analysis['profile'] == LATEST_PROFILE and len(analysis['requirements']) == 33
    context = session_context(analysis)
    assert context['configuration_selection'] == 'DECLARATION_BOUND'
    assert context['configuration_references'][0]['reference']['REFOBJECTNAME'] == 'default_session_config'
    assert context['effective_configuration'] is None and context['product_version'] is None
    assert context['precedence_status'] == 'NOT_VERIFIED'
    assert context['repository_export_versions'] == ['189.98', '189.98']
    assert len(context['instance_scopes']) == 9 and context['unmatched_instance_declaration_ids'] == []
    assert context['parameter_file']['declaration_state'] == 'EMPTY'
    assert context['parameter_file']['resolved_values'] is None
    assert all(p['effective_value'] is None for p in context['properties'])


def test_lookup_contributions_keep_both_exported_values_and_their_evidence(original, analysis):
    scope = next(s for s in session_context(analysis)['instance_scopes'] if s['instance_name'] == 'LKPTRANS')
    prop = next(p for p in scope['properties'] if p['name'] == 'Connection Information')
    assert prop['declaration_status'] == 'DIFFERING_DECLARATIONS'
    assert {c['origin']:c['value'] for c in prop['contributions']} == {'MAPPING_DEFINITION':'$Target', 'SESSION_INSTANCE_OVERRIDE':'Oracle'}
    assert prop['effective_value'] is None
    requirement = next(r for r in analysis['requirements'] if r['kind'] == 'SESSION')
    nodes = {n['node_id']:n for n in original.normalized_ir['nodes']}
    for contribution in prop['contributions']:
        assert nodes[contribution['setting_node_id']]['evidence'][0] in requirement['evidence']


def test_writer_instances_are_not_flattened_into_one_setting_namespace(analysis):
    context = session_context(analysis)
    writers = {s['instance_name']:s for s in context['instance_scopes'] if s['instance_name'] in {'target','target2'}}
    assert writers['target']['definition_node_id'] == writers['target2']['definition_node_id']
    reject_names = {}
    for name,scope in writers.items():
        prop = next(p for p in scope['properties'] if p['name'] == 'Reject filename')
        assert prop['declaration_status'] == 'SINGLE_DECLARATION'
        reject_names[name] = prop['contributions'][0]['value']
        assert scope['effective_object_binding'] is None
    assert reject_names == {'target':'target1.bad', 'target2':'target21.bad'}
    assert not any(p['name'] == 'Reject filename' for p in context['properties'])


def test_same_alias_keeps_four_owners_without_inventing_physical_database_or_schema(analysis):
    refs = [r['declarations']['binding_context'] for r in connections(analysis)]
    assert len(refs) == 4
    assert {r['connection_alias'] for r in refs} == {'Oracle'}
    assert {r['owner_instance_name'] for r in refs} == {'target','target2','SQ_CUSTOMER','LKPTRANS'}
    assert len({r['owner_extension_node_id'] for r in refs}) == 4
    assert all(r['owner_resolution'] == 'DECLARATION_BOUND' for r in refs)
    for context in refs:
        physical = context['physical_binding']
        assert physical['status'] == 'MISSING_METADATA'
        assert physical['database'] is None and physical['schema'] is None
        assert physical['connection_definition_node_id'] is None and physical['parameter_values'] is None
        assert context['required_exports']
    source = next(r for r in refs if r['owner_instance_name'] == 'SQ_CUSTOMER')['associated_source_declarations'][0]
    assert source['instance_name'] == 'CUSTOMER' and source['resolution'] == 'DECLARATION_BOUND'
    assert source['instance_scope']['definition_declaration']['OWNERNAME'] == 'SYSTEM'
    assert source['instance_scope']['definition_declaration']['DBDNAME'] == 'source_db'


def test_single_workflow_does_not_guess_owners_from_neighbor_mapping():
    source = get_adapter('powercenter').analyze(SOURCES/'w_scd1.XML')
    document = analyze_behavior(source.normalized_ir)
    assert session_context(document)['instance_scopes'] == []
    assert all(r['declarations']['binding_context']['owner_resolution'] == 'UNRESOLVED' for r in connections(document))


def test_missing_config_reference_name_does_not_select_isdefault_object(original):
    altered = copy.deepcopy(original.normalized_ir)
    reference = next(n for n in altered['nodes'] if n['operation'] == 'CONFIGREFERENCE')
    reference['attributes']['export_attributes']['REFOBJECTNAME'] = ''
    index = ConfigurationBehaviorAnalysis(altered)
    session = next(n for n in altered['nodes'] if n['operation'] == 'SESSION')
    context,_ = configuration_context(index, session)
    assert context['configuration_selection'] == 'UNRESOLVED'
    assert not any(layer['origin'] == 'CONFIGURATION_OBJECT' for layer in context['layers'])


def test_duplicate_property_names_in_hostile_review_keep_both_contributions(original):
    altered = copy.deepcopy(original.normalized_ir)
    index = ConfigurationBehaviorAnalysis(altered)
    session = next(n for n in altered['nodes'] if n['operation'] == 'SESSION')
    settings = index.child(session, 'ATTRIBUTE')
    settings[1]['attributes']['export_attributes']['NAME'] = settings[0]['attributes']['export_attributes']['NAME']
    context,_ = configuration_context(index, session)
    prop = next(p for p in context['properties'] if p['name'] == settings[0]['attributes']['export_attributes']['NAME'])
    assert prop['declaration_status'] == 'DUPLICATE_WITHIN_LAYER'
    assert len(prop['contributions']) == 2 and prop['effective_value'] is None


@pytest.mark.parametrize('profile', [PROFILE, DEFAULTS_PROFILE])
def test_earlier_profiles_keep_their_prior_representations(original, profile):
    legacy = analyze_behavior(original.normalized_ir, profile=profile)
    assert 'configuration_contributions' not in next(r for r in legacy['requirements'] if r['kind'] == 'SESSION')['declarations']
    assert all('binding_context' not in r['declarations'] for r in connections(legacy))
    plan,contracts = build_native_review_plan(original.normalized_ir, 'w_scd1', behavior_analysis=legacy)
    validate_export_review(original.source_envelope, original.normalized_ir, plan, contracts)


@pytest.mark.parametrize('change', ['winner','database','collapse_owner','erase_difference','product_version'])
def test_rebuilt_artifacts_cannot_fabricate_configuration_or_physical_binding(original, analysis, change):
    altered = copy.deepcopy(analysis)
    context = session_context(altered)
    connection = connections(altered)[0]['declarations']['binding_context']
    if change == 'winner':context['effective_configuration'] = {}
    elif change == 'database':connection['physical_binding']['database'] = connection['connection_alias']
    elif change == 'collapse_owner':connection['owner_instance_name'] = connections(altered)[1]['declarations']['binding_context']['owner_instance_name']
    elif change == 'product_version':context['product_version'] = context['repository_export_versions'][0]
    else:
        lookup = next(s for s in context['instance_scopes'] if s['instance_name'] == 'LKPTRANS')
        next(p for p in lookup['properties'] if p['name'] == 'Connection Information')['contributions'].pop()
    for r in altered['requirements']:
        r['requirement_id'] = 'requirement-' + content_digest([altered['profile'], {k:v for k,v in r.items() if k != 'requirement_id'}])[:24]
    plan,contracts = build_native_review_plan(original.normalized_ir, 'w_scd1', behavior_analysis=altered)
    with pytest.raises(SchemaValidationError):validate_export_review(original.source_envelope, original.normalized_ir, plan, contracts)
    with pytest.raises(SchemaValidationError):validate_behavior_analysis(altered, original.normalized_ir)


@pytest.mark.parametrize('limit', ['MAX_INSTANCE_SCOPES', 'MAX_SETTING_CONTRIBUTIONS'])
def test_configuration_context_limit_applies_before_partial_publication(monkeypatch, tmp_path, limit):
    from ssis_migration.adapters.informatica import configuration
    monkeypatch.setattr(configuration, limit, 8)
    output = tmp_path/'output'
    with pytest.raises(UnsupportedSourceError, match='limit'):
        run_migration(str(SOURCES/'w_scd1.XML'), str(output), dependency_files=[str(SOURCES/'m_scd1.XML')])
    assert not output.exists()


def test_latest_report_exposes_needed_metadata_without_acceptance(tmp_path):
    report = run_migration(str(SOURCES/'w_scd1.XML'), str(tmp_path), dependency_files=[str(SOURCES/'m_scd1.XML')])
    assert report['behavior_analysis']['profile'] == LATEST_PROFILE and report['behavior_analysis']['requirement_count'] == 33
    assert any('connection properties' in issue for issue in report['required_human_actions'])
    document = json.loads(Path(report['artifacts']['source_behavior_analysis']).read_text())
    assert all(r['declarations']['binding_context']['physical_binding']['status'] == 'MISSING_METADATA' for r in connections(document))
    assert report['status'] == 'BLOCKED' and report['migration_accepted'] is False
    assert report['execution_status'] == 'NOT_RUN' and report['coverage']['semantic']['verified_percent'] is None
    assert not list(tmp_path.rglob('*.ipynb'))
