import hashlib
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from ssis_migration.languages.csharp.compiler import compile_csharp
from ssis_migration.schemas import SchemaValidationError


def record(path):
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def inputs(tmp_path):
    source=tmp_path/'Source.cs'; source.write_text('public class C {}')
    reference=tmp_path/'Reference.dll'; reference.write_bytes(b'explicit fake metadata for unit tests only')
    return {'request_version':'1.0','scope':'SUPPLIED_REFERENCES','sdk_version':'8.0.426',
            'language_version':'12.0','check_overflow':False,'output_kind':'LIBRARY',
            'sources':[record(source)],'references':[record(reference)]}


def test_missing_toolchain_remains_blocked_with_inputs_and_no_execution(tmp_path, inputs, monkeypatch):
    monkeypatch.setattr('ssis_migration.languages.csharp.compiler.shutil.which',lambda _:None)
    report=compile_csharp(inputs,tmp_path/'out')
    assert report['status']=='BLOCKED'
    assert report['source_execution']=='NOT_RUN'
    assert report['migration_accepted'] is False
    assert report['sources'][0]['sha256']==inputs['sources'][0]['sha256']
    assert (tmp_path/'out'/'compilation_report.json').is_file()


@pytest.mark.parametrize('change',['source','reference','symlink'])
def test_missing_changed_or_symlink_inputs_do_not_reach_compiler(tmp_path, inputs, monkeypatch, change):
    def unexpected(*args,**kwargs):
        raise AssertionError('Toolchain must not run before evidence is checked.')
    monkeypatch.setattr('ssis_migration.languages.csharp.compiler.subprocess.run',unexpected)
    if change=='symlink':
        link=tmp_path/'linked.cs'; link.symlink_to(inputs['sources'][0]['path'])
        inputs['sources'][0]['path']=str(link)
    else:
        collection='sources' if change=='source' else 'references'
        Path(inputs[collection][0]['path']).write_text('changed')
    report=compile_csharp(inputs,tmp_path/'out')
    assert report['status']=='BLOCKED'
    assert not report['artifacts']


def test_output_directory_is_never_reused_or_overwritten(tmp_path, inputs):
    output=tmp_path/'out'; output.mkdir(); existing=output/'keep'; existing.write_text('keep')
    with pytest.raises(FileExistsError):
        compile_csharp(inputs,output)
    assert existing.read_text()=='keep'


@pytest.mark.parametrize('change',['project','analyzers','bad_sdk','missing_refs','console_entry'])
def test_request_schema_does_not_admit_arbitrary_build_arguments(tmp_path, inputs, change):
    if change=='project': inputs['project']='untrusted.csproj'
    elif change=='analyzers': inputs['analyzers']=['payload.dll']
    elif change=='bad_sdk': inputs['sdk_version']='8.0.426 /analyzer:payload.dll'
    elif change=='missing_refs': inputs['references']=[]
    else: inputs['output_kind']='CONSOLE'
    with pytest.raises(SchemaValidationError):
        compile_csharp(inputs,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def mocked_sdk(tmp_path, monkeypatch, callback):
    root=tmp_path/'sdk'; compiler=root/'8.0.426'/'Roslyn'/'bincore'/'csc.dll'
    compiler.parent.mkdir(parents=True); compiler.write_bytes(b'compiler identity only; not executable')
    monkeypatch.setattr('ssis_migration.languages.csharp.compiler.shutil.which',lambda _:str(tmp_path/'dotnet'))
    def run(command,**kwargs):
        if '--list-sdks' in command:
            return SimpleNamespace(returncode=0,stdout='8.0.426 ['+str(root)+']\n',stderr='')
        return callback(command,kwargs)
    monkeypatch.setattr('ssis_migration.languages.csharp.compiler.subprocess.run',run)


def test_timeout_cannot_produce_a_compilation_pass(tmp_path, inputs, monkeypatch):
    def timeout(command,kwargs): raise subprocess.TimeoutExpired(command,1)
    mocked_sdk(tmp_path,monkeypatch,timeout)
    report=compile_csharp(inputs,tmp_path/'out',timeout_seconds=1)
    assert report['status']=='TIMEOUT'
    assert 'assembly' not in report['artifacts']


def test_mutated_original_after_compilation_invalidates_result(tmp_path, inputs, monkeypatch):
    def mutate(command,kwargs):
        destination=next(arg[len('/out:'):] for arg in command if arg.startswith('/out:'))
        Path(destination).write_bytes(b'fake assembly')
        Path(inputs['sources'][0]['path']).write_text('changed during compile')
        return SimpleNamespace(returncode=0)
    mocked_sdk(tmp_path,monkeypatch,mutate)
    report=compile_csharp(inputs,tmp_path/'out')
    assert report['status']=='BLOCKED'
    assert 'assembly' not in report['artifacts']


def test_compiler_command_disables_implicit_references_and_never_executes_assembly(tmp_path, inputs, monkeypatch):
    observed=[]
    def compile_only(command,kwargs):
        observed.append(command)
        Path(next(arg[len('/out:'):] for arg in command if arg.startswith('/out:'))).write_bytes(b'unit fixture only')
        return SimpleNamespace(returncode=0)
    mocked_sdk(tmp_path,monkeypatch,compile_only)
    report=compile_csharp(inputs,tmp_path/'out')
    assert report['status']=='PASS'
    assert len(observed)==1
    command=observed[0]
    assert {'/noconfig','/nostdlib+','/unsafe-','/checked-','/langversion:12.0','/target:library'} <= set(command)
    assert report['source_execution']=='NOT_RUN'
    assert not any(arg in {'build','restore','run'} for arg in command)


@pytest.mark.parametrize('changed',['source','reference','assembly','log','request','external'])
def test_recorded_compilation_artifacts_are_hash_checked_before_use(tmp_path, inputs, monkeypatch, changed):
    from ssis_migration.languages.csharp.compiler import verify_compilation_artifacts
    def compiled(command,kwargs):
        Path(next(arg[len('/out:'):] for arg in command if arg.startswith('/out:'))).write_bytes(b'unit-only assembly')
        return SimpleNamespace(returncode=0)
    mocked_sdk(tmp_path,monkeypatch,compiled)
    output=tmp_path/'out'
    report=compile_csharp(inputs,output)
    verify_compilation_artifacts(report,output)
    if changed=='external':
        external=tmp_path/'external.dll'; external.write_bytes(b'unit-only assembly')
        report['artifacts']['assembly']['path']=str(external)
    else:
        path={'source':'sources-0.cs','reference':'references-0.dll','assembly':'compiled.dll','log':'compiler.log','request':'request.json'}[changed]
        if changed=='request':
            import json
            request=json.loads((output/path).read_text()); request['check_overflow']=True
            (output/path).write_text(json.dumps(request))
        else:
            (output/path).write_text('changed after compile')
    with pytest.raises((ValueError,OSError)):
        verify_compilation_artifacts(report,output)
