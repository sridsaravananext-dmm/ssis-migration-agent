"""ODI normalization on unchanged Oracle originals; no invented ETL/data."""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.base import UnsupportedSourceError
from ssis_migration.adapters.odi import native
from ssis_migration.adapters.odi.behavior import analyze_behavior, validate_behavior_analysis, PROFILE
from ssis_migration.adapters.export_pipeline import run_export_review, validate_export_review
from ssis_migration.adapters.xml_export import validate_export_graph
from ssis_migration.generator.odi.assessment import build_assessment, validate_assessment
from ssis_migration.ir.normalized import content_digest, normalized_ir_id
from ssis_migration.migration.contract_bridge import prepare_generation_step
from ssis_migration.migration.native_review import build_native_review_plan
from ssis_migration.schemas import SchemaValidationError
from ssis_migration.ui import extract_bundle
from ssis_migration.validator.acceptance import load_package

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / 'examples/public/oracle_odi_etl_offload/upstream/CloudAtCustomerODIETLOffload/ODISmartExport_ETLOffload_BigDataCloud@Customer.zip'


@pytest.fixture(scope='module')
def source(tmp_path_factory):
    directory = extract_bundle(ARCHIVE.read_bytes(), tmp_path_factory.mktemp('native-odi') / 'source')
    original, = directory.iterdir()
    return original


@pytest.fixture(scope='module')
def result(source):
    return get_adapter('odi').analyze(source)


@pytest.fixture(scope='module')
def analysis(result):
    return analyze_behavior(result.normalized_ir, profile=PROFILE)


@pytest.fixture(scope='module')
def contract_bundle(result, analysis, source):
    return build_native_review_plan(result.normalized_ir, source.stem, behavior_analysis=analysis)


def test_original_elements_are_all_nodes_or_typed_fields_with_exact_locators_and_bytes(source, result):
    graph = result.normalized_ir
    assert graph['extensions']['source_analysis']['profile'] == native.PROFILE
    assert len(graph['nodes']) == 2500 and len(graph['edges']) == 4760
    metadata = graph['extensions']['source_analysis']['metadata']
    assert metadata['element_count'] == 41411 and metadata['folded_field_count'] == 38911
    assert metadata['element_count'] == len(graph['nodes']) + metadata['folded_field_count']
    snapshot = graph['extensions']['source_analysis']['source_snapshot']
    assert snapshot['encoding'] == 'iso-8859-1'
    assert snapshot['text'].encode(snapshot['encoding']) == source.read_bytes()
    assert snapshot['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    expected = {}
    def index(element, path):
        expected[path] = element; counts = Counter()
        for child in element:
            counts[child.tag] += 1
            index(child, path + '/' + child.tag + '[' + str(counts[child.tag]) + ']')
    root = ET.fromstring(source.read_bytes()); index(root, '/SunopsisExport[1]')
    covered = set(); mismatches = []
    for node in graph['nodes']:
        original = expected[node['source_ref']]; covered.add(node['source_ref'])
        if node['attributes']['export_attributes'] != original.attrib: mismatches.append(node['source_ref'])
        fields = node['attributes'].get('declared_fields', [])
        for declaration in fields:
            locator = declaration['evidence'][0]['locator'].removeprefix('xml:')
            original_field = expected[locator]; covered.add(locator)
            if (declaration['attributes'] != original_field.attrib or declaration['value'] != (original_field.text or '')
                    or declaration['tail'] != (original_field.tail or '')):
                mismatches.append(locator)
    assert not mismatches and covered == set(expected)
    assert graph['status'] == 'BLOCKED' and graph['semantic_status'] == 'NOT_VERIFIED'


def test_declared_data_flow_roles_and_column_references_are_scoped_without_execution_inference(result):
    graph = result.normalized_ir; nodes = {n['node_id']: n for n in graph['nodes']}
    flows = [e for e in graph['edges'] if e['kind'] == 'DATA_FLOW']
    lineage = [e for e in graph['edges'] if e['kind'] == 'COLUMN_LINEAGE']
    assert len(flows) == 12 and all(e['resolution'] == 'RESOLVED' for e in flows)
    assert len(lineage) == 50 and all(e['resolution'] == 'RESOLVED' for e in lineage)
    assert all(e['attributes']['validity_declaration'] == '0' for e in lineage)
    assert not [e for e in graph['edges'] if e['kind'] == 'CONTROL_FLOW']
    components = [n for n in graph['nodes'] if n['operation'] == 'MAPPING_COMPONENT']
    assert Counter(n['kind'] for n in components) == {'SOURCE': 9, 'TARGET': 7, 'TRANSFORMATION': 3}
    assert graph['extensions']['source_analysis']['metadata']['execution_order'] == 'NOT_ESTABLISHED'
    for edge in flows:
        assert native.value(nodes[edge['source_id']], 'Direction') == 'O'
        assert native.value(nodes[edge['target_id']], 'Direction') == 'I'


def test_external_bindings_require_class_key_and_guid_and_keep_missing_real_definitions(result):
    graph = result.normalized_ir; nodes = {n['node_id']: n for n in graph['nodes']}
    refs = [e for e in graph['edges'] if e['attributes'].get('field') == 'IRefId']
    assert len(refs) == 225
    missing = Counter(e['attributes']['interface'] for e in refs if e['resolution'] == 'UNRESOLVED')
    assert missing == {'IKnowledgeModule': 52, 'IDataType': 32, 'IColumn': 7, 'IDataStore': 1}
    for edge in refs:
        if edge['resolution'] != 'RESOLVED': continue
        target = nodes[edge['target_id']]
        assert native.value(target, 'GlobalId') == edge['attributes']['declared_guid']
        assert target['attributes']['object_class'] == edge['attributes']['target_class']
        assert native.value(target, target['attributes']['declared_key_field']) == edge['attributes']['declared_identity']
    assert len([d for d in graph['diagnostics'] if d['code'] == 'UNRESOLVED_GRAPH_REFERENCE']) == 92
    assert len([d for d in graph['diagnostics'] if d['code'] == 'UNSUPPORTED_ODI_OBJECT_CLASS']) == 20


def test_seven_mapping_requirements_preserve_native_expressions_designs_null_text_and_truncate(analysis):
    assert analysis['profile'] == PROFILE and len(analysis['requirements']) == 8
    mappings = [r for r in analysis['requirements'] if r['kind'] == 'ODI_MAPPING']
    assert len(mappings) == 7
    assert sum(len(r['declarations']['components']) for r in mappings) == 19
    assert sum(len(r['declarations']['physical_designs']) for r in mappings) == 11
    assert sum(len(r['declarations']['expressions']) for r in mappings) == 89
    assert all(r['declarations']['selected_physical_design'] is None for r in mappings)
    assert all(r['declarations']['execution_order'] == 'NOT_ESTABLISHED' for r in mappings)
    truncated = [r for r in mappings if any(p['name'] == 'TRUNCATE' and p['value'] == 'true' for p in r['declarations']['properties'])]
    assert {r['declarations']['declared_identity'] for r in truncated} == {'14', '18'}
    first = next(r for r in mappings if r['declarations']['declared_identity'] == '14')
    assert any(e['declarations']['ParsedTxt'] == '@{R0}' and e['declarations']['Text'] == 'null' for e in first['declarations']['expressions'])
    assert all(e['syntax_status'] == 'DECLARATIONS_ONLY' for r in mappings for e in r['declarations']['expressions'])


def test_contracts_conserve_all_mapping_and_graph_obligations_without_enabling_generation(result, analysis, contract_bundle):
    graph = result.normalized_ir; plan, contracts = contract_bundle
    binding = plan['metadata']['normalized_ir_binding']
    assert binding['node_count'] == binding['assigned_node_count'] == 2500
    assert binding['edge_count'] == binding['assigned_edge_count'] == 4760
    obligations = contracts[0]['required_behavior']['obligations']
    assert len(obligations) == 7268
    assert [o['source_value'] for o in obligations if o['kind'] == 'source_requirement'] == analysis['requirements']
    assert contracts[0]['target_capabilities']['generation_languages'] == []
    assert all(o['weight'] is None and o['critical'] is None for o in obligations)
    with pytest.raises(ValueError, match='(?i)native|review'):
        prepare_generation_step(plan['steps'][0])


def test_new_odi_profile_cannot_drop_mapping_requirements_by_claiming_an_old_generic_plan(result, source):
    graph = result.normalized_ir
    plan, contracts = build_native_review_plan(graph, source.stem)
    with pytest.raises(SchemaValidationError, match='source behavior requirements'):
        validate_export_review(result.source_envelope, graph, plan, contracts)


def test_databricks_assessment_reports_every_mapping_and_source_blocker(result, analysis, contract_bundle):
    _, contracts = contract_bundle
    assessment = build_assessment(result.normalized_ir, analysis, contracts)
    assert len(assessment['mappings']) == 7
    assert all(m['strategy'] == 'UNSUPPORTED' and not m['requires_ai'] for m in assessment['mappings'])
    assert sum(len(m['unresolved_external_references']) for m in assessment['mappings']) == 92
    assert sum(any(b['code'] == 'DESTRUCTIVE_OPTION_REVIEW_REQUIRED' for b in m['blockers']) for m in assessment['mappings']) == 2
    assert assessment['generated_notebooks'] == [] and assessment['migration_accepted'] is False


@pytest.mark.parametrize('change', ['omit_field', 'change_expression', 'remove_flow', 'resolve_missing_definition', 'change_encoding'])
def test_self_rehashed_graph_changes_cannot_bypass_original_byte_replay(result, change):
    graph = copy.deepcopy(result.normalized_ir)
    if change == 'omit_field': next(n for n in graph['nodes'] if n['operation'] == 'MAPPING_COMPONENT')['attributes']['declared_fields'].pop()
    elif change == 'change_expression':
        node = next(n for n in graph['nodes'] if n['operation'] == 'MAPPING_EXPRESSION')
        next(f for f in node['attributes']['declared_fields'] if f['name'] == 'Txt')['value'] = '1'
    elif change == 'remove_flow': graph['edges'] = [e for e in graph['edges'] if e['kind'] != 'DATA_FLOW']
    elif change == 'resolve_missing_definition':
        edge = next(e for e in graph['edges'] if e['attributes'].get('interface') == 'IKnowledgeModule' and e['resolution'] == 'UNRESOLVED')
        edge.update(resolution='RESOLVED', target_id=graph['nodes'][0]['node_id'])
    else: graph['extensions']['source_analysis']['source_snapshot']['encoding'] = 'utf-8'
    graph['provenance']['source_analysis_sha256'] = content_digest(graph['extensions']['source_analysis'])
    graph['ir_id'] = normalized_ir_id(graph)
    with pytest.raises(SchemaValidationError): validate_export_graph(graph)


@pytest.mark.parametrize('change', ['select_design', 'erase_truncate', 'drop_missing_reference'])
def test_requirement_replay_rejects_chosen_physical_behavior_and_hidden_gaps(result, analysis, change):
    modified = copy.deepcopy(analysis)
    mapping = next(r for r in modified['requirements'] if r['kind'] == 'ODI_MAPPING' and r['declarations']['declared_identity'] == '14')
    declarations = mapping['declarations']
    if change == 'select_design': declarations['selected_physical_design'] = declarations['physical_designs'][0]['node_id']
    elif change == 'erase_truncate': declarations['properties'] = [p for p in declarations['properties'] if p['name'] != 'TRUNCATE']
    else: declarations['external_references'] = [r for r in declarations['external_references'] if r['resolution'] == 'RESOLVED']
    mapping['requirement_id'] = 'requirement-' + content_digest([PROFILE, {k: v for k, v in mapping.items() if k != 'requirement_id'}])[:24]
    with pytest.raises(SchemaValidationError): validate_behavior_analysis(modified, result.normalized_ir)


def test_assessment_replay_rejects_silently_omitted_destructive_behavior(result, analysis, contract_bundle):
    plan, contracts = contract_bundle
    assessment = build_assessment(result.normalized_ir, analysis, contracts)
    mapping = next(m for m in assessment['mappings'] if any(b['code'] == 'DESTRUCTIVE_OPTION_REVIEW_REQUIRED' for b in m['blockers']))
    mapping['blockers'] = [b for b in mapping['blockers'] if b['code'] != 'DESTRUCTIVE_OPTION_REVIEW_REQUIRED']
    with pytest.raises(SchemaValidationError):
        validate_assessment(assessment, result.source_envelope, result.normalized_ir, plan, contracts, analysis)


@pytest.mark.parametrize('limit,value', [('MAX_ELEMENTS', 10000), ('MAX_NODES', 2499), ('MAX_FIELDS_PER_OBJECT', 63), ('MAX_MAPPINGS', 6), ('MAX_EDGES', 4759)])
def test_odi_specific_bounds_fail_before_publishing_any_artifact(source, tmp_path, monkeypatch, limit, value):
    monkeypatch.setattr(native, limit, value)
    output = tmp_path / 'review'
    with pytest.raises(UnsupportedSourceError): run_export_review(source, output, 'odi')
    assert not output.exists()


def test_native_review_replays_after_source_removal_and_cannot_be_accepted(source, tmp_path):
    original = tmp_path / source.name; original.write_bytes(source.read_bytes())
    report = run_export_review(original, tmp_path / 'output', 'odi')
    assert report['target_generation_assessment']['mapping_count'] == report['target_generation_assessment']['blocked_mapping_count'] == 7
    assert report['source_element_preservation']['element_count'] == 41411
    assert report['generated_artifact_count'] == 0 and report['migration_accepted'] is False
    assert report['execution_status'] == 'NOT_RUN' and report['coverage']['semantic']['verified_percent'] is None
    documents = {key: json.loads(Path(path).read_text()) for key, path in report['artifacts'].items()}
    original.unlink()
    validate_export_review(documents['source_envelope'], documents['normalized_ir'], documents['migration_plan'],
                           documents['migration_contracts']['contracts'])
    validate_assessment(documents['target_generation_assessment'], documents['source_envelope'], documents['normalized_ir'],
                        documents['migration_plan'], documents['migration_contracts']['contracts'], documents['source_behavior_analysis'])
    assert not list((tmp_path / 'output').rglob('*.ipynb'))
    with pytest.raises(ValueError, match='cannot be accepted'):
        load_package(Path(report['artifacts']['migration_report']).parent)
