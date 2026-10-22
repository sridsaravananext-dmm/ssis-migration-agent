"""Real Roslyn + .NET differential fixtures; no external user code executes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from csharp_fixtures import make_contract
from ssis_migration.generator.csharp import generate_csharp_candidate
from ssis_migration.languages.csharp.compiler import compile_csharp
from ssis_migration.languages.csharp import parse_csharp
from ssis_migration.validator.csharp_equivalence import compare_csharp_traces, document_digest

FIXTURES = Path(__file__).resolve().parents[1]/'fixtures'/'csharp'


def record(path):
    return {'path':str(path.absolute()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture(scope='module')
def sdk():
    executable = os.environ.get('SSIS_TEST_DOTNET') or shutil.which('dotnet')
    if not executable:
        pytest.skip('Real C# integration requires an installed .NET 8 SDK; set SSIS_TEST_DOTNET.')
    text = subprocess.run([executable,'--list-sdks'],check=True,capture_output=True,text=True,timeout=15).stdout
    choices = [re.fullmatch(r'(8\.0\.\d+) \[(.+)\]',line.strip()) for line in text.splitlines()]
    choices = [match for match in choices if match]
    if not choices:
        pytest.skip('Real C# integration requires a .NET 8 SDK.')
    chosen = choices[-1]
    root = Path(chosen[2]).parent
    packs = sorted((root/'packs'/'Microsoft.NETCore.App.Ref').glob('8.0.*/ref/net8.0'))
    assert packs, 'Installed SDK lacks the explicitly required .NET 8 reference pack.'
    return {'dotnet':executable,'version':chosen[1], 'references':[record(p) for p in sorted(packs[-1].glob('*.dll'))]}


def request(sdk, sources, **changes):
    return {'request_version':'1.0','scope':'FIXTURE_STUB_RUNTIME','sdk_version':sdk['version'],
            'language_version':'12.0','check_overflow':False,'output_kind':'LIBRARY',
            'sources':[record(path) for path in sources],'references':sdk['references'], **changes}


def source_text(contract):
    return contract['source_behavior']['legacy_step']['metadata']['normalized_source']['nodes'][0]['record']['attributes']['script_assessment']['files'][0]['assessment']['source']['text']


@pytest.mark.parametrize('body,inputs,bindings', [
    ('int n=(int)Dts.Variables["User::N"].Value; Dts.Variables["User::Out"].Value=n+1; Dts.TaskResult=0;',
     [{'User::N':n,'User::Out':0} for n in [-2147483648,-1,0,1,2147483647]], None),
    ('int n=(int)Dts.Variables["User::N"].Value; Dts.Variables["User::Out"].Value=n-1; Dts.TaskResult=0;',
     [{'User::N':n,'User::Out':0} for n in [-2147483648,-1,0,2147483647]], None),
    ('int n=(int)Dts.Variables["User::N"].Value; Dts.Variables["User::Out"].Value=n*2; Dts.TaskResult=0;',
     [{'User::N':n,'User::Out':0} for n in [-2147483648,-1,0,1073741824,2147483647]], None),
    ('int n=(int)Dts.Variables["User::N"].Value; Dts.Variables["User::Out"].Value=-n; Dts.TaskResult=0;',
     [{'User::N':n,'User::Out':0} for n in [-2147483648,-1,0,2147483647]], None),
    ('int n=(int)Dts.Variables["User::N"].Value; if(n>0 && n<10) { Dts.Variables["User::Out"].Value=n+1; } else { Dts.Variables["User::Out"].Value=-1; } Dts.TaskResult=0;',
     [{'User::N':n,'User::Out':0} for n in [-1,0,1,9,10]], None),
    ('bool b=(bool)Dts.Variables["User::Flag"].Value; string s=(string)Dts.Variables["User::Text"].Value; if(!b || s=="ready") { Dts.Variables["User::Out"].Value=true; } else { Dts.Variables["User::Out"].Value=false; } Dts.TaskResult=0;',
     [{'User::Flag':b,'User::Text':s,'User::Out':False} for b in [True,False] for s in ['ready','other','😀']],
     {'User::Flag':{'type':'bool','access':'READ'},'User::Text':{'type':'string','access':'READ'},'User::Out':{'type':'bool','access':'WRITE'}}),
])
def test_real_compiled_csharp_and_generated_python_agree_on_controlled_cases(tmp_path, sdk, body, inputs, bindings):
    contract, _ = make_contract(tmp_path, body, bindings=bindings)
    candidate = generate_csharp_candidate(contract)
    assert candidate.artifact_type == 'CODE'
    source = tmp_path/'ScriptMain.cs'
    source.write_text(source_text(contract))
    output = tmp_path/'compiled'
    compilation = compile_csharp(request(sdk, [source,FIXTURES/'FixtureRuntime.cs',FIXTURES/'FixtureRunner.cs'],
                                         output_kind='CONSOLE',main_type='FixtureRunner'), output, dotnet=sdk['dotnet'])
    assert compilation['status']=='PASS', compilation['diagnostics']
    cases=[{'case_id':str(i),'input_variables':variables} for i,variables in enumerate(inputs)]
    cases_file=tmp_path/'cases.json'
    cases_file.write_text(json.dumps(cases))
    (output/'compiled.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'net8.0','framework':{'name':'Microsoft.NETCore.App','version':'8.0.0'}}}))
    # Only this repository's bounded fixture source and trusted stub/runner run.
    observed=subprocess.run([sdk['dotnet'],str(output/'compiled.dll'),str(cases_file)],
                            cwd=output,check=True,capture_output=True,text=True,timeout=15)
    source_cases=json.loads(observed.stdout)
    namespace={}
    exec(compile(candidate.code,'<controlled-fixture>','exec'),namespace)
    target_cases=[]
    for case in cases:
        state=copy.deepcopy(case['input_variables'])
        success=namespace['run_ssis_script'](state)
        target_cases.append({**case,'outcome':{'status':'RETURNED','task_result':success,'variables':state,'exception':None}})
    common={'trace_version':'1.0','contract_id':contract['contract_id'],'source_sha256':candidate.metadata['source_sha256'],
            'candidate_sha256':hashlib.sha256(candidate.code.encode()).hexdigest(),
            'compilation_report_sha256':document_digest(compilation)}
    source_trace={**common,'side':'SOURCE','runtime_scope':'FIXTURE_STUB_RUNTIME','runtime_description':'.NET 8 with explicit test-only Dts stubs','cases':source_cases}
    target_trace={**common,'side':'TARGET','runtime_scope':'PYTHON_DRIVER','runtime_description':'Local Python pure fixture function','cases':target_cases}
    report=compare_csharp_traces(contract,compilation,source_trace,target_trace,compilation_root=output)
    assert report['compilation_artifacts_verified'] is True
    assert report['status']=='MATCH',report
    assert report['matched_case_count']==len(cases)
    assert report['runtime_scope']=='FIXTURE_STUB_RUNTIME'
    assert report['migration_accepted'] is False and report['weighted_semantic_coverage'] is None
    for name,data in [('migration_contract',contract),('source_trace',source_trace),('target_trace',target_trace),('equivalence_report',report)]:
        (tmp_path/(name+'.json')).write_text(json.dumps(data,indent=2))


def test_roslyn_rejects_syntactically_valid_but_unbound_symbols(tmp_path, sdk):
    source=tmp_path/'Unbound.cs'
    source.write_text('public class C { public int M() { return MissingSymbol; } }')
    assert parse_csharp(source.read_text(),file_id='f',locator='file:/')['status']=='PARSED'
    report=compile_csharp(request(sdk,[source]),tmp_path/'output',dotnet=sdk['dotnet'])
    assert report['status']=='FAIL'
    assert any('CS0103' in message for message in report['diagnostics'])
    assert report['source_execution']=='NOT_RUN'


def test_no_ssis_reference_is_invented_for_compilation(tmp_path, sdk):
    contract,_=make_contract(tmp_path,'Dts.TaskResult=0;')
    source=tmp_path/'NoRuntime.cs'
    source.write_text(source_text(contract))
    report=compile_csharp(request(sdk,[source],scope='SUPPLIED_REFERENCES'),tmp_path/'output',dotnet=sdk['dotnet'])
    assert report['status']=='FAIL'
    assert report['references']==[{**r,'snapshot_path':'references-'+str(i)+'.dll'} for i,r in enumerate(sdk['references'])]
