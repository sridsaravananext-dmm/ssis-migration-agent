from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from ssis_migration.adapters.ssis import SSISAdapter
from ssis_migration.ir.normalized import normalized_ir_id
from ssis_migration.migration import build_migration_contract, validate_contract, bind_plan_to_ir, validate_plan_ir_binding
from ssis_migration.schemas import SchemaValidationError
from ssis_migration.languages.sql import analyze_sql


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path/'nested.dtsx'
    source.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:refId="Package" DTS:ObjectName="Example" DTS:ExecutableType="Microsoft.Package">
<DTS:Property DTS:Name="PackageFormatVersion">8</DTS:Property>
<DTS:Executables><DTS:Executable DTS:refId="odd-parent" DTS:ObjectName="Same" DTS:ExecutableType="STOCK:SEQUENCE">
<DTS:Executables><DTS:Executable DTS:refId="unrelated-native-id" DTS:ObjectName="Same" DTS:ExecutableType="Microsoft.ExecuteSQLTask"/></DTS:Executables>
</DTS:Executable><DTS:Executable DTS:refId="other" DTS:ObjectName="Same" DTS:ExecutableType="Microsoft.ExecuteSQLTask"/>
</DTS:Executables></DTS:Executable>''')
    result = SSISAdapter().analyze(source)
    plan = {'package_name':'Example','steps':[
        {'step_id':'a','name':'Same','source_objects':[],'target_objects':[], 'dependencies':[],
         'metadata':{'ref_id':'odd-parent','migration_intent':{'control_flow_operations':['LOAD']},
                     'migration_strategy':{'strategy':'DETERMINISTIC'}}},
        {'step_id':'b','name':'Same','source_objects':[],'target_objects':[], 'dependencies':[],
         'metadata':{'ref_id':'other','migration_intent':{'control_flow_operations':['LOAD']},
                     'migration_strategy':{'strategy':'DETERMINISTIC'}}}], 'metadata':{}}
    return plan, result


def test_binding_uses_xml_parentage_not_names_or_native_prefixes(inputs):
    plan, result = inputs
    original = copy.deepcopy(plan)
    bound = bind_plan_to_ir(plan,result.normalized_ir)
    validate_plan_ir_binding(bound,result.normalized_ir)
    assert plan == original
    first=bound['steps'][0]['metadata']['normalized_source']
    assert {n['record']['source_ref'] for n in first['nodes']} == {'odd-parent','unrelated-native-id'}
    report=bound['metadata']['normalized_ir_binding']
    assert report['unplanned_node_ids'] == []
    assert report['ambiguous_node_ids'] == []
    assert 'task_hierarchy' not in result.normalized_ir['capability_gaps']
    assert [s['dependencies'] for s in bound['steps']] == [[],[]]
    for old,new in zip(plan['steps'],bound['steps']):
        assert new['metadata']['migration_intent'] == old['metadata']['migration_intent']
        assert new['metadata']['migration_strategy'] == old['metadata']['migration_strategy']


def test_graph_records_are_contract_obligations_exactly_once(inputs):
    plan,result=inputs
    bound=bind_plan_to_ir(plan,result.normalized_ir)
    node_ids,edge_ids=[],[]
    for step in bound['steps']:
        contract=build_migration_contract(step,source_platform='ssis',evidence_files=result.source_envelope['evidence_files']).to_dict()
        validate_contract(contract,step)
        for obligation in contract['required_behavior']['obligations']:
            if obligation['kind'].startswith('normalized_'):
                record=obligation['source_value']['record']
                assert obligation['evidence'] == record['evidence']
                assert obligation['verification']=='NOT_VERIFIED'
                assert obligation['critical'] is None and obligation['weight'] is None
                (node_ids if obligation['kind']=='normalized_node' else edge_ids).append(record.get('node_id') or record['edge_id'])
    assert Counter(node_ids)==Counter(n['node_id'] for n in result.normalized_ir['nodes'])
    assert Counter(edge_ids)==Counter(e['edge_id'] for e in result.normalized_ir['edges'])


def test_unplanned_behavior_is_retained_as_review_not_claimed_implemented(inputs):
    plan,result=inputs
    plan['steps'].pop()
    bound=bind_plan_to_ir(plan,result.normalized_ir)
    report=bound['metadata']['normalized_ir_binding']
    assert len(report['unplanned_node_ids'])==1
    scope=bound['steps'][0]['metadata']['normalized_source']
    assert len([n for n in scope['nodes'] if n['obligation_scope']=='PACKAGE_REVIEW'])==1
    contract=build_migration_contract(bound['steps'][0],source_platform='ssis',evidence_files=result.source_envelope['evidence_files']).to_dict()
    assert any('UNPLANNED_NEUTRAL_BEHAVIOR' in issue for issue in contract['unresolved_items'])
    assert contract['status']=='REVIEW_REQUIRED'
    assert contract['implementation_strategy']['decision']=='DETERMINISTIC'  # Decision isn't promoted.


def test_overlapping_roots_do_not_double_count_or_choose_implementation_owner(inputs):
    plan,result=inputs
    plan['steps'][1]['metadata']['ref_id']='unrelated-native-id'
    bound=bind_plan_to_ir(plan,result.normalized_ir)
    report=bound['metadata']['normalized_ir_binding']
    assert len(report['ambiguous_node_ids'])==1
    nodes=[n for s in bound['steps'] for n in s['metadata']['normalized_source']['nodes']]
    assert len(nodes)==len(result.normalized_ir['nodes'])
    assert len({n['record']['node_id'] for n in nodes})==len(nodes)


@pytest.mark.parametrize('reference',[None,'missing'])
def test_no_fallback_from_missing_root_to_matching_display_name(inputs,reference):
    plan,result=inputs
    plan['steps'][0]['metadata']['ref_id']=reference
    bound=bind_plan_to_ir(plan,result.normalized_ir)
    assert bound['steps'][0]['metadata']['normalized_source']['root_node_id'] is None
    assert any(d['code']=='UNRESOLVED_PLAN_ROOT' for d in bound['metadata']['normalized_ir_binding']['diagnostics'])


def test_explicit_neutral_identity_supported_without_vendor_reference(inputs):
    plan,result=inputs
    root=next(n['node_id'] for n in result.normalized_ir['nodes'] if n['source_ref']=='odd-parent')
    plan['steps'][0]['metadata'].update(source_node_id=root,ref_id=None)
    bound=bind_plan_to_ir(plan,result.normalized_ir)
    assert bound['steps'][0]['metadata']['normalized_source']['root_node_id']==root


def test_cross_step_control_is_reported_without_inventing_dependencies(inputs):
    plan,result=inputs
    graph=copy.deepcopy(result.normalized_ir)
    roots={n['source_ref']:n['node_id'] for n in graph['nodes']}
    template=copy.deepcopy(graph['edges'][0])
    template.update(edge_id='cross-edge',kind='CONTROL_FLOW',source_id=roots['odd-parent'],target_id=roots['other'])
    graph['edges'].append(template);graph['ir_id']=normalized_ir_id(graph)
    bound=bind_plan_to_ir(plan,graph)
    report=bound['metadata']['normalized_ir_binding']
    assert report['cross_step_edge_ids']==['cross-edge']
    assert {'MISSING_NEUTRAL_DEPENDENCY','CROSS_STEP_CONTROL_REVIEW'} <= {d['code'] for d in report['diagnostics']}
    assert bound['steps'][1]['dependencies']==[]


@pytest.mark.parametrize('change',['node','edge','report'])
def test_dropped_records_or_forged_binding_summary_rejected(inputs,change):
    plan,result=inputs
    bound=bind_plan_to_ir(plan,result.normalized_ir)
    if change=='node':bound['steps'][0]['metadata']['normalized_source']['nodes'].pop()
    elif change=='edge':bound['steps'][0]['metadata']['normalized_source']['edges'].pop()
    else:bound['metadata']['normalized_ir_binding']['assigned_node_count']=999
    with pytest.raises(SchemaValidationError):validate_plan_ir_binding(bound,result.normalized_ir)


def test_contract_omission_of_neutral_obligation_is_rejected_even_with_rehashed_identity(inputs):
    from ssis_migration.migration.contract_bridge import _contract_id
    plan,result=inputs
    bound=bind_plan_to_ir(plan,result.normalized_ir)
    contract=build_migration_contract(bound['steps'][0],source_platform='ssis',evidence_files=result.source_envelope['evidence_files']).to_dict()
    contract['required_behavior']['obligations'].pop()
    contract['contract_id']=_contract_id(contract)
    with pytest.raises(SchemaValidationError,match='obligation'):validate_contract(contract)


@pytest.mark.parametrize('filename,dialect',[('sample_tsql.sql','tsql'),('sample_plsql.sql','oracle')])
def test_shared_sql_samples_preserved_and_not_misrepresented_as_translated(filename,dialect):
    root=Path(__file__).resolve().parents[2]/'examples/shared_sql'
    manifest=json.loads((root/'provenance.json').read_text())
    entry=next(e for e in manifest['reviewed_entries'] if e['entry'].endswith('/'+filename))
    data=(root/filename).read_bytes()
    assert hashlib.sha256(data).hexdigest()==entry['sha256']
    report=analyze_sql(data.decode('utf-8-sig'),dialect=dialect)
    assert report['status'] in {'UNSUPPORTED','UNRESOLVED'}
    assert report['semantic_status']=='NOT_VERIFIED'
    assert report['diagnostics']
    assert report['source_sha256']==hashlib.sha256(data.decode('utf-8-sig').encode()).hexdigest()
