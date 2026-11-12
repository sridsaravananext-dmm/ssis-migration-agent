"""Synthetic variable Execute SQL tasks retain static SQL and typed results."""
import json
from pathlib import Path
import pytest

from ssis_migration.bound_sql import BindingContext,variable_query
from ssis_migration.config import AssessmentConfig
from ssis_migration.context.builder import build_migration_context
from ssis_migration.ir.models import PackageIR,VariableIR,ExecutableIR,PrecedenceConstraintIR
from ssis_migration.mapping import ConversionGap
from ssis_migration.migrate import generate_migration_artifacts,run_migration
from ssis_migration.planner.task_graph import build_graph_plan
from ssis_migration.validator.native_spark import validate_native_spark
from ssis_migration.variable_tasks import variable_sql_task
from tests.test_bound_sql import environment
from tests.test_target_audit import Spark,execute,task


def cfg():return AssessmentConfig(catalog='c',target_schema='data',object_mappings={'dbo.Orders':'c.data.orders'})


def fixture(expression=True):
    sql=VariableIR(name='Query',namespace='User',scope_ref_id='Package',data_type='8',evaluate_as_expression=expression,
        expression='"SELECT COUNT(*) FROM dbo.Orders WHERE id=" + @[User::Key]' if expression else None,
        value='EXEC dbo.Stale' if expression else 'SELECT COUNT(*) FROM dbo.Orders WHERE id=@key')
    exe=task('QueryTask','User::Query',SqlStatementSourceType='Variable',ResultType='ResultSetType_SingleRow',
        result_bindings=[{'ResultName':'0','DtsVariableName':'User::Result'}])
    if not expression:exe.properties['parameter_bindings']=[{'ParameterName':'key','DtsVariableName':'User::Key'}]
    ir=PackageIR(package_name='Generic',variables=[sql,
        VariableIR(name='Key',namespace='User',scope_ref_id='Package',data_type='3',value='7'),
        VariableIR(name='Result',namespace='User',scope_ref_id='Package',data_type='3')],executables=[exe])
    return ir,exe


@pytest.mark.parametrize('expression',[False,True])
def test_expression_and_captured_query_bind_results_without_runtime_sql_eval(expression):
    ir,exe=fixture(expression);runtime=BindingContext(ir)
    code,uses,evidence=variable_sql_task(exe.properties,exe.ref_id,'Legacy',cfg(),runtime)
    env,calls,limits=environment(runtime,[(3,)])
    exec(code,env)
    assert calls==[('SELECT COUNT(*) FROM `c`.`data`.`orders` WHERE id = :ssis_p0',{'args':{'ssis_p0':7}})]
    assert env['_ssis_values']['User::Result']==3 and limits==[2]
    assert evidence['runtime_sql_evaluation'] is False and evidence['query_only']
    assert evidence['kind']==('EXPRESSION_TEMPLATE' if expression else 'CAPTURED_QUERY')
    assert uses[0]['source']=='dbo.Orders' and validate_native_spark(code)['status']=='PASS'
    assert 'dbo.Stale' not in code


@pytest.mark.parametrize('rows',[[],[(1,),(2,)]])
def test_variable_query_rejects_wrong_row_cardinality_without_setting_result(rows):
    ir,exe=fixture();runtime=BindingContext(ir)
    code,_,_=variable_sql_task(exe.properties,exe.ref_id,None,cfg(),runtime)
    env,_,limits=environment(runtime,rows)
    with pytest.raises(RuntimeError,match='exactly one row'):exec(code,env)
    assert 'User::Result' not in env['_ssis_values'] and limits==[2]


def test_expression_uses_declaration_scope_instead_of_shadowing_task_variable():
    ir,exe=fixture();exe.properties['_parent_ref_id']='Local'
    ir.executables.insert(0,ExecutableIR(ref_id='Local',executable_type='STOCK:SEQUENCE'))
    ir.variables.append(VariableIR(name='Key',namespace='User',scope_ref_id='Local',data_type='3',value='999'))
    runtime=BindingContext(ir)
    code,_,_=variable_sql_task(exe.properties,exe.ref_id,None,cfg(),runtime)
    env,calls,_=environment(runtime,[(2,)])
    exec(code,env)
    assert calls[0][1]['args']=={'ssis_p0':7}
    # The shared query compiler also supplies data-flow sources.
    from ssis_migration.source_context import describe_variable
    context=describe_variable('User::Query',exe.ref_id,ir)
    other=BindingContext(ir);call,_=variable_query(context,exe.ref_id,None,cfg(),other)
    env,calls,_=environment(other,[]);exec('frame = '+call,env)
    assert calls[0][1]['args']=={'ssis_p0':7}


@pytest.mark.parametrize('sql',[
    'EXEC dbo.Missing', 'DELETE FROM dbo.Orders', 'SELECT 1; SELECT 2',
    'INSERT INTO dbo.Orders (id) VALUES (1); SELECT COUNT(*) FROM dbo.Orders',
    'SELECT * FROM dbo.Orders WHERE id=?',
])
def test_procedures_writes_batches_and_positional_bindings_stay_pending(sql):
    ir,exe=fixture(False);ir.variables[0].value=sql;exe.properties.pop('parameter_bindings')
    runtime=BindingContext(ir)
    with pytest.raises(ConversionGap):variable_sql_task(exe.properties,exe.ref_id,None,cfg(),runtime)
    assert not runtime.inputs


@pytest.mark.parametrize('change',[
    {'ResultType':'ResultSetType_Full','result_bindings':[]},
    {'ResultType':'ResultSetType_None','result_bindings':[]},
    {'result_bindings':[{'ResultName':'named','DtsVariableName':'User::Result'}]},
    {'parameter_bindings':[{'ParameterName':'key','DtsVariableName':'User::Key'}]},
])
def test_unsupported_result_modes_and_extra_expression_bindings_leave_no_partial_state(change):
    ir,exe=fixture();exe.properties.update(change);runtime=BindingContext(ir)
    with pytest.raises(ConversionGap):variable_sql_task(exe.properties,exe.ref_id,None,cfg(),runtime)
    assert not runtime.inputs


@pytest.mark.parametrize('change',[{'sensitive':True},{'evaluate_as_expression':None},{'data_type':'3'}])
def test_sensitive_undeclared_or_nonstring_sql_is_not_compiled(change):
    ir,exe=fixture(False)
    for key,value in change.items():setattr(ir.variables[0],key,value)
    with pytest.raises(ConversionGap):variable_sql_task(exe.properties,exe.ref_id,None,cfg(),BindingContext(ir))


def test_known_sql_variable_mutation_is_blocked_but_disabled_writer_is_ignored():
    ir,exe=fixture(False)
    writer=ExecutableIR(ref_id='Writer',executable_type='Microsoft.ExpressionTask',properties={'Expression':'@[User::Query] = "SELECT 42"'})
    ir.executables.append(writer)
    with pytest.raises(ConversionGap,match='written by a source task'):
        variable_sql_task(exe.properties,exe.ref_id,None,cfg(),BindingContext(ir))
    runtime=BindingContext(ir)
    code,_,_=variable_sql_task(exe.properties,exe.ref_id,None,cfg(),runtime,disabled=lambda ref:ref=='Writer')
    assert code and runtime.inputs['User::Query']['fixed_sql_text']==ir.variables[0].value


@pytest.mark.parametrize('expression',[False,True])
def test_graph_generation_preserves_variable_context_and_pending_result_provenance(tmp_path,expression):
    ir,exe=fixture(expression)
    result=generate_migration_artifacts(ir,{},None,'.',tmp_path,config=cfg())
    assert not [f for f in result['findings'] if f['severity']=='ERROR']
    graph=json.loads((tmp_path/'migration_plan.json').read_text())
    assert graph['steps'][0]['step_type']=='NATIVE_VARIABLE_SQL_TASK'
    record=next(r for r in result['assessment']['runtime_bindings']['variables'] if r['key']=='User::Result')
    assert record['origin']=='source_result' and not record['required_external_input']
    notebook=json.loads(Path(result['notebook_file']).read_text())
    assert all(validate_native_spark(''.join(c['source']))['status']=='PASS' for c in notebook['cells'] if c['cell_type']=='code')


def test_changed_captured_sql_override_is_rejected_before_earlier_truncate(tmp_path):
    ir,exe=fixture(False)
    truncate=task('Truncate','TRUNCATE TABLE dbo.Orders')
    # A later business use must not erase the fixed-query constraint.
    read=task('ReadText','SELECT @text AS main',parameter_bindings=[{'ParameterName':'text','DtsVariableName':'User::Query'}])
    ir.executables=[truncate,exe,read]
    ir.precedence_constraints=[PrecedenceConstraintIR(from_executable=a.ref_id,to_executable=b.ref_id,value='Success') for a,b in zip(ir.executables,ir.executables[1:])]
    result=generate_migration_artifacts(ir,{},None,'.',tmp_path,config=cfg())
    assert not [f for f in result['findings'] if f['severity']=='ERROR']
    notebook=json.loads(Path(result['notebook_file']).read_text())
    spark=Spark()
    with pytest.raises(RuntimeError,match='preflight failed') as error:
        execute(notebook,spark,runtime_values={'User::Query':'SELECT secret_value FROM private.Other'})
    assert spark.calls==[] and 'secret_value' not in str(error.value)


@pytest.mark.parametrize('expression',[False,True])
def test_variable_sql_dtsx_intake_works_for_utf16_and_nonstandard_root(tmp_path,expression):
    p=tmp_path/'generic.DTSX'
    raw='''<?xml version="1.0" encoding="utf-16"?>
<D:Executable xmlns:D="www.microsoft.com/SqlServer/Dts" xmlns:S="sql" D:refId="Root" D:ObjectName="Generic">
 <D:Variables>
  <D:Variable D:ObjectName="Query" D:Namespace="User" D:EvaluateAsExpression="False"><D:VariableValue D:DataType="8">SELECT 7</D:VariableValue></D:Variable>
  <D:Variable D:ObjectName="Result" D:Namespace="User"><D:VariableValue D:DataType="3"/></D:Variable>
 </D:Variables>
 <D:Executables><D:Executable D:refId="Opaque" D:ExecutableType="Microsoft.ExecuteSQLTask"><D:ObjectData>
  <S:SqlTaskData S:SqlStatementSource="User::Query" S:SqlStatementSourceType="Variable" S:ResultType="ResultSetType_SingleRow">
   <S:ResultBinding S:ResultName="0" S:DtsVariableName="User::Result"/>
  </S:SqlTaskData>
 </D:ObjectData></D:Executable></D:Executables>
</D:Executable>'''
    if expression:
        raw=raw.replace('D:EvaluateAsExpression="False"','D:EvaluateAsExpression="True" D:Expression="&quot;SELECT 7&quot;"')
        raw=raw.replace('>SELECT 7<','>EXEC dbo.Stale<').replace('S:SqlStatementSourceType="Variable"','S:SqlStatementSourceType="2"')
    p.write_bytes(raw.encode('utf-16'))
    report=run_migration(p,tmp_path/'out',catalog='c',target_schema='data')
    item=report['packages'][0]
    assert item['status']!='FAILED'
    assert not [f for f in item['findings'] if f['severity']=='ERROR']
    graph=json.loads(Path(item['artifacts']['plan']).read_text())
    assert graph['steps'][0]['step_type']=='NATIVE_VARIABLE_SQL_TASK'
