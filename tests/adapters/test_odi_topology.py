"""Topology declarations from Oracle's original export, without new workloads."""
from collections import Counter
import copy
from pathlib import Path
import os.path
import socket

import pytest
import sqlglot

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.base import UnsupportedSourceError
from ssis_migration.adapters.odi import topology
from ssis_migration.adapters.odi.behavior import MappingIndex, analyze_behavior, validate_behavior_analysis
from ssis_migration.adapters.odi.behavior_v5 import PROFILE
from ssis_migration.adapters.odi.native import value
from ssis_migration.ir.normalized import content_digest
from ssis_migration.migration.native_review import build_native_review_plan
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ssis_migration.ui import extract_bundle

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / 'examples/public/oracle_odi_etl_offload/upstream/CloudAtCustomerODIETLOffload/ODISmartExport_ETLOffload_BigDataCloud@Customer.zip'
CLASSES = {'SnpLschema': 4, 'SnpContext': 2, 'SnpConnect': 5,
           'SnpPschema': 5, 'SnpPschemaCont': 5, 'SnpConnectProp': 30}


@pytest.fixture(scope='module')
def result(tmp_path_factory):
    directory = extract_bundle(ARCHIVE.read_bytes(), tmp_path_factory.mktemp('odi-topology') / 'source')
    original, = directory.iterdir()
    return get_adapter('odi').analyze(original)


@pytest.fixture(scope='module')
def index(result):
    return MappingIndex(result.normalized_ir)


@pytest.fixture(scope='module')
def analysis(result):
    return analyze_behavior(result.normalized_ir)


@pytest.fixture(scope='module')
def report(analysis):
    return topology.extract_topology_analysis(analysis)


@pytest.fixture(scope='module')
def bundle(result, analysis):
    from ssis_migration.generator.odi.expressions_v4 import build_candidates
    plan, contracts = build_native_review_plan(result.normalized_ir, result.parsed['name'], behavior_analysis=analysis)
    return plan, contracts, build_candidates(result.normalized_ir, analysis, contracts)


def records(report, cls):
    return [record for record in report['records'] if record['object_class'] == cls]


def field_value(record, name):
    fields = [field for field in record['declared_fields'] if field['name'] == name]
    return fields[0]['value'] if len(fields) == 1 else None


def change_field(node, name, replacement):
    field, = [field for field in node['attributes']['declared_fields'] if field['name'] == name]
    field['value'] = replacement


def reference(record, field):
    found, = [item for item in record['references'] if item['field'] == field]
    return found


def test_original_topology_records_and_every_typed_field_remain_once_with_evidence(result, index, report):
    assert report['profile'] == topology.PROFILE
    assert report['ir_id'] == result.normalized_ir['ir_id']
    assert report['source_analysis_sha256'] == result.normalized_ir['provenance']['source_analysis_sha256']
    assert report['evidence_files'] == result.normalized_ir['evidence_files']
    assert Counter(record['object_class'] for record in report['records']) == CLASSES
    identities = [record['node_id'] for record in report['records']]
    assert len(identities) == len(set(identities)) == 51
    assert set(identities) == {node['node_id'] for cls in CLASSES for node in index.classes[cls]}
    for record in report['records']:
        assert record['declared_fields'] == index.nodes[record['node_id']]['attributes']['declared_fields']
        assert record['evidence'] and not record['diagnostics']
    assert len(result.normalized_ir['nodes']) == 2500 and len(result.normalized_ir['edges']) == 4760
    assert all(not node['attributes']['structure_supported'] for cls in ('SnpPschema', 'SnpPschemaCont', 'SnpConnectProp')
               for node in index.classes[cls])
    validate_evidence_references(report)


def test_all_five_declared_context_bridges_require_exact_class_key_links(index, report):
    bridges = []
    for association in records(report, 'SnpPschemaCont'):
        logical = reference(association, 'ILschema'); context = reference(association, 'IContext')
        physical = reference(association, 'IPschema')
        assert all(item['resolution'] == 'RESOLVED' for item in (logical, context, physical))
        schema = next(record for record in records(report, 'SnpPschema') if record['node_id'] == physical['target_node_id'])
        connection = reference(schema, 'IConnect')
        assert connection['resolution'] == 'RESOLVED'
        bridges.append((logical['declared_identity'], context['declared_identity'],
                        physical['declared_identity'], connection['declared_identity']))
        for item in (logical, context, physical, connection):
            assert item['candidate_node_ids'] == [item['target_node_id']] and item['evidence']
    assert set(bridges) == {('8', '2', '7', '21'), ('9', '2', '48', '72'),
                            ('9', '3', '58', '72'), ('7', '2', '6', '20'), ('20', '2', '59', '4')}


def test_all_execution_units_keep_context_alternatives_despite_default_and_design_context(index, report):
    uses = report['execution_unit_uses']
    assert len(uses) == 25 and {use['execution_unit_node_id'] for use in uses} == {node['node_id'] for node in index.classes['SnpExecUnit']}
    candidates = [candidate for use in uses for candidate in use['context_candidates']]
    assert len(candidates) == 34
    assert Counter(candidate['declared_context_relation'] for candidate in candidates) == {'MATCH': 25, 'DIFFERENT': 9}
    assert all(candidate['effective_binding'] is None for candidate in candidates)
    oracle_uses = [use for use in uses if use['logical_schema_reference']['definition_reference']['declared_identity'] == '9']
    assert len(oracle_uses) == 9 and all(len(use['context_candidates']) == 2 for use in oracle_uses)
    assert all({candidate['context_node_id'] for candidate in use['context_candidates']} ==
               {node['node_id'] for node in index.classes['SnpContext']} for use in oracle_uses)
    assert any(field_value(record, 'DefCont') == '1' for record in records(report, 'SnpContext'))
    assert report['status'] == 'REVIEW_REQUIRED' and report['execution_status'] == 'NOT_RUN'
    assert report['semantic_status'] == 'NOT_VERIFIED' and not report['migration_accepted']


def test_connection_parent_links_do_not_fill_or_inherit_property_values(report):
    connections = records(report, 'SnpConnect')
    parents = {field_value(record, 'IConnect'): reference(record, 'ParIConnect') for record in connections}
    assert parents['21']['declared_identity'] == parents['20']['declared_identity'] == '18'
    assert parents['21']['resolution'] == parents['20']['resolution'] == 'RESOLVED'
    assert all(parents[identity]['declared_identity'] == 'null' for identity in ('18', '72', '4'))
    properties = records(report, 'SnpConnectProp')
    assert Counter(field_value(record, 'IConnect') for record in properties) == {'18': 10, '20': 20}
    assert Counter(field_value(record, 'IndUndefined') for record in properties) == {'0': 23, 'null': 5, '1': 2}
    values = [field_value(record, 'PropValueClob') for record in properties]
    assert Counter('literal_null' if text == 'null' else 'empty' if text == '' else 'nonempty' for text in values) == {
        'nonempty': 17, 'empty': 11, 'literal_null': 2}
    assert sum('${HADOOP_CLASSPATH' in text for text in values) == 1
    assert all(reference(record, 'IConnect')['resolution'] == 'RESOLVED' for record in properties)


def test_connection_url_text_and_parameter_markers_remain_opaque_original_declarations(index, report):
    bindings = report['text_bindings']
    assert len(bindings) == 15
    bound = [binding for binding in bindings if binding['resolution'] == 'ONE_DECLARED_RECORD']
    assert {binding['declared_text_identity'] for binding in bound} == {'4441', '4492', '4386'}
    assert len(bound) == 3 and all(binding['field'] == 'ITxtJavaUrl' for binding in bound)
    assert all(binding['effective_text'] is None and binding['interpretation'] == 'DECLARED_TEXT_ONLY' for binding in bindings)
    for binding in bound:
        original, = binding['records']
        assert original['object_class'] == 'SnpMtxt'
        assert original['declared_fields'] == index.nodes[original['node_id']]['attributes']['declared_fields']
        assert binding['evidence'] and original['evidence']
    declarations = report['opaque_marker_declarations']
    shell = [marker['token'] for declaration in declarations for marker in declaration['markers']
             if marker['kind'] == 'SHELL_PARAMETER_TEXT']
    assert shell and any('${HADOOP_CLASSPATH' in token for token in shell)
    for declaration in declarations:
        original = index.nodes[declaration['node_id']]
        field = value(original, declaration['field'])
        assert all(marker['token'] in field for marker in declaration['markers'])
        assert declaration['evidence']


def test_topology_source_analysis_never_calls_a_sql_parser(index, report, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Topology declarations reached SQL parsing, connection or parameter expansion.')
    monkeypatch.setattr(sqlglot, 'parse', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(os.path, 'expandvars', forbidden)
    assert topology.analyze_topology(index) == report


def test_model_names_cannot_create_identity_bindings(result, report):
    copied = MappingIndex(copy.deepcopy(result.normalized_ir))
    existing_name = value(copied.classes['SnpLschema'][0], 'LschemaName')
    for model in copied.classes['SnpModel']:
        change_field(model, 'LschemaName', existing_name)
    assert topology.analyze_topology(copied) == report


def test_topology_requirement_enters_contracts_without_semantic_or_execution_credit(analysis, report, bundle):
    plan, contracts, candidates = bundle
    assert analysis['profile'] == PROFILE and len(analysis['requirements']) == 259
    topology_requirement, = [item for item in analysis['requirements'] if item['kind'] == 'ODI_TOPOLOGY_REVIEW']
    assert topology_requirement['declarations'] == report
    export_review, = [item for item in analysis['requirements'] if item['kind'] == 'ODI_EXPORT_REVIEW']
    assert topology_requirement['subject_node_id'] == export_review['subject_node_id']
    assert sum(item['kind'] == 'ODI_PHYSICAL_DESIGN' for item in analysis['requirements']) == 11
    obligations = [item['source_value'] for contract in contracts for item in contract['required_behavior']['obligations']
                   if item['kind'] == 'source_requirement']
    assert obligations == analysis['requirements']
    assert plan['metadata']['behavior_analysis_binding']['requirement_count'] == 259
    assert all(item['weight'] is None and item['critical'] is None for contract in contracts
               for item in contract['required_behavior']['obligations'])
    assert candidates['candidate_count'] == 44 and len(candidates['entries']) == 89
    assert candidates['generated_notebooks'] == [] and candidates['execution_status'] == 'NOT_RUN'
    assert candidates['semantic_status'] == 'NOT_VERIFIED' and not candidates['migration_accepted']
    assert all(contract['target_capabilities']['generation_languages'] == [] for contract in contracts)


@pytest.mark.parametrize('key,replacement', [('migration_accepted', True), ('semantic_status', 'VERIFIED'), ('execution_status', 'PASSED')])
def test_schema_rejects_promoting_declared_topology_to_runtime_evidence(report, key, replacement):
    changed = copy.deepcopy(report); changed[key] = replacement
    with pytest.raises(SchemaValidationError):
        validate_document(changed, 'odi_topology_analysis')


def test_schema_rejects_assigning_an_existing_context_as_an_effective_binding(report):
    changed = copy.deepcopy(report)
    candidate = changed['execution_unit_uses'][0]['context_candidates'][0]
    candidate['effective_binding'] = candidate['context_node_id']
    with pytest.raises(SchemaValidationError):
        validate_document(changed, 'odi_topology_analysis')


@pytest.mark.parametrize('change', ['record', 'property', 'candidate'])
def test_original_source_replay_rejects_copied_report_omissions(result, report, change):
    changed = copy.deepcopy(report)
    if change == 'candidate':
        next(use for use in changed['execution_unit_uses'] if len(use['context_candidates']) == 2)['context_candidates'].pop()
    else:
        cls = 'SnpConnectProp' if change == 'property' else 'SnpPschemaCont'
        original = next(record for record in changed['records'] if record['object_class'] == cls)
        changed['records'].remove(original)
    with pytest.raises(SchemaValidationError):
        topology.validate_topology_analysis(changed, result.normalized_ir)


def test_source_replay_rejects_self_rehashed_requirement_with_reassigned_original_property_values(result, analysis):
    changed = copy.deepcopy(analysis)
    requirement, = [item for item in changed['requirements'] if item['kind'] == 'ODI_TOPOLOGY_REVIEW']
    properties = records(requirement['declarations'], 'SnpConnectProp')
    empty = next(record for record in properties if field_value(record, 'PropValueClob') == '')
    literal_null = next(record for record in properties if field_value(record, 'PropValueClob') == 'null')
    for record, replacement in ((empty, 'null'), (literal_null, '')):
        field, = [field for field in record['declared_fields'] if field['name'] == 'PropValueClob']
        field['value'] = replacement
    requirement['requirement_id'] = 'requirement-' + content_digest([PROFILE, {key: val for key, val in requirement.items() if key != 'requirement_id'}])[:24]
    with pytest.raises(SchemaValidationError):
        validate_behavior_analysis(changed, result.normalized_ir)


@pytest.mark.parametrize('change', ['missing_context', 'ambiguous_physical_schema', 'duplicate_bridge',
                                    'duplicate_property', 'parent_cycle'])
def test_copied_original_topology_conflicts_stay_visible_without_dropping_declarations(result, report, change):
    copied = MappingIndex(copy.deepcopy(result.normalized_ir))
    if change == 'missing_context':
        node = copied.classes['SnpPschemaCont'][0]
        actual_other_class_identity = value(copied.classes['SnpPschema'][0], 'IPschema')
        change_field(node, 'IContext', actual_other_class_identity)
        expected = 'MISSING_TARGET'
    elif change == 'ambiguous_physical_schema':
        first, second = copied.classes['SnpPschema'][:2]
        change_field(second, 'IPschema', value(first, 'IPschema'))
        node = next(node for node in copied.classes['SnpPschemaCont'] if value(node, 'IPschema') == value(first, 'IPschema'))
        expected = 'AMBIGUOUS_TARGET'
    elif change == 'duplicate_bridge':
        associations = [node for node in copied.classes['SnpPschemaCont'] if value(node, 'ILschema') == '9']
        first, node = associations
        change_field(node, 'IContext', value(first, 'IContext'))
        expected = 'DUPLICATE_CONTEXT_ASSOCIATION'
    elif change == 'duplicate_property':
        same_owner = [node for node in copied.classes['SnpConnectProp'] if value(node, 'IConnect') == '20']
        first, node = same_owner[:2]
        change_field(node, 'PropKey', value(first, 'PropKey'))
        expected = 'DUPLICATE_CONNECTION_PROPERTY'
    else:
        node = next(node for node in copied.classes['SnpConnect'] if value(node, 'IConnect') == '18')
        actual_child = next(node for node in copied.classes['SnpConnect'] if value(node, 'IConnect') == '20')
        change_field(node, 'ParIConnect', value(actual_child, 'IConnect'))
        expected = 'CONNECTION_PARENT_CYCLE'
    changed = topology.analyze_topology(copied)
    assert Counter(record['object_class'] for record in changed['records']) == CLASSES
    assert len(changed['execution_unit_uses']) == 25
    record = next(record for record in changed['records'] if record['node_id'] == node['node_id'])
    assert record['declared_fields'] == node['attributes']['declared_fields']
    if change in ('missing_context', 'ambiguous_physical_schema'):
        item = reference(record, 'IContext' if change == 'missing_context' else 'IPschema')
        assert item['resolution'] == expected and item['target_node_id'] is None
        assert bool(item['candidate_node_ids']) == (change == 'ambiguous_physical_schema')
    else:
        assert any(finding['code'] == expected for finding in record['diagnostics'])
    assert all(candidate['effective_binding'] is None for use in changed['execution_unit_uses'] for candidate in use['context_candidates'])
    assert changed['execution_status'] == 'NOT_RUN' and not changed['migration_accepted']


@pytest.mark.parametrize('change', ['logical_guid', 'cross_mapping_logical', 'missing_design_owner', 'declared_context_guid'])
def test_copied_unit_reference_conflicts_do_not_create_runtime_bindings_or_hide_units(result, report, change):
    copied = MappingIndex(copy.deepcopy(result.normalized_ir))
    original = report['execution_unit_uses'][0]
    unit = copied.nodes[original['execution_unit_node_id']]
    if change == 'logical_guid':
        logical = copied.target(unit, 'ILschemaRef')
        other = next(node for node in copied.classes['SnpLschema'] if value(node, 'ILschema') != value(logical, 'IRefId'))
        change_field(logical, 'RefGuid', value(other, 'GlobalId'))
    elif change == 'cross_mapping_logical':
        logical = copied.target(unit, 'ILschemaRef')
        other = next(node for node in copied.classes['SnpMapRef'] if value(node, 'AdapterIntfType') == 'ILogicalSchema'
                     and value(node, 'IRefId') == value(logical, 'IRefId') and copied.owner(node) != copied.owner(unit))
        change_field(unit, 'ILschemaRef', value(other, 'IMapRef'))
    elif change == 'missing_design_owner':
        change_field(unit, 'IOwnerDs', 'null')
    else:
        design = copied.target(unit, 'IOwnerDs')
        original_reference = original['declared_context_reference']
        context_reference = copied.nodes[original_reference['target_node_id']]
        other = next(node for node in copied.classes['SnpContext']
                     if value(node, 'IContext') != value(context_reference, 'IRefId'))
        change_field(context_reference, 'RefGuid', value(other, 'GlobalId'))
    changed = topology.analyze_topology(copied)
    assert len(changed['execution_unit_uses']) == 25
    use, = [use for use in changed['execution_unit_uses'] if use['execution_unit_node_id'] == unit['node_id']]
    assert use['declared_fields'] == unit['attributes']['declared_fields'] and use['unresolved_items']
    if change == 'logical_guid':
        assert use['logical_schema_reference']['definition_reference']['resolution'] == 'GUID_MISMATCH'
        assert use['context_candidates'] == []
    elif change == 'cross_mapping_logical':
        assert use['logical_schema_reference']['resolution'] == 'CROSS_MAPPING'
        assert use['context_candidates'] == []
    elif change == 'missing_design_owner':
        assert use['owner_design_reference']['target_node_id'] is None
        assert use['mapping_node_id'] is None and use['design_node_id'] is None
        assert use['declared_context_reference'] is None
    else:
        assert use['declared_context_reference']['definition_reference']['resolution'] == 'GUID_MISMATCH'
        assert len(use['context_candidates']) == len(original['context_candidates'])
        assert all(candidate['declared_context_relation'] == 'UNRESOLVED' for candidate in use['context_candidates'])
    assert all(candidate['effective_binding'] is None for use in changed['execution_unit_uses'] for candidate in use['context_candidates'])
    assert not changed['migration_accepted'] and changed['semantic_status'] == 'NOT_VERIFIED'


def test_missing_opaque_url_text_identity_stays_unresolved_instead_of_fabricating_a_url(result, report):
    copied = MappingIndex(copy.deepcopy(result.normalized_ir))
    original = next(binding for binding in report['text_bindings'] if binding['resolution'] == 'ONE_DECLARED_RECORD')
    connection = copied.nodes[original['connection_node_id']]
    text_ids = {value(node, 'ITxt') for cls in ('SnpTxtHeader', 'SnpMtxt') for node in copied.classes[cls]}
    other_class_id = next(value(node, 'IConnect') for node in copied.classes['SnpConnect'] if value(node, 'IConnect') not in text_ids)
    change_field(connection, original['field'], other_class_id)
    changed = topology.analyze_topology(copied)
    binding, = [binding for binding in changed['text_bindings']
                if binding['connection_node_id'] == connection['node_id'] and binding['field'] == original['field']]
    assert binding['declared_text_identity'] == other_class_id and binding['resolution'] == 'MISSING'
    assert binding['records'] == [] and binding['effective_text'] is None


@pytest.mark.parametrize('limit,low', [('MAX_RECORDS', 50), ('MAX_EXECUTION_UNITS', 24),
                                      ('MAX_CONTEXT_CANDIDATES', 33), ('MAX_TEXT_CHARS', 15)])
def test_topology_limits_reject_the_original_before_a_partial_report(index, monkeypatch, limit, low):
    monkeypatch.setattr(topology, limit, low)
    with pytest.raises(UnsupportedSourceError):
        topology.analyze_topology(index)


@pytest.mark.parametrize('limit,low', [('MAX_BOUND_TEXT_RECORDS', 2), ('MAX_BOUND_TEXT_CHARS', 1),
                                      ('MAX_BOUND_TEXT_FIELDS', 2), ('MAX_EMITTED_EVIDENCE', 1)])
def test_aggregate_output_limits_reject_existing_original_declarations(index, monkeypatch, limit, low):
    monkeypatch.setattr(topology, limit, low)
    with pytest.raises(UnsupportedSourceError):
        topology.analyze_topology(index)


def test_topology_replays_from_captured_original_after_source_file_removal(result, report):
    source = Path(result.normalized_ir['evidence_files'][0]['path'])
    source.unlink()
    topology.validate_topology_analysis(report, result.normalized_ir)
