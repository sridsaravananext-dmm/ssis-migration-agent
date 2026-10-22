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
    assert not result['migration_accepted'] and result['next_phase_gate']=='STOP_FOR_USER_DATABRICKS_REVIEW'
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
