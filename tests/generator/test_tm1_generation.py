"""Contract replay, semantic boundaries and the generated Databricks handoff."""
import copy
import ast
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.generator.tm1.contract import build_contract, contract_id, load_mapping, validate_contract
from ssis_migration.generator.tm1.emitter import render, validate_rendered, verify_fixtures
from ssis_migration.generator.tm1.runtime import execute_program, exact_integer
from ssis_migration.generator.tm1.leaf_runtime import execute_program as execute_leaf_program
from ssis_migration.migrate import run_migration
from ssis_migration.schemas import SchemaValidationError

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT/'examples/tm1/databricks_demo'


def write(path,document):
    path.write_text(json.dumps(document,indent=2)+'\n')


def refresh(root):
    manifest = json.loads((root/'manifest.json').read_text())
    for unit in manifest['units']:
        source = root/unit['path']
        if unit['language']=='tm1_turbointegrator':
            process = json.loads(source.read_text())
            for section in process['sections'].values():
                section['sha256'] = hashlib.sha256((root/section['path']).read_bytes()).hexdigest()
            write(source,process)
        unit['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    for metadata in manifest['metadata_files']:
        metadata['sha256'] = hashlib.sha256((root/metadata['path']).read_bytes()).hexdigest()
    write(root/'manifest.json',manifest)
    mapping = json.loads((root/'target_mapping.json').read_text())
    mapping['source_bundle_sha256'] = hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()
    write(root/'target_mapping.json',mapping)


def build(root):
    return build_contract(get_adapter('tm1').analyze(root/'manifest.json').normalized_ir,load_mapping(root/'target_mapping.json'))


@pytest.fixture(scope='module')
def candidate():return build(DEMO)


@pytest.fixture
def supplied(tmp_path):
    root=tmp_path/'source';shutil.copytree(DEMO,root);return root


def program(contract,unit):return next(c['program'] for c in contract['capabilities'] if c['unit_id']==unit)


def set_leaf_rules(root, source):
    (root/'rules.rux').write_text(source)
    path=root/'target_mapping.json';mapping=json.loads(path.read_text())
    next(m for m in mapping['models'] if m['cube']=='RuleCube')['value_semantics']='STORED_BEFORE_SUPPLIED_LEAF_RULES'
    write(path,mapping);refresh(root)


@pytest.mark.parametrize('source,expected,order',[
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity')*3;",[6,2],[0]),
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity')*3; ['Product':'B','Measures':'Quantity']=N:5;",[15,5],[1,0]),
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity')+DB('RuleCube','B','Quantity'); ['Product':'B','Measures':'Quantity']=N:5;",[10,5],[1,0]),
    ("['Product':'B','Measures':'Quantity']=N:5; ['Product':'A','Measures':'Quantity']=N:-(DB('RuleCube','B','Quantity')+2);",[-7,5],[0,1]),
])
def test_leaf_rule_reads_use_evaluated_dependencies_not_source_order(supplied,source,expected,order):
    set_leaf_rules(supplied,source);contract=build(supplied)
    assert contract['status']=='CANDIDATE_READY'
    p=program(contract,'rules');assert p['kind']=='leaf_rules'
    assert [r['source_order'] for r in p['rules']]==order
    cells=[{'product':'A','measure':'Quantity','value':1},{'product':'B','measure':'Quantity','value':2}]
    original=copy.deepcopy(cells)
    assert [r['value'] for r in execute_leaf_program(p,cells,[],{})]==expected
    assert cells==original
    assert all(r['source_statement_id'] and r['source_span'] for r in p['rules'])
    if len(order)>1:
        assert len(p['rule_dependencies'])==1
        assert p['rule_dependencies'][0]=={'source_coordinates':['B','Quantity'],'target_coordinates':['A','Quantity']}


@pytest.mark.parametrize('source,reason',[
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube','A','Quantity')+1;",'Cyclic'),
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity'); ['Product':'B','Measures':'Quantity']=N:DB('RuleCube','A','Quantity');",'Cyclic'),
    ("['Product':'A','Measures':'Quantity']=N:DB('OtherCube','B','Quantity');",'Cross-cube'),
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube','Missing','Quantity');",'missing/non-leaf'),
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B');",'every ordered coordinate'),
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube',1,'Quantity');",'literal'),
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube',!Product,'Quantity');",'literal'),
    ("['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity')/2;",'Unsupported scalar operator'),
    ("['Product':'A','Measures':'Quantity']=N:STET;",'Unsupported scalar construct'),
    ("['Product':'A','Measures':'Quantity']=N:1; ['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity');",'Overlapping'),
    ("SKIPCHECK; ['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity');",'directives/feeders'),
])
def test_unsupported_leaf_rule_dependencies_block_all_generation(supplied,source,reason):
    set_leaf_rules(supplied,source);contract=build(supplied)
    capability=next(c for c in contract['capabilities'] if c['unit_id']=='rules')
    assert contract['status']=='BLOCKED' and capability['program'] is None
    assert any(reason in r for r in capability['reasons'])
    with pytest.raises(ValueError,match='blocks all'):render(contract)


def test_legacy_constant_rule_profile_does_not_authorize_cell_reads(supplied,candidate):
    assert program(candidate,'rules')['kind']=='constant_rules'
    (supplied/'rules.rux').write_text("['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity')*3;")
    refresh(supplied)
    assert build(supplied)['status']=='BLOCKED'


def test_leaf_rule_input_changes_are_observed_and_overflow_never_returns_a_prefix(supplied):
    set_leaf_rules(supplied,"['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity')+1;")
    p=program(build(supplied),'rules')
    cells=[{'product':'A','measure':'Quantity','value':0},{'product':'B','measure':'Quantity','value':7}]
    assert execute_leaf_program(p,cells,[],{})[0]['value']==8
    cells[1]['value']=(1<<53)-1;original=copy.deepcopy(cells)
    with pytest.raises(ValueError,match='exceeds'):execute_leaf_program(p,cells,[],{})
    assert cells==original
    with pytest.raises(ValueError,match='no row/parameter'):execute_leaf_program(p,cells,[],{'unused':1})


@pytest.mark.parametrize('change',['dependency','order','read','expression'])
def test_leaf_rule_contract_replay_rejects_rehashed_dependency_changes(supplied,change):
    set_leaf_rules(supplied,"['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity')*3; ['Product':'B','Measures':'Quantity']=N:2;")
    contract=build(supplied);p=program(contract,'rules')
    if change=='dependency':p['rule_dependencies']=[]
    elif change=='order':p['rules'].reverse()
    elif change=='read':p['rules'][1]['read_coordinates']=[]
    else:p['rules'][1]['value']['left']['coordinates']=['A','Quantity']
    contract['contract_id']=contract_id(contract)
    with pytest.raises(SchemaValidationError):validate_contract(contract)


def test_optional_leaf_kernel_preserves_old_candidate_utility_bytes(candidate):
    from ssis_migration.generator.tm1 import runtime
    artifacts,_=render(candidate)
    assert artifacts['utilities.py']==Path(runtime.__file__).read_text()
    assert '_resolve_leaf_reads' not in artifacts['utilities.py']


@pytest.mark.parametrize('bundle',['leaf','mixed'])
def test_leaf_rules_generate_standalone_notebooks_and_unassessed_acceptance_drafts(tmp_path,supplied,bundle):
    from ssis_migration.validator.acceptance import load_package,draft_documents,evaluate_acceptance
    source=ROOT/'examples/tm1/leaf_rules_demo'
    if bundle=='mixed':
        source=supplied
        set_leaf_rules(source,"['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity')*3; ['Product':'B','Measures':'Quantity']=N:2;")
    report=run_migration(str(source/'manifest.json'),str(tmp_path/'output'),target_mapping=str(source/'target_mapping.json'))
    assert report['status']=='GENERATED' and report['execution_status']=='NOT_RUN'
    assert not report['migration_accepted'] and report['coverage']['semantic']['weighted_verified_percent'] is None
    assert len(report['fixture_results'])==(2 if bundle=='leaf' else 4)
    assert all(r['status']=='PASS' for r in report['fixture_results'])
    directory=Path(report['artifacts']['report']).parent
    contract=json.loads((directory/'generation_contract.json').read_text())
    evaluation=report['rule_evaluation']['rules']
    assert next(r for r in evaluation['rules'] if r['coordinates']==['A','Quantity'])['read_coordinates']==[['B','Quantity']]
    expected_edges=[] if bundle=='leaf' else [{'source_coordinates':['B','Quantity'],'target_coordinates':['A','Quantity']}]
    assert evaluation['dependencies']==expected_edges
    utility=(directory/'utilities.py').read_text()
    assert 'from .runtime' not in utility
    namespace={};exec(compile(utility,'<controlled-leaf-kernel>','exec'),namespace)
    for case in contract['mapping_source']['document']['fixture_cases']:
        assert namespace['execute_program'](program(contract,case['unit_id']),case['cells'],case['rows'],case['parameters'])==case['expected']
    for name in ('Migration.ipynb','Validation.ipynb'):
        notebook=json.loads((directory/name).read_text())
        assert any(''.join(c['source'])==utility for c in notebook['cells'] if c['cell_type']=='code')
    package=load_package(directory);policy,evidence=draft_documents(package)
    assert len(policy['behaviors'])==len(contract['obligation_ids'])
    result=evaluate_acceptance(package,policy,evidence,report_root=tmp_path)
    assert not result['acceptance']['accepted'] and result['coverage']['semantic']['verified_percent'] is None


def test_failed_leaf_fixture_prevents_any_runnable_artifacts(supplied,tmp_path):
    set_leaf_rules(supplied,"['Product':'A','Measures':'Quantity']=N:DB('RuleCube','B','Quantity')*4;")
    report=run_migration(str(supplied/'manifest.json'),str(tmp_path/'output'),target_mapping=str(supplied/'target_mapping.json'))
    assert report['status']=='BLOCKED'
    assert any(r['status']=='FAIL' for r in report['fixture_results'])
    directory=Path(report['artifacts']['report']).parent
    assert not list(directory.glob('*.ipynb')) and not (directory/'job.json').exists()


def test_all_units_lower_without_promoting_source_review_contracts(candidate):
    assert candidate['status']=='CANDIDATE_READY'
    assert candidate['migration_accepted'] is False and candidate['semantic_status']=='NOT_VERIFIED'
    graph=candidate['source_ir']
    assert Counter(candidate['obligation_ids'])==Counter([n['node_id'] for n in graph['nodes']]+[e['edge_id'] for e in graph['edges']])
    assert [c['decision'] for c in candidate['capabilities']]==['DETERMINISTIC']*3
    assert all(c['evidence'] for c in candidate['capabilities'])
    assert [f['status'] for f in verify_fixtures(candidate)]==['PASS']*4


def test_golden_outputs_preserve_record_order_write_precedence_and_itemskip(candidate):
    cases=candidate['mapping_source']['document']['fixture_cases']
    for case in cases:
        original=copy.deepcopy(case)
        assert execute_program(program(candidate,case['unit_id']),case['cells'],case['rows'],case['parameters'])==case['expected']
        assert case==original
    case=cases[0];p=program(candidate,'load')
    # Ordinals, not physical input order, select the last duplicate-cell write.
    assert execute_program(p,case['cells'],list(reversed(case['rows'])),case['parameters'])==case['expected']
    rows=copy.deepcopy(case['rows']);rows[0]['record_ordinal']=0
    assert execute_program(p,case['cells'],rows,case['parameters'])[0]['value']==9


@pytest.mark.parametrize('value',[True,None,'1',1.5,float('nan'),float('inf'),Decimal('1.1'),(1<<53),-(1<<53)])
def test_numeric_domain_fails_closed(value):
    with pytest.raises(ValueError):exact_integer(value)


@pytest.mark.parametrize('value',[0,-3,9007199254740991,Decimal('3'),3.0])
def test_safe_integer_inputs(value):assert exact_integer(value)==int(value)


@pytest.mark.parametrize('change',['missing_cell','duplicate_cell','unknown_member','extra_column','fraction','overflow','duplicate_order','null','missing_parameter','too_many_rows'])
def test_runtime_rejects_invalid_snapshots_without_partial_results(candidate,change):
    case=copy.deepcopy(candidate['mapping_source']['document']['fixture_cases'][0]);p=copy.deepcopy(program(candidate,'load'))
    if change=='missing_cell':case['cells'].pop()
    if change=='duplicate_cell':case['cells'].append(copy.deepcopy(case['cells'][0]))
    if change=='unknown_member':case['cells'][0]['product']='AliasA'
    if change=='extra_column':case['cells'][0]['hidden']='not guessed'
    if change=='fraction':case['rows'][0]['Quantity']=1.5
    if change=='overflow':case['rows'][0]['Quantity']=(1<<53)-1
    if change=='duplicate_order':case['rows'][0]['record_ordinal']=case['rows'][1]['record_ordinal']
    if change=='null':case['rows'][0]['ProductCode']=None
    if change=='missing_parameter':case['parameters']={}
    if change=='too_many_rows':p['max_rows']=2
    original=copy.deepcopy(case)
    with pytest.raises(ValueError):execute_program(p,case['cells'],case['rows'],case['parameters'])
    assert case==original


@pytest.mark.parametrize('change',['code','obligation','decision','status','source_hash','mapping_document'])
def test_rehashing_cannot_make_an_edited_contract_valid(candidate,change):
    contract=copy.deepcopy(candidate)
    if change=='code':program(contract,'load')['statements']=[]
    if change=='obligation':contract['obligation_ids'].pop()
    if change=='decision':contract['capabilities'][0]['decision']='UNSUPPORTED'
    if change=='status':contract['status']='BLOCKED'
    if change=='source_hash':contract['mapping_source']['record']['sha256']='0'*64
    if change=='mapping_document':contract['mapping_source']['document']['max_rows']=99
    contract['contract_id']=contract_id(contract)
    with pytest.raises((ValueError,SchemaValidationError)):validate_contract(contract)


@pytest.mark.parametrize('text',[
    "WHILE(vQuantity>0); CellPutN(1,'LoadCube',vProduct,'Quantity'); END;",
    "ExecuteCommand('do not run',1);",
    "ItemSkip; ExecuteCommand('unreachable still blocked',1);",
    "CellPutN(nValue,'LoadCube',vProduct,'Quantity');",  # no definite value
    "IF(vQuantity>0); nValue=1; ENDIF; CellPutN(nValue,'LoadCube',vProduct,'Quantity');",
    "CellIncrementN(1,'LoadCube',vProduct,'Quantity');",
    "CellPutN(1/2,'LoadCube',vProduct,'Quantity');",
    "CellPutN(1,'OtherCube',vProduct,'Quantity');",
    "IF(vQuantity<0); CellPutN(1,'LoadCube','Missing','Quantity'); ENDIF;",
    "nValue=1; ProcessQuit;",
])
def test_unsupported_ti_never_generates_a_prefix(supplied,text):
    (supplied/'data.ti').write_text(text);refresh(supplied)
    contract=build(supplied)
    assert contract['status']=='BLOCKED' and contract['capabilities'][0]['program'] is None
    with pytest.raises(ValueError,match='blocks all'):render(contract)


@pytest.mark.parametrize('filename,text',[
    ('query.mdx','SELECT NON EMPTY {[Product].[Product].[A]} ON 0 FROM [QueryCube]'),
    ('query.mdx','SELECT {[Product].[Product].[A]} ON 0 FROM [QueryCube]'), # missing measure
    ('query.mdx','SELECT {[Product].[Product].[A],[Product].[Product].[A]} ON 0 FROM [QueryCube]'),
    ('rules.rux',"SKIPCHECK; ['Product':'A','Measures':'Quantity']=N:6;"),
    ('rules.rux',"['Product':'A','Measures':'Quantity']=N:DB('OtherCube','A');"),
    ('rules.rux',"['A']=N:6;"),
    ('rules.rux',"['Product':'A','Measures':'Quantity']=N:6; ['Product':'A','Measures':'Quantity']=N:8;"),
    ('prolog.ti',"nValue=1;"),
    ('metadata.ti',"nValue=1;"),
    ('epilog.ti',"nValue=1;"),
])
def test_unsupported_semantics_in_any_unit_block_entire_candidate(supplied,filename,text):
    (supplied/filename).write_text(text);refresh(supplied)
    assert build(supplied)['status']=='BLOCKED'


@pytest.mark.parametrize('change',['duplicate_unit','duplicate_model','missing_mapping','extra_mapping','unused_model','no_fixture','duplicate_case','column_collision','consolidation','parameter_default'])
def test_ambiguous_or_incomplete_mapping_is_rejected_or_blocked(supplied,change):
    path=supplied/'target_mapping.json';mapping=json.loads(path.read_text())
    if change=='duplicate_unit':mapping['units'].append(copy.deepcopy(mapping['units'][0]))
    if change=='duplicate_model':mapping['models'].append(copy.deepcopy(mapping['models'][0]))
    if change=='missing_mapping':mapping['units'].pop()
    if change=='extra_mapping':mapping['units'].append({**mapping['units'][0],'unit_id':'unknown'})
    if change=='unused_model':mapping['models'].append({**mapping['models'][0],'model_id':'unused'})
    if change=='no_fixture':mapping['fixture_cases']=[c for c in mapping['fixture_cases'] if c['unit_id']!='load']
    if change=='duplicate_case':mapping['fixture_cases'].append(copy.deepcopy(mapping['fixture_cases'][0]))
    if change=='column_collision':mapping['models'][0]['value_column']='PRODUCT'
    if change=='parameter_default':mapping['units'][0]['parameters']={}
    if change=='consolidation':
        meta=json.loads((supplied/'metadata.json').read_text());meta['dimensions'][0]['hierarchies'][0]['members'].append({'name':'Total','type':'C','children':[{'member':'A','weight':'1'}]});write(supplied/'metadata.json',meta)
    write(path,mapping);refresh(supplied)
    try:contract=build(supplied)
    except ValueError:return
    assert contract['status']=='BLOCKED'


def test_notebooks_are_self_contained_and_target_workflow_has_no_source_order(candidate):
    artifacts,results=render(candidate);validate_rendered(candidate,artifacts)
    assert len(results)==4
    cells='\n'.join(''.join(c['source']) for c in artifacts['Migration.ipynb']['cells'] if c['cell_type']=='code')
    tree = ast.parse(cells)
    calls = [node.func for node in ast.walk(tree) if isinstance(node,ast.Call)]
    assert not any(isinstance(call,ast.Name) and call.id in {'exec','eval'} for call in calls)
    assert not any(isinstance(call,ast.Attribute) and call.attr in {'saveAsTable','sql','save','insertInto'} for call in calls)
    assert 'createOrReplaceTempView' in cells
    assert 'exceptAll' in ''.join(artifacts['Validation.ipynb']['cells'][-1]['source'])
    job=artifacts['job.json'];assert not job.get('schedule') and not job.get('job_clusters')
    assert all(t['existing_cluster_id']=='${var.existing_cluster_id}' for t in job['tasks'])
    assert all(not t.get('depends_on') for t in job['tasks'][:-1])
    assert {t['task_key'] for t in job['tasks'][:-1]}=={d['task_key'] for d in job['tasks'][-1]['depends_on']}
    edited=copy.deepcopy(artifacts);edited['Migration.ipynb']['cells'][-1]['source']=['print("edited")']
    with pytest.raises(ValueError,match='replayed'):validate_rendered(candidate,edited)


def test_supplied_expected_output_is_not_computed_or_overwritten(candidate):
    changed=copy.deepcopy(candidate)
    changed['mapping_source']['document']['fixture_cases'][0]['expected'][0]['value']=123
    assert verify_fixtures(changed)[0]['status']=='FAIL'


def test_generation_produces_a_verifiable_download_bundle_and_stops(tmp_path):
    result=run_migration(str(DEMO/'manifest.json'),str(tmp_path),target_mapping=str(DEMO/'target_mapping.json'))
    assert result['status']=='GENERATED' and result['execution_status']=='NOT_RUN'
    assert not result['migration_accepted'] and result['next_phase_gate']=='CONTINUE_OFFLINE_IMPLEMENTATION'
    assert result['databricks_human_review'] == 'DEFERRED_BY_USER'
    assert result['coverage']['semantic']['weighted_verified_percent'] is None
    with zipfile.ZipFile(result['artifacts']['bundle']) as archive:
        inventory=json.loads(archive.read('artifact_inventory.json'))
        for name,digest in inventory['sha256'].items():assert hashlib.sha256(archive.read(name)).hexdigest()==digest
        assert {'Migration.ipynb','Validation.ipynb','databricks.yml','DATABRICKS_REVIEW.md'}<=set(archive.namelist())
        review_contracts=json.loads(archive.read('migration_contracts.json'))['contracts']
        assert all(c['target_design']['execution_mode']=='REVIEW_ONLY' for c in review_contracts)


def test_unsupported_bundle_outputs_only_diagnostics_and_no_runnable_prefix(supplied,tmp_path):
    (supplied/'epilog.ti').write_text('ProcessQuit;');refresh(supplied)
    result=run_migration(str(supplied/'manifest.json'),str(tmp_path/'out'),target_mapping=str(supplied/'target_mapping.json'))
    assert result['status']=='BLOCKED' and result['generated_artifact_count']==0
    directory=Path(result['artifacts']['report']).parent
    assert not list(directory.glob('*.ipynb')) and not (directory/'job.json').exists()
    assert result['unsupported']['load']


def test_failed_fixture_prevents_executable_output(supplied,tmp_path):
    path=supplied/'target_mapping.json';mapping=json.loads(path.read_text());mapping['fixture_cases'][0]['expected'][0]['value']=123;write(path,mapping)
    result=run_migration(str(supplied/'manifest.json'),str(tmp_path/'out'),target_mapping=str(path))
    assert result['status']=='BLOCKED' and result['fixture_results'][0]['status']=='FAIL'
    assert not list(Path(result['artifacts']['report']).parent.glob('*.ipynb'))


def test_cli_does_not_silently_accept_native_mapping_for_ssis(tmp_path):
    with pytest.raises(ValueError,match='only for explicit TM1'):
        run_migration('does-not-need-to-exist.dtsx',str(tmp_path),source_platform='ssis',target_mapping='mapping.json')


def test_mdx_tuple_slicer_and_axis_zero_fastest_order(supplied):
    # Independent literal two-by-two axes, with no inferred dimension defaults.
    meta=json.loads((supplied/'metadata.json').read_text())
    meta['dimensions'][1]['hierarchies'][0]['members'].append({'name':'Other','type':'N','children':[]})
    write(supplied/'metadata.json',meta)
    (supplied/'query.mdx').write_text('SELECT {[Product].[Product].[B],[Product].[Product].[A]} ON 0, {[Measures].[Measures].[Quantity],[Measures].[Measures].[Other]} ON 1 FROM [QueryCube]')
    refresh(supplied);contract=build(supplied);p=program(contract,'query')
    cells=[{'product':product,'measure':measure,'value':value} for product,measure,value in [('A','Quantity',1),('B','Quantity',2),('A','Other',3),('B','Other',4)]]
    result=execute_program(p,cells,[],{})
    assert [(r['axis_0'],r['axis_1'],r['value']) for r in result]==[(0,0,2),(1,0,1),(0,1,4),(1,1,3)]
    (supplied/'query.mdx').write_text('SELECT {([Product].[Product].[A],[Measures].[Measures].[Other])} ON 0 FROM [QueryCube]')
    refresh(supplied);p=program(build(supplied),'query')
    assert execute_program(p,cells,[],{})==[{'axis_0':0,'product':'A','measure':'Other','value':3}]
    (supplied/'query.mdx').write_text('SELECT {[Product].[Product].[B]} ON 0 FROM [QueryCube] WHERE ([Measures].[Measures].[Other])')
    refresh(supplied);p=program(build(supplied),'query')
    assert execute_program(p,cells,[],{})==[{'axis_0':0,'product':'B','measure':'Other','value':4}]


def test_mapping_hash_and_duplicate_json_keys_are_never_ignored(supplied):
    path=supplied/'target_mapping.json'
    value=json.loads(path.read_text());value['source_bundle_sha256']='0'*64;write(path,value)
    with pytest.raises(ValueError,match='different source bundle'):build(supplied)
    path.write_text('{"mapping_version":"1.0","mapping_version":"1.0"}')
    with pytest.raises(ValueError,match='Duplicate'):load_mapping(path)


@pytest.mark.parametrize('expression,products',[
    ('CrossJoin({[Product].[Product].[B],[Product].[Product].[A]},{[Measures].[Measures].[Quantity]})',['B','A']),
    ('CrossJoin(Head({[Product].[Product].[B],[Product].[Product].[A]},1),{[Measures].[Measures].[Quantity]})',['B']),
    ('CrossJoin(Tail({[Product].[Product].[B],[Product].[Product].[A]}),{[Measures].[Measures].[Quantity]})',['A']),
    ('Head(CrossJoin({[Product].[Product].[B],[Product].[Product].[A]},{[Measures].[Measures].[Quantity]}),2)',['B','A']),
])
def test_bounded_mdx_set_functions_preserve_order_and_coordinates(supplied,expression,products):
    (supplied/'query.mdx').write_text('SELECT '+expression+' ON 0 FROM [QueryCube]')
    refresh(supplied);contract=build(supplied)
    assert contract['status']=='CANDIDATE_READY'
    cells=[{'product':'A','measure':'Quantity','value':3},{'product':'B','measure':'Quantity','value':7}]
    result=execute_program(program(contract,'query'),cells,[],{})
    assert result==[{'axis_0':i,'product':name,'measure':'Quantity','value':3 if name=='A' else 7} for i,name in enumerate(products)]


@pytest.mark.parametrize('expression',[
    'Head({[Product].[Product].[A]},0)',
    'Tail({[Product].[Product].[A]},100001)',
    'CrossJoin({[Product].[Product].[A]},{[Product].[Product].[B]})',
    'Head({[Product].[Product].[A],[Product].[Product].[A]},1)',
    'Head({[Product].[Product].[A],[Measures].[Measures].[Quantity]},1)',
    'Head({[Product].[Product].[A],[Product].[Product].[Missing]},1)',
])
def test_mdx_selection_cannot_hide_unsupported_or_ambiguous_inputs(supplied,expression):
    (supplied/'query.mdx').write_text('SELECT '+expression+' ON 0 FROM [QueryCube] WHERE ([Measures].[Measures].[Quantity])')
    refresh(supplied)
    assert build(supplied)['status']=='BLOCKED'


def test_constant_expression_unary_and_no_rule_input_defaults(supplied):
    (supplied/'rules.rux').write_text("['Product':'A','Measures':'Quantity']=N:-(2+3)*2;")
    refresh(supplied);p=program(build(supplied),'rules')
    cells=[{'product':'A','measure':'Quantity','value':1},{'product':'B','measure':'Quantity','value':2}]
    assert execute_program(p,cells,[],{})[0]['value']==-10
    with pytest.raises(ValueError,match='no row/parameter'):execute_program(p,cells,[],{'invented':1})


def test_reserved_widget_unit_cannot_run_other_units_by_accident(supplied):
    manifest=json.loads((supplied/'manifest.json').read_text())
    manifest['units'][0]['unit_id']='all'
    write(supplied/'manifest.json',manifest)
    mapping=json.loads((supplied/'target_mapping.json').read_text())
    mapping['units'][0]['unit_id']='all'
    for case in mapping['fixture_cases']:
        if case['unit_id']=='load':case['unit_id']='all'
    write(supplied/'target_mapping.json',mapping)
    refresh(supplied)
    contract=build(supplied)
    assert contract['status']=='BLOCKED' and 'reserved' in contract['capabilities'][0]['reasons'][0]
