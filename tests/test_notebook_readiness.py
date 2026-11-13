"""Review clarity and initial-value failures before destructive source tasks."""
import json
from pathlib import Path
import pytest

from ssis_migration.config import AssessmentConfig
from ssis_migration.ir.models import PackageIR,VariableIR,ExecutableIR,PrecedenceConstraintIR,DataFlowIR,ComponentIR
from ssis_migration.migrate import generate_migration_artifacts
from ssis_migration.validator.native_spark import validate_native_spark
from tests.test_target_audit import cfg,task,plan,Spark,Frame,execute


def chain(*tasks):
    return [PrecedenceConstraintIR(from_executable=a.ref_id,to_executable=b.ref_id,value='Success') for a,b in zip(tasks,tasks[1:])]


def generate(ir,tmp_path,config=None):
    result=generate_migration_artifacts(ir,{},None,'.',tmp_path,config=config or cfg(
        audit_capture_table_stats=False,object_mappings={'dbo.Business':'c.data.business'}))
    assert not [f for f in result['findings'] if f['severity']=='ERROR']
    return result,json.loads(Path(result['notebook_file']).read_text())


def numeric_query_package():
    truncate=task('truncate','TRUNCATE TABLE dbo.Business')
    read=ExecutableIR(ref_id='read',executable_type='Microsoft.Pipeline')
    source=ComponentIR(ref_id='query',detected_type='OLE_DB_SOURCE',properties={'AccessMode':'3','SqlCommandVariable':'User::Query'},
        outputs=[{'ref_id':'out','columns':[{'name':'main','lineage_id':'1'}]}])
    return PackageIR(package_name='Synthetic',variables=[VariableIR(name='Query',namespace='User',scope_ref_id='Package',
        data_type='8',evaluate_as_expression=True,expression='"SELECT " + @[$Package::Date] + " AS main"')],
        metadata={'package_parameters':[{'name':'Date','data_type':'8','required':True}]},
        executables=[truncate,read],data_flows=[DataFlowIR(ref_id='read',components=[source])],precedence_constraints=chain(truncate,read))


def preflight_only(n,values):
    # Stop just after preflight; workload execution is covered by local Spark.
    import copy
    draft=copy.deepcopy(n)
    stop=next(i for i,c in enumerate(n['cells']) if c.get('metadata',{}).get('ssis_migration',{}).get('role')=='input_preflight')
    draft['cells']=draft['cells'][:stop+1]
    return execute(draft,Spark(),runtime_values=values)


@pytest.mark.parametrize('values',[{}, {'$Package::Date':'not-numeric'}, {'$Package::Date':None}, {'$Package::Date':20261111}])
def test_missing_invalid_or_wrongly_typed_numeric_input_precedes_audit_and_truncate(tmp_path,values):
    _,n=generate(numeric_query_package(),tmp_path)
    spark=Spark()
    with pytest.raises(RuntimeError,match='SSIS input preflight failed') as error:
        execute(n,spark,runtime_values=values)
    assert '$Package::Date' in str(error.value)
    assert 'not-numeric' not in str(error.value)
    assert spark.calls==[]  # Neither audit INSERT nor business TRUNCATE happened.


def test_valid_numeric_text_passes_preflight_and_preserves_source_order(tmp_path):
    _,n=generate(numeric_query_package(),tmp_path)
    env,_=preflight_only(n,{'$Package::Date':'20261111'})
    assert not env['_ssis_input_errors']
    code='\n'.join(''.join(c['source']) for c in n['cells'] if c['cell_type']=='code')
    assert code.index('TRUNCATE TABLE')<code.index('SELECT :ssis_p0 AS main')


@pytest.mark.parametrize('self_read',[False,True])
def test_assignment_outputs_need_initial_input_only_when_they_read_it(tmp_path,self_read):
    assign=ExecutableIR(ref_id='assign',executable_type='Microsoft.ExpressionTask',properties={
        'Expression':'@[User::Counter] = '+('@[User::Counter] + 1' if self_read else '7')})
    truncate=task('truncate','TRUNCATE TABLE dbo.Business')
    read=task('read','SELECT @n AS main',parameter_bindings=[{'ParameterName':'n','DtsVariableName':'User::Counter'}])
    ir=PackageIR(package_name='Synthetic',variables=[VariableIR(name='Counter',namespace='User',scope_ref_id='Package',data_type='3')],
        executables=[truncate,assign,read],precedence_constraints=chain(truncate,assign,read))
    result,n=generate(ir,tmp_path)
    record=next(r for r in result['assessment']['runtime_bindings']['variables'] if r['key']=='User::Counter')
    assert record['required_external_input'] is self_read
    assert record['source_producer_ids']==['assign']
    spark=Spark()
    if self_read:
        with pytest.raises(RuntimeError,match='preflight failed'):execute(n,spark)
        assert spark.calls==[]
    env,_=execute(n,Spark(),runtime_values={'User::Counter':10} if self_read else {})
    assert env['_ssis_values']['User::Counter']==(11 if self_read else 7)


def test_later_producer_does_not_hide_an_earlier_read_requirement(tmp_path):
    truncate=task('truncate','TRUNCATE TABLE dbo.Business')
    read=task('read','SELECT @n AS main',parameter_bindings=[{'ParameterName':'n','DtsVariableName':'User::Counter'}])
    assign=ExecutableIR(ref_id='assign',executable_type='Microsoft.ExpressionTask',properties={'Expression':'@[User::Counter] = 7'})
    ir=PackageIR(package_name='Synthetic',variables=[VariableIR(name='Counter',namespace='User',scope_ref_id='Package',data_type='3')],
        executables=[truncate,read,assign],precedence_constraints=chain(truncate,read,assign))
    _,n=generate(ir,tmp_path)
    spark=Spark()
    with pytest.raises(RuntimeError,match='preflight failed'):execute(n,spark)
    assert spark.calls==[]


def test_invalid_captured_initial_default_is_validated_before_mutations(tmp_path):
    ir=numeric_query_package();ir.metadata['package_parameters'][0].update(required=False,default_value='invalid-default')
    _,n=generate(ir,tmp_path)
    spark=Spark()
    with pytest.raises(RuntimeError,match='preflight failed'):execute(n,spark)
    assert spark.calls==[]
    preflight_only(n,{'$Package::Date':'20261111'})


def test_derived_initial_date_conversion_is_checked_without_freezing_values(tmp_path):
    from tests.test_scalar_expressions import state
    ir=state().package
    ir.package_name='Synthetic'
    ir.metadata={'package_parameters':[{'name':'Date','data_type':'8','required':True}]}
    truncate=task('truncate','TRUNCATE TABLE dbo.Business')
    read=task('read','SELECT @date AS main',parameter_bindings=[{'ParameterName':'date','DtsVariableName':'User::Date'}])
    ir.executables=[truncate,read];ir.precedence_constraints=chain(truncate,read)
    _,n=generate(ir,tmp_path)
    spark=Spark()
    with pytest.raises(RuntimeError,match='User::Date: invalid initial derived'):
        execute(n,spark,runtime_values={'$Package::Date':'20261340'})
    assert spark.calls==[]
    env,_=preflight_only(n,{'$Package::Date':'20261111'})
    assert 'User::Date' not in env['_ssis_values']
    execute(n,Spark(),runtime_values={'$Package::Date':'20261111'})


def test_all_missing_external_inputs_are_listed_without_echoing_values(tmp_path):
    ir=PackageIR(package_name='Synthetic',executables=[task('main','SELECT @a, @b',parameter_bindings=[
        {'ParameterName':'a','DtsVariableName':'$Package::A'},{'ParameterName':'b','DtsVariableName':'$Package::B'}])])
    _,n=generate(ir,tmp_path)
    spark=Spark()
    with pytest.raises(RuntimeError,match='preflight failed') as error:execute(n,spark)
    assert '$Package::A' in str(error.value) and '$Package::B' in str(error.value)
    assert spark.calls==[]


def test_source_result_is_produced_before_consumption_without_manual_override(tmp_path):
    result=task('produce','SELECT 7',ResultType='ResultSetType_SingleRow',result_bindings=[{'ResultName':'0','DtsVariableName':'User::Number'}])
    read=task('read','SELECT @n AS main',parameter_bindings=[{'ParameterName':'n','DtsVariableName':'User::Number'}])
    ir=PackageIR(package_name='Synthetic',variables=[VariableIR(name='Number',namespace='User',scope_ref_id='Package',data_type='3')],
        executables=[result,read],precedence_constraints=chain(result,read))
    report,n=generate(ir,tmp_path)
    record=next(r for r in report['assessment']['runtime_bindings']['variables'] if r['key']=='User::Number')
    assert not record['required_external_input'] and not record['initial_value_required']
    class ResultSpark(Spark):
        def sql(self,sql,args=None):
            if sql=='SELECT 7':self.calls.append((sql,args));return Frame([{0:7}])
            return super().sql(sql,args)
    env,_=execute(n,ResultSpark())
    assert env['_ssis_values']['User::Number']==7


def test_shared_review_groups_audit_control_sections_but_keeps_business_bodies(tmp_path):
    audit=task('group\\audit',"INSERT INTO log.Run (TableProcessKey,BranchName,ProcessingSummaryGroup) VALUES (1,'b','g')",_parent_ref_id='group')
    group=ExecutableIR(ref_id='group',executable_type='STOCK:SEQUENCE')
    main=task('main','SELECT @n AS main',parameter_bindings=[{'ParameterName':'n','DtsVariableName':'$Package::Key'}])
    ir=PackageIR(package_name='Synthetic',executables=[group,audit,main],precedence_constraints=chain(group,main))
    _,n=generate(ir,tmp_path)
    markdown='\n'.join(''.join(c['source']) for c in n['cells'] if c['cell_type']=='markdown')
    code='\n'.join(''.join(c['source']) for c in n['cells'] if c['cell_type']=='code')
    assert 'Target audit replacements (1)' in markdown and 'Control and disabled sections (2)' in markdown
    assert 'group\\audit' in markdown and 'group' in markdown and '_completed_' not in code
    assert 'SELECT :ssis_p0 AS main' in code
    assert 'Required runtime inputs' in markdown and '"$Package::Key": null' in markdown
    assert all(validate_native_spark(''.join(c['source']))['status']=='PASS' for c in n['cells'] if c['cell_type']=='code')


def test_readable_blockers_keep_ids_in_evidence_and_stop_before_work(tmp_path):
    ir=PackageIR(package_name='Synthetic',executables=[task('missing','EXEC dbo.Missing')])
    result=generate_migration_artifacts(ir,{},None,'.',tmp_path,config=cfg())
    n=json.loads(Path(result['notebook_file']).read_text())
    markdown='\n'.join(''.join(c['source']) for c in n['cells'] if c['cell_type']=='markdown')
    assert '**Execution readiness: BLOCKED_BY_REVIEW**' in markdown
    assert 'Blocking findings (' in markdown and 'MISSING_PROCEDURE_DEFINITION' in markdown
    assert "'finding_id':" not in markdown and 'BEGIN PENDING' not in markdown
    code='\n'.join(''.join(c['source']) for c in n['cells'] if c['cell_type']=='code')
    assert 'BEGIN PENDING' in code and 'EXEC dbo.Missing' in code
    spark=Spark()
    with pytest.raises(RuntimeError,match='Conversion has unresolved findings'):execute(n,spark)
    assert spark.calls==[]


def test_nonshared_per_step_notebooks_keep_control_cells():
    from ssis_migration.generator.notebook import NotebookAssembler
    p={'package_name':'P','metadata':{'planner':'source_graph'},'steps':[{'step_id':'S','step_type':'SEQUENCE_START',
        'name':'Container','metadata':{'source_ref_id':'S'}}]}
    n=NotebookAssembler().assemble(p,[],{'S':[{'operation_id':'S','operation_type':'STRUCTURAL','code':'_completed_S = True'}]})
    assert any('_completed_S' in ''.join(c['source']) for c in n['cells'] if c['cell_type']=='code')


def test_lookup_resource_cleanup_does_not_mask_business_failure():
    from ssis_migration.generator.notebook import NotebookAssembler
    p={'package_name':'P','metadata':{'planner':'source_graph'},'steps':[{'step_id':'S','step_type':'NATIVE_DATA_FLOW','name':'Flow',
        'metadata':{'source_ref_id':'S','requires_cache_cleanup':True}}]}
    n=NotebookAssembler().assemble(p,[],{'S':[{'operation_id':'Lookup','operation_type':'NATIVE_DATAFRAME',
        'code':"_ssis_cached_frames.append(_fake_cached)\nraise ValueError('original business failure')"}]})
    cell=next(c for c in n['cells'] if c.get('metadata',{}).get('ssis_migration',{}).get('source_ref_id')=='S')
    class Cached:
        def unpersist(self,blocking):raise RuntimeError('cleanup failure')
    data={'_step_id':'all','_fake_cached':Cached()}
    with pytest.raises(ValueError,match='original business'):exec(''.join(cell['source']),data)
    assert data['_ssis_cache_cleanup_errors']==['RuntimeError']


@pytest.mark.parametrize('values',[{}, {'$Runtime::SourceTimeZone':'Invalid/Zone'},
    {'$Runtime::SourceTimeZone':'UTC','$Runtime::SourceClock':'2026-11-13T12:00:00'}])
def test_source_clock_policy_is_validated_before_truncate(tmp_path,values):
    truncate=task('truncate','TRUNCATE TABLE dbo.Business')
    clock=ExecutableIR(ref_id='clock',executable_type='Microsoft.ExpressionTask',properties={'Expression':'@[User::Date] = GETDATE()'})
    ir=PackageIR(package_name='Synthetic',variables=[VariableIR(name='Date',namespace='User',scope_ref_id='Package',data_type='7')],
        executables=[truncate,clock],precedence_constraints=chain(truncate,clock))
    _,n=generate(ir,tmp_path)
    spark=Spark()
    with pytest.raises(RuntimeError,match='preflight failed'):execute(n,spark,runtime_values=values)
    assert spark.calls==[]


def test_sum_overflow_setting_is_checked_before_earlier_truncate(tmp_path):
    from types import SimpleNamespace
    from tests.test_backend_transforms import aggregate_flow
    flow=aggregate_flow();truncate=task('truncate','TRUNCATE TABLE dbo.Business')
    owner=ExecutableIR(ref_id=flow.ref_id,executable_type='Microsoft.Pipeline')
    ir=PackageIR(package_name='Synthetic',executables=[truncate,owner],data_flows=[flow],precedence_constraints=chain(truncate,owner))
    config=cfg(audit_capture_table_stats=False,object_mappings={'dbo.Business':'c.data.business','dbo.A':'c.data.a'})
    _,n=generate(ir,tmp_path,config)
    spark=Spark();spark.conf=SimpleNamespace(get=lambda key:'false')
    with pytest.raises(RuntimeError,match='Required Spark setting before writes'):execute(n,spark)
    assert spark.calls==[]
