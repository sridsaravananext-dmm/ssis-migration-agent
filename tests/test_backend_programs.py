from datetime import datetime
import pytest
from ssis_migration.bound_sql import BindingContext,runtime_setup
from ssis_migration.ir.models import PackageIR,VariableIR
from ssis_migration.config import AssessmentConfig
from ssis_migration.procedure_translation import translate_procedure
from ssis_migration.scalar_expressions import assignment_code
from ssis_migration.mapping import ConversionGap
from ssis_migration.validator.native_spark import validate_native_spark
from tests.test_bound_sql import environment


def runtime():return BindingContext(PackageIR(variables=[VariableIR(name='Date',namespace='User',scope_ref_id='Package',data_type='7'),
    VariableIR(name='Result',namespace='User',scope_ref_id='Package',data_type='3')]))


@pytest.mark.parametrize('expression,expected',[
    ('DATEADD("month",1,(DT_DATE) "2024-01-31")',datetime(2024,2,29)),
    ('DATEADD("year",-1,(DT_DATE) "2024-02-29")',datetime(2023,2,28)),
    ('DATEADD("ms",-DATEPART("ms",(DT_DATE) "2026-11-13T12:01:02.123"),(DT_DATE) "2026-11-13T12:01:02.123")',datetime(2026,11,13,12,1,2)),
    ('DATEADD("Millisecond",0 - DATEPART("Millisecond",(DT_DATE) "2026-11-13T12:01:02.123"),(DT_DATE) "2026-11-13T12:01:02.123")',datetime(2026,11,13,12,1,2)),
])
def test_calendar_and_cutoff_expressions(expression,expected):
    state=runtime();code=assignment_code('@[User::Date] = '+expression,'Package',state)
    env,_,_=environment(state,[]);exec(code,env)
    assert env['_ssis_values']['User::Date']==expected
    assert validate_native_spark(code)['status']=='PASS'


def test_getdate_requires_timezone_and_supports_replay_clock():
    state=runtime();code=assignment_code('@[User::Date] = GETDATE()','Package',state)
    assert state.inputs['$Runtime::SourceTimeZone']['timezone']
    env,_,_=environment(state,[],{'$Runtime::SourceTimeZone':'Asia/Kolkata','$Runtime::SourceClock':'2026-11-13T07:00:00+00:00'})
    exec(code,env);assert env['_ssis_values']['User::Date']==datetime(2026,11,13,12,30)
    assert env['_ssis_source_now']('UTC')==datetime(2026,11,13,7)
    env,_,_=environment(state,[])
    with pytest.raises(RuntimeError,match='SourceTimeZone'):exec(code,env)


def test_integer_negation_checks_overflow_before_later_arithmetic_can_hide_it():
    state=runtime();state.package.variables.append(VariableIR(name='Minimum',namespace='User',scope_ref_id='Package',data_type='3',value='-2147483648'))
    code=assignment_code('@[User::Result] = -@[User::Minimum] + @[User::Minimum]','Package',state)
    env,_,_=environment(state,[])
    with pytest.raises(RuntimeError,match='overflow'):exec(code,env)


def test_datefirst_dependent_parts_and_unknown_functions_remain_blocked():
    for text in ['DATEPART("weekday",GETDATE())','DATEADD("week",1,GETDATE())','FOO(GETDATE())']:
        state=runtime()
        with pytest.raises(ConversionGap):assignment_code('@[User::Date] = '+text,'Package',state)
        assert not state.inputs


def test_procedure_locals_conditionals_emit_only_native_static_sql():
    definition='''CREATE PROC dbo.P @n int AS BEGIN
      DECLARE @local int = @n + 1;
      IF @local > 0 BEGIN SET @local = @local + 2; END ELSE BEGIN SET @local = 0; END;
      SELECT @local;
    END'''
    state=runtime();props={'ResultType':'ResultSetType_SingleRow','result_bindings':[{'ResultName':'0','DtsVariableName':'User::Result'}]}
    code,_,evidence=translate_procedure('EXEC dbo.P 2',definition,props,'Package',None,AssessmentConfig(),state)
    assert evidence['procedure_program'] and evidence['local_types']=={'local':'INTEGER'}
    assert validate_native_spark(code)['status']=='PASS'
    assert 'if _ssis_proc_condition' in code and '.limit(2).collect()' in code


@pytest.mark.parametrize('body',[
    'DECLARE @x int; EXEC dbo.Other;',
    'DECLARE @x int; IF 1=1 SELECT 1;',
    'DECLARE @x int; IF 1=1 BEGIN SET @x=1;',
    'DECLARE @x int; WHILE @x>0 SET @x=@x-1;',
    'DECLARE @x int; SELECT @x = id FROM dbo.A;',
])
def test_incomplete_or_unsupported_programs_preserve_no_partial_bindings(body):
    state=runtime()
    with pytest.raises(ConversionGap):translate_procedure('EXEC dbo.P','CREATE PROC dbo.P AS '+body,{},'Package',None,AssessmentConfig(target_schema='data'),state)
    assert not state.inputs


def test_procedure_program_survives_full_dtsx_dependency_intake(tmp_path):
    import json
    from pathlib import Path
    from ssis_migration.migrate import run_migration
    source=tmp_path/'program.dtsx'
    source.write_text('''<D:Executable xmlns:D="www.microsoft.com/SqlServer/Dts" xmlns:S="sql" D:refId="Package" D:ObjectName="Program">
    <D:Variables><D:Variable D:ObjectName="Result" D:Namespace="User"><D:VariableValue D:DataType="3"/></D:Variable></D:Variables>
    <D:Executables><D:Executable D:refId="Task" D:ExecutableType="Microsoft.ExecuteSQLTask"><D:ObjectData>
    <S:SqlTaskData S:SqlStatementSource="EXEC dbo.P 2" S:ResultType="ResultSetType_SingleRow"><S:ResultBinding S:ResultName="0" S:DtsVariableName="User::Result"/></S:SqlTaskData>
    </D:ObjectData></D:Executable></D:Executables></D:Executable>''')
    definition=tmp_path/'procedure.sql'
    definition.write_text('CREATE PROC dbo.P @n int AS BEGIN SET NOCOUNT ON; DECLARE @x int=@n+1; IF @x>2 BEGIN SET @x=10; END ELSE BEGIN SET @x=0; END; SELECT @x; END')
    report=run_migration(source,tmp_path/'out',procedures_paths=[definition],catalog='main',target_schema='data')
    item=report['packages'][0]
    assert item['status']!='FAILED',item.get('error')
    assert not [f for f in item['findings'] if f['severity']=='ERROR']
    notebook=json.loads(Path(item['artifacts']['notebook']).read_text())
    assert any('if _ssis_proc_condition' in ''.join(c['source']) for c in notebook['cells'])
