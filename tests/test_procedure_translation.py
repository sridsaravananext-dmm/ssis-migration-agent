"""Available definitions replace bounded procedure placeholders with native SQL."""
from types import SimpleNamespace
import pytest

from ssis_migration.bound_sql import BindingContext, runtime_setup
from ssis_migration.config import AssessmentConfig
from ssis_migration.ir.models import PackageIR,VariableIR
from ssis_migration.mapping import ConversionGap
from ssis_migration.procedure_translation import translate_procedure
from ssis_migration.validator.native_spark import validate_native_spark


def runtime():
    return BindingContext(PackageIR(variables=[VariableIR(name='Input',namespace='User',scope_ref_id='Package',data_type='3',value='9'),
        VariableIR(name='Result',namespace='User',scope_ref_id='Package',data_type='3')]))


def test_positional_task_arguments_are_matched_to_formals_and_cast():
    state=runtime()
    props={'parameter_bindings':[{'ParameterName':'@task_value','DtsVariableName':'User::Input'}],
        'ResultType':'ResultSetType_SingleRow','result_bindings':[{'ResultName':'0','DtsVariableName':'User::Result'}]}
    code,uses,evidence=translate_procedure('EXEC dbo.P @task_value','CREATE PROC dbo.P @formal int AS BEGIN SELECT @formal; END',
        props,'Package','Legacy',AssessmentConfig(),state)
    assert 'CAST(:ssis_p0 AS INT)' in code and 'EXEC ' not in code
    assert evidence['procedure_parameters'][0]['name']=='formal'
    assert validate_native_spark(code)['status']=='PASS'
    assert state.inputs['User::Input']['kind']=='int32'


def test_named_arguments_defaults_and_literal_commas_are_parsed_without_runtime_interpolation():
    state=runtime()
    definition="CREATE PROCEDURE dbo.P @id int, @label nvarchar(20)=N'a,b' AS UPDATE dbo.Target SET label=@label WHERE id=@id"
    code,uses,_=translate_procedure('EXEC dbo.P @id=3',definition,{},'Package','Legacy',
        AssessmentConfig(target_schema='silver'),state)
    assert "a,b" in code and 'CAST(3 AS INT)' in code
    assert uses[0]['source']=='dbo.Target'
    assert not state.inputs


@pytest.mark.parametrize('call,definition',[
    ('EXEC dbo.P','CREATE PROC dbo.P @x int AS SELECT @x'),
    ('EXEC dbo.P @x=1,@x=2','CREATE PROC dbo.P @x int AS SELECT @x'),
    ('EXEC dbo.P @x=1,2','CREATE PROC dbo.P @x int,@y int AS SELECT @x+@y'),
    ('EXEC @rc=dbo.P 1','CREATE PROC dbo.P @x int AS SELECT @x'),
    ('EXEC dbo.P 1','CREATE PROC dbo.P @x int OUTPUT AS SELECT @x'),
    ('EXEC dbo.P 1','CREATE PROC dbo.P @x dbo.CustomType AS SELECT @x'),
    ('EXEC dbo.P GETDATE()','CREATE PROC dbo.P @x datetime AS SELECT @x'),
    ('EXEC dbo.P 1','CREATE PROC dbo.P @x int AS DECLARE @local int; SELECT @x'),
])
def test_unsupported_procedure_semantics_remain_explicit_gaps(call,definition):
    state=runtime()
    with pytest.raises(ConversionGap):translate_procedure(call,definition,{},'Package','Legacy',AssessmentConfig(),state)
    assert not state.inputs


def test_available_parameterized_definition_is_native_in_the_main_planner():
    from ssis_migration.context.builder import build_migration_context
    from ssis_migration.ir.models import ExecutableIR
    from ssis_migration.planner.task_graph import build_graph_plan
    package=runtime().package
    package.executables=[ExecutableIR(ref_id='Package\\Call',executable_type='Microsoft.ExecuteSQLTask',
        properties={'SqlStatementSource':'EXEC dbo.P @actual','parameter_bindings':[{'ParameterName':'actual','DtsVariableName':'User::Input'}]})]
    ctx=build_migration_context(package,{},procedure_definitions={'dbo.p':'CREATE PROC dbo.P @formal int AS SELECT @formal'})
    cfg=AssessmentConfig();ctx.metadata.update(cfg.to_dict())
    plan=build_graph_plan(ctx,cfg)
    ops=[o for values in plan.metadata['operations_by_step'].values() for o in values]
    assert ops[0]['operation_type']=='NATIVE_PROCEDURE_BODY'
    assert not any(o.get('pending_item') for o in ops)


def test_generated_argument_names_do_not_capture_unknown_body_variables():
    with pytest.raises(ConversionGap,match='No input binding'):
        translate_procedure('EXEC dbo.P @actual','CREATE PROC dbo.P @formal int AS SELECT @formal + @ssis_proc0',
            {'parameter_bindings':[{'ParameterName':'actual','DtsVariableName':'User::Input'}]},'Package','Legacy',AssessmentConfig(),runtime())


def test_unused_formals_do_not_silently_skip_source_argument_conversions():
    with pytest.raises(ConversionGap,match='Unused formals'):
        translate_procedure("EXEC dbo.P 'not-an-int'",'CREATE PROC dbo.P @formal int AS SELECT 1',{},'Package','Legacy',AssessmentConfig(),runtime())
