"""Native bound SQL preserves parameters and result state without SQL interpolation."""
import json
from types import SimpleNamespace

import pytest

from ssis_migration.bound_sql import BindingContext, bound_task, variable_query, runtime_setup
from ssis_migration.config import AssessmentConfig
from ssis_migration.ir.models import PackageIR, VariableIR
from ssis_migration.mapping import ConversionGap, translate_sql
from ssis_migration.validator.native_spark import validate_native_spark


def context():
    return BindingContext(PackageIR(package_name='Bound',variables=[
        VariableIR(name='Input',namespace='User',data_type='3',scope_ref_id='Package',value='7'),
        VariableIR(name='Result',namespace='User',data_type='3',scope_ref_id='Package',value='0'),
        VariableIR(name='Table',namespace='User',data_type='8',scope_ref_id='Package',value='dbo.Orders')]))


def props(sql='SELECT @value AS result', result=True):
    return {'SqlStatementSource':sql,'parameter_bindings':[{'ParameterName':'@value','DtsVariableName':'User::Input','ParameterDirection':'Input'}],
        'ResultType':'ResultSetType_SingleRow' if result else 'ResultSetType_None',
        'result_bindings':[{'ResultName':'0','DtsVariableName':'User::Result'}] if result else []}


def environment(runtime, rows, supplied=None):
    limits, calls = [], []
    class Frame:
        def limit(self,n): limits.append(n);return self
        def collect(self): return rows[:limits[-1]]
    env={'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda key:json.dumps(supplied or {}))),
        'spark':SimpleNamespace(sql=lambda sql,**kwargs:(calls.append((sql,kwargs)) or Frame()))}
    exec(runtime_setup(runtime.defaults),env)
    return env,calls,limits


def test_named_arguments_and_bounded_result_bindings_execute_as_native_state_updates():
    runtime=context()
    code,uses,evidence=bound_task(props(),'Package','Legacy',AssessmentConfig(),runtime)
    env,calls,limits=environment(runtime,[(19,)])
    exec(code,env)
    assert calls==[('SELECT :ssis_p0 AS result',{'args':{'ssis_p0':7}})]
    assert limits==[2] and env['_ssis_values']['User::Result']==19
    assert not uses and not evidence['batch']
    assert validate_native_spark(code)['status']=='PASS'


@pytest.mark.parametrize('rows',[[],[(1,),(2,)]])
def test_single_row_contract_rejects_missing_and_multiple_rows(rows):
    runtime=context();code,_,_=bound_task(props(),'Package','Legacy',AssessmentConfig(),runtime)
    env,_,_=environment(runtime,rows)
    with pytest.raises(RuntimeError,match='exactly one row'):exec(code,env)
    assert env['_ssis_values']['User::Result']=='0'


def test_result_ordinal_is_checked_before_state_assignment():
    runtime=context();p=props();p['result_bindings'][0]['ResultName']='1'
    code,_,_=bound_task(p,'Package','Legacy',AssessmentConfig(),runtime)
    env,_,_=environment(runtime,[(1,)])
    with pytest.raises(RuntimeError,match='ordinal'):exec(code,env)


def test_insert_values_then_select_without_semicolon_is_ordered_and_flags_identity_review():
    runtime=context();p=props('INSERT INTO dbo.Orders (id) VALUES (@value)\nSELECT MAX(id) FROM dbo.Orders')
    code,uses,evidence=bound_task(p,'Package','Legacy',AssessmentConfig(target_schema='silver'),runtime)
    env,calls,_=environment(runtime,[(12,)])
    exec(code,env)
    assert len(calls)==2 and calls[0][0].startswith('INSERT') and calls[1][0].startswith('SELECT MAX')
    assert evidence['identity_review'] and evidence['batch']
    assert len(uses)==2
    assert validate_native_spark(code)['status']=='PASS'


def test_unbound_or_output_parameters_do_not_become_native_sql():
    for p in [props('SELECT @missing'),{**props(),'parameter_bindings':[{'ParameterName':'@value','DtsVariableName':'User::Input','ParameterDirection':'Output'}]}]:
        runtime=context()
        with pytest.raises(ConversionGap):bound_task(p,'Package','Legacy',AssessmentConfig(),runtime)
        assert not runtime.inputs  # Failed conversion cannot register executable requirements.


@pytest.mark.parametrize('sql',['SELECT @value; SELECT 2','DECLARE @x int; SELECT @value','EXEC dbo.Unknown @value'])
def test_unsupported_batches_and_procedural_sql_remain_gaps(sql):
    with pytest.raises(ConversionGap):bound_task(props(sql),'Package','Legacy',AssessmentConfig(),context())


def test_dynamic_row_count_resolves_identifier_before_runtime_and_checks_override():
    runtime=context();p=props("exec ('SELECT Count(*) FROM ' + @Table)")
    p['parameter_bindings']=[{'ParameterName':'Table','DtsVariableName':'User::Table'}]
    code,uses,evidence=bound_task(p,'Package','Legacy',AssessmentConfig(target_schema='silver'),runtime)
    assert evidence['dynamic_row_count']
    env,calls,_=environment(runtime,[(3,)])
    exec(code,env)
    assert calls[0][0]=='SELECT COUNT(*) AS source_count FROM `main`.`silver`.`orders`'
    assert env['_ssis_values']['User::Result']==3
    env,calls,_=environment(runtime,[(3,)],{'User::Table':'dbo.Other; DROP TABLE x'})
    with pytest.raises(RuntimeError,match='identity changed'):exec(code,env)
    assert not calls


def test_variable_sql_multiline_expression_binds_numeric_references_without_snapshot_values():
    runtime=context()
    declaration={'status':'DECLARED_REQUIRES_RUNTIME_REVIEW','evaluate_as_expression':True,
        'expression':'"SELECT id\nFROM dbo.Orders WHERE id=" + @[$Package::Key]', 'value':'SELECT id FROM dbo.Stale'}
    call,uses=variable_query(declaration,'Package','Legacy',AssessmentConfig(target_schema='silver'),runtime)
    env,calls,_=environment(runtime,[],{'$Package::Key':12})
    exec('result = '+call,env)
    assert 'Stale' not in calls[0][0] and ':ssis_p0' in calls[0][0]
    assert calls[0][1]['args']['ssis_p0']==12
    assert uses[0]['source']=='dbo.Orders'
    assert validate_native_spark('result = '+call)['status']=='PASS'
    env,calls,_=environment(runtime,[],{'$Package::Key':'12 OR 1=1'})
    with pytest.raises(RuntimeError,match='numeric'):exec('result = '+call,env)
    assert not calls


@pytest.mark.parametrize('expression',[
    '"SELECT * FROM " + @[$Package::Table]',
    '"SELECT \'" + @[$Package::Value] + "\'"',
    '"SELECT 1" + (DT_WSTR, 10)@[$Package::Key]',
    '"SELECT 1" +',
])
def test_nonvalue_concatenation_and_unsupported_expression_forms_remain_pending(expression):
    runtime=context()
    with pytest.raises((ConversionGap,ValueError)):
        variable_query({'status':'DECLARED_REQUIRES_RUNTIME_REVIEW','evaluate_as_expression':True,'expression':expression},
            'Package','Legacy',AssessmentConfig(target_schema='silver'),runtime)
    assert not runtime.inputs


@pytest.mark.parametrize('bad', ['7; DROP TABLE x',True,2**40,None])
def test_runtime_integer_type_and_range_are_checked_before_spark_calls(bad):
    runtime=context();code,_,_=bound_task(props(),'Package','Legacy',AssessmentConfig(),runtime)
    env,calls,_=environment(runtime,[(1,)],{'User::Input':bad})
    with pytest.raises(RuntimeError):exec(code,env)
    assert not calls


def test_validator_requires_explicit_matching_argument_keys_and_keeps_driver_bounds():
    assert validate_native_spark("r = spark.sql('SELECT :p', args={'p': 1})")['status']=='PASS'
    for code in ["r = spark.sql('SELECT :p')", "r = spark.sql('SELECT :p', args={'q': 1})",
                 "r = spark.sql('SELECT :p', args=unknown)", "r = df.limit(1000).collect()"]:
        assert validate_native_spark(code)['status']=='REJECTED'
    assert validate_native_spark('rows = df.limit(2).collect()')['status']=='PASS'


def test_unregistered_parameters_are_still_rejected_by_sql_mapping():
    with pytest.raises(ConversionGap):translate_sql('SELECT :bad',None,AssessmentConfig(),parameter_names=['ssis_p0'])


def test_invalid_variable_sql_stays_a_conversion_gap_without_runtime_requirements():
    runtime=context()
    with pytest.raises(ConversionGap):
        variable_query({'status':'DECLARED_REQUIRES_RUNTIME_REVIEW','evaluate_as_expression':True,
            'expression':'"SELECT FROM WHERE (" + @[$Package::Key]'},'Package','Legacy',AssessmentConfig(),runtime)
    assert not runtime.inputs


def test_generated_notebook_transfers_bound_results_and_requires_shared_execution(tmp_path):
    from ssis_migration.ir.models import ExecutableIR, PrecedenceConstraintIR
    from ssis_migration.migrate import generate_migration_artifacts
    from ssis_migration.generator.job_generator import JobGenerator
    runtime=context()
    first=ExecutableIR(ref_id='Package\\Read',name='Read',executable_type='Microsoft.ExecuteSQLTask',properties=props())
    second=ExecutableIR(ref_id='Package\\Write',name='Write',executable_type='Microsoft.ExecuteSQLTask',properties={
        'SqlStatementSource':'UPDATE dbo.Orders SET id=@result',
        'parameter_bindings':[{'ParameterName':'result','DtsVariableName':'User::Result'}]})
    package=runtime.package;package.executables=[first,second]
    package.precedence_constraints=[PrecedenceConstraintIR(from_executable=first.ref_id,to_executable=second.ref_id)]
    result=generate_migration_artifacts(package,{},None,'.',tmp_path,config=AssessmentConfig(target_schema='silver',strict_mode=True))
    assert result['assessment']['generation']['pending_operations']==0
    assert result['assessment']['generation']['statically_valid_operations']==2
    notebook=json.loads(__import__('pathlib').Path(result['notebook_file']).read_text())
    env,calls,_=environment(runtime,[(11,)])
    env['dbutils'].widgets=SimpleNamespace(text=lambda *a:None,get=lambda key:{'catalog':'main','schema':'silver','step_id':'all','ssis_runtime_values':'{}'}[key])
    for cell in notebook['cells']:
        if cell['cell_type']=='code':exec(''.join(cell['source']),env)
    assert calls[0][1]['args']['ssis_p0']==7
    assert calls[1][1]['args']['ssis_p0']==11
    plan=json.loads((tmp_path/'migration_plan.json').read_text())
    assert plan['metadata']['requires_shared_runtime_state']
    with pytest.raises(ValueError,match='shared notebook'):JobGenerator().generate(plan,'/Workspace/test')
    env['dbutils'].widgets.get=lambda key:{'catalog':'main','schema':'silver','step_id':'single','ssis_runtime_values':'{}'}[key]
    with pytest.raises(RuntimeError,match='shared notebook'):exec(''.join(notebook['cells'][1]['source']),env)
