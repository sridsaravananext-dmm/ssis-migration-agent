import copy
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from ssis_migration.adapters import SourceAdapter, SourceEvidenceError, UnsupportedSourceError, get_adapter
from ssis_migration.adapters.tm1.bundle import bundle_id, validate_bundle
from ssis_migration.adapters.tm1.metadata import bind_tm1_metadata
from ssis_migration.adapters.tm1.normalizer import validate_tm1_normalized_ir
from ssis_migration.intake import build_intake_manifest, describe_file
from ssis_migration.ir.normalized import content_digest, normalized_ir_id, validate_normalized_ir
from ssis_migration.languages.mdx.analysis import mdx_analysis_id
from ssis_migration.migration import prepare_generation_step
from ssis_migration.migration.native_review import build_native_review_plan, validate_native_review_plan
from ssis_migration.schemas import SchemaValidationError

ROOT=Path(__file__).resolve().parents[3]


@pytest.fixture(scope='module')
def native_result():
    return get_adapter('tm1').analyze(ROOT/'examples/tm1/bundle/manifest.json')


@pytest.fixture
def supplied_bundle(tmp_path):
    root=tmp_path/'supplied'
    shutil.copytree(ROOT/'examples/tm1',root)
    return root/'bundle/manifest.json'


def write(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n')


def refresh(path):
    document=json.loads(path.read_text())
    for unit in document['units']:
        source=path.parent/unit['path']
        if unit['language']=='tm1_turbointegrator':
            manifest=json.loads(source.read_text())
            for section in manifest['sections'].values():
                section['sha256']=hashlib.sha256((source.parent/section['path']).read_bytes()).hexdigest()
            write(source,manifest)
        unit['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    for metadata in document['metadata_files']:
        metadata['sha256']=hashlib.sha256((path.parent/metadata['path']).read_bytes()).hexdigest()
    write(path,document)


def test_registered_adapter_has_native_result_without_fabricating_ssis(native_result):
    assert isinstance(get_adapter('planning_analytics'),SourceAdapter)
    assert get_adapter('IBM TM1').platform=='tm1'
    assert native_result.package_ir is None
    assert native_result.source_envelope['envelope_version']=='2.0'
    assert 'legacy_ir' not in native_result.source_envelope
    graph=native_result.normalized_ir
    assert graph['ir_version']=='2.0' and graph['provenance']['evidence_scope']=='SOURCE_ANALYSIS'
    assert 'legacy_ir_sha256' not in graph['provenance']
    assert graph['provenance']['source_analysis_sha256']==content_digest(graph['extensions']['source_analysis'])
    validate_tm1_normalized_ir(graph)
    with pytest.raises(UnsupportedSourceError,match='no legacy PackageIR'):native_result.to_legacy_result()
    with pytest.raises(SchemaValidationError,match='fabricated'):validate_normalized_ir(graph,legacy_ir={})


def test_explicit_bundle_header_is_detected_without_enabling_raw_native_formats():
    result=build_intake_manifest(ROOT/'examples/tm1/bundle/manifest.json',platform='tm1')
    assert result['detected_platform']=='tm1'
    assert result['files'][0]['format']=='tm1_source_bundle_v1'
    assert not any(d['code']=='UNSUPPORTED_PLATFORM' for d in result['diagnostics'])


def test_neutral_behavior_and_preservation_counts_match_portable_golden(native_result):
    graph=native_result.normalized_ir
    summary={'node_count':len(graph['nodes']),'edge_count':len(graph['edges']),
             'node_kinds':dict(sorted(Counter(n['kind'] for n in graph['nodes']).items())),
             'edge_kinds':dict(sorted(Counter(e['kind'] for e in graph['edges']).items())),
             'unit_languages':[u['analysis']['language'] for u in graph['extensions']['source_analysis']['units']],
             'status':graph['status'],'semantic_status':graph['semantic_status']}
    assert summary==json.loads((Path(__file__).parent/'fixtures/native_bundle_summary.json').read_text())


def test_native_plan_contracts_preserve_every_graph_record_once_without_execution_dependencies(native_result):
    graph=native_result.normalized_ir;original=copy.deepcopy(graph)
    plan,contracts=build_native_review_plan(graph,'SyntheticTM1Review')
    assert graph==original and len(plan['steps'])==len(contracts)==3
    validate_native_review_plan(graph,plan,contracts)
    actual={'normalized_node':[],'normalized_edge':[]}
    for contract in contracts:
        assert contract['source_platform']=='tm1' and contract['target_platform']=='databricks'
        assert contract['target_design']=={'execution_mode':'REVIEW_ONLY','deployment':'DISABLED'}
        assert contract['target_capabilities']['generation_languages']==[]
        assert contract['source_behavior']['migration_intent']=={}
        for obligation in contract['required_behavior']['obligations']:
            assert obligation['verification']=='NOT_VERIFIED' and obligation['critical'] is None and obligation['weight'] is None
            record=obligation['source_value']['record']
            actual[obligation['kind']].append(record.get('node_id') or record['edge_id'])
    assert Counter(actual['normalized_node'])==Counter(n['node_id'] for n in graph['nodes'])
    assert Counter(actual['normalized_edge'])==Counter(e['edge_id'] for e in graph['edges'])
    assert all(s['dependencies']==[] and s['metadata']['normalized_source']['ir_version']=='2.0' for s in plan['steps'])
    assert plan['metadata']['normalized_ir_binding']['unplanned_node_ids']  # Supplied collection/metadata kept for review.
    assert not any('legacy_ir_sha256' in s['metadata']['normalized_source'] for s in plan['steps'])


def test_ti_loop_guards_early_controls_and_separate_passes_survive_normalization(supplied_bundle):
    source=supplied_bundle.parent/'../ti/data.ti'
    source.write_text("WHILE(vQuantity>0); IF(vQuantity>1); ItemSkip; ELSE; vQuantity=vQuantity-1; ENDIF; END;")
    refresh(supplied_bundle)
    graph=get_adapter('tm1').analyze(supplied_bundle).normalized_ir
    flow=[e for e in graph['edges'] if e['kind']=='CONTROL_FLOW']
    assert {'LOOP_ENTER','LOOP_EXIT','LOOP_BACK','CONTROL_TRANSFER'}<={e['attributes']['kind'] for e in flow}
    transfer,=[e for e in flow if e['attributes']['kind']=='CONTROL_TRANSFER']
    assert transfer['target_id'] is None and transfer['resolution']=='UNRESOLVED'
    assert not any(e['source_id']==transfer['source_id'] and e['target_id'] is not None for e in flow)
    sections=[n for n in graph['nodes'] if n['operation']=='SECTION' and n['name'] in {'metadata','data'}]
    assert len(sections)==2 and all(n['attributes']['iteration_scope']=='SOURCE_DEPENDENT_SEPARATE_RECORD_PASS' for n in sections)
    feeder,=[e for e in graph['edges'] if e['kind']=='DECLARED_RELATION' and e['attributes']['relationship']=='DECLARED_FEEDER']
    assert feeder['attributes']['meaning']=='SPARSE_CELL_ACTIVATION_NOT_ETL_DATA_FLOW'
    assert not any(e['kind']=='DATA_FLOW' for e in graph['edges'])


def test_metadata_parameters_and_weighted_consolidations_are_typed_graph_records(native_result):
    graph=native_result.normalized_ir
    parameters=[n for n in graph['nodes'] if n['kind']=='PARAMETER']
    assert len(parameters)==6
    path=next(p for p in parameters if p['name']=='pInputFile')
    assert path['attributes']['default']=='/legacy/input.csv'
    assert path['attributes']['scope_declared_process']=='SyntheticSalesLoad'
    assert path['attributes']['runtime_value_status']=='NOT_VERIFIED'
    links=[e for e in graph['edges'] if e['kind']=='DECLARED_RELATION' and e['attributes']['relationship']=='DECLARED_CONSOLIDATION']
    assert [e['attributes']['weight'] for e in links]==['1','-0.5']
    assert all(e['attributes']['aggregation_not_evaluated'] for e in links)
    assert len([n for n in graph['nodes'] if n['operation']=='SOURCE_COLUMN_DECLARATION'])==2
    assert {n['name'] for n in graph['nodes'] if n['kind']=='CONNECTION'}=={'InputRows'}
    assert len([n for n in graph['nodes'] if n['kind']=='TARGET'])==2


def test_same_filename_and_bytes_in_different_directories_have_distinct_evidence(supplied_bundle):
    doc=json.loads(supplied_bundle.read_text());query=(supplied_bundle.parent/'../mdx/sales_axes.mdx').read_bytes()
    doc['units']=[]
    for i in range(2):
        path=supplied_bundle.parent/('folder'+str(i))/'query.mdx';path.parent.mkdir();path.write_bytes(query)
        doc['units'].append({'unit_id':'query'+str(i),'language':'mdx','path':str(path.relative_to(supplied_bundle.parent)),
                             'sha256':hashlib.sha256(query).hexdigest(),'source_cube':None,'process_name':None})
    write(supplied_bundle,doc)
    result=get_adapter('tm1').analyze(supplied_bundle)
    units=result.source_envelope['native_analysis']['units']
    assert len({u['source_file']['file_id'] for u in units})==2
    assert len(result.source_envelope['evidence_files'])==4  # Manifest, two queries, one metadata file.


@pytest.mark.parametrize('change',['node','edge','guard','dependency','source','inventory'])
def test_rehashed_graph_omissions_and_changed_guards_are_rejected(native_result,change):
    graph=copy.deepcopy(native_result.normalized_ir)
    if change=='node':
        identity=graph['nodes'][-1]['node_id'];graph['nodes'].pop()
        graph['edges']=[e for e in graph['edges'] if identity not in {e['source_id'],e['target_id']}]
    elif change=='edge':graph['edges'].pop()
    elif change=='guard':next(e for e in graph['edges'] if e['kind']=='CONTROL_FLOW' and e['attributes']['guards'])['attributes']['guards'][0]['expected']=False
    elif change=='dependency':next(n for n in graph['nodes'] if n['operation']=='SOURCE_DEPENDENCY')['attributes']['status']='VERIFIED'
    elif change=='source':graph['source']['export_version']='invented-version'
    else:graph['evidence_files'][0]['sha256']='0'*64
    graph['ir_id']=normalized_ir_id(graph)
    with pytest.raises((ValueError,SchemaValidationError)):validate_tm1_normalized_ir(graph)


def test_rehashed_source_analysis_cannot_change_literal_cube_evidence(native_result):
    bundle=copy.deepcopy(native_result.source_envelope['native_analysis'])
    unit=bundle['units'][0];unit['analysis']['query']['cube']['name']='InventedCube'
    unit['analysis']['analysis_id']=mdx_analysis_id(unit['analysis'])
    binding=bind_tm1_metadata(unit['analysis'],bundle['metadata_inputs'])
    unit['metadata_binding']={k:binding[k] for k in unit['metadata_binding']}
    bundle['bundle_id']=bundle_id(bundle)
    with pytest.raises(SchemaValidationError,match='replay'):validate_bundle(bundle,native_result.source_envelope['evidence_files'])


@pytest.mark.parametrize('change',['obligation','strategy','scope','contract'])
def test_review_plan_cannot_omit_or_promote_source_behavior(native_result,change):
    graph=native_result.normalized_ir;plan,contracts=build_native_review_plan(graph,'SyntheticTM1Review')
    if change=='obligation':plan['steps'][0]['metadata']['normalized_source']['nodes'].pop()
    elif change=='strategy':plan['steps'][0]['metadata']['migration_strategy']['strategy']='DETERMINISTIC'
    elif change=='scope':plan['metadata']['normalized_ir_binding']['assigned_node_count']=0
    else:contracts.pop()
    with pytest.raises(SchemaValidationError):validate_native_review_plan(graph,plan,contracts)


def test_generation_and_ai_routes_reject_native_contracts_before_dispatch(native_result,monkeypatch):
    from ssis_migration.generator.dispatcher import GeneratorDispatcher
    from ssis_migration.agent.orchestrator import AIMigrationOrchestrator
    from ssis_migration.migrate import _run_ai_assisted_generation
    import ssis_migration.agent.provider_factory as factory
    graph=native_result.normalized_ir;plan,_=build_native_review_plan(graph,'SyntheticTM1Review')
    def forbidden(*args,**kwargs):raise AssertionError('No provider/translator may run for native review contracts.')
    monkeypatch.setattr(factory,'create_provider',forbidden)
    step=plan['steps'][0]
    for action in [lambda:prepare_generation_step(step),lambda:GeneratorDispatcher().generate(step),
                   lambda:AIMigrationOrchestrator(object()).run(step),
                   lambda:_run_ai_assisted_generation(plan,[],Path('/tmp'),[])]:
        with pytest.raises(ValueError,match='review-only'):action()


@pytest.mark.parametrize('change',['missing','hash','duplicate_id','unknown_language','format','context','extra'])
def test_invalid_bundle_roles_hashes_and_formats_fail_closed(supplied_bundle,change):
    doc=json.loads(supplied_bundle.read_text())
    if change=='missing':doc['units'][0]['path']='missing.mdx'
    elif change=='hash':doc['units'][0]['sha256']='0'*64
    elif change=='duplicate_id':doc['units'][1]['unit_id']=doc['units'][0]['unit_id']
    elif change=='unknown_language':doc['units'][0]['language']='sql'
    elif change=='format':doc['export_format']='native_pro'
    elif change=='context':doc['units'][0]['source_cube']='MustNotOverrideMDX'
    else:doc['invented_schema']={}
    write(supplied_bundle,doc)
    with pytest.raises(ValueError):get_adapter('tm1').analyze(supplied_bundle)


def test_stale_caller_inventory_and_ssis_arguments_are_rejected(supplied_bundle):
    record=describe_file(supplied_bundle)
    supplied_bundle.write_bytes(supplied_bundle.read_bytes()+b' ')
    with pytest.raises(SourceEvidenceError):get_adapter('tm1').analyze(supplied_bundle,file_record=record)
    with pytest.raises(UnsupportedSourceError):get_adapter('tm1').analyze(supplied_bundle,procedures_path='definitions.sql')


def test_changed_nested_input_is_rejected_after_normalization(supplied_bundle,monkeypatch):
    import ssis_migration.adapters.tm1.adapter as module
    original=module.normalize_tm1_bundle
    def changed(*args,**kwargs):
        graph=original(*args,**kwargs)
        (supplied_bundle.parent/'../ti/data.ti').write_text('ChangedSource;')
        return graph
    monkeypatch.setattr(module,'normalize_tm1_bundle',changed)
    with pytest.raises(SourceEvidenceError,match='changed during analysis'):get_adapter('tm1').analyze(supplied_bundle)


def test_symlink_and_excluded_content_cannot_be_confirmed_as_tm1(supplied_bundle):
    doc=json.loads(supplied_bundle.read_text())
    source=supplied_bundle.parent/'external.dsx';source.write_text('SELECT {[A]} ON 0 FROM [C]')
    doc['units'][0].update(path=source.name,sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    write(supplied_bundle,doc)
    with pytest.raises(UnsupportedSourceError,match='excluded'):get_adapter('tm1').analyze(supplied_bundle)
    source.rename(source.with_suffix('.original'));source.symlink_to(source.with_suffix('.original'))
    with pytest.raises(SourceEvidenceError,match='non-regular'):get_adapter('tm1').analyze(supplied_bundle)


def test_unsupported_source_is_retained_and_never_counted_as_completed(supplied_bundle):
    source=supplied_bundle.parent/'../mdx/sales_axes.mdx';source.write_text('WITH MEMBER [Measures].[X] AS 1 SELECT {[Measures].[X]} ON 0 FROM [Sales]')
    refresh(supplied_bundle)
    result=get_adapter('tm1').analyze(supplied_bundle)
    assert result.normalized_ir['status']=='BLOCKED'
    unit=result.source_envelope['native_analysis']['units'][0]
    assert unit['analysis']['status']=='UNSUPPORTED' and unit['analysis']['source']['text']==source.read_text()
    plan,contracts=build_native_review_plan(result.normalized_ir,'SyntheticTM1Review')
    assert all(c['status']=='BLOCKED' and c['implementation_strategy']['decision']=='UNSUPPORTED' for c in contracts)
