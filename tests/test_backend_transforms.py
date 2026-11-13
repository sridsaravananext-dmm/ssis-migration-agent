import pytest
from ssis_migration.config import AssessmentConfig
from ssis_migration.ir.models import ComponentIR,DataFlowIR,PackageIR,VariableIR
from ssis_migration.bound_sql import BindingContext
from ssis_migration.generator.graph_native import dataflow_operations,variable
from ssis_migration.validator.native_spark import validate_native_spark


def cfg():return AssessmentConfig(object_mappings={'dbo.A':'spark_catalog.default.backend_a','dbo.B':'spark_catalog.default.backend_b','dbo.Ref':'spark_catalog.default.backend_reference'})
def column(name,id,dtype='i4',**props):return {'name':name,'lineage_id':str(id),'data_type':dtype,'properties':props}
def source(ref,table,columns):return ComponentIR(ref_id=ref,detected_type='OLE_DB_SOURCE',properties={'OpenRowset':table},outputs=[{'ref_id':ref+':out','columns':columns}])
def path(a,b):return {'start_id':a,'end_id':b}


def union_flow():
    a=source('A','dbo.A',[column('id',1)]);b=source('B','dbo.B',[column('id',2)])
    union=ComponentIR(ref_id='U',detected_type='UNION_ALL',inputs=[{'ref_id':'U:a','columns':[column('id',1,OutputColumnLineageID='3')]},
        {'ref_id':'U:b','columns':[column('id',2,OutputColumnLineageID='3')]}],outputs=[{'ref_id':'U:o','columns':[column('id',3)]}])
    sort=ComponentIR(ref_id='Sort',detected_type='SORT',inputs=[{'ref_id':'Sort:i','columns':[column('id',3,SortKeyPosition='1')]}],outputs=[{'ref_id':'Sort:o','columns':[column('id',4)]}])
    return DataFlowIR(ref_id='Flow',components=[sort,union,b,a],paths=[path('A:out','U:a'),path('B:out','U:b'),path('U:o','Sort:i')])


def aggregate_flow():
    a=source('A','dbo.A',[column('id',1),column('value',2,'i8')])
    agg=ComponentIR(ref_id='G',detected_type='AGGREGATE',inputs=[{'ref_id':'G:i'}],outputs=[{'ref_id':'G:o','columns':[
        column('id',3,AggregationType='0',AggregationColumnId='1'),column('total',4,'i8',AggregationType='4',AggregationColumnId='2'),
        column('rows',5,'i8',AggregationType='2'),column('nonnull',6,'i8',AggregationType='1',AggregationColumnId='2')]}])
    return DataFlowIR(ref_id='Flow',components=[agg,a],paths=[path('A:out','G:i')])


def lookup_flow():
    a=source('A','dbo.A',[column('id',1)])
    lookup=ComponentIR(ref_id='L',detected_type='LOOKUP',properties={'SqlCommand':'SELECT id,label FROM dbo.Ref','CacheType':'0','NoMatchBehavior':'0'},
        inputs=[{'ref_id':'L:i','columns':[column('id',1,JoinToReferenceColumns='id')]}],outputs=[{'ref_id':'L:o','columns':[
            column('label',3,'wstr',CopyFromReferenceColumn='label')]}])
    return DataFlowIR(ref_id='Flow',components=[lookup,a],paths=[path('A:out','L:i')])


def join_flow(join='2',nulls='true'):
    a=source('A','dbo.A',[column('id',1)]);b=source('B','dbo.B',[column('id',2)])
    join=ComponentIR(ref_id='J',detected_type='MERGE_JOIN',properties={'JoinType':join,'NumKeyColumns':'1','TreatNullsAsEqual':nulls},
        inputs=[{'ref_id':'J:a','columns':[column('id',1,SortKeyPosition='1')]},{'ref_id':'J:b','columns':[column('id',2,SortKeyPosition='1')]}],
        outputs=[{'ref_id':'J:o','columns':[column('left_id',3,InputColumnID='1'),column('right_id',4,InputColumnID='2')]}])
    return DataFlowIR(ref_id='Flow',components=[a,b,join],paths=[path('A:out','J:a'),path('B:out','J:b')])


@pytest.mark.parametrize('factory',[union_flow,aggregate_flow,lookup_flow,join_flow])
def test_extended_components_have_statically_native_bodies(factory):
    ops,findings,graph=dataflow_operations(factory(),cfg())
    assert not [f for f in findings if f['severity']=='ERROR']
    assert len(ops)==len(graph['components'])
    assert all(validate_native_spark(op['code'])['status']=='PASS' for op in ops)


def test_incomplete_union_does_not_implicitly_pad_nulls():
    flow=union_flow();next(c for c in flow.components if c.ref_id=='U').inputs[1]['columns']=[]
    ops,findings,_=dataflow_operations(flow,cfg())
    assert any(f['code']=='UNION_MAPPING_REQUIRED' for f in findings)
    assert next(op for op in ops if op['operation_id']=='Sort')['operation_type']=='REVIEW_ONLY'


def test_collation_aggregates_and_unknown_lookup_policies_stay_blocked():
    flow=aggregate_flow();flow.components[0].outputs[0]['columns'][0]['properties']['AggregationComparisonFlags']='1'
    assert any(f['code']=='AGGREGATE_COLLATION_REQUIRED' for f in dataflow_operations(flow,cfg())[1])
    flow=lookup_flow();flow.components[0].properties['NoMatchBehavior']='1'
    assert any(f['code']=='LOOKUP_POLICY_UNSUPPORTED' for f in dataflow_operations(flow,cfg())[1])


def test_rowcount_records_a_source_variable_producer():
    a=source('A','dbo.A',[column('id',1)])
    count=ComponentIR(ref_id='Count',detected_type='ROW_COUNT',properties={'VariableName':'User::Count'},inputs=[{'ref_id':'C:i'}],outputs=[{'ref_id':'C:o'}])
    runtime=BindingContext(PackageIR(variables=[VariableIR(name='Count',namespace='User',scope_ref_id='Package',data_type='20')]))
    flow=DataFlowIR(ref_id='Flow',components=[a,count],paths=[path('A:out','C:i')])
    ops,findings,_=dataflow_operations(flow,cfg(),runtime=runtime)
    assert not [f for f in findings if f['severity']=='ERROR']
    assert runtime.inputs['User::Count']['write']
    assert '.count()' in ops[-1]['code'] and validate_native_spark(ops[-1]['code'])['status']=='PASS'


def test_known_class_ids_override_misleading_descriptions_and_unknown_ids_do_not_certify():
    from ssis_migration.parser.pipeline_parser import SSISPipelineParser
    parser=SSISPipelineParser()
    for identifier in ['Microsoft.OLEDBSource','{BCEFE59B-6819-47F7-A125-63753B33ABB7}','{165A526D-D5DE-47FF-96A6-F8274C19826B}']:
        component={'component_class_id':identifier,'description':'OLEDBDestination','properties':{}}
        assert parser._detect_component_type(component)=='OLE_DB_SOURCE'
        assert component['properties']['_type_evidence']=='class_id'
    component={'component_class_id':'Microsoft.CustomUnrecognized','description':'OLEDBSource','properties':{}}
    parser._detect_component_type(component)
    assert component['properties']['_type_evidence']=='description'


def test_new_output_lineage_can_feed_a_downstream_derived_expression():
    flow=union_flow();flow.components=[c for c in flow.components if c.ref_id!='Sort'];flow.paths=[p for p in flow.paths if p['end_id']!='Sort:i']
    derived=ComponentIR(ref_id='D',detected_type='DERIVED_COLUMN',inputs=[{'ref_id':'D:i'}],outputs=[{'ref_id':'D:o','columns':[
        column('copy',7,Expression='#3')]}])
    flow.components.append(derived);flow.paths.append(path('U:o','D:i'))
    ops,findings,_=dataflow_operations(flow,cfg())
    assert not [f for f in findings if f['severity']=='ERROR']
    assert '.withColumn' in next(o['code'] for o in ops if o['operation_id']=='D')


def test_known_unsupported_key_types_remain_pending_at_generation():
    flow=union_flow();sort=next(c for c in flow.components if c.ref_id=='Sort');sort.inputs[0]['columns'][0]['data_type']='wstr'
    ops,findings,_=dataflow_operations(flow,cfg())
    assert any(f['code']=='SORT_COLLATION_REQUIRED' for f in findings)
    assert next(o for o in ops if o['operation_id']=='Sort')['operation_type']=='REVIEW_ONLY'
