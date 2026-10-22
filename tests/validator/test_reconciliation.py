import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ssis_migration.validator.reconciliation import read_observation, reconcile, validate_observation, validate_engine_reports


@pytest.fixture
def pair():
    source = {'observation_version':'1.0','observation_id':'source-case','side':'SOURCE','source_platform':'tm1',
              'case_id':'case-1','unit_id':'load','comparison_mode':'MULTISET',
              'binding':{'generation_contract_id':'contract','source_sha256':'a'*64,'artifact_hashes':{'notebook':'b'*64},
                         'input_snapshot_sha256':'c'*64,'parameters_sha256':'d'*64},
              'columns':[{'name':'id','type':'INT64','nullable':False},{'name':'amount','type':'DECIMAL','nullable':True}],
              'rows':[[1,'10.00'],[2,None],[1,'10.00']],
              'provenance':{'kind':'SOURCE_ENGINE','engine':'TM1','engine_version':'explicit-version',
                            'capture_method':'operator supplied exported results','captured_at':'2026-10-22T12:00:00Z','snapshot_id':'snapshot-1'}}
    target = copy.deepcopy(source);target.update(observation_id='target-case',side='TARGET')
    target['provenance'].update(kind='TARGET_ENGINE',engine='Databricks',engine_version='explicit-runtime')
    return source,target


def test_multiset_comparison_preserves_duplicates_nulls_and_exact_decimal_equivalence(pair):
    source,target = pair
    target['rows'] = [[2,None],[1,'+10.0'],[1,'10']]
    report = reconcile(source,target)
    assert report['status'] == 'MATCH'
    assert report['source_equivalence'] == 'MATCHED_OBSERVED_CASE_ONLY'
    assert not report['migration_accepted']
    target['rows'].pop()
    report = reconcile(source,target)
    assert report['status'] == 'MISMATCH' and report['counts']['missing_rows'] == 1


def test_order_is_compared_only_when_declared(pair):
    source,target = pair
    source['comparison_mode']=target['comparison_mode']='ORDERED'
    target['rows'] = target['rows'][1:]+target['rows'][:1]
    report = reconcile(source,target)
    assert report['status'] == 'MISMATCH' and report['counts']['missing_rows'] == 0
    assert report['counts']['order_matches'] is False


@pytest.mark.parametrize('field',['source_platform','case_id','unit_id','comparison_mode','columns','binding'])
def test_incompatible_observations_are_not_compared(pair,field):
    source,target = pair
    if field == 'source_platform': target[field]='odi'
    elif field == 'columns':target[field][1]['name']='other'
    elif field == 'comparison_mode':target[field]='ORDERED'
    elif field == 'binding':target[field]['input_snapshot_sha256']='e'*64
    else:target[field]='other'
    report = reconcile(source,target)
    assert report['status'] == 'INCOMPARABLE' and report['counts'] is None
    assert report['source_equivalence'] == 'NOT_VERIFIED'


def test_synthetic_matching_is_not_engine_equivalence(pair):
    source,target = pair
    source['provenance']['kind']='SYNTHETIC'
    report = reconcile(source,target)
    assert report['status'] == 'MATCH' and report['source_equivalence']=='NOT_VERIFIED'


@pytest.mark.parametrize('kind,first,second',[
    ('BOOLEAN',True,True),('STRING','001','001'),('DATE','2026-10-22','2026-10-22'),
    ('TIMESTAMP_UTC','2026-10-22T12:00:00Z','2026-10-22T12:00:00.000000+00:00'),
    ('DECIMAL','99999999999999999999999999999999999999','99999999999999999999999999999999999999'),
])
def test_typed_exact_values(pair,kind,first,second):
    source,target = pair
    source['columns'][1]=target['columns'][1]={'name':'value','type':kind,'nullable':False}
    source['rows']=[[1,first]];target['rows']=[[1,second]]
    assert reconcile(source,target)['status']=='MATCH'


@pytest.mark.parametrize('change',['bool_int','float_decimal','float_integer','int_overflow','decimal_exponent','decimal_precision','null','width','duplicate_column','wrong_engine','version','timestamp'])
def test_invalid_values_cannot_silently_coerce(pair,change):
    source,_ = pair
    if change == 'bool_int':source['rows'][0][0]=True
    elif change == 'float_decimal':source['rows'][0][1]=10.0
    elif change == 'float_integer':source['rows'][0][0]=1.0
    elif change == 'int_overflow':source['rows'][0][0]=1<<63
    elif change == 'decimal_exponent':source['rows'][0][1]='1e400'
    elif change == 'decimal_precision':source['rows'][0][1]='1'*39
    elif change == 'null':source['rows'][0][0]=None
    elif change == 'width':source['rows'][0].append(3)
    elif change == 'duplicate_column':source['columns'][1]['name']='ID'
    elif change == 'wrong_engine':source['provenance']['kind']='TARGET_ENGINE'
    elif change == 'version':source['observation_version']='99'
    else:source['provenance']['captured_at']='2026-10-22T12:00:00'
    with pytest.raises(ValueError):validate_observation(source)


@pytest.mark.parametrize('text',['{"rows":[],"rows":[]}','{"number":NaN}','{"number":1e999}'])
def test_observation_reader_rejects_ambiguous_or_nonfinite_json(tmp_path,text):
    path=tmp_path/'observation.json';path.write_text(text)
    with pytest.raises(ValueError):read_observation(path)


def engine_reports(tmp_path,pair):
    source,target=pair
    refs={}
    for side,doc in [('source',source),('target',target)]:
        path=tmp_path/(side+'.json');path.write_text(json.dumps(doc))
        refs[side]={'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    report=reconcile(source,target);report['observation_files']=refs
    path=tmp_path/'reconciliation.json';path.write_text(json.dumps(report))
    package={'source_platform':'tm1','unit_ids':['load'],
             'observation_binding':{k:source['binding'][k] for k in ('generation_contract_id','source_sha256','artifact_hashes')}}
    return [{'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}],package


def test_native_gate_replays_original_engine_captures(tmp_path,pair):
    refs,package=engine_reports(tmp_path,pair)
    validate_engine_reports(refs,report_root=tmp_path,package=package)


@pytest.mark.parametrize('change',['synthetic','missing_unit','changed_source','stale_binding','edited_report','external'])
def test_native_gate_rejects_unverified_or_tampered_engine_reports(tmp_path,pair,change):
    if change=='synthetic':pair[0]['provenance']['kind']='SYNTHETIC'
    refs,package=engine_reports(tmp_path,pair)
    if change=='missing_unit':package['unit_ids'].append('query')
    elif change=='changed_source':(tmp_path/'source.json').write_text('{}')
    elif change=='stale_binding':package['observation_binding']['source_sha256']='f'*64
    elif change=='edited_report':
        doc=json.loads((tmp_path/'reconciliation.json').read_text());doc['counts']['source_rows']=99
        (tmp_path/'reconciliation.json').write_text(json.dumps(doc));refs[0]['sha256']=hashlib.sha256((tmp_path/'reconciliation.json').read_bytes()).hexdigest()
    elif change=='external':refs[0]['path']='../outside.json'
    with pytest.raises(ValueError):validate_engine_reports(refs,report_root=tmp_path,package=package)


def test_cli_compares_files_without_running_workloads_and_refuses_overwrite(tmp_path,pair):
    for name,doc in zip(('source','target'),pair):(tmp_path/(name+'.json')).write_text(json.dumps(doc))
    args=[sys.executable,'-m','ssis_migration.validator.reconciliation','--source-observation',str(tmp_path/'source.json'),
          '--target-observation',str(tmp_path/'target.json'),'--output',str(tmp_path/'result.json')]
    result=subprocess.run(args,capture_output=True,text=True)
    assert result.returncode==0 and result.stdout.strip()=='MATCH'
    assert json.loads((tmp_path/'result.json').read_text())['migration_accepted'] is False
    assert subprocess.run(args,capture_output=True,text=True).returncode==2
