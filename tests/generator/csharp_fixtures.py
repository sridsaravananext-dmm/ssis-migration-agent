import copy
from ssis_migration.adapters.ssis import SSISAdapter
from ssis_migration.migration import bind_plan_to_ir, build_migration_contract, validate_contract

BASE = 'Microsoft.SqlServer.Dts.Tasks.ScriptTask.VSTARTScriptObjectModelBase'


def make_contract(tmp_path, body, *, extra_members='', prefix='', strategy='DETERMINISTIC', bindings=None):
    source = prefix + 'namespace Demo { public class ScriptMain : ' + BASE + ' { public void Main() {' + body + '} ' + extra_members + '} }'
    path = tmp_path/'script.dtsx'
    path.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:refId="Package" DTS:ObjectName="Example" DTS:ExecutableType="Microsoft.Package"><DTS:Property DTS:Name="PackageFormatVersion">8</DTS:Property><DTS:Executable DTS:refId="script" DTS:ObjectName="Script" DTS:ExecutableType="Microsoft.ScriptTask"><ScriptProject><ProjectItem Name="ScriptMain.cs" Encoding="UTF8"><![CDATA[''' + source + ''']]></ProjectItem></ScriptProject></DTS:Executable></DTS:Executable>''')
    result = SSISAdapter().analyze(path)
    record = result.normalized_ir['nodes'][0]
    source_record = record['attributes']['script_assessment']['files'][0]['assessment']['source']
    refs = [{'file_id':result.source_envelope['source']['file_id'], 'locator':'xml:/0', 'rule':'supplied_runtime_bindings'}]
    variable_bindings = bindings if bindings is not None else {'User::N': {'type':'int32','access':'READ'}, 'User::Out':{'type':'int32','access':'WRITE'}}
    variable_bindings = {key:{**value,'evidence':copy.deepcopy(refs)} for key,value in variable_bindings.items()}
    spec = {'source_node_id':record['node_id'], 'source_locator':source_record['locator'],
            'source_sha256':source_record['sha256'], 'entry_point':'Demo.ScriptMain.Main',
            'runtime':{'kind':'SSIS_SCRIPT_TASK','overflow_context':'UNCHECKED','success_value':0,'evidence':refs},
            'variables':variable_bindings}
    plan = {'steps':[{'step_id':'script','name':'Script','dependencies':[], 'source_objects':[], 'target_objects':[],
                     'metadata':{'ref_id':'script','migration_intent':{},'migration_strategy':{'strategy':strategy},'csharp_translation':spec}}], 'metadata':{}}
    bound = bind_plan_to_ir(plan, result.normalized_ir)
    step = bound['steps'][0]
    contract = build_migration_contract(step, source_platform='ssis', evidence_files=result.source_envelope['evidence_files']).to_dict()
    validate_contract(contract, step)
    return contract, step
