"""Source metadata supplies defaults without guessing scripts or leaking secrets."""
import base64
import json
from types import SimpleNamespace
import pytest

from ssis_migration.bound_sql import BindingContext,runtime_setup
from ssis_migration.ir.builder import IRBuilder
from ssis_migration.parser.package_parser import SSISPackageParser


def parse(tmp_path,content):
    source=tmp_path/'source.dtsx'
    source.write_text('<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:refId="Package">'+content+'</DTS:Executable>')
    return IRBuilder().build(SSISPackageParser(source).parse())


def test_package_parameter_defaults_required_and_sensitive_behavior(tmp_path):
    ir=parse(tmp_path,'''<DTS:PackageParameters>
      <DTS:PackageParameter DTS:ObjectName="Default" DTS:DataType="3"><DTS:Property DTS:Name="ParameterValue">41</DTS:Property></DTS:PackageParameter>
      <DTS:PackageParameter DTS:ObjectName="Required" DTS:DataType="3" DTS:Required="1"><DTS:Property DTS:Name="ParameterValue">42</DTS:Property></DTS:PackageParameter>
      <DTS:PackageParameter DTS:ObjectName="Secret" DTS:DataType="8" DTS:Sensitive="-1"><DTS:Property DTS:Name="ParameterValue">never-export-me</DTS:Property></DTS:PackageParameter>
    </DTS:PackageParameters>''')
    runtime=BindingContext(ir)
    expressions={name:runtime.read('$Package::'+name,'Package') for name in ['Default','Required','Secret']}
    assert runtime.defaults=={'$Package::Default':'41'}
    assert 'never-export-me' not in json.dumps(ir.metadata)
    env={'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda key:'{}'))}
    exec(runtime_setup(runtime.defaults),env)
    exec('result = '+expressions['Default'],env)
    assert env['result']==41
    for name in ['Required','Secret']:
        with pytest.raises(RuntimeError,match='Missing SSIS runtime value'):
            exec('result = '+expressions[name],env)


@pytest.mark.parametrize('encoding',['plain','utf8','utf16'])
def test_script_source_is_captured_as_review_data_not_executable_python(tmp_path,encoding):
    program='// source program\npublic void Main() { /* source behavior */ }'
    raw=program.encode('utf-16' if encoding=='utf16' else 'utf-8')
    value=program if encoding=='plain' else base64.b64encode(raw).decode()
    attribute='' if encoding=='plain' else ' Encoding="Base64"'
    ir=parse(tmp_path,'<DTS:Executables><DTS:Executable DTS:refId="Script" DTS:ExecutableType="Microsoft.ScriptTask"><DTS:ObjectData>'
        '<ScriptTaskData><ProjectItem Name="ScriptMain.cs"'+attribute+'>'+value+'</ProjectItem>'
        '<BinaryItem Name="Script.dll">compiled-binary</BinaryItem></ScriptTaskData>'
        '</DTS:ObjectData></DTS:Executable></DTS:Executables>')
    props=ir.executables[0].properties
    assert props['script_sources'][0]['text']==program
    assert props['script_sources'][0]['status']=='CAPTURED'
    assert props['script_binaries'][0]['encoded_sha256']
    assert 'compiled-binary' not in json.dumps(props)


def test_corrupt_script_encoding_remains_a_source_gap(tmp_path):
    ir=parse(tmp_path,'''<DTS:Executables><DTS:Executable DTS:refId="Script" DTS:ExecutableType="Microsoft.ScriptTask"><DTS:ObjectData>
      <ScriptTaskData><ProjectItem Name="ScriptMain.cs" Encoding="Base64">not-base64!</ProjectItem></ScriptTaskData>
    </DTS:ObjectData></DTS:Executable></DTS:Executables>''')
    source=ir.executables[0].properties['script_sources'][0]
    assert source['status']=='ENCODING_REVIEW_REQUIRED' and source['text'] is None


def test_script_project_access_metadata_is_captured(tmp_path):
    ir=parse(tmp_path,'''<DTS:Executables><DTS:Executable DTS:refId="Script" DTS:ExecutableType="Microsoft.ScriptTask"><DTS:ObjectData>
      <ScriptProject Name="Synthetic" Language="CSharp" ReadOnlyVariables="User::A" ReadWriteVariables="User::B"/>
    </DTS:ObjectData></DTS:Executable></DTS:Executables>''')
    props=ir.executables[0].properties
    assert props['ReadOnlyVariables']=='User::A' and props['ReadWriteVariables']=='User::B'
