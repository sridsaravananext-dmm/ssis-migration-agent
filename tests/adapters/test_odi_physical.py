"""Physical declarations from Oracle's original export, without new workloads."""
from collections import Counter
import copy
from pathlib import Path

import pytest
import sqlglot

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.base import UnsupportedSourceError
from ssis_migration.adapters.odi import physical
from ssis_migration.adapters.odi.behavior import MappingIndex, analyze_behavior, validate_behavior_analysis
from ssis_migration.adapters.odi.behavior_v4 import PROFILE
from ssis_migration.adapters.odi.native import value
from ssis_migration.generator.odi.expressions_v3 import build_candidates
from ssis_migration.ir.normalized import content_digest
from ssis_migration.migration.native_review import build_native_review_plan
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ssis_migration.ui import extract_bundle

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / 'examples/public/oracle_odi_etl_offload/upstream/CloudAtCustomerODIETLOffload/ODISmartExport_ETLOffload_BigDataCloud@Customer.zip'
COLLECTIONS = {'physical_nodes': 'SnpPhyNode', 'execution_units': 'SnpExecUnit',
               'execution_groups': 'SnpExecUnitGrp', 'properties': 'SnpMapProp'}


@pytest.fixture(scope='module')
def result(tmp_path_factory):
    directory = extract_bundle(ARCHIVE.read_bytes(), tmp_path_factory.mktemp('odi-physical') / 'source')
    original, = directory.iterdir()
    return get_adapter('odi').analyze(original)


@pytest.fixture(scope='module')
def index(result):
    return MappingIndex(result.normalized_ir)


@pytest.fixture(scope='module')
def analysis(result):
    return analyze_behavior(result.normalized_ir, profile=PROFILE)


@pytest.fixture(scope='module')
def report(analysis):
    return physical.extract_physical_analysis(analysis)


@pytest.fixture(scope='module')
def bundle(result, analysis):
    plan, contracts = build_native_review_plan(result.normalized_ir, result.parsed['name'], behavior_analysis=analysis)
    return plan, contracts, build_candidates(result.normalized_ir, analysis, contracts)


def records(report, collection):
    return [record for design in report['designs'] for record in design[collection]]


def references(report):
    return [reference for collection in COLLECTIONS for record in records(report, collection)
            for reference in record['references']]


def field_value(record, name):
    found = [field for field in record['declared_fields'] if field['name'] == name]
    return found[0]['value'] if len(found) == 1 else None


def change_field(node, name, replacement):
    declaration, = [field for field in node['attributes']['declared_fields'] if field['name'] == name]
    declaration['value'] = replacement


def test_original_physical_records_and_every_typed_field_are_retained_once(result, index, report):
    assert report['profile'] == physical.PROFILE
    assert report['ir_id'] == result.normalized_ir['ir_id']
    assert report['source_analysis_sha256'] == result.normalized_ir['provenance']['source_analysis_sha256']
    assert report['evidence_files'] == result.normalized_ir['evidence_files']
    assert len(report['designs']) == 11 and report['unscoped_records'] == []
    assert report['summary']['design_count'] == 11
    assert report['summary']['physical_node_count'] == 48
    assert report['summary']['execution_unit_count'] == 25
    assert report['summary']['execution_group_count'] == 25
    assert report['summary']['property_count'] == 153
    assert report['summary']['unscoped_record_count'] == 0
    for design in report['designs']:
        original = index.nodes[design['design_node_id']]
        assert design['declared_fields'] == original['attributes']['declared_fields']
        assert design['mapping_node_id'] == index.owner(original)
        assert design['evidence']
    for collection, cls in COLLECTIONS.items():
        found = records(report, collection)
        ids = [record['node_id'] for record in found]
        assert len(ids) == len(set(ids))
        if cls != 'SnpMapProp':
            assert set(ids) == {node['node_id'] for node in index.classes[cls]}
        for record in found:
            original = index.nodes[record['node_id']]
            assert record['object_class'] == cls
            assert record['declared_fields'] == original['attributes']['declared_fields']
            assert record['evidence'] and record['scope_resolution'] == 'BOUND'
    validate_evidence_references(report)


def test_all_alternative_designs_remain_unselected_and_no_runtime_order_is_invented(report):
    per_mapping = Counter(design['mapping_node_id'] for design in report['designs'])
    assert len(per_mapping) == 7 and Counter(per_mapping.values()) == {2: 4, 1: 3}
    assert all(design['selected_for_execution'] is None for design in report['designs'])
    assert all(design['execution_order_status'] == 'NOT_ESTABLISHED' for design in report['designs'])
    assert report['status'] == 'REVIEW_REQUIRED' and report['execution_status'] == 'NOT_RUN'
    assert report['semantic_status'] == 'NOT_VERIFIED' and not report['migration_accepted']


def test_declared_context_and_group_links_require_exact_identity_and_scope(index, report):
    contexts = [design['context_reference'] for design in report['designs']]
    assert len(contexts) == 11
    for design, reference in zip(report['designs'], contexts):
        assert reference['field'] == 'IMapRef' and reference['target_class'] == 'SnpMapRef'
        assert reference['resolution'] == 'RESOLVED'
        target = index.nodes[reference['target_node_id']]
        assert reference['candidate_node_ids'] == [target['node_id']]
        assert value(target, 'IMapRef') == reference['declared_identity']
        assert value(target, 'AdapterIntfType') == 'IContext'
        assert index.owner(target) == design['mapping_node_id']
        definition = reference['definition_reference']
        assert definition['resolution'] == 'RESOLVED'
        assert value(index.nodes[definition['target_node_id']], 'GlobalId') == definition['declared_guid']
    groups = [reference for reference in references(report) if reference['field'] == 'IExecUnitGrp']
    assert len(groups) == 25 and all(reference['resolution'] == 'RESOLVED' for reference in groups)
    for design in report['designs']:
        design_groups = {record['node_id'] for record in design['execution_groups']}
        for unit in design['execution_units']:
            group, = [reference for reference in unit['references'] if reference['field'] == 'IExecUnitGrp']
            assert group['target_node_id'] in design_groups
            assert value(index.nodes[group['target_node_id']], 'IExecUnitGrp') == group['declared_identity']


def test_physical_property_parent_chains_preserve_all_eight_destructive_declarations(index, report):
    properties = records(report, 'properties')
    descendants = [record for record in properties if str(field_value(record, 'IMapPropParent')).isdigit()]
    assert len(properties) == 153 and len(descendants) == 16
    destructive = Counter(field_value(record, 'Name') for record in properties
                          if field_value(record, 'PropValue') == 'true'
                          and field_value(record, 'Name') in {'TRUNCATE', 'TRUNCATE_TARGET_TABLE', 'OVERWRITE_TARGET_TABLE'})
    assert destructive == {'TRUNCATE': 2, 'TRUNCATE_TARGET_TABLE': 3, 'OVERWRITE_TARGET_TABLE': 3}
    flagged = {record['node_id'] for record in properties if field_value(record, 'PropValue') == 'true'
               and field_value(record, 'Name') in destructive}
    assert set(report['destructive_property_node_ids']) == flagged
    assert len(report['destructive_property_node_ids']) == report['summary']['destructive_property_count'] == 8
    for design in report['designs']:
        for record in design['properties']:
            assert record['scope_design_node_ids'] == [design['design_node_id']]
            assert record['scope_chain_node_ids']
            assert all(node in index.nodes for node in record['scope_chain_node_ids'])


def test_km_dependency_use_counts_preserve_missing_definitions_instead_of_binding_names(report):
    by_role = {}
    for role in ('IEuKmRef', 'ISrcCompKm', 'ICheckKm', 'ITgtCompKm'):
        uses = [reference for reference in references(report)
                if reference['field'] == role and str(reference['declared_identity']).isdigit()]
        assert all(reference['resolution'] == 'RESOLVED' for reference in uses)
        assert all(reference['definition_reference'] is not None for reference in uses)
        by_role[role] = Counter('BOUND' if reference['definition_reference']['target_node_id'] else 'MISSING'
                                for reference in uses)
    assert by_role == {'IEuKmRef': {'MISSING': 25}, 'ISrcCompKm': {'MISSING': 37},
                       'ICheckKm': {'BOUND': 8}, 'ITgtCompKm': {'BOUND': 4, 'MISSING': 21}}
    logical_schemas = [reference for reference in references(report) if reference['field'] == 'ILschemaRef']
    assert len(logical_schemas) == 25
    assert all(reference['definition_reference']['resolution'] == 'RESOLVED' for reference in logical_schemas)


def test_physical_source_analysis_never_parses_sql_or_expands_commands(index, report, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Physical declarations reached a SQL parser.')
    monkeypatch.setattr(sqlglot, 'parse', forbidden)
    assert physical.analyze_physical(index) == report


def test_physical_requirements_bind_into_contracts_without_promoting_expression_fragments(analysis, report, bundle):
    plan, contracts, candidates = bundle
    assert analysis['profile'] == PROFILE and len(analysis['requirements']) == 258
    requirements = [requirement for requirement in analysis['requirements'] if requirement['kind'] == 'ODI_PHYSICAL_DESIGN']
    assert len(requirements) == 11
    assert [requirement['declarations'] for requirement in requirements] == report['designs']
    assert [requirement['subject_node_id'] for requirement in requirements] == [design['design_node_id'] for design in report['designs']]
    obligations = [obligation['source_value'] for contract in contracts
                   for obligation in contract['required_behavior']['obligations'] if obligation['kind'] == 'source_requirement']
    assert obligations == analysis['requirements']
    assert all(obligation['weight'] is None and obligation['critical'] is None for contract in contracts
               for obligation in contract['required_behavior']['obligations'])
    assert plan['metadata']['behavior_analysis_binding']['requirement_count'] == 258
    assert candidates['candidate_count'] == 44 and len(candidates['entries']) == 89
    assert candidates['generated_notebooks'] == [] and candidates['execution_status'] == 'NOT_RUN'
    assert candidates['semantic_status'] == 'NOT_VERIFIED' and not candidates['migration_accepted']
    assert all(contract['target_capabilities']['generation_languages'] == [] for contract in contracts)


@pytest.mark.parametrize('change', ['omitted_node', 'omitted_property', 'changed_group', 'changed_context'])
def test_physical_replay_rejects_copied_artifact_omissions_and_validly_typed_rebindings(result, report, change):
    altered = copy.deepcopy(report)
    design = next(design for design in altered['designs'] if len(design['execution_groups']) > 1)
    if change == 'omitted_node':
        design['physical_nodes'].pop()
    elif change == 'omitted_property':
        design['properties'].pop()
    elif change == 'changed_group':
        group = next(reference for reference in design['execution_units'][0]['references'] if reference['field'] == 'IExecUnitGrp')
        group['target_node_id'] = next(record['node_id'] for record in design['execution_groups'] if record['node_id'] != group['target_node_id'])
    else:
        other = next(other for other in altered['designs'] if other['mapping_node_id'] != design['mapping_node_id'])
        design['context_reference'] = copy.deepcopy(other['context_reference'])
    with pytest.raises(SchemaValidationError):
        physical.validate_physical_analysis(altered, result.normalized_ir)


@pytest.mark.parametrize('key,value', [('migration_accepted', True), ('semantic_status', 'VERIFIED'), ('execution_status', 'PASSED')])
def test_schema_rejects_promoting_physical_declarations_to_runtime_evidence(report, key, value):
    altered = copy.deepcopy(report); altered[key] = value
    with pytest.raises(SchemaValidationError):
        validate_document(altered, 'odi_physical_analysis')


def test_source_replay_rejects_a_self_rehashed_physical_requirement_omission(result, analysis):
    altered = copy.deepcopy(analysis)
    requirement = next(requirement for requirement in altered['requirements'] if requirement['kind'] == 'ODI_PHYSICAL_DESIGN')
    requirement['declarations']['properties'].pop()
    requirement['requirement_id'] = 'requirement-' + content_digest([PROFILE, {key: value for key, value in requirement.items() if key != 'requirement_id'}])[:24]
    with pytest.raises(SchemaValidationError):
        validate_behavior_analysis(altered, result.normalized_ir)


@pytest.mark.parametrize('change', ['cross_design_group', 'ambiguous_group', 'cross_mapping_context', 'property_cycle'])
def test_faults_in_copied_original_declarations_keep_scope_conflicts_visible(result, report, change):
    copied = MappingIndex(copy.deepcopy(result.normalized_ir))
    design = next(design for design in report['designs'] if len(design['execution_groups']) > 1)
    if change in ('cross_design_group', 'ambiguous_group'):
        unit = copied.nodes[design['execution_units'][0]['node_id']]
        original_group = next(reference for reference in design['execution_units'][0]['references'] if reference['field'] == 'IExecUnitGrp')
        if change == 'cross_design_group':
            target = next(group for other in report['designs'] if other['design_node_id'] != design['design_node_id']
                          and other['mapping_node_id'] == design['mapping_node_id'] for group in other['execution_groups'])
            change_field(unit, 'IExecUnitGrp', target['declared_identity'])
        else:
            other = next(group for group in design['execution_groups'] if group['node_id'] != original_group['target_node_id'])
            change_field(copied.nodes[other['node_id']], 'IExecUnitGrp', original_group['declared_identity'])
        reviewed = physical.analyze_physical(copied)
        changed_unit = next(record for record in records(reviewed, 'execution_units') if record['node_id'] == unit['node_id'])
        reference, = [reference for reference in changed_unit['references'] if reference['field'] == 'IExecUnitGrp']
        assert reference['resolution'] == ('CROSS_DESIGN' if change == 'cross_design_group' else 'AMBIGUOUS_TARGET')
        assert reference['target_node_id'] is None and reference['candidate_node_ids']
    elif change == 'cross_mapping_context':
        other = next(other for other in report['designs'] if other['mapping_node_id'] != design['mapping_node_id'])
        change_field(copied.nodes[design['design_node_id']], 'IMapRef', other['context_reference']['declared_identity'])
        reviewed = physical.analyze_physical(copied)
        changed_design = next(record for record in reviewed['designs'] if record['design_node_id'] == design['design_node_id'])
        assert changed_design['context_reference']['resolution'] == 'CROSS_MAPPING'
        assert changed_design['context_reference']['target_node_id'] is None
    else:
        descendant = next(record for record in records(report, 'properties') if str(field_value(record, 'IMapPropParent')).isdigit())
        change_field(copied.nodes[descendant['node_id']], 'IMapPropParent', descendant['declared_identity'])
        reviewed = physical.analyze_physical(copied)
        unresolved = next(record for record in reviewed['unscoped_records'] if record['node_id'] == descendant['node_id'])
        assert unresolved['scope_resolution'] == 'CYCLE'
        assert any(finding['code'] == 'PROPERTY_SCOPE_CYCLE' for finding in unresolved['diagnostics'])
        assert unresolved['declared_fields'] == copied.nodes[descendant['node_id']]['attributes']['declared_fields']


@pytest.mark.parametrize('change', ['component_cp', 'physical_parent', 'unit_parent'])
def test_joint_reference_conflicts_in_copied_declarations_are_diagnosed_without_selecting_a_design(result, report, change):
    copied = MappingIndex(copy.deepcopy(result.normalized_ir))
    collection = 'execution_units' if change == 'unit_parent' else 'physical_nodes'
    original = records(report, collection)[0]
    node = copied.nodes[original['node_id']]
    if change == 'component_cp':
        component = copied.target(node, 'IMapComp')
        other_cp = next(cp for cp in copied.classes['SnpMapCp']
                        if copied.owner(cp) == copied.owner(node) and copied.target(cp, 'IOwnerMapComp') != component)
        change_field(node, 'IMapCp', value(other_cp, 'IMapCp'))
        expected = 'COMPONENT_CONNECTION_POINT_MISMATCH'
    elif change == 'physical_parent':
        change_field(node, 'IParPhyNode', value(node, 'IPhyNode'))
        expected = 'PHYSICAL_PARENT_CYCLE'
    else:
        change_field(node, 'IParExecUnit', value(node, 'IExecUnit'))
        expected = 'EXECUTION_UNIT_PARENT_CYCLE'
    reviewed = physical.analyze_physical(copied)
    record = next(record for record in records(reviewed, collection) if record['node_id'] == node['node_id'])
    finding, = [finding for finding in record['diagnostics'] if finding['code'] == expected]
    assert node['node_id'] in finding['node_ids'] and finding['evidence']
    assert record['declared_fields'] == node['attributes']['declared_fields']
    assert all(design['selected_for_execution'] is None for design in reviewed['designs'])
    assert reviewed['semantic_status'] == 'NOT_VERIFIED' and not reviewed['migration_accepted']


@pytest.mark.parametrize('change', ['nonnumeric_owner', 'ambiguous_owner'])
def test_unresolved_design_owners_remain_reviewable_without_assigning_an_owner(result, analysis, report, change):
    copied = MappingIndex(copy.deepcopy(result.normalized_ir))
    original_design = report['designs'][0]
    node = copied.nodes[original_design['design_node_id']]
    if change == 'nonnumeric_owner':
        change_field(node, 'IOwnerMapping', 'null')
    else:
        original_owner, = [field for field in node['attributes']['declared_fields'] if field['name'] == 'IOwnerMapping']
        node['attributes']['declared_fields'].append(copy.deepcopy(original_owner))
    unresolved = physical.analyze_physical(copied)
    orphan = next(design for design in unresolved['designs'] if design['design_node_id'] == node['node_id'])
    assert orphan['mapping_node_id'] is None and orphan['owner_reference']['target_node_id'] is None
    assert orphan['owner_reference']['resolution'] == ('NONNUMERIC_DECLARATION' if change == 'nonnumeric_owner' else 'AMBIGUOUS_DECLARATION')
    assert unresolved['unscoped_records'] and unresolved['summary']['design_count'] == 11
    assert unresolved['summary']['physical_node_count'] == 48 and unresolved['summary']['property_count'] == 153
    changed = copy.deepcopy(analysis)
    designs = {design['design_node_id']: design for design in unresolved['designs']}
    for requirement in changed['requirements']:
        if requirement['kind'] == 'ODI_PHYSICAL_DESIGN':
            design = designs[requirement['subject_node_id']]
            requirement['declarations'] = copy.deepcopy(design)
            requirement['evidence'] = copy.deepcopy(design['evidence'])
        elif requirement['kind'] == 'ODI_EXPORT_REVIEW':
            requirement['declarations']['physical_analysis_manifest'] = {key: copy.deepcopy(value) for key, value in unresolved.items() if key != 'designs'}
    mapping = next(requirement for requirement in changed['requirements'] if requirement['kind'] == 'ODI_MAPPING'
                   and requirement['subject_node_id'] == original_design['mapping_node_id'])
    original_inventory = next(design for design in mapping['declarations']['physical_designs'] if design['node_id'] == node['node_id'])
    mapping['declarations']['physical_designs'] = [design for design in mapping['declarations']['physical_designs'] if design['node_id'] != node['node_id']]
    for requirement in changed['requirements']:
        requirement['requirement_id'] = 'requirement-' + content_digest([PROFILE, {key: value for key, value in requirement.items() if key != 'requirement_id'}])[:24]
    assert physical.extract_physical_analysis(changed) == unresolved
    assert not unresolved['migration_accepted'] and unresolved['execution_status'] == 'NOT_RUN'
    with pytest.raises(SchemaValidationError):
        physical.validate_physical_analysis(unresolved, result.normalized_ir)
    forged = copy.deepcopy(changed)
    fabricated_owner = next(requirement for requirement in forged['requirements'] if requirement['kind'] == 'ODI_PHYSICAL_DESIGN'
                            and requirement['subject_node_id'] == node['node_id'])
    fabricated_owner['declarations']['mapping_node_id'] = original_design['mapping_node_id']
    restored_mapping = next(requirement for requirement in forged['requirements'] if requirement['kind'] == 'ODI_MAPPING'
                            and requirement['subject_node_id'] == original_design['mapping_node_id'])
    restored_mapping['declarations']['physical_designs'].append(copy.deepcopy(original_inventory))
    with pytest.raises(SchemaValidationError):
        physical.extract_physical_analysis(forged)


@pytest.mark.parametrize('limit,low', [('MAX_DESIGNS', 10), ('MAX_PHYSICAL_NODES', 47),
                                      ('MAX_EXECUTION_GROUPS', 24), ('MAX_PROPERTIES', 152)])
def test_physical_record_limits_reject_the_original_before_publishing_partial_analysis(index, monkeypatch, limit, low):
    monkeypatch.setattr(physical, limit, low)
    with pytest.raises(UnsupportedSourceError):
        physical.analyze_physical(index)


def test_physical_replay_uses_captured_original_evidence_after_source_removal(result, report):
    source = Path(result.normalized_ir['evidence_files'][0]['path'])
    source.unlink()
    physical.validate_physical_analysis(report, result.normalized_ir)
