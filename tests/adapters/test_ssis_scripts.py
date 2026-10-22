import copy
import xml.etree.ElementTree as ET

import pytest

from ssis_migration.adapters.ssis import SSISAdapter
from ssis_migration.adapters.ssis.scripts import extract_script_assessments
from ssis_migration.migration import bind_plan_to_ir, build_migration_contract, validate_contract
from ssis_migration.parser.package_parser import SSISPackageParser
from ssis_migration.ir.builder import IRBuilder


def package(tmp_path, source_xml='', extra=''):
    path = tmp_path/'scripts.dtsx'
    path.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:refId="Package" DTS:ObjectName="Example" DTS:ExecutableType="Microsoft.Package">
<DTS:Property DTS:Name="PackageFormatVersion">8</DTS:Property>
<DTS:Executable DTS:refId="script" DTS:ObjectName="Script" DTS:ExecutableType="Microsoft.ScriptTask">
<DTS:ObjectData><ScriptProject>''' + source_xml + '</ScriptProject></DTS:ObjectData>' + extra + '</DTS:Executable></DTS:Executable>')
    return path


def test_exported_source_survives_ir_binding_and_contract_without_legacy_changes(tmp_path):
    source = 'public void Main() { File.Delete(path); Dts.TaskResult=0; }'
    path = package(tmp_path, '<File Name="ScriptMain.cs"><![CDATA[' + source + ']]></File>')
    result = SSISAdapter().analyze(path)
    assert result.package_ir.to_dict() == IRBuilder().build(SSISPackageParser(path).parse()).to_dict()
    script, = result.normalized_ir['nodes']
    assessment = script['attributes']['script_assessment']
    assert script['operation'] == 'UNKNOWN'
    assert assessment['classification'] == 'REQUIRES_REDESIGN'
    assert assessment['files'][0]['assessment']['source']['text'] == source
    element = ET.parse(path).getroot()
    locator = assessment['files'][0]['assessment']['source']['locator']
    for position in locator.removeprefix('xml:/').split('/'):
        element = element[int(position)]
    assert element.text == source
    assert assessment['files'][0]['assessment']['source']['file_id'] == result.source_envelope['source']['file_id']
    assert any(d['code'] == 'UNVERIFIED_CSHARP_SCRIPT' for d in result.normalized_ir['diagnostics'])
    plan = {'steps':[{'step_id':'script','name':'Script','dependencies':[], 'source_objects':[], 'target_objects':[],
        'metadata':{'ref_id':'script','migration_intent':{},'migration_strategy':{'strategy':'UNSUPPORTED'}}}], 'metadata':{}}
    bound = bind_plan_to_ir(plan, result.normalized_ir)
    step = bound['steps'][0]
    contract = build_migration_contract(step, source_platform='ssis', evidence_files=result.source_envelope['evidence_files']).to_dict()
    validate_contract(contract, step)
    obligation, = [o for o in contract['required_behavior']['obligations'] if o['kind'] == 'normalized_node']
    assert obligation['source_value']['record']['attributes']['script_assessment'] == assessment
    assert obligation['verification'] == 'NOT_VERIFIED'
    assert contract['status'] == 'BLOCKED'  # UNSUPPORTED strategy is not promoted.


@pytest.mark.parametrize('source_xml', ['', '<BinaryItem Name="ScriptMain.dll">AAAA</BinaryItem>', '<File Name="ScriptMain.vb">Console.WriteLine(1)</File>'])
def test_missing_binary_and_other_languages_never_become_csharp_source(tmp_path, source_xml):
    result = SSISAdapter().analyze(package(tmp_path, source_xml))
    assessment = result.normalized_ir['nodes'][0]['attributes']['script_assessment']
    assert assessment['source_status'] == 'MISSING_OR_UNSUPPORTED'
    assert assessment['classification'] == 'REVIEW_REQUIRED'
    assert assessment['files'] == []


def test_nested_task_cannot_supply_parent_script_source(tmp_path):
    nested = '<DTS:Executable DTS:refId="child" DTS:ExecutableType="Microsoft.ScriptTask"><File Name="Child.cs">Dts.TaskResult=0;</File></DTS:Executable>'
    result = SSISAdapter().analyze(package(tmp_path, extra=nested))
    by_ref = {n['source_ref']:n['attributes']['script_assessment'] for n in result.normalized_ir['nodes']}
    assert by_ref['script']['files'] == []
    assert len(by_ref['child']['files']) == 1


def test_script_component_uses_native_identity_not_display_name():
    root = ET.fromstring('<Root><component refId="component" componentClassID="Microsoft.ScriptComponent"><File Name="Main.cs">Dts.Variables["x"].Value=1;</File></component></Root>')
    legacy = {'data_flows':[{'components':[{'ref_id':'component','component_class_id':'Microsoft.ScriptComponent'}]}]}
    assessment = extract_script_assessments(root, legacy, 'source')['/data_flows/0/components/0']
    assert assessment['classification'] == 'PARTIALLY_TRANSLATABLE'
    # Duplicate identities must not choose either source arbitrarily.
    root.append(copy.deepcopy(root[0]))
    assessment = extract_script_assessments(root, legacy, 'source')['/data_flows/0/components/0']
    assert assessment['identity_status'] == 'AMBIGUOUS_OR_MISSING'
    assert assessment['files'] == []


@pytest.mark.parametrize('tag,encoding,available', [
    ('ProjectItem', 'UTF8', True), ('File', 'UTF-8', True),
    ('ProjectItem', 'base64', False), ('File', 'binary', False),
])
def test_explicit_source_layout_and_encoding(tmp_path, tag, encoding, available):
    path = package(tmp_path, f'<{tag} Name="ScriptMain.cs" Encoding="{encoding}">Dts.TaskResult=0;</{tag}>')
    assessment = SSISAdapter().analyze(path).normalized_ir['nodes'][0]['attributes']['script_assessment']
    assert bool(assessment['files']) is available
