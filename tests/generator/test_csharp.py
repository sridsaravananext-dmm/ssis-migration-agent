import copy
import hashlib
from pathlib import Path

import pytest

from ssis_migration.adapters.ssis import SSISAdapter
from ssis_migration.generator.csharp import generate_csharp_candidate
from ssis_migration.generator.dispatcher import GeneratorDispatcher
from ssis_migration.migration import bind_plan_to_ir, build_migration_contract, validate_contract
from ssis_migration.schemas import SchemaValidationError

from csharp_fixtures import make_contract


def generate(tmp_path, body, **kwargs):
    contract, _ = make_contract(tmp_path, body, **kwargs)
    return generate_csharp_candidate(contract)


def run_candidate(artifact, variables):
    assert artifact.artifact_type == 'CODE', artifact.metadata
    # Only generated pure fixture functions execute here; no source assembly,
    # Spark workload, network call, filesystem effect or deployment runs.
    namespace = {}
    exec(compile(artifact.code, '<unit-csharp-candidate>', 'exec'), namespace)
    return namespace['run_ssis_script'](variables)


def test_typed_branch_translation_with_explicit_binding_and_no_auto_invocation(tmp_path):
    contract, step = make_contract(tmp_path, '''int n = (int)Dts.Variables["User::N"].Value;
if (n > 0 && n < 10) { Dts.Variables["User::Out"].Value = n + 1; }
else { Dts.Variables["User::Out"].Value = -1; }
Dts.TaskResult=0;''')
    before = copy.deepcopy(contract)
    artifact = GeneratorDispatcher().generate({**step, 'metadata':{**step['metadata'],'migration_contract':contract}})
    assert artifact.metadata['status'] == 'REVIEW_REQUIRED'
    assert artifact.metadata['semantic_status'] == 'NOT_VERIFIED'
    assert artifact.metadata['migration_contract_id'] == contract['contract_id']
    assert artifact.metadata['lowered_program']['source_sha256'] == hashlib.sha256(
        contract['source_behavior']['legacy_step']['metadata']['normalized_source']['nodes'][0]['record']['attributes']['script_assessment']['files'][0]['assessment']['source']['text'].encode()).hexdigest()
    for value, expected in [(-1,-1),(0,-1),(1,2),(9,10),(10,-1)]:
        variables = {'User::N':value,'User::Out':0}
        assert run_candidate(artifact, variables) == 0
        assert variables['User::Out'] == expected
    assert contract == before


@pytest.mark.parametrize('expression,input_value,expected', [
    ('n + 1',2147483647,-2147483648), ('n - 1',-2147483648,2147483647),
    ('n * 2',2147483647,-2), ('-n',-2147483648,-2147483648),
    ('n + -1',0,-1), ('-2147483648',0,-2147483648),
])
def test_int32_runtime_wrapping_matches_explicit_unchecked_boundary_cases(tmp_path, expression, input_value, expected):
    artifact = generate(tmp_path, 'int n=(int)Dts.Variables["User::N"].Value; Dts.Variables["User::Out"].Value=' + expression + '; Dts.TaskResult=0;')
    variables = {'User::N':input_value,'User::Out':0}
    run_candidate(artifact, variables)
    assert variables['User::Out'] == expected


def test_boolean_string_and_short_circuit_translation(tmp_path):
    artifact = generate(tmp_path, '''bool yes=(bool)Dts.Variables["User::Flag"].Value;
string s=(string)Dts.Variables["User::Text"].Value;
if (!yes || s == "ready") { Dts.Variables["User::Out"].Value = true; }
else { Dts.Variables["User::Out"].Value = false; } Dts.TaskResult=0;''',
        bindings={'User::Flag':{'type':'bool','access':'READ'},'User::Text':{'type':'string','access':'READ'},'User::Out':{'type':'bool','access':'WRITE'}})
    for flag, text, expected in [(True,'ready',True),(True,'other',False),(False,'other',True)]:
        variables = {'User::Flag':flag,'User::Text':text,'User::Out':False}
        run_candidate(artifact, variables)
        assert variables['User::Out'] is expected


@pytest.mark.parametrize('value', [True, 2147483648, None, '1', 1.0])
def test_runtime_values_cannot_silently_coerce_to_supplied_int32_type(tmp_path, value):
    artifact = generate(tmp_path, 'int n=(int)Dts.Variables["User::N"].Value; Dts.Variables["User::Out"].Value=n; Dts.TaskResult=0;')
    variables = {'User::N':value,'User::Out':0}
    with pytest.raises(TypeError):
        run_candidate(artifact, variables)
    assert variables['User::Out'] == 0


@pytest.mark.parametrize('body', [
    'File.Delete(path); Dts.TaskResult=0;',
    'CallCustom(); Dts.TaskResult=0;',
    'for(int n=0;n<3;n++) {} Dts.TaskResult=0;',
    'try {} catch {} Dts.TaskResult=0;',
    'int n = 2147483647+1; Dts.TaskResult=0;',
    'long n = 1; Dts.TaskResult=0;',
    'int n=(int)Dts.Variables["Missing"].Value; Dts.TaskResult=0;',
    'Dts.Variables["User::N"].Value=1; Dts.TaskResult=0;',
    'string n=(string)Dts.Variables["User::N"].Value; Dts.TaskResult=0;',
    'int n=(int)Dts.Variables["User::N"].Value; n/=2; Dts.TaskResult=0;',
    'int n=(int)Dts.Variables["User::N"].Value; int x=n/2; Dts.TaskResult=0;',
    'if(true) {Dts.TaskResult=0;} Dts.TaskResult=0;',
    'Dts.TaskResult=1;',
    'Dts.TaskResult=0; int n=1;',
    'int Dts=1; Dts.TaskResult=0;',
    'string s=$"{1}"; Dts.TaskResult=0;',
    'checked { int n=1; } Dts.TaskResult=0;',
    'int n = ; Dts.TaskResult=0;',
])
def test_unsupported_behavior_anywhere_prevents_partial_code_emission(tmp_path, body):
    artifact = generate(tmp_path, body)
    assert artifact.artifact_type == 'UNRESOLVED'
    assert artifact.code == ''
    assert artifact.metadata['unresolved_items']


@pytest.mark.parametrize('extras', [
    {'extra_members':'static int n = Call();'},
    {'extra_members':'void Hidden(){ File.Delete(path); }'},
    {'prefix':'using Alias = Other.Type;'},
    {'prefix':'#define FLAG\n'},
    {'strategy':'UNSUPPORTED'},
    {'strategy':'AI_ASSISTED'},
])
def test_project_features_and_existing_strategy_are_never_discarded_or_promoted(tmp_path, extras):
    artifact = generate(tmp_path, 'Dts.TaskResult=0;', **extras)
    assert artifact.artifact_type == 'UNRESOLVED'
    assert artifact.code == ''


def test_stale_contract_inputs_rejected_before_generation(tmp_path):
    contract, _ = make_contract(tmp_path, 'Dts.TaskResult=0;')
    contract['source_behavior']['legacy_step']['metadata']['csharp_translation']['runtime']['success_value']=2
    with pytest.raises(SchemaValidationError):
        generate_csharp_candidate(contract)


def rebuild(contract):
    return build_migration_contract(copy.deepcopy(contract['source_behavior']['legacy_step']),
                                    source_platform='ssis', evidence_files=contract['evidence_files']).to_dict()


@pytest.mark.parametrize('mutation', ['hash','locator','evidence','entry','permission','type','scope','multiple_sources','missing_spec'])
def test_rehashed_contract_cannot_bypass_binding_or_source_checks(tmp_path, mutation):
    contract, _ = make_contract(tmp_path, 'int n=(int)Dts.Variables["User::N"].Value; Dts.TaskResult=0;')
    metadata = contract['source_behavior']['legacy_step']['metadata']
    spec = metadata['csharp_translation']
    if mutation == 'hash':
        spec['source_sha256']='a'*64
    elif mutation == 'locator':
        spec['source_locator']='xml:/999'
    elif mutation == 'evidence':
        spec['runtime']['evidence'][0]['file_id']='missing-file'
    elif mutation == 'entry':
        spec['entry_point']='Demo.Other.Main'
    elif mutation == 'permission':
        spec['variables']['User::N']['access']='WRITE'
    elif mutation == 'type':
        spec['variables']['User::N']['type']='bool'
    elif mutation == 'scope':
        metadata['normalized_source']['nodes'][0]['obligation_scope']='PACKAGE_REVIEW'
    elif mutation == 'multiple_sources':
        files=metadata['normalized_source']['nodes'][0]['record']['attributes']['script_assessment']['files']
        files.append(copy.deepcopy(files[0]))
    else:
        metadata['csharp_translation']=None
    artifact = generate_csharp_candidate(rebuild(contract))
    assert artifact.artifact_type == 'UNRESOLVED'
    assert artifact.code == ''


def test_exact_primitive_types_and_non_null_strings_are_guarded(tmp_path):
    artifact = generate(tmp_path, 'string s=(string)Dts.Variables["User::Text"].Value; Dts.TaskResult=0;',
                        bindings={'User::Text':{'type':'string','access':'READ'}})
    for value in [None, 1, '\ud800']:
        with pytest.raises(TypeError):
            run_candidate(artifact, {'User::Text':value})


def test_block_scopes_preserve_outer_values_and_do_not_leak_locals(tmp_path):
    artifact = generate(tmp_path, '''int n=3;
if(true) { int x=4; n=x; } else { int x=5; n=x; }
Dts.Variables["User::Out"].Value=n; Dts.TaskResult=0;''')
    variables={'User::Out':0}
    run_candidate(artifact, variables)
    assert variables['User::Out']==4
    bad = generate(tmp_path, 'if(true) { int x=4; } Dts.Variables["User::Out"].Value=x; Dts.TaskResult=0;')
    assert bad.artifact_type == 'UNRESOLVED'
