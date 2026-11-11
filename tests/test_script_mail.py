"""Synthetic C# scripts preserve mail values; tests never contact SMTP servers."""
from types import SimpleNamespace
import pytest

from ssis_migration.bound_sql import BindingContext,runtime_setup
from ssis_migration.config import AssessmentConfig
from ssis_migration.context.builder import build_migration_context
from ssis_migration.ir.models import PackageIR,ExecutableIR,VariableIR,PrecedenceConstraintIR
from ssis_migration.mapping import ConversionGap
from ssis_migration.planner.task_graph import build_graph_plan
from ssis_migration.script_mail import translate_script_mail
from ssis_migration.validator.native_spark import validate_native_spark


def program(body=None,extra=''):
    body=body or r'''
      string origin; string recipients; string copy; string hidden; string content; string relay;
      MailMessage notice; SmtpClient transport;
      origin = Dts.Variables["User::Origin"].Value.ToString();
      recipients = Dts.Variables["User::Recipients"].Value.ToString();
      copy = Dts.Variables["User::Copy"].Value.ToString();
      hidden = Dts.Variables["User::Hidden"].Value.ToString();
      content = Dts.Variables["User::Content"].Value.ToString();
      relay = Dts.Connections["Relay"].Properties["SmtpServer"].GetValue(Dts.Connections["Relay"]).ToString();
      notice = new MailMessage();
      notice.From = new MailAddress(origin);
      notice.To.Add(recipients);
      if (copy != String.Empty) { notice.CC.Add(copy); }
      if (hidden != String.Empty) notice.Bcc.Add(hidden);
      notice.Subject = "Synthetic alert";
      notice.Body = OPEN + "<slot>".Replace("<slot>", content).Replace("\n", "<br>") + CLOSE;
      notice.IsBodyHtml = true;
      transport = new SmtpClient(relay);
      transport.Send(notice);
      Dts.TaskResult = (int) ScriptResults.Success;
    '''
    return '''using System; using System.Net.Mail;
    namespace Synthetic {
      [Microsoft.SqlServer.Dts.Tasks.ScriptTask.SSISScriptTaskEntryPointAttribute]
      public partial class ScriptMain:Microsoft.SqlServer.Dts.Tasks.ScriptTask.VSTARTScriptObjectModelBase {
        enum ScriptResults {Success=Microsoft.SqlServer.Dts.Runtime.DTSExecResult.Success,Failure=Microsoft.SqlServer.Dts.Runtime.DTSExecResult.Failure};
        const string OPEN = "<section>";
        const string CLOSE = "</section>";
        public void Main() {'''+body+'}\n'+extra+'}}'


def fixture(source=None,content_type='8'):
    values={'Origin':'sender@example.invalid','Recipients':'a@example.invalid,b@example.invalid',
        'Copy':'cc@example.invalid','Hidden':'secret@example.invalid','Content':'line1\nline2'}
    if content_type!='8':values['Content']='12'
    ir=PackageIR(variables=[VariableIR(name=k,namespace='User',scope_ref_id='Package',data_type=content_type if k=='Content' else '8',value=v) for k,v in values.items()],
        metadata={'smtp_connections':{'SMTP_REF':{'name':'Relay','host':'smtp.example.invalid','enable_ssl':'True','port':'465'}}})
    task=ExecutableIR(ref_id='Package.EventHandlers[OnError]\\Notify',executable_type='Microsoft.ScriptTask',properties={
        '_event_handler':True,'script_sources':[{'name':'ScriptMain.cs','status':'CAPTURED','text':source or program()}]})
    return task,ir,BindingContext(ir)


@pytest.mark.parametrize('content_type',['8','3'])
@pytest.mark.parametrize('optional_recipients',[True,False])
def test_compiled_mail_preserves_html_headers_envelope_and_source_constructor_defaults(monkeypatch,content_type,optional_recipients):
    import smtplib
    captured=[]
    class SMTP:
        def __init__(self,*args,**kwargs):captured.append(('connect',args))
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def send_message(self,message,**kwargs):captured.append(('message',message,kwargs))
    monkeypatch.setattr(smtplib,'SMTP',SMTP)
    task,ir,runtime=fixture(content_type=content_type)
    if not optional_recipients:
        for variable in ir.variables:
            if variable.name in ('Copy','Hidden'):variable.value=''
    code=translate_script_mail(task,ir,runtime)
    assert validate_native_spark(code)['status']=='PASS'
    env={'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda key:'{}'))}
    exec(runtime_setup(runtime.defaults),env);exec(code,env)
    # The source reads only the connection host. The C# host-only constructor
    # does not copy the SSIS connection's TLS or port settings.
    assert captured[0]==('connect',('smtp.example.invalid',25))
    msg=captured[1][1]
    assert msg.get_content_type()=='text/html'
    assert msg['Subject']=='Synthetic alert' and msg.get('Bcc') is None
    assert msg.get_content().strip()==('<section>line1<br>line2</section>' if content_type=='8' else '<section>12</section>')
    assert captured[1][2]['from_addr']=='sender@example.invalid'
    assert bool(msg.get('Cc')) is optional_recipients
    assert captured[1][2]['to_addrs']==['a@example.invalid','b@example.invalid']+(['cc@example.invalid','secret@example.invalid'] if optional_recipients else [])


@pytest.mark.parametrize('mutation',[
    lambda s:s.replace('transport.Send(notice);','System.IO.File.Delete("some-file"); transport.Send(notice);'),
    lambda s:s.replace('transport.Send(notice);','if (copy != String.Empty) transport.Send(notice);'),
    lambda s:s.replace('new SmtpClient(relay)','new SmtpClient("unmapped.example.invalid")'),
    lambda s:s.replace('notice.IsBodyHtml = true;','notice.IsBodyHtml = true; transport.EnableSsl = true;'),
    lambda s:s.replace('const string OPEN = "<section>";','const string OPEN = Helper();'),
    lambda s:s.replace('public void Main()', 'public void Main(int extra)'),
    lambda s:s.replace('(int) ScriptResults.Success','(int) ScriptResults.Failure'),
    lambda s:s.replace('Replace("<slot>", content)','Replace("", content)'),
    lambda s:s.replace('public void Main()', 'public static void Main()'),
    lambda s:s.replace('transport.Send(notice);','transport.Send(notice); transport.Send(notice);'),
])
def test_unrecognized_code_is_not_partially_translated_or_executed(mutation):
    task,ir,runtime=fixture(mutation(program()))
    with pytest.raises(ConversionGap):translate_script_mail(task,ir,runtime)
    assert runtime.inputs=={} and runtime.defaults=={}


def test_additional_main_class_methods_are_not_ignored():
    task,ir,runtime=fixture(program(extra='public void Other() { System.IO.File.Delete("some-file"); }'))
    with pytest.raises(ConversionGap):translate_script_mail(task,ir,runtime)


def test_additional_project_source_is_not_silently_ignored():
    task,ir,runtime=fixture()
    task.properties['script_sources'].append({'name':'Extra.cs','status':'CAPTURED','text':'class Extra {}'})
    with pytest.raises(ConversionGap,match='project source'):translate_script_mail(task,ir,runtime)


def test_captured_script_variable_access_is_respected():
    task,ir,runtime=fixture()
    task.properties['ReadOnlyVariables']='User::Content'
    with pytest.raises(ConversionGap,match='access declarations'):translate_script_mail(task,ir,runtime)
    assert not runtime.inputs


def test_missing_or_ambiguous_connections_remain_explicit_gaps():
    task,ir,runtime=fixture()
    ir.metadata['smtp_connections']['other']={'name':'Relay','host':'other.example.invalid'}
    with pytest.raises(ConversionGap,match='uniquely'):translate_script_mail(task,ir,runtime)


def test_unsupported_scalar_formatting_stays_pending():
    task,ir,runtime=fixture(content_type='5')
    with pytest.raises(ConversionGap,match='culture'):translate_script_mail(task,ir,runtime)
    assert not runtime.inputs


def test_script_mail_is_planned_as_a_failure_only_handler_action():
    task,ir,runtime=fixture()
    ir.executables=[ExecutableIR(ref_id='Package\\Main',executable_type='Microsoft.ExecuteSQLTask',properties={'SqlStatementSource':'SELECT 1'}),task]
    plan=build_graph_plan(build_migration_context(ir,{}),AssessmentConfig())
    assert plan.metadata['package_error_handler']['source_ids']==[task.ref_id]
    ops=[o for group in plan.metadata['operations_by_step'].values() for o in group]
    assert any(o['operation_type']=='NATIVE_SCRIPT_MAIL' for o in ops)
    assert not any(f['severity']=='ERROR' for f in plan.unresolved)


def test_pending_source_results_and_failure_context_are_not_reported_as_external_inputs():
    from ssis_migration.generator.notebook import NotebookAssembler
    task,ir,_=fixture()
    context=ExecutableIR(ref_id='Package.EventHandlers[OnError]\\Context',executable_type='Microsoft.ExecuteSQLTask',properties={
        '_event_handler':True,'SqlStatementSource':'SELECT @description, @code','parameter_bindings':[
            {'ParameterName':'description','DtsVariableName':'System::ErrorDescription'},
            {'ParameterName':'code','DtsVariableName':'System::ErrorCode'}]})
    producer=ExecutableIR(ref_id='Package.EventHandlers[OnError]\\Producer',executable_type='Microsoft.ExecuteSQLTask',properties={
        '_event_handler':True,'SqlStatementSource':'EXEC dbo.Unknown','ResultType':'ResultSetType_SingleRow',
        'result_bindings':[{'ResultName':'0','DtsVariableName':'User::Origin'}]})
    ir.executables=[ExecutableIR(ref_id='Package\\Main',executable_type='Microsoft.ExecuteSQLTask',properties={'SqlStatementSource':'SELECT 1'}),context,producer,task]
    ir.precedence_constraints=[PrecedenceConstraintIR(from_executable=context.ref_id,to_executable=producer.ref_id,value='Success'),
        PrecedenceConstraintIR(from_executable=producer.ref_id,to_executable=task.ref_id,value='Success')]
    plan=build_graph_plan(build_migration_context(ir,{}),AssessmentConfig())
    records={r['key']:r for r in plan.metadata['runtime_variables']}
    assert records['User::Origin']['origin']=='source_result'
    assert records['System::ErrorDescription']['origin']=='failure_context'
    assert records['System::ErrorCode']['origin']=='error_code_mapping'
    assert not any(records[key]['required_external_input'] for key in ['User::Origin','System::ErrorCode','System::ErrorDescription'])
    notebook=NotebookAssembler().assemble(plan.to_dict(),[],plan.metadata['operations_by_step'])
    bindings=next(''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='markdown' and '## Native runtime bindings' in ''.join(c['source']))
    assert 'supplied by package OnError handler' in bindings and 'through ssis_error_codes' in bindings
