"""OnError actions are failure-only, respect source edges, and retain the original error."""
import json
from types import SimpleNamespace
from pathlib import Path
import pytest

from ssis_migration.bound_sql import BindingContext,runtime_setup
from ssis_migration.config import AssessmentConfig
from ssis_migration.context.builder import build_migration_context
from ssis_migration.event_handlers import condition,edge_expression,mail_code
from ssis_migration.generator.notebook import NotebookAssembler
from ssis_migration.ir.models import PackageIR,ExecutableIR,PrecedenceConstraintIR
from ssis_migration.migrate import generate_migration_artifacts
from ssis_migration.parser.package_parser import SSISPackageParser
from ssis_migration.ir.builder import IRBuilder
from ssis_migration.validator.native_spark import validate_native_spark


def package():
    main=ExecutableIR(ref_id='Package\\Main',name='Main',executable_type='Microsoft.ExecuteSQLTask',properties={'SqlStatementSource':'SELECT 1 AS main'})
    a=ExecutableIR(ref_id='Package.EventHandlers[OnError]\\A',name='Audit A',executable_type='Microsoft.ExecuteSQLTask',properties={'SqlStatementSource':'SELECT 2 AS audit_a','_event_handler':True})
    b=ExecutableIR(ref_id='Package.EventHandlers[OnError]\\B',name='Audit B',executable_type='Microsoft.ExecuteSQLTask',properties={'SqlStatementSource':'SELECT 3 AS audit_b','_event_handler':True})
    return PackageIR(package_name='Errors',executables=[main,a,b],precedence_constraints=[PrecedenceConstraintIR(from_executable=a.ref_id,to_executable=b.ref_id,value='Success')])


def run_notebook(tmp_path,fail_main=False,fail_handler=False):
    result=generate_migration_artifacts(package(),{},None,'.',tmp_path,config=AssessmentConfig())
    assert not [f for f in result['findings'] if f['severity']=='ERROR']
    notebook=json.loads(Path(result['notebook_file']).read_text())
    calls=[]
    def sql(text):
        calls.append(text)
        if fail_main and 'AS main' in text:raise ValueError('original failure')
        if fail_handler and 'audit_a' in text:raise RuntimeError('handler failure')
    env={'spark':SimpleNamespace(sql=sql),'dbutils':SimpleNamespace(widgets=SimpleNamespace(text=lambda *a:None,
        get=lambda key:{'catalog':'main','schema':'','step_id':'all','ssis_runtime_values':'{}','ssis_error_codes':'{}'}[key]))}
    if fail_main:
        with pytest.raises(ValueError,match='original failure'):
            for cell in notebook['cells']:
                if cell['cell_type']=='code':exec(''.join(cell['source']),env)
    else:
        for cell in notebook['cells']:
            if cell['cell_type']=='code':exec(''.join(cell['source']),env)
    return calls,env


def test_success_does_not_execute_package_error_actions(tmp_path):
    calls,_=run_notebook(tmp_path)
    assert calls==['SELECT 1 AS main']


def test_failure_executes_handler_in_source_order_and_rethrows_original(tmp_path):
    calls,env=run_notebook(tmp_path,fail_main=True)
    assert calls==['SELECT 1 AS main','SELECT 2 AS audit_a','SELECT 3 AS audit_b']
    assert env['_ssis_values']['System::ErrorDescription']=='original failure'
    assert env['_ssis_values']['System::SourceName']=='Main'


def test_handler_failure_skips_success_dependent_action_without_masking_original(tmp_path):
    calls,env=run_notebook(tmp_path,fail_main=True,fail_handler=True)
    assert calls==['SELECT 1 AS main','SELECT 2 AS audit_a']
    assert env['_ssis_event_errors'][0]['error']=='handler failure'


@pytest.mark.parametrize('operation,value,expected', [('Constraint','Failure',True),('Constraint','Completion',True),
    ('ExpressionOrConstraint','Success',True),('ExpressionAndConstraint','Success',False),('Expression','Success',True)])
def test_event_constraints_use_explicit_result_and_expression_semantics(operation,value,expected):
    runtime=BindingContext(PackageIR())
    edge={'from':'A','to':'B','evaluation_operation':operation,'value':value,'expression':'@[$Package::Parent] == -1'}
    code=edge_expression(edge,runtime,'Package')
    env={'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda key:'{"$Package::Parent":-1}'))}
    exec(runtime_setup(runtime.defaults),env)
    env['_ssis_event_status']={'A':'Failure'}
    exec('result = '+code,env)
    assert env['result'] is expected
    env['_ssis_event_status']={'A':'Skipped'}
    exec('result = '+code,env)
    assert env['result'] is False


def test_mail_translation_uses_captured_transport_and_never_sends_in_tests(monkeypatch):
    import smtplib
    calls=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs):calls.append(('connect',args))
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def starttls(self,**kwargs):calls.append(('tls',))
        def send_message(self,message,**kwargs):calls.append(('message',message,kwargs))
    monkeypatch.setattr(smtplib,'SMTP',FakeSMTP)
    ir=PackageIR(metadata={'smtp_connections':{'SMTP':{'host':'smtp.example.invalid','port':'25','enable_ssl':'True','windows_authentication':'False'}}})
    task=ExecutableIR(ref_id='Mail',properties={'SMTPConnection':'SMTP','FromLine':'sender@example.invalid','ToLine':'one@example.invalid;two@example.invalid',
        'BCCLine':'hidden@example.invalid','Subject':'Failure','MessageSource':'Body','MessageSourceType':'DirectInput'})
    code=mail_code(task,ir,BindingContext(ir))
    assert validate_native_spark(code)['status']=='PASS'
    exec(code,{})
    assert calls[0]==('connect',('smtp.example.invalid',25))
    message=calls[-1][1]
    assert message.get('Bcc') is None
    assert calls[-1][2]['to_addrs']==['one@example.invalid','two@example.invalid','hidden@example.invalid']


def test_parser_retains_smtp_task_and_connection_without_exporting_unknown_secrets(tmp_path):
    p=tmp_path/'mail.dtsx'
    p.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" xmlns:SMTP="smtp" DTS:refId="Package">
      <DTS:ConnectionManagers><DTS:ConnectionManager DTS:refId="SMTP" DTS:CreationName="SMTP"><DTS:ObjectData>
        <SMTP:SmtpConnectionManager SMTP:ConnectionString="SmtpServer=smtp.example.invalid;EnableSsl=True;Password=not-exported;"/>
      </DTS:ObjectData></DTS:ConnectionManager></DTS:ConnectionManagers>
      <DTS:Executables><DTS:Executable DTS:refId="Mail" DTS:ExecutableType="Microsoft.SendMailTask"><DTS:ObjectData>
        <SMTP:SendMailTaskData SMTP:SMTPConnection="SMTP" SMTP:FromLine="sender@example.invalid" SMTP:ToLine="receiver@example.invalid"/>
      </DTS:ObjectData></DTS:Executable></DTS:Executables></DTS:Executable>''')
    ir=IRBuilder().build(SSISPackageParser(p).parse())
    assert ir.executables[0].properties['SMTPConnection']=='SMTP'
    assert ir.metadata['smtp_connections']['SMTP']['enable_ssl']=='True'
    assert 'not-exported' not in json.dumps(ir.metadata['smtp_connections'])


def test_event_local_variables_are_visible_with_opaque_executable_ids(tmp_path):
    from ssis_migration.source_context import describe_variable
    p=tmp_path/'scope.dtsx'
    p.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:refId="Package"><DTS:EventHandlers>
      <DTS:EventHandler DTS:refId="Package.EventHandlers[OnError]"><DTS:Variables>
        <DTS:Variable DTS:ObjectName="Local" DTS:Namespace="User"><DTS:VariableValue DTS:DataType="8">event value</DTS:VariableValue></DTS:Variable>
      </DTS:Variables><DTS:Executables><DTS:Executable DTS:refId="opaque" DTS:ExecutableType="Microsoft.ExecuteSQLTask"/></DTS:Executables>
      </DTS:EventHandler></DTS:EventHandlers></DTS:Executable>''')
    ir=IRBuilder().build(SSISPackageParser(p).parse())
    assert describe_variable('User::Local','opaque',ir)['value']=='event value'
    assert describe_variable('User::Local','Package',ir)['status']=='NOT_FOUND'


def test_opaque_event_task_ids_use_captured_handler_scope():
    from ssis_migration.planner.task_graph import build_graph_plan
    ir=package()
    previous=ir.executables[1].ref_id
    ir.executables[1].ref_id='opaque-a'
    ir.executables[1].properties['_event_handler_attributes']={'refId':'Package.EventHandlers[OnError]'}
    ir.precedence_constraints[0].from_executable='opaque-a'
    plan=build_graph_plan(build_migration_context(ir,{}),AssessmentConfig())
    assert plan.metadata['package_error_handler']['source_ids'][0]=='opaque-a'
    assert not any(f['severity']=='ERROR' for f in plan.unresolved)


@pytest.mark.parametrize('change,code',[('concurrent','EVENT_CONCURRENCY_REVIEW_REQUIRED'),('transaction','TRANSACTION_SEMANTICS_UNSUPPORTED')])
def test_unsupported_handler_runtime_policies_remain_blocked(change,code):
    from ssis_migration.planner.task_graph import build_graph_plan
    ir=package()
    if change=='concurrent':ir.precedence_constraints=[]
    else:ir.executables[1].properties['_event_handler_attributes']={'refId':'Package.EventHandlers[OnError]','TransactionOption':'Required'}
    plan=build_graph_plan(build_migration_context(ir,{}),AssessmentConfig())
    assert any(f['code']==code and f['severity']=='ERROR' for f in plan.unresolved)
    assert 'package_error_handler' not in plan.metadata


def test_error_code_mapping_is_explicit_and_previous_failure_codes_are_cleared():
    from ssis_migration.event_handlers import handler_setup
    plan={'metadata':{'package_error_handler':{'source_ids':[],'edges':[]}},'steps':[]}
    env={'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda key:'{"ValueError":-7}'))}
    exec(runtime_setup({}),env)
    exec(handler_setup(plan,{}),env)
    env['_ssis_run_package_error']('First','',ValueError('first'))
    assert env['_ssis_values']['System::ErrorCode']==-7
    env['_ssis_run_package_error']('Second','',RuntimeError('second'))
    assert 'System::ErrorCode' not in env['_ssis_values']
