import json
from types import SimpleNamespace
from decimal import Decimal
import pytest
pytest.importorskip('pyspark')
from tests.test_bound_sql_spark import spark
from tests.test_backend_transforms import cfg,union_flow,aggregate_flow,lookup_flow,join_flow,column,source,path
from ssis_migration.generator.graph_native import dataflow_operations,variable
from ssis_migration.bound_sql import BindingContext,runtime_setup
from ssis_migration.ir.models import PackageIR,VariableIR
from ssis_migration.procedure_translation import translate_procedure
from ssis_migration.verification import compare_frames


def data_env(spark,variables=None):
    data={'spark':spark,'dbutils':SimpleNamespace(widgets=SimpleNamespace(get=lambda name:'{}'))}
    exec(runtime_setup(variables or {}),data);return data


def test_union_sort_preserves_duplicates_nulls_and_lineage(spark):
    spark.createDataFrame([(3,),(None,),(1,)],'id int').write.mode('overwrite').saveAsTable('backend_a')
    spark.createDataFrame([(1,),(2,)],'id int').write.mode('overwrite').saveAsTable('backend_b')
    ops,findings,_=dataflow_operations(union_flow(),cfg());assert not [f for f in findings if f['severity']=='ERROR']
    data=data_env(spark)
    for op in ops:exec(op['code'],data)
    assert [r.id for r in data[variable('Sort:o')].collect()]==[None,1,1,2,3]


def test_grouped_aggregate_uses_real_source_null_and_count_semantics(spark):
    spark.createDataFrame([(1,2),(1,None),(2,5)],'id int,value long').write.mode('overwrite').saveAsTable('backend_a')
    ops,findings,_=dataflow_operations(aggregate_flow(),cfg());assert not [f for f in findings if f['severity']=='ERROR']
    data=data_env(spark)
    for op in ops:exec(op['code'],data)
    rows={r.id:r.asDict() for r in data[variable('G:o')].collect()}
    assert rows[1]=={'id':1,'total':2,'rows':2,'nonnull':1}


def test_lookup_preserves_rows_and_rejects_duplicate_reference_matches(spark):
    spark.createDataFrame([(1,),(1,),(2,)],'id int').write.mode('overwrite').saveAsTable('backend_a')
    spark.createDataFrame([(1,'one'),(2,'two')],'id int,label string').write.mode('overwrite').saveAsTable('backend_reference')
    ops,findings,_=dataflow_operations(lookup_flow(),cfg());assert not [f for f in findings if f['severity']=='ERROR']
    data=data_env(spark)
    for op in ops:exec(op['code'],data)
    assert sorted((r.id,r.label) for r in data[variable('L')].collect())==[(1,'one'),(1,'one'),(2,'two')]
    for cached in data['_ssis_cached_frames']:cached.unpersist(blocking=True)
    spark.createDataFrame([(1,'duplicate')],'id int,label string').write.mode('append').saveAsTable('backend_reference')
    data=data_env(spark)
    with pytest.raises(RuntimeError,match='not unique'):
        for op in ops:exec(op['code'],data)
    for cached in data['_ssis_cached_frames']:cached.unpersist(blocking=True)


@pytest.mark.parametrize('n,expected',[(2,13),(0,0)])
def test_procedure_locals_and_both_if_branches_on_spark(spark,n,expected):
    from ssis_migration.config import AssessmentConfig
    state=BindingContext(PackageIR(variables=[VariableIR(name='Result',namespace='User',scope_ref_id='Package',data_type='3')]))
    props={'ResultType':'ResultSetType_SingleRow','result_bindings':[{'ResultName':'0','DtsVariableName':'User::Result'}]}
    definition='CREATE PROC dbo.P @n int AS BEGIN DECLARE @x int = @n+1; IF @x>2 BEGIN SET @x=@x+10; END ELSE BEGIN SET @x=0; END; SELECT @x; END'
    code,_,_=translate_procedure('EXEC dbo.P '+str(n),definition,props,'Package',None,AssessmentConfig(),state)
    data=data_env(spark);exec(code,data)
    assert data['_ssis_values']['User::Result']==expected


def test_comparison_preserves_duplicate_counts_and_reports_measured_scope(spark):
    a=spark.createDataFrame([(1,'x'),(1,'x'),(2,None)],'id int,value string')
    b=spark.createDataFrame([(1,'x'),(2,None)],'id int,value string')
    result=compare_frames(a,b)
    assert result['status']=='MISMATCH' and result['missing_rows']==1 and result['extra_rows']==0
    assert result['legacy_match_percent'] is None and result['scope']=='DATASET'


def test_comparison_key_constraints_and_numeric_tolerances(spark):
    a=spark.createDataFrame([(1,Decimal('1.00')),(2,None)],'id int,value decimal(10,2)')
    b=spark.createDataFrame([(1,Decimal('1.01')),(2,None)],'id int,value decimal(10,2)')
    assert compare_frames(a,b,keys=['id'],absolute_tolerances={'value':Decimal('.02')})['status']=='MATCH'
    assert compare_frames(a,b,keys=['id'])['column_mismatch_counts']['value']==1
    duplicate=spark.createDataFrame([(1,Decimal('1.00')),(1,None)],a.schema)
    assert compare_frames(duplicate,b,keys=['id'])['status']=='INVALID_COMPARISON'


@pytest.mark.parametrize('join,nulls,expected',[('2','true',3),('2','false',2),('1','true',4),('1','false',4),('0','true',5),('0','false',6)])
def test_merge_join_captures_join_kind_null_equality_and_duplicates(spark,join,nulls,expected):
    spark.createDataFrame([(None,),(1,),(1,),(3,)],'id int').write.mode('overwrite').saveAsTable('backend_a')
    spark.createDataFrame([(None,),(1,),(2,)],'id int').write.mode('overwrite').saveAsTable('backend_b')
    ops,findings,_=dataflow_operations(join_flow(join,nulls),cfg());assert not [f for f in findings if f['severity']=='ERROR']
    data=data_env(spark)
    for op in ops:exec(op['code'],data)
    rows=data[variable('J:o')].collect()
    assert len(rows)==expected
    assert sum(r.left_id==1 and r.right_id==1 for r in rows)==2


def test_lossless_conversion_and_rowcount_share_real_pipeline_state(spark):
    from ssis_migration.ir.models import ComponentIR,DataFlowIR
    spark.createDataFrame([(None,),(1,),(1,),(3,)],'id int').write.mode('overwrite').saveAsTable('backend_a')
    a=source('A','dbo.A',[column('id',1)])
    convert=ComponentIR(ref_id='Convert',detected_type='DATA_CONVERSION',inputs=[{'ref_id':'D:i'}],outputs=[{'ref_id':'D:o','columns':[
        column('wide',3,'i8',SourceInputColumnLineageID='1')]}])
    count=ComponentIR(ref_id='Count',detected_type='ROW_COUNT',properties={'VariableName':'User::Count'},inputs=[{'ref_id':'C:i'}],outputs=[{'ref_id':'C:o'}])
    flow=DataFlowIR(ref_id='Flow',components=[a,convert,count],paths=[path('A:out','D:i'),path('D:o','C:i')])
    state=BindingContext(PackageIR(variables=[VariableIR(name='Count',namespace='User',scope_ref_id='Package',data_type='20')]))
    ops,findings,_=dataflow_operations(flow,cfg(),runtime=state);assert not [f for f in findings if f['severity']=='ERROR']
    data=data_env(spark)
    for op in ops:exec(op['code'],data)
    assert data['_ssis_values']['User::Count']==4
    assert data[variable('C:o')].schema['wide'].dataType.simpleString()=='bigint'


def test_comparison_does_not_round_large_decimal_differences_into_tolerance(spark):
    a=spark.createDataFrame([(1,Decimal('9007199254740993'))],'id int,value decimal(20,0)')
    b=spark.createDataFrame([(1,Decimal('0'))],'id int,value decimal(20,0)')
    result=compare_frames(a,b,keys=['id'],absolute_tolerances={'value':9007199254740992.0})
    assert result['status']=='MISMATCH'


def test_target_inspection_is_metadata_only_and_does_not_confirm_identities(spark):
    from ssis_migration.target_inspection import inspect_targets
    spark.createDataFrame([(1,)],'id int').write.mode('overwrite').saveAsTable('backend_a')
    report=inspect_targets(spark,{'metadata':{'mapping_uses':[
        {'source':'dbo.A','target':'spark_catalog.default.backend_a','connection_reference':'one'},
        {'source':'dbo.A','target':'spark_catalog.default.backend_a','connection_reference':'two'},
        {'source':'dbo.Missing','target':'spark_catalog.default.missing_backend_table'}]}})
    existing=next(o for o in report['objects'] if o['status']=='INSPECTED')
    assert len(existing['origins'])==2 and existing['provider']=='parquet'
    assert existing['business_identity_confirmed'] is False and report['execution_gate_changed'] is False
    assert any(o['status']=='MISSING' for o in report['objects'])


def test_generated_lookup_notebook_releases_disk_cache_when_business_checks_fail(spark,tmp_path):
    from pathlib import Path
    from ssis_migration.migrate import generate_migration_artifacts
    from ssis_migration.ir.models import ExecutableIR
    spark.catalog.clearCache()
    spark.createDataFrame([(1,)],'id int').write.mode('overwrite').saveAsTable('backend_a')
    spark.createDataFrame([(1,'a'),(1,'b')],'id int,label string').write.mode('overwrite').saveAsTable('backend_reference')
    flow=lookup_flow();ir=PackageIR(package_name='Synthetic',executables=[ExecutableIR(ref_id='Flow',executable_type='Microsoft.Pipeline')],data_flows=[flow])
    result=generate_migration_artifacts(ir,{},None,'.',tmp_path,config=cfg())
    assert not [f for f in result['findings'] if f['severity']=='ERROR']
    notebook=json.loads(Path(result['notebook_file']).read_text())
    widgets={'catalog':'main','schema':'','step_id':'all','ssis_runtime_values':'{}'}
    data={'spark':spark,'dbutils':SimpleNamespace(widgets=SimpleNamespace(text=lambda *a:None,get=widgets.__getitem__))}
    with pytest.raises(RuntimeError,match='not unique'):
        for cell in notebook['cells']:
            if cell['cell_type']=='code':exec(''.join(cell['source']),data)
    assert spark._jsparkSession.sharedState().cacheManager().cachedData().size()==0
