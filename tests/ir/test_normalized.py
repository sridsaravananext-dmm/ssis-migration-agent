from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ssis_migration.adapters import SourceAdapterResult
from ssis_migration.adapters.ssis import SSISAdapter
from ssis_migration.adapters.ssis.normalizer import normalize_ssis_ir
from ssis_migration.intake import describe_file
from ssis_migration.ir.normalized import normalized_ir_id, validate_normalized_ir
from ssis_migration.schemas import SchemaValidationError, validate_document


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / 'package.dtsx'
    source.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts"
DTS:refId="Package" DTS:ObjectName="Example" DTS:ExecutableType="Microsoft.Package">
<DTS:Property DTS:Name="PackageFormatVersion">8</DTS:Property></DTS:Executable>''')
    record = describe_file(source)
    legacy = {
        'package_name': 'Example', 'source_file': {'file_path': str(source)},
        'metadata': {'unknown_future_field': ['preserve']},
        'executables': [
            {'name': 'SameName', 'ref_id': 'A', 'executable_type': 'Microsoft.ExecuteSQLTask',
             'properties': {'Connection': 'C', 'SqlStatementSource': 'SELECT id FROM exports'}},
            {'name': 'SameName', 'ref_id': 'B', 'executable_type': 'Microsoft.Pipeline', 'properties': {}},
        ],
        'connection_manager_references': [{'ref_id': 'C', 'connection_manager_id': 'C', 'name': 'Oracle_Legacy'}],
        'variables': [{'ref_id': 'V', 'name': 'Cutoff', 'namespace': 'User', 'data_type': '7',
                       'value': 'unconverted export value', 'expression': '@[User::Other]'}],
        'precedence_constraints': [{'ref_id': 'AB', 'from_executable': 'A', 'to_executable': 'B',
                                    'logical_and': False, 'evaluation_operation': 'ExpressionOrConstraint',
                                    'value': 'Failure', 'expression': '@[User::Ready]', 'defaulted_fields': []}],
        'data_flows': [{
            'name': 'Flow', 'ref_id': 'B', 'future_setting': {'preserve': True},
            'components': [
                {'name': 'Same', 'ref_id': 'S', 'component_class_id': 'Microsoft.OLEDBSource',
                 'properties': {}, 'connection_manager_refs': [{'connection_manager_ref_id': 'C'}],
                 'inputs': [], 'outputs': [{'ref_id': 'S.out', 'columns': [{'ref_id': 'S.id', 'lineage_id': 'S.id'}]}]},
                {'name': 'Same', 'ref_id': 'T', 'component_class_id': 'Microsoft.OLEDBDestination',
                 'properties': {}, 'inputs': [{'ref_id': 'T.in', 'columns': [{'ref_id': 'T.id'}]}], 'outputs': []},
            ],
            'paths': [{'name': 'Path', 'start_id': 'S.out', 'end_id': 'T.in'}],
            'column_mappings': [{'destination_component': 'Same', 'destination_column': 'id',
                                 'destination_column_ref': 'T.id', 'source_lineage_id': 'S.id'}],
        }],
    }
    envelope = {'envelope_version': '1.0',
                'source': {'platform': 'ssis', 'format': 'ssis_dtsx_xml', 'export_version': '8', 'file_id': record['file_id']},
                'evidence_files': [record], 'diagnostics': [], 'status': 'READY', 'legacy_ir': legacy}
    return legacy, envelope


def project(inputs):
    return normalize_ssis_ir(*inputs)


def nodes(document, kind):
    return [n for n in document['nodes'] if n['kind'] == kind]


def edges(document, kind):
    return [e for e in document['edges'] if e['kind'] == kind]


def resign(document):
    document['ir_id'] = normalized_ir_id(document)


def test_projection_is_deterministic_and_does_not_mutate_inputs(inputs):
    before = copy.deepcopy(inputs)
    first, second = project(inputs), project(inputs)
    assert inputs == before
    assert first == second
    validate_document(first, 'normalized_ir')
    validate_normalized_ir(first, legacy_ir=inputs[0])
    assert first['semantic_status'] == 'NOT_VERIFIED'
    assert first['status'] == 'REVIEW_REQUIRED'
    assert all(n['semantic_status'] == 'NOT_VERIFIED' for n in first['nodes'])
    assert len(nodes(first, 'TASK')) == 2  # Duplicate names never collapse identities.
    assert not first['diagnostics']


def test_explicit_control_conditions_and_native_extensions_preserved(inputs):
    doc = project(inputs)
    condition = edges(doc, 'CONTROL_FLOW')[0]
    assert condition['attributes']['logical_and'] is False
    assert condition['attributes']['evaluation_operation'] == 'ExpressionOrConstraint'
    assert condition['attributes']['value'] == 'Failure'
    assert condition['attributes']['expression_language'] == 'ssis_expression'
    assert condition['extensions']['ssis'] == inputs[0]['precedence_constraints'][0]
    assert doc['extensions']['ssis']['metadata'] == inputs[0]['metadata']
    assert nodes(doc, 'DATA_FLOW')[0]['extensions']['ssis']['future_setting'] == {'preserve': True}


def test_lineage_uses_native_column_refs_even_when_component_names_collide(inputs):
    doc = project(inputs)
    link = edges(doc, 'COLUMN_LINEAGE')[0]
    assert link['resolution'] == 'RESOLVED'
    assert link['source_id'] == nodes(doc, 'SOURCE')[0]['node_id']
    assert link['target_id'] == nodes(doc, 'TARGET')[0]['node_id']
    assert edges(doc, 'DATA_FLOW')[0]['resolution'] == 'RESOLVED'


def test_parameters_and_expressions_do_not_invent_types_dialects_or_bindings(inputs):
    doc = project(inputs)
    param = nodes(doc, 'PARAMETER')[0]
    assert param['attributes'] == {'namespace': 'User', 'source_data_type': '7',
                                   'initial_value': 'unconverted export value', 'scope': None}
    assert all(n['attributes']['dialect'] is None for n in nodes(doc, 'EXPRESSION'))
    sql = next(n for n in nodes(doc, 'EXPRESSION') if 'SELECT' in n['attributes']['text'])
    assert sql['attributes']['language'] == 'unknown'
    assert sql['attributes']['binding_status'] == 'UNVERIFIED'
    assert nodes(doc, 'CONNECTION')[0]['attributes']['endpoint'] is None
    assert 'incremental_logic' in doc['capability_gaps']
    assert 'complete_error_handling' in doc['capability_gaps']


def test_unknown_vendor_types_are_retained_and_heuristic_names_ignored(inputs):
    inputs[0]['executables'][0]['executable_type'] = 'Vendor.CustomTask'
    component = inputs[0]['data_flows'][0]['components'][0]
    component.update(component_class_id='Vendor.CustomComponent', detected_type='OLE_DB_SOURCE', description='lookup')
    doc = project(inputs)
    assert len(nodes(doc, 'TASK')) == 2
    assert nodes(doc, 'TASK')[0]['operation'] == 'UNKNOWN'
    assert not nodes(doc, 'SOURCE')
    assert nodes(doc, 'TRANSFORMATION')[0]['operation'] == 'UNKNOWN'
    assert {'UNSUPPORTED_TASK_SEMANTICS', 'UNSUPPORTED_COMPONENT_SEMANTICS'} <= {d['code'] for d in doc['diagnostics']}


@pytest.mark.parametrize('reference', ['missing', None])
def test_missing_control_references_are_explicit_not_guessed_from_names(inputs, reference):
    inputs[0]['precedence_constraints'][0]['from_executable'] = reference
    doc = project(inputs)
    link = edges(doc, 'CONTROL_FLOW')[0]
    assert link['source_id'] is None
    assert link['resolution'] == 'UNRESOLVED'
    assert any(d['code'] == 'UNRESOLVED_GRAPH_REFERENCE' for d in doc['diagnostics'])
    validate_normalized_ir(doc)


def test_duplicate_task_reference_retains_both_nodes_but_does_not_select_one(inputs):
    inputs[0]['executables'][1]['ref_id'] = 'A'
    doc = project(inputs)
    assert len(nodes(doc, 'TASK')) == 2
    assert edges(doc, 'CONTROL_FLOW')[0]['source_id'] is None
    assert any(d['code'] == 'AMBIGUOUS_TASK_REFERENCE' for d in doc['diagnostics'])


@pytest.mark.parametrize('kind', ['port', 'column'])
def test_duplicate_port_or_column_ids_within_one_component_are_ambiguous(inputs, kind):
    component = inputs[0]['data_flows'][0]['components'][0]
    if kind == 'port':
        component['outputs'].append(copy.deepcopy(component['outputs'][0]))
    else:
        component['outputs'][0]['columns'].append(copy.deepcopy(component['outputs'][0]['columns'][0]))
    doc = project(inputs)
    link = edges(doc, 'DATA_FLOW' if kind == 'port' else 'COLUMN_LINEAGE')[0]
    assert link['resolution'] == 'UNRESOLVED'


def test_missing_lineage_not_satisfied_by_matching_column_names(inputs):
    inputs[0]['data_flows'][0]['column_mappings'][0]['source_lineage_id'] = 'missing'
    doc = project(inputs)
    link = edges(doc, 'COLUMN_LINEAGE')[0]
    assert link['source_id'] is None
    assert link['attributes']['source_lineage_id'] == 'missing'


def test_missing_identity_has_diagnostic_without_fabricating_native_ref(inputs):
    inputs[0]['executables'][0]['ref_id'] = None
    doc = project(inputs)
    assert nodes(doc, 'TASK')[0]['source_ref'] is None
    assert any(d['code'] == 'MISSING_NATIVE_REFERENCE' for d in doc['diagnostics'])


def test_control_default_origins_are_preserved(inputs):
    inputs[0]['precedence_constraints'][0]['defaulted_fields'] = ['value']
    doc = project(inputs)
    assert edges(doc, 'CONTROL_FLOW')[0]['attributes']['defaulted_fields'] == ['value']
    assert any(d['code'] == 'DEFAULTED_CONTROL_CONDITION' for d in doc['diagnostics'])


def test_cycles_are_preserved_for_loop_review_not_removed(inputs):
    reverse = copy.deepcopy(inputs[0]['precedence_constraints'][0])
    reverse.update(from_executable='B', to_executable='A')
    inputs[0]['precedence_constraints'].append(reverse)
    doc = project(inputs)
    assert len(edges(doc, 'CONTROL_FLOW')) == 2
    assert any(d['code'] == 'CYCLIC_CONTROL_FLOW' for d in doc['diagnostics'])
    validate_normalized_ir(doc)


def test_connection_collisions_do_not_resolve_by_name(inputs):
    inputs[0]['connection_manager_references'].append(copy.deepcopy(inputs[0]['connection_manager_references'][0]))
    doc = project(inputs)
    assert len(nodes(doc, 'CONNECTION')) == 2
    assert all(e['resolution'] == 'UNRESOLVED' for e in edges(doc, 'USES_CONNECTION'))


@pytest.mark.parametrize('field', ['ir_version', 'semantic_status', 'status'])
def test_unknown_version_or_claimed_acceptance_is_rejected(inputs, field):
    doc = project(inputs)
    doc[field] = 'ACCEPTED'
    resign(doc)
    with pytest.raises(SchemaValidationError):
        validate_normalized_ir(doc)


@pytest.mark.parametrize('collection', ['nodes', 'edges'])
def test_duplicate_graph_identity_rejected(inputs, collection):
    doc = project(inputs)
    doc[collection].append(copy.deepcopy(doc[collection][0]))
    resign(doc)
    with pytest.raises(SchemaValidationError, match='Duplicate'):
        validate_normalized_ir(doc)


def test_dangling_node_reference_rejected(inputs):
    doc = project(inputs)
    doc['edges'][0]['target_id'] = 'no-such-node'
    resign(doc)
    with pytest.raises(SchemaValidationError, match='endpoint'):
        validate_normalized_ir(doc)


def test_missing_node_evidence_rejected(inputs):
    doc = project(inputs)
    doc['nodes'][0]['evidence'][0]['file_id'] = 'missing'
    resign(doc)
    with pytest.raises(SchemaValidationError, match='evidence'):
        validate_normalized_ir(doc)


def test_unresolved_edge_without_diagnostic_rejected(inputs):
    inputs[0]['precedence_constraints'][0]['from_executable'] = None
    doc = project(inputs)
    doc['diagnostics'] = []
    resign(doc)
    with pytest.raises(SchemaValidationError, match='diagnostic'):
        validate_normalized_ir(doc)


def test_content_tampering_or_wrong_input_binding_rejected(inputs):
    doc = project(inputs)
    doc['nodes'][0]['name'] = 'changed'
    with pytest.raises(SchemaValidationError, match='identity'):
        validate_normalized_ir(doc)
    doc = project(inputs)
    changed = copy.deepcopy(inputs[0])
    changed['package_name'] = 'another package'
    with pytest.raises(SchemaValidationError, match='legacy IR'):
        validate_normalized_ir(doc, legacy_ir=changed)


def test_mismatched_source_envelope_rejected(inputs):
    changed = copy.deepcopy(inputs[0])
    changed['package_name'] = 'different'
    with pytest.raises(SchemaValidationError, match='source envelope'):
        normalize_ssis_ir(changed, inputs[1])


def test_existing_source_error_cannot_be_downgraded(inputs):
    inputs[1]['diagnostics'] = [{'code': 'SOURCE_ERROR', 'severity': 'ERROR', 'message': 'Missing dependency',
                                 'file_ids': [], 'evidence': []}]
    doc = project(inputs)
    assert doc['status'] == 'BLOCKED'
    doc['status'] = 'REVIEW_REQUIRED'
    resign(doc)
    with pytest.raises(SchemaValidationError, match='status'):
        validate_normalized_ir(doc)


def test_source_adapter_result_legacy_constructor_and_keys_remain_available():
    result = SourceAdapterResult({}, None, {}, {})
    assert result.normalized_ir is None
    assert set(result.to_legacy_result()) == {'parsed', 'package_ir', 'analysis', 'source_envelope'}


def test_ssis_adapter_returns_neutral_graph_without_changing_legacy_ir():
    root = Path(__file__).resolve().parents[2]
    result = SSISAdapter().analyze(root / 'examples/wwi/DailyETLMain.dtsx')
    doc = result.normalized_ir
    validate_normalized_ir(doc, legacy_ir=result.package_ir.to_dict())
    assert len(nodes(doc, 'TASK')) == len(result.package_ir.executables)
    assert len(nodes(doc, 'DATA_FLOW')) == len(result.package_ir.data_flows)
    assert len(edges(doc, 'CONTROL_FLOW')) == len(result.package_ir.precedence_constraints)
    assert len(edges(doc, 'COLUMN_LINEAGE')) == sum(len(f.column_mappings) for f in result.package_ir.data_flows)
    assert result.to_legacy_result()['normalized_ir'] == doc
    assert result.source_envelope['legacy_ir'] == result.package_ir.to_dict()


def test_wwi_projection_matches_portable_golden_structure():
    from collections import Counter
    root = Path(__file__).resolve().parents[2]
    doc = SSISAdapter().analyze(root / 'examples/wwi/DailyETLMain.dtsx').normalized_ir
    actual = {
        'node_kinds': dict(Counter(n['kind'] for n in doc['nodes'])),
        'edge_kinds': dict(Counter(e['kind'] for e in doc['edges'])),
        'diagnostic_codes': dict(Counter(d['code'] for d in doc['diagnostics'])),
        'unresolved_edges': sum(e['resolution'] == 'UNRESOLVED' for e in doc['edges']),
        'semantic_status': doc['semantic_status'], 'status': doc['status'],
    }
    expected = json.loads((Path(__file__).parent / 'fixtures/wwi_structural_summary.json').read_text())
    assert actual == expected


def test_expression_text_is_retained_without_execution(inputs, tmp_path):
    sentinel = tmp_path / 'executed'
    text = f"__import__('pathlib').Path({str(sentinel)!r}).write_text('executed')"
    inputs[0]['executables'][0]['properties']['SqlStatementSource'] = text
    doc = project(inputs)
    assert any(n['attributes']['text'] == text for n in nodes(doc, 'EXPRESSION'))
    assert not sentinel.exists()


@pytest.mark.parametrize('platform,language', [('tm1', 'mdx'), ('informatica', 'expression'), ('bods', 'expression'), ('odi', 'plsql')])
def test_neutral_schema_does_not_require_sql_or_ssis_syntax(inputs, platform, language):
    doc = project(inputs)
    doc['source'].update(platform=platform, format='future_adapter_export')
    for item in nodes(doc, 'EXPRESSION'):
        item['attributes'].update(language=language, text='source supplied expression')
    resign(doc)
    # Only establishes schema compatibility, not a working adapter or semantics.
    validate_document(doc, 'normalized_ir')


def test_normalized_projection_checks_source_bytes_after_build(tmp_path, monkeypatch):
    from ssis_migration.adapters import SourceEvidenceError
    from ssis_migration.adapters.ssis import normalizer
    source = tmp_path / 'mutating.dtsx'
    source.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts"
DTS:refId="Package" DTS:ObjectName="Example" DTS:ExecutableType="Microsoft.Package">
<DTS:Property DTS:Name="PackageFormatVersion">8</DTS:Property></DTS:Executable>''')
    original = normalizer.normalize_ssis_ir
    def mutate(*args, **kwargs):
        doc = original(*args, **kwargs)
        source.write_text(source.read_text() + '<!-- changed -->')
        return doc
    monkeypatch.setattr(normalizer, 'normalize_ssis_ir', mutate)
    with pytest.raises(SourceEvidenceError, match='no longer match'):
        SSISAdapter().analyze(source)


@pytest.mark.parametrize("pointer", ["legacy-ir:/executables/9999", "legacy-ir:/executables/-1", "legacy-ir:/executables/00"])
def test_provenance_pointer_must_exist_in_bound_input(inputs, pointer):
    doc = project(inputs)
    doc['nodes'][0]['evidence'][0]['locator'] = pointer
    resign(doc)
    with pytest.raises(SchemaValidationError, match='evidence pointer'):
        validate_normalized_ir(doc, legacy_ir=inputs[0])


def test_connection_evidence_uses_the_field_actually_supplied(inputs):
    props = inputs[0]['executables'][0]['properties']
    props['connection_manager_ref_id'] = props.pop('Connection')
    doc = project(inputs)
    link = next(e for e in edges(doc, 'USES_CONNECTION')
                if e['evidence'][0]['locator'].startswith('legacy-ir:/executables/'))
    assert link['evidence'][0]['locator'].endswith('/connection_manager_ref_id')
    validate_normalized_ir(doc, legacy_ir=inputs[0])
