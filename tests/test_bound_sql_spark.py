"""Optional local Spark checks using synthetic tables only; no SSIS/Databricks claim."""
import json
from types import SimpleNamespace

import pytest

pyspark=pytest.importorskip('pyspark')
from pyspark.sql import SparkSession

from ssis_migration.bound_sql import BindingContext, bound_task, variable_query, runtime_setup
from ssis_migration.config import AssessmentConfig
from ssis_migration.ir.models import PackageIR, VariableIR


@pytest.fixture(scope='module')
def spark(tmp_path_factory):
    warehouse=tmp_path_factory.mktemp('spark-warehouse')
    session=(SparkSession.builder.master('local[1]').appName('ssis-native-bindings-tests')
        .config('spark.ui.enabled','false').config('spark.sql.shuffle.partitions','1')
        .config('spark.driver.host','127.0.0.1').config('spark.driver.bindAddress','127.0.0.1')
        .config('spark.sql.warehouse.dir',str(warehouse)).getOrCreate())
    session.sparkContext.setLogLevel('ERROR')
    session.createDataFrame([(1,10),(2,20)],'id int, batch_key int').write.saveAsTable('bound_orders')
    yield session
    session.stop()


def cfg():
    return AssessmentConfig(object_mappings={'dbo.Orders':'spark_catalog.default.bound_orders'})


def env(runtime,spark,values):
    data={'spark':spark,'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda name:json.dumps(values)))}
    exec(runtime_setup(runtime.defaults),data)
    return data


def test_real_spark_named_query_and_single_row_result_binding(spark):
    runtime=BindingContext(PackageIR(variables=[VariableIR(name='Result',namespace='User',scope_ref_id='Package',data_type='3')]))
    props={'SqlStatementSource':'SELECT COUNT(*) FROM dbo.Orders WHERE batch_key=@key',
        'parameter_bindings':[{'ParameterName':'@key','DtsVariableName':'$Package::Key'}],
        'ResultType':'ResultSetType_SingleRow','result_bindings':[{'ResultName':'0','DtsVariableName':'User::Result'}]}
    code,_,_=bound_task(props,'Package','Legacy',cfg(),runtime)
    data=env(runtime,spark,{'$Package::Key':10})
    exec(code,data)
    assert data['_ssis_values']['User::Result']==1


def test_expression_task_updates_values_used_by_later_native_sql(spark):
    from ssis_migration.scalar_expressions import assignment_code
    runtime=BindingContext(PackageIR(variables=[VariableIR(name='Key',namespace='User',scope_ref_id='Package',data_type='3',value='10'),
        VariableIR(name='Result',namespace='User',scope_ref_id='Package',data_type='3')]))
    assignment=assignment_code('@[User::Key] = 20','Package',runtime)
    code,_,_=bound_task({'SqlStatementSource':'SELECT id FROM dbo.Orders WHERE batch_key=@key',
        'parameter_bindings':[{'ParameterName':'key','DtsVariableName':'User::Key'}],
        'ResultType':'ResultSetType_SingleRow','result_bindings':[{'ResultName':'0','DtsVariableName':'User::Result'}]},
        'Package','Legacy',cfg(),runtime)
    data=env(runtime,spark,{})
    exec(assignment,data);exec(code,data)
    assert data['_ssis_values']['User::Result']==2


def test_native_target_audit_start_inserts_into_existing_schema_with_bound_values(spark):
    from ssis_migration.generator.target_audit import RUN_TYPES,setup_code
    from ssis_migration.ir.models import ExecutableIR
    from ssis_migration.context.builder import build_migration_context
    from ssis_migration.planner.task_graph import build_graph_plan
    schema=', '.join(name+' '+kind for name,kind in RUN_TYPES.items())
    spark.sql('CREATE TABLE audit_notebooks ('+schema+') USING PARQUET')
    ir=PackageIR(package_name="quoted 'package'",executables=[ExecutableIR(ref_id='Package\\Main',executable_type='Microsoft.ExecuteSQLTask',properties={'SqlStatementSource':'SELECT 1'})])
    config=AssessmentConfig(catalog='spark_catalog',audit_mode='target',audit_schema='default',audit_capture_table_stats=False)
    context=build_migration_context(ir,{});context.metadata.update(config.to_dict())
    plan=build_graph_plan(context,config)
    values={'target_audit_job':'{}','execution_key':''}
    data={'spark':spark,'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda name:values[name]))}
    code=setup_code(plan.to_dict())
    with pytest.raises(RuntimeError,match='requires existing Delta'):exec(code,data)
    assert spark.table('audit_notebooks').count()==0
    # Exercise the generated insert independently with a Parquet fixture;
    # the full adapter intentionally refuses non-Delta storage.
    import ast
    tree=ast.parse(code)
    definition=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_ta_insert_run')
    exec(compile(ast.Module(body=[definition],type_ignores=[]),'<audit-insert>','exec'),data)
    from datetime import datetime
    data['_ta_run_fields']=set(RUN_TYPES)
    data['_ta_insert_run']({'notebook_key':41,'master_key':41,'notebook':"quoted 'package'",'time_start':datetime(2026,1,1),
        'time_end':None,'duration_time':None,'user_id':'synthetic','status':'Running','source_file_path':None,'target_table':'','message':''})
    row=spark.table('audit_notebooks').first()
    assert row['notebook']=="quoted 'package'" and row['status']=='Running'
    assert row['notebook_key']==41 and row['master_key']==row['notebook_key']
    # Delta UPDATE/DESCRIBE behavior belongs to the target-runtime checks;
    # this local Parquet fixture verifies native argument/type/storage mechanics.


def test_real_spark_variable_expression_uses_bound_numeric_values(spark):
    runtime=BindingContext(PackageIR())
    call,_=variable_query({'status':'DECLARED_REQUIRES_RUNTIME_REVIEW','evaluate_as_expression':True,
        'expression':'"SELECT id FROM dbo.Orders WHERE batch_key=" + @[$Package::Key]'},
        'Package','Legacy',cfg(),runtime)
    data=env(runtime,spark,{'$Package::Key':20})
    exec('result = '+call,data)
    assert data['result'].first()['id']==2


def test_mapped_target_audit_insert_uses_real_spark_identifiers_and_bound_status(spark):
    import ast
    from datetime import datetime
    from ssis_migration.generator.target_audit import setup_code
    from ssis_migration.context.builder import build_migration_context
    from ssis_migration.planner.task_graph import build_graph_plan
    from tests.test_general_audit import CUSTOM
    from ssis_migration.audit_contract import storage_contract, RUN_TYPES
    # Spark's local session catalog forbids spaces in table names. Keep
    # mapped/quoted columns while using a valid local table identifier.
    overrides={**CUSTOM,'notebook_table':'mapped_run_ledger'}
    contract=storage_contract(overrides)
    aliases=contract['notebook_columns']
    schema=', '.join('`'+aliases[k]+'` '+kind for k,kind in RUN_TYPES.items() if aliases[k] is not None)
    spark.sql('CREATE TABLE mapped_run_ledger ('+schema+') USING PARQUET')
    config=AssessmentConfig(catalog='spark_catalog',audit_mode='target',audit_schema='default',audit_contract=overrides)
    context=build_migration_context(PackageIR(package_name='Synthetic'),{})
    plan=build_graph_plan(context,config)
    tree=ast.parse(setup_code(plan.to_dict()))
    definition=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_ta_insert_run')
    data={'spark':spark,'_ta_run_fields':{k for k,v in aliases.items() if v is not None}}
    exec(compile(ast.Module(body=[definition],type_ignores=[]),'<mapped-insert>','exec'),data)
    data['_ta_insert_run']({'notebook_key':52,'master_key':52,'notebook':"package 'name'",'time_start':datetime(2026,1,1),
        'time_end':None,'user_id':'synthetic','status':"DONE 'OK'",'message':"quoted 'message'"})
    row=spark.table('mapped_run_ledger').first()
    assert row['run notebook_key']==52 and row['run status']=="DONE 'OK'" and row['run message']=="quoted 'message'"


def test_generated_preflight_prevents_truncate_until_numeric_inputs_are_valid(spark,tmp_path):
    from pathlib import Path
    from ssis_migration.migrate import generate_migration_artifacts
    from ssis_migration.generator.graph_native import variable
    from tests.test_notebook_readiness import numeric_query_package
    spark.createDataFrame([(1,)],'id int').write.saveAsTable('preflight_business')
    config=AssessmentConfig(catalog='spark_catalog',target_schema='default',
        object_mappings={'dbo.Business':'spark_catalog.default.preflight_business'})
    result=generate_migration_artifacts(numeric_query_package(),{},None,'.',tmp_path,config=config)
    assert not [f for f in result['findings'] if f['severity']=='ERROR']
    notebook=json.loads(Path(result['notebook_file']).read_text())
    def run(values):
        widgets={'catalog':'spark_catalog','schema':'default','step_id':'all','ssis_runtime_values':json.dumps(values)}
        data={'spark':spark,'dbutils':SimpleNamespace(widgets=SimpleNamespace(text=lambda *a:None,get=widgets.__getitem__))}
        for cell in notebook['cells']:
            if cell['cell_type']=='code':exec(''.join(cell['source']),data)
        return data
    with pytest.raises(RuntimeError,match='preflight failed'):run({})
    with pytest.raises(RuntimeError,match='preflight failed'):run({'$Package::Date':'invalid'})
    assert spark.table('preflight_business').count()==1
    data=run({'$Package::Date':'20261111'})
    assert spark.table('preflight_business').count()==0
    assert data[variable('out')].limit(1).collect()[0]['main']==20261111


@pytest.mark.parametrize('expression',[False,True])
def test_real_variable_execute_sql_result_drives_later_bound_insert(spark,tmp_path,expression):
    from pathlib import Path
    from ssis_migration.migrate import generate_migration_artifacts
    from ssis_migration.ir.models import ExecutableIR,PrecedenceConstraintIR
    from tests.test_variable_tasks import fixture
    ir,query=fixture(expression);ir.variables[1].value='2'
    table='variable_results_'+('expression' if expression else 'captured')
    spark.createDataFrame([],'value int').write.saveAsTable(table)
    writer=ExecutableIR(ref_id='InsertResult',executable_type='Microsoft.ExecuteSQLTask',properties={
        'SqlStatementSource':'INSERT INTO dbo.Results (value) VALUES (@value)',
        'parameter_bindings':[{'ParameterName':'value','DtsVariableName':'User::Result'}]})
    ir.executables.append(writer)
    ir.precedence_constraints=[PrecedenceConstraintIR(from_executable=query.ref_id,to_executable=writer.ref_id,value='Success')]
    config=AssessmentConfig(catalog='spark_catalog',target_schema='default',object_mappings={
        'dbo.Orders':'spark_catalog.default.bound_orders','dbo.Results':'spark_catalog.default.'+table})
    result=generate_migration_artifacts(ir,{},None,'.',tmp_path,config=config)
    assert not [f for f in result['findings'] if f['severity']=='ERROR']
    n=json.loads(Path(result['notebook_file']).read_text())
    widgets={'catalog':'spark_catalog','schema':'default','step_id':'all','ssis_runtime_values':'{}'}
    data={'spark':spark,'dbutils':SimpleNamespace(widgets=SimpleNamespace(text=lambda *a:None,get=widgets.__getitem__))}
    for cell in n['cells']:
        if cell['cell_type']=='code':exec(''.join(cell['source']),data)
    assert data['_ssis_values']['User::Result']==1
    assert spark.table(table).limit(1).collect()[0]['value']==1


def test_real_spark_insert_then_select_batch_preserves_order_and_state(spark):
    runtime=BindingContext(PackageIR(variables=[VariableIR(name='Result',namespace='User',scope_ref_id='Package',data_type='3')]))
    props={'SqlStatementSource':'INSERT INTO dbo.Orders (id,batch_key) VALUES (@id,30)\nSELECT MAX(id) FROM dbo.Orders',
        'parameter_bindings':[{'ParameterName':'@id','DtsVariableName':'$Package::ID'}],
        'ResultType':'ResultSetType_SingleRow','result_bindings':[{'ResultName':'0','DtsVariableName':'User::Result'}]}
    code,_,evidence=bound_task(props,'Package','Legacy',cfg(),runtime)
    data=env(runtime,spark,{'$Package::ID':3})
    exec(code,data)
    assert evidence['identity_review']  # Synthetic execution does not waive the production review.
    assert data['_ssis_values']['User::Result']==3


def test_real_spark_destination_insert_preserves_mapping_and_target_column_order(spark):
    from pyspark.sql import functions as F
    from tests.test_destination_write import destination
    from ssis_migration.generator.destination_write import append_code
    from ssis_migration.mapping import target_table
    spark.createDataFrame([('seed',0)],'label string, id int').write.saveAsTable('mapped_destination')
    frame=spark.createDataFrame([(10,'ok'),(11,'😀😀')],'src_id int, src_label string')
    code,_=append_code(destination(),'frame',['src_id','src_label'],{'1':'src_id','2':'src_label'},target_table('spark_catalog.default.mapped_destination'))
    exec(code,{'spark':spark,'frame':frame,'F':F})
    assert spark.table('mapped_destination').where('id=10').first()['label']=='ok'
    assert spark.table('mapped_destination').where('id=11').first()['label']=='😀😀'


@pytest.mark.parametrize('label',['abcde','😀😀😀'])
def test_real_spark_destination_length_failure_does_not_append_rows(spark,label):
    from pyspark.sql import functions as F
    from tests.test_destination_write import destination
    from ssis_migration.generator.destination_write import append_code
    from ssis_migration.mapping import target_table
    frame=spark.createDataFrame([(12,label)],'src_id int, src_label string')
    code,_=append_code(destination(),'frame',['src_id','src_label'],{'1':'src_id','2':'src_label'},target_table('spark_catalog.default.mapped_destination'))
    before=spark.table('mapped_destination').count()
    with pytest.raises(Exception,match='SSIS destination'):
        exec(code,{'spark':spark,'frame':frame,'F':F})
    assert spark.table('mapped_destination').count()==before


def test_real_spark_destination_missing_table_and_type_changes_stop_before_insert(spark):
    from pyspark.sql import functions as F
    from tests.test_destination_write import destination
    from ssis_migration.generator.destination_write import append_code
    from ssis_migration.mapping import target_table
    frame=spark.createDataFrame([(1,'ok')],'src_id long, src_label string')
    code,_=append_code(destination(),'frame',['src_id','src_label'],{'1':'src_id','2':'src_label'},target_table('spark_catalog.default.missing_destination'))
    with pytest.raises(RuntimeError,match='must already exist'):exec(code,{'spark':spark,'frame':frame,'F':F})
    assert not spark.catalog.tableExists('missing_destination')
    code,_=append_code(destination(),'frame',['src_id','src_label'],{'1':'src_id','2':'src_label'},target_table('spark_catalog.default.mapped_destination'))
    before=spark.table('mapped_destination').count()
    with pytest.raises(RuntimeError,match='types differ'):exec(code,{'spark':spark,'frame':frame,'F':F})
    assert spark.table('mapped_destination').count()==before


def test_real_spark_parameterized_procedure_and_derived_date(spark):
    from ssis_migration.procedure_translation import translate_procedure
    runtime=BindingContext(PackageIR(variables=[
        VariableIR(name='Date',namespace='User',scope_ref_id='Package',data_type='7',evaluate_as_expression=True,
            expression='(DT_DATE) (SUBSTRING(@[$Package::Date],1,4) + "-" + SUBSTRING(@[$Package::Date],5,2) + "-" + RIGHT(@[$Package::Date],2))'),
        VariableIR(name='Result',namespace='User',scope_ref_id='Package',data_type='3')]))
    props={'parameter_bindings':[{'ParameterName':'@actual','DtsVariableName':'User::Date'}],
        'ResultType':'ResultSetType_SingleRow','result_bindings':[{'ResultName':'0','DtsVariableName':'User::Result'}]}
    code,_,_=translate_procedure('EXEC dbo.P @actual','CREATE PROC dbo.P @d datetime AS SELECT YEAR(@d)',props,
        'Package','Legacy',cfg(),runtime)
    data=env(runtime,spark,{'$Package::Date':'20261111'})
    exec(code,data)
    assert data['_ssis_values']['User::Result']==2026
