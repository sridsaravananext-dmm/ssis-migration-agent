"""Computed SSIS variables remain source-derived rather than extra manual inputs."""
from datetime import datetime
from types import SimpleNamespace
import json
import pytest

from ssis_migration.bound_sql import BindingContext,bound_task,runtime_setup
from ssis_migration.ir.models import PackageIR,VariableIR
from ssis_migration.mapping import ConversionGap
from ssis_migration.config import AssessmentConfig
from ssis_migration.validator.native_spark import validate_native_spark


def state():
    return BindingContext(PackageIR(variables=[
        VariableIR(name='Date',namespace='User',scope_ref_id='Package',data_type='7',evaluate_as_expression=True,
            expression='(DT_DATE) (SUBSTRING(@[$Package::Date],1,4) + "-" + SUBSTRING(@[$Package::Date],5,2) + "-" + RIGHT(@[$Package::Date],2))'),
        VariableIR(name='DateKey',namespace='User',scope_ref_id='Package',data_type='3',evaluate_as_expression=True,
            expression='(DT_I4) @[$Package::Date]')]))


def test_date_and_integer_variables_follow_source_expressions_and_latest_inputs():
    runtime=state()
    date=runtime.read('User::Date','Package');key=runtime.read('User::DateKey','Package')
    env={'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda key:'{"$Package::Date":"20261111"}'))}
    exec(runtime_setup(runtime.defaults),env)
    exec('date = '+date+'\nkey = '+key,env)
    assert env['date']==datetime(2026,11,11) and env['key']==20261111
    assert runtime.inputs['User::Date']['derived']
    assert 'User::Date' not in runtime.defaults
    env['_ssis_values']['$Package::Date']='20261231'
    exec('date = '+date,env)
    assert env['date']==datetime(2026,12,31)
    assert validate_native_spark('value = '+date)['status']=='PASS'


def test_string_slices_preserve_utf16_units_without_row_udfs():
    runtime=BindingContext(PackageIR())
    env={'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda key:'{}'))}
    exec(runtime_setup(runtime.defaults),env)
    assert env['_ssis_substring']('a😀b',2,2)=='😀'
    assert env['_ssis_right']('a😀',2)=='😀'
    assert env['_ssis_left']('abc',0)==''
    with pytest.raises(RuntimeError):env['_ssis_substring']('abc',0,1)


@pytest.mark.parametrize('expression',['LEN("abc")','__import__("os")','(DT_WSTR, 4) @[$Package::Date]','"a" + @[$Package::Unknown]'])
def test_unknown_functions_casts_and_ambiguous_addition_remain_gaps(expression):
    runtime=BindingContext(PackageIR(variables=[VariableIR(name='X',namespace='User',scope_ref_id='Package',data_type='8',
        evaluate_as_expression=True,expression=expression)]))
    with pytest.raises(ConversionGap):runtime.read('User::X','Package')


def test_cycles_are_rejected_and_failed_task_does_not_register_executable_state():
    runtime=BindingContext(PackageIR(variables=[VariableIR(name='A',namespace='User',scope_ref_id='Package',data_type='3',evaluate_as_expression=True,expression='@[User::B]'),
        VariableIR(name='B',namespace='User',scope_ref_id='Package',data_type='3',evaluate_as_expression=True,expression='@[User::A]')]))
    with pytest.raises(ConversionGap):bound_task({'SqlStatementSource':'SELECT @p','parameter_bindings':[{'ParameterName':'p','DtsVariableName':'User::A'}]},
        'Package','Legacy',AssessmentConfig(),runtime)
    assert not runtime.inputs


def test_sensitive_literals_require_supplied_values_instead_of_compiling_redacted_text():
    runtime=BindingContext(PackageIR(variables=[VariableIR(name='Sensitive',namespace='User',scope_ref_id='Package',data_type='8',sensitive=True,value='not-exported')]))
    code=runtime.read('User::Sensitive','Package')
    assert runtime.defaults=={} and 'not-exported' not in code


def test_expression_tasks_assign_shared_state_without_row_udfs(tmp_path):
    from ssis_migration.parser.package_parser import SSISPackageParser
    from ssis_migration.ir.builder import IRBuilder
    from ssis_migration.scalar_expressions import assignment_code
    p=tmp_path/'expression.dtsx'
    p.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:refId="Package"><DTS:Variables>
      <DTS:Variable DTS:ObjectName="Table" DTS:Namespace="User"><DTS:VariableValue DTS:DataType="8">Old</DTS:VariableValue></DTS:Variable>
    </DTS:Variables><DTS:Executables><DTS:Executable DTS:refId="Assign" DTS:ExecutableType="Microsoft.ExpressionTask"><DTS:ObjectData>
      <ExpressionTask Expression="@[User::Table] = &quot;New&quot;"/>
    </DTS:ObjectData></DTS:Executable></DTS:Executables></DTS:Executable>''')
    ir=IRBuilder().build(SSISPackageParser(p).parse());runtime=BindingContext(ir)
    code=assignment_code(ir.executables[0].properties['Expression'],'Assign',runtime)
    env={'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda key:'{}'))}
    exec(runtime_setup(runtime.defaults),env);exec(code,env)
    assert env['_ssis_values']['User::Table']=='New'
    assert validate_native_spark(code)['status']=='PASS'


def test_unsupported_expression_assignment_preserves_no_partial_runtime_state():
    from ssis_migration.scalar_expressions import assignment_code
    runtime=BindingContext(PackageIR(variables=[VariableIR(name='Date',namespace='User',scope_ref_id='Package',data_type='7')]))
    with pytest.raises(ConversionGap):assignment_code('@[User::Date] = GETDATE()','Package',runtime)
    assert not runtime.inputs
