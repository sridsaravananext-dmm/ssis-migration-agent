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


def test_real_spark_variable_expression_uses_bound_numeric_values(spark):
    runtime=BindingContext(PackageIR())
    call,_=variable_query({'status':'DECLARED_REQUIRES_RUNTIME_REVIEW','evaluate_as_expression':True,
        'expression':'"SELECT id FROM dbo.Orders WHERE batch_key=" + @[$Package::Key]'},
        'Package','Legacy',cfg(),runtime)
    data=env(runtime,spark,{'$Package::Key':20})
    exec('result = '+call,data)
    assert data['result'].first()['id']==2


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
