"""Target audit adapters replace source audit SQL without changing business work."""
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from ssis_migration.config import AssessmentConfig
from ssis_migration.context.builder import build_migration_context
from ssis_migration.generator.notebook import NotebookAssembler
from ssis_migration.generator.target_audit import RUN_TYPES,STATS_TYPES
from ssis_migration.ir.models import PackageIR,ExecutableIR,VariableIR,PrecedenceConstraintIR
from ssis_migration.migrate import generate_migration_artifacts
from ssis_migration.planner.task_graph import build_graph_plan
from ssis_migration.validator.native_spark import validate_native_spark


def cfg(**values):return AssessmentConfig(catalog='c',target_schema='data',audit_mode='target',audit_schema='audit',**values)


def task(ref,sql,**props):
    return ExecutableIR(ref_id=ref,name=ref.split('\\')[-1],executable_type='Microsoft.ExecuteSQLTask',properties={'SqlStatementSource':sql,**props})


def package(key_type='3',business='SELECT @key AS business'):
    audit=task('Package\\Arbitrary label',"INSERT INTO log.CustomName (TableProcessKey,BranchName,ProcessingSummaryGroup) VALUES (1,'main','main'); SELECT MAX(AuditKey) FROM log.CustomName",
        ResultType='ResultSetType_SingleRow',result_bindings=[{'ResultName':'0','DtsVariableName':'User::Key'}])
    main=task('Package\\Business',business,parameter_bindings=[{'ParameterName':'key','DtsVariableName':'User::Key'}])
    return PackageIR(package_name='Synthetic',variables=[VariableIR(name='Key',namespace='User',scope_ref_id='Package',data_type=key_type,value='-1')],
        executables=[audit,main],precedence_constraints=[PrecedenceConstraintIR(from_executable=audit.ref_id,to_executable=main.ref_id,value='Success')])


def plan(ir,config=None):
    config=config or cfg()
    context=build_migration_context(ir,{});context.metadata.update(config.to_dict())
    return build_graph_plan(context,config)


def test_source_audit_contract_is_recognized_without_task_or_table_name_conventions():
    p=plan(package())
    assert p.metadata['target_audit']['replaced_sources']==['Package\\Arbitrary label']
    assert any(f['code']=='TARGET_AUDIT_VALUE_BINDING_REQUIRED' for f in p.unresolved)
    assert 'User::Key' not in p.metadata['runtime_defaults']
    assert not any(f['code']=='RESULT_KEY_GENERATION_REVIEW_REQUIRED' for f in p.unresolved)


def test_business_sql_sharing_an_audit_task_is_not_discarded():
    ir=package();ir.executables[0].properties['SqlStatementSource']="INSERT INTO log.CustomName (TableProcessKey,BranchName,ProcessingSummaryGroup) VALUES (1,'main','main'); UPDATE dbo.Business SET id=2; SELECT MAX(AuditKey) FROM log.CustomName"
    p=plan(ir)
    assert not p.metadata['target_audit']['replaced_sources']
    assert any(f['code']=='SOURCE_AUDIT_IN_BUSINESS_TASK' for f in p.unresolved)
    assert not p.metadata['target_audit']['stats_targets']


def test_row_count_still_consumed_by_business_sql_is_preserved():
    ir=package();ir.variables.append(VariableIR(name='Count',namespace='User',scope_ref_id='Package',data_type='3'))
    count=task('Package\\Count','SELECT COUNT(*) FROM dbo.Business',ResultType='ResultSetType_SingleRow',
        result_bindings=[{'ResultName':'0','DtsVariableName':'User::Count'}])
    update=task('Package\\Audit update','UPDATE log.CustomName SET BranchRowCnt=@n',
        parameter_bindings=[{'ParameterName':'n','DtsVariableName':'User::Count'}])
    ir.executables.extend([count,update])
    ir.executables[1].properties['SqlStatementSource']='SELECT @key, @n'
    ir.executables[1].properties['parameter_bindings'].append({'ParameterName':'n','DtsVariableName':'User::Count'})
    p=plan(ir,cfg(object_mappings={'dbo.Business':'c.data.business'}))
    assert update.ref_id in p.metadata['target_audit']['replaced_sources']
    assert count.ref_id not in p.metadata['target_audit']['replaced_sources']


def test_row_count_read_through_a_unicode_sql_variable_is_not_dropped():
    from ssis_migration.audit_replacement import select_replacements
    from ssis_migration.ir.models import DataFlowIR,ComponentIR
    ir=package();ir.variables.extend([VariableIR(name='Count',namespace='User',scope_ref_id='Package',data_type='3'),
        VariableIR(name='查询',namespace='User',scope_ref_id='Package',data_type='8',evaluate_as_expression=True,expression='"SELECT id FROM dbo.Business WHERE id=" + @[User::Count]')])
    count=task('Package\\Count','SELECT COUNT(*) FROM dbo.Business',ResultType='ResultSetType_SingleRow',result_bindings=[{'ResultName':'0','DtsVariableName':'User::Count'}])
    update=task('Package\\Audit update','UPDATE log.CustomName SET BranchRowCnt=@n',parameter_bindings=[{'ParameterName':'n','DtsVariableName':'User::Count'}])
    owner=ExecutableIR(ref_id='Package\\Flow',executable_type='Microsoft.Pipeline')
    ir.executables.extend([count,update,owner])
    ir.data_flows=[DataFlowIR(ref_id=owner.ref_id,components=[ComponentIR(ref_id='Source',detected_type='OLE_DB_SOURCE',properties={'AccessMode':'3','SqlCommandVariable':'User::查询'})])]
    selected=select_replacements(ir,cfg(),lambda ref:False)
    assert count.ref_id not in selected['replaced_sources']


def test_unknown_error_recovery_work_is_never_removed_with_audit_logging():
    ir=package()
    error_audit=task('Package.EventHandlers[OnError]\\Audit',"INSERT INTO log.ErrorRows (PkgExecKey,PkgErrorCode,PkgErrorDescription,PkgSourceName,PkgSourceDescription) VALUES (1,2,'e','n','d')",_event_handler=True)
    recovery=task('Package.EventHandlers[OnError]\\Recovery','DELETE FROM dbo.Business',_event_handler=True)
    ir.executables.extend([error_audit,recovery])
    ir.precedence_constraints.append(PrecedenceConstraintIR(from_executable=error_audit.ref_id,to_executable=recovery.ref_id,value='Success'))
    p=plan(ir,cfg(object_mappings={'dbo.Business':'c.data.business'}))
    assert error_audit.ref_id in p.metadata['target_audit']['replaced_sources']
    assert recovery.ref_id not in p.metadata['target_audit']['replaced_sources']
    assert any(f['code']=='TARGET_AUDIT_RECOVERY_CONTROL_REVIEW' for f in p.unresolved)


def test_audit_notification_replacement_does_not_need_old_procedures_or_smtp_settings():
    from tests.test_script_mail import fixture
    mail,ir,_=fixture();ir.metadata['smtp_connections']={}
    audit=task('Package.EventHandlers[OnError]\\Audit',"INSERT INTO log.Errors (PkgExecKey,PkgErrorCode,PkgErrorDescription,PkgSourceName,PkgSourceDescription) VALUES (1,2,'e','n','d')",_event_handler=True)
    template=task('Package.EventHandlers[OnError]\\Template','EXEC dbo.OldTemplate',_event_handler=True,
        ResultType='ResultSetType_SingleRow',result_bindings=[{'ResultName':'0','DtsVariableName':'User::Origin'}])
    body=task('Package.EventHandlers[OnError]\\Body','EXEC dbo.OldBody',_event_handler=True,
        ResultType='ResultSetType_SingleRow',result_bindings=[{'ResultName':'0','DtsVariableName':'User::Content'}])
    ir.executables=[task('Package\\Main','SELECT 1'),audit,template,body,mail]
    ir.precedence_constraints=[PrecedenceConstraintIR(from_executable=a.ref_id,to_executable=b.ref_id,value='Success') for a,b in zip([audit,template,body],[template,body,mail])]
    p=plan(ir)
    assert set(p.metadata['target_audit']['replaced_sources'])=={audit.ref_id,template.ref_id,body.ref_id,mail.ref_id}
    assert not any(f['code']=='MISSING_PROCEDURE_DEFINITION' for f in p.unresolved)
    assert 'package_error_handler' not in p.metadata


def test_int32_source_key_cannot_silently_receive_an_int64_target_key():
    p=plan(package(),cfg(audit_value_bindings={'User::Key':'notebook_key'}))
    assert any(f['code']=='TARGET_AUDIT_KEY_TYPE_REVIEW_REQUIRED' for f in p.unresolved)


def test_explicit_int64_key_bridge_is_not_a_manual_runtime_input():
    p=plan(package(key_type='20'),cfg(audit_value_bindings={'User::Key':'notebook_key'}))
    record=next(r for r in p.metadata['runtime_variables'] if r['key']=='User::Key')
    assert record['origin']=='target_audit_notebook_key' and not record['required_external_input']
    assert not any(f['severity']=='ERROR' for f in p.unresolved)


def test_source_mode_keeps_original_audit_generation():
    p=plan(package(),AssessmentConfig(catalog='c',target_schema='data'))
    assert 'target_audit' not in p.metadata
    assert any(f['code']=='RESULT_KEY_GENERATION_REVIEW_REQUIRED' for f in p.unresolved)


def test_missing_target_location_stays_blocked_and_original_source_is_traceable(tmp_path):
    result=generate_migration_artifacts(package(),{},None,'.',tmp_path,
        config=AssessmentConfig(audit_mode='target',audit_value_bindings={'User::Key':'runtime_input'}))
    assert any(f['code']=='TARGET_AUDIT_LOCATION_REQUIRED' for f in result['findings'])
    replacement=json.loads((tmp_path/'audit_replacement_plan.json').read_text())
    assert replacement['replacements'][0]['original_sql'].startswith('INSERT')
    assert result['assessment']['generation']['audit_replaced_operation_count']==1


class Row(dict):
    def asDict(self):return dict(self)


class Frame:
    def __init__(self,rows=(),types=None,count=0):
        self.rows=[Row(row) for row in rows];self.row_count=count
        self.schema=SimpleNamespace(fields=[SimpleNamespace(name=k,nullable=True,dataType=SimpleNamespace(simpleString=lambda v=v:v)) for k,v in (types or {}).items()])
    def limit(self,n):
        assert n in (1,2)
        return self
    def collect(self):return self.rows
    def count(self):return self.row_count
    def filter(self,condition):return self


class Spark:
    def __init__(self,fail_business=False,fail_audit=False,fail_success=False,optional=False):
        self.calls=[];self.fail_business=fail_business;self.fail_audit=fail_audit;self.fail_success=fail_success
        self.rows=10;self.parent=Row(master_key=9,time_start=datetime(2026,1,1),status='Running')
        self.run_types=dict(RUN_TYPES);self.stats_types=dict(STATS_TYPES)
        if optional:
            self.run_types.update(job_id='bigint',job_name='string');self.stats_types.update(job_id='bigint',job_name='string')
        self.catalog=SimpleNamespace(tableExists=lambda table:True)
    def table(self,name):
        return Frame(types=self.run_types if 'audit_notebooks' in name else self.stats_types if 'audit_tables' in name else {},count=self.rows)
    def sql(self,sql,args=None):
        self.calls.append((sql,args))
        if sql.startswith('SELECT current_user'):return Frame([{'user_id':'synthetic-user'}])
        if sql.startswith('SELECT current_timestamp'):return Frame([{'time_start':datetime(2026,1,1),'time_end':datetime(2026,1,1,0,1)}])
        if sql.startswith('SELECT master_key'):return Frame([self.parent])
        if sql.startswith('DESCRIBE TABLE EXTENDED'):return Frame([{'data_type':'delta'}])
        if sql.startswith('DESCRIBE DETAIL'):return Frame([{'sizeInBytes':self.rows*10}])
        if sql.startswith('DESCRIBE HISTORY'):return Frame([{'version':self.rows-10}])
        if sql.startswith('UPDATE') and 'audit_notebooks' in sql:
            if self.fail_audit or self.fail_success and args['status']=='Success':raise RuntimeError('audit write failed')
        elif 'AS main' in sql or sql.startswith('INSERT INTO `c`.`data`'):
            if self.fail_business:raise ValueError("original 'business' failure")
            self.rows+=1
        return Frame()


def notebook(tmp_path,write=False):
    ir=PackageIR(package_name='Synthetic',executables=[task('Package\\Main','INSERT INTO dbo.Business (id) VALUES (1)' if write else 'SELECT 7 AS main')])
    config=cfg(object_mappings={'dbo.Business':'c.data.business'})
    result=generate_migration_artifacts(ir,{},None,'.',tmp_path,config=config)
    assert not [f for f in result['findings'] if f['severity']=='ERROR']
    return json.loads(Path(result['notebook_file']).read_text())


def execute(n,spark,job=None,execution_key='',context=None,env_out=None,runtime_values=None):
    exits=[]
    env={'spark':spark,'dbutils':SimpleNamespace(widgets=SimpleNamespace(text=lambda *a:None,
        get=lambda name:{'catalog':'c','schema':'data','step_id':'all','ssis_runtime_values':json.dumps(runtime_values or {}),
            'target_audit_job':json.dumps(job or {}),'execution_key':execution_key}[name]),
        notebook=SimpleNamespace(exit=exits.append))}
    if context is not None:
        env['dbutils'].notebook.entry_point=SimpleNamespace(getDbutils=lambda:SimpleNamespace(notebook=lambda:SimpleNamespace(getContext=lambda:context)))
    if env_out is not None:env_out.update(env);env=env_out
    for cell in n['cells']:
        if cell['cell_type']=='code':exec(''.join(cell['source']),env)
    return env,exits


@pytest.mark.parametrize('optional',[False,True])
def test_success_records_one_run_and_optional_job_columns_without_udfs(tmp_path,optional):
    n=notebook(tmp_path);spark=Spark(optional=optional)
    env,_=execute(n,spark,job={'job_id':42,'job_name':"quoted 'job'"})
    starts=[args for sql,args in spark.calls if sql.startswith('INSERT') and 'audit_notebooks' in sql]
    finishes=[args for sql,args in spark.calls if sql.startswith('UPDATE')]
    assert len(starts)==1 and finishes[0]['status']=='Success'
    assert finishes[0]['key']==starts[0]['notebook_key'] and 0<finishes[0]['key']<2**63
    assert ('job_id' in starts[0]) is optional
    assert env['_ta_finished_status']=='Success'
    assert all(validate_native_spark(''.join(c['source']))['status']=='PASS' for c in n['cells'] if c['cell_type']=='code')


@pytest.mark.parametrize('fail_audit',[False,True])
def test_main_failure_is_preserved_when_target_logging_also_fails(tmp_path,fail_audit):
    spark=Spark(fail_business=True,fail_audit=fail_audit)
    with pytest.raises(ValueError,match="original 'business' failure"):execute(notebook(tmp_path),spark)
    finishes=[args for sql,args in spark.calls if sql.startswith('UPDATE')]
    assert finishes and finishes[-1]['status']=='Error'
    assert finishes[-1]['message']=="original 'business' failure"


def test_failed_success_update_attempts_error_status_and_reraises(tmp_path):
    spark=Spark(fail_success=True)
    with pytest.raises(RuntimeError,match='audit write failed'):execute(notebook(tmp_path),spark)
    assert [args['status'] for sql,args in spark.calls if sql.startswith('UPDATE')]==['Success','Error']


def test_existing_framework_execution_is_attached_without_duplicate_insert(tmp_path):
    spark=Spark();env,exits=execute(notebook(tmp_path),spark,execution_key='41')
    assert not any(sql.startswith('INSERT') for sql,_ in spark.calls)
    assert env['_target_audit_execution_key']==41 and env['_ta_master_key']==9
    assert exits==['[$OK][$STR]']


def test_target_job_context_is_detected_without_manual_ssis_system_values(tmp_path):
    option=lambda value:SimpleNamespace(get=lambda:value)
    context=SimpleNamespace(jobId=lambda:option('23'),jobName=lambda:option('runtime-job'),parentRunId=lambda:option('77'),notebookPath=lambda:option('/synthetic/notebook'))
    spark=Spark(optional=True);execute(notebook(tmp_path),spark,context=context)
    inserted=next(args for sql,args in spark.calls if sql.startswith('INSERT'))
    assert (inserted['job_id'],inserted['job_name'],inserted['master_key'])==(23,'runtime-job',77)
    assert inserted['notebook']=='/synthetic/notebook'


def test_completed_framework_execution_cannot_be_reused(tmp_path):
    spark=Spark();spark.parent['status']='Success'
    with pytest.raises(RuntimeError,match='Running record'):execute(notebook(tmp_path),spark,execution_key='41')
    assert not any(sql.startswith(('INSERT','UPDATE')) for sql,_ in spark.calls)


def test_schema_mismatch_prevents_business_and_audit_writes(tmp_path):
    spark=Spark();spark.run_types.pop('message')
    with pytest.raises(RuntimeError,match='schema/type contract'):execute(notebook(tmp_path),spark)
    assert not any(sql.startswith(('INSERT','UPDATE')) for sql,_ in spark.calls)


def test_target_table_statistics_measure_before_and_after_native_writes(tmp_path):
    spark=Spark();execute(notebook(tmp_path,write=True),spark)
    records=[args for sql,args in spark.calls if sql.startswith('INSERT') and 'audit_tables' in sql]
    assert len(records)==1
    assert (records[0]['before_row_count'],records[0]['after_row_count'])==(10,11)
    assert (records[0]['before_table_version'],records[0]['after_table_version'])==(0,1)


def test_failure_statistics_are_best_effort_and_do_not_mask_business_failure(tmp_path):
    spark=Spark(fail_business=True)
    with pytest.raises(ValueError,match='original'):execute(notebook(tmp_path,write=True),spark)
    records=[args for sql,args in spark.calls if sql.startswith('INSERT') and 'audit_tables' in sql]
    assert len(records)==1 and records[0]['before_row_count']==records[0]['after_row_count']==10


def test_failed_run_cannot_report_success_or_execute_more_business_cells(tmp_path):
    n=notebook(tmp_path);spark=Spark(fail_business=True);env={}
    with pytest.raises(ValueError):execute(n,spark,env_out=env)
    with pytest.raises(RuntimeError,match='success exit'):env['_target_audit_success']()
    main=next(c for c in n['cells'] if c.get('metadata',{}).get('ssis_migration',{}).get('source_ref_id')=='Package\\Main')
    calls=len(spark.calls)
    with pytest.raises(RuntimeError,match='already finished'):exec(''.join(main['source']),env)
    assert len(spark.calls)==calls
