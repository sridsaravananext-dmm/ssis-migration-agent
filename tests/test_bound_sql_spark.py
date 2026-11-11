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
