import copy
import hashlib

import pytest

from csharp_fixtures import make_contract
from ssis_migration.generator.csharp import generate_csharp_candidate
from ssis_migration.validator.csharp_equivalence import compare_csharp_traces, document_digest
from ssis_migration.schemas import SchemaValidationError


@pytest.fixture
def observations(tmp_path):
    contract,_=make_contract(tmp_path,'int n=(int)Dts.Variables["User::N"].Value; Dts.Variables["User::Out"].Value=n+1; Dts.TaskResult=0;')
    candidate=generate_csharp_candidate(contract)
    # Unit-test report only. Real compiler/execution integration is tested in
    # test_csharp_compiled.py; this factory does not assert actual execution.
    compilation={'compilation_version':'1.0','status':'PASS','scope':'FIXTURE_STUB_RUNTIME',
                 'request_sha256':'a'*64,'toolchain':{'sdk_version':'8.0.426','dotnet':'/fixture/dotnet','compiler_sha256':'b'*64},
                 'options':{'language_version':'12.0','check_overflow':False,'output_kind':'CONSOLE'},
                 'sources':[{'path':'/fixture/Source.cs','sha256':candidate.metadata['source_sha256'],'snapshot_path':'sources-0.cs'}],
                 'references':[{'path':'/fixture/Reference.dll','sha256':'c'*64,'snapshot_path':'references-0.dll'}],
                 'diagnostics':[],'exit_code':0,'artifacts':{'assembly':{'path':'compiled.dll','sha256':'d'*64},'compiler_log':{'path':'compiler.log','sha256':'e'*64}},'source_execution':'NOT_RUN',
                 'semantic_status':'NOT_VERIFIED','runtime_binding_status':'NOT_VERIFIED','migration_accepted':False,
                 'limitations':['Unit fixture observation only.']}
    common={'trace_version':'1.0','contract_id':contract['contract_id'],'source_sha256':candidate.metadata['source_sha256'],
            'candidate_sha256':hashlib.sha256(candidate.code.encode()).hexdigest(),
            'compilation_report_sha256':document_digest(compilation)}
    cases=[{'case_id':'one','input_variables':{'User::N':1,'User::Out':0},
            'outcome':{'status':'RETURNED','task_result':0,'variables':{'User::N':1,'User::Out':2},'exception':None}}]
    source={**common,'side':'SOURCE','runtime_scope':'FIXTURE_STUB_RUNTIME','runtime_description':'unit fixture','cases':copy.deepcopy(cases)}
    target={**common,'side':'TARGET','runtime_scope':'PYTHON_DRIVER','runtime_description':'unit fixture','cases':copy.deepcopy(cases)}
    return contract,compilation,source,target


def test_match_is_only_observed_cases_and_cannot_award_weighted_acceptance(observations):
    result=compare_csharp_traces(*observations)
    assert result['status']=='MATCH'
    assert result['scope']=='OBSERVED_CASES_ONLY'
    assert result['matched_case_count']==result['case_count']==1
    assert result['migration_accepted'] is False
    assert result['weighted_semantic_coverage'] is None


@pytest.mark.parametrize('change',['contract','source','candidate','compilation','scope','missing_case','duplicate_case','compile_failed','checked_context'])
def test_stale_incomplete_or_conflicting_observations_block_comparison(observations,change):
    contract,compilation,source,target=observations
    if change in {'contract','source','candidate','compilation'}:
        key={'contract':'contract_id','source':'source_sha256','candidate':'candidate_sha256','compilation':'compilation_report_sha256'}[change]
        target[key]='f'*64
    elif change=='scope': source['runtime_scope']='SUPPLIED_REFERENCES'
    elif change=='missing_case': target['cases'][0]['case_id']='other'
    elif change=='duplicate_case': source['cases'].append(copy.deepcopy(source['cases'][0]))
    elif change=='compile_failed': compilation['status']='FAIL'
    else: compilation['options']['check_overflow']=True
    result=compare_csharp_traces(contract,compilation,source,target)
    assert result['status']=='BLOCKED'
    assert result['diagnostics']


@pytest.mark.parametrize('change',['value','bool_is_not_int','input','missing_output','readonly','exception','wrong_success'])
def test_state_type_and_result_differences_are_reported_without_partial_acceptance(observations,change):
    contract,compilation,source,target=observations
    outcome=target['cases'][0]['outcome']
    if change=='value': outcome['variables']['User::Out']=3
    elif change=='bool_is_not_int': outcome['variables']['User::N']=True
    elif change=='input': target['cases'][0]['input_variables']['User::N']=2
    elif change=='missing_output': outcome['variables'].pop('User::Out')
    elif change=='readonly': outcome['variables']['User::N']=99
    elif change=='exception': outcome.update(status='THREW',task_result=None,exception='TypeError')
    else: outcome['task_result']=1
    report=compare_csharp_traces(contract,compilation,source,target)
    assert report['status']=='MISMATCH'
    assert report['matched_case_count']==0
    assert report['case_results'][0]['issues']


def test_matching_exceptions_do_not_count_as_verified_success(observations):
    contract,compilation,source,target=observations
    for trace in [source,target]:
        trace['cases'][0]['outcome'].update(status='THREW',task_result=None,exception='fixture error')
    report=compare_csharp_traces(contract,compilation,source,target)
    assert report['status']=='MISMATCH'


def test_empty_case_set_is_rejected_instead_of_vacuous_equivalence(observations):
    contract,compilation,source,target=observations
    source['cases']=[]; target['cases']=[]
    with pytest.raises(SchemaValidationError):
        compare_csharp_traces(contract,compilation,source,target)
