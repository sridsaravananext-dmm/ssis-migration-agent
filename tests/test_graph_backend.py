import json
from copy import deepcopy
from pathlib import Path
import pytest
from ssis_migration.config import AssessmentConfig
from ssis_migration.ir.models import (PackageIR, ExecutableIR, PrecedenceConstraintIR, DataFlowIR, ComponentIR, ConnectionReferenceIR)
from ssis_migration.context.builder import build_migration_context
from ssis_migration.planner.task_graph import build_graph_plan, step_id
from ssis_migration.generator.graph_native import dataflow_operations, variable, expression_sql
from ssis_migration.mapping import translate_sql, ConversionGap


def plan(ir, config=None):
    config=config or AssessmentConfig()
    context=build_migration_context(ir,{})
    context.metadata.update(config.to_dict())
    return build_graph_plan(context,config)


def task(ref, sql='SELECT 1', name='arbitrary', parent=None, **props):
    return ExecutableIR(ref_id=ref,name=name,executable_type='Microsoft.ExecuteSQLTask',
        properties={'SqlStatementSource':sql, '_parent_ref_id':parent, **props})


def test_tasks_and_sequence_barriers_do_not_depend_on_names():
    seq=ExecutableIR(ref_id='Package\\S',name='Unfamiliar container',executable_type='STOCK:SEQUENCE')
    a,b,c=task('Package\\S\\A',parent=seq.ref_id),task('Package\\S\\B',parent=seq.ref_id),task('Package\\C')
    ir=PackageIR(package_name='P',executables=[c,b,seq,a],precedence_constraints=[
        PrecedenceConstraintIR(from_executable=a.ref_id,to_executable=b.ref_id,value='Success'),
        PrecedenceConstraintIR(from_executable=seq.ref_id,to_executable=c.ref_id,value='Success')])
    first=plan(ir)
    order=first.metadata['task_graph']['order']
    assert order.index(a.ref_id)<order.index(b.ref_id)<order.index(seq.ref_id+'::complete')<order.index(c.ref_id)
    assert not first.unresolved
    for exe in ir.executables:exe.name='completely renamed'
    second=plan(ir)
    assert first.metadata['task_graph']==second.metadata['task_graph']
    assert all(s.step_id.startswith('task_') for s in second.steps)
    assert not first.metadata['uses_legacy_controls']


@pytest.mark.parametrize('edge',[
    {'value':'Failure'},{'value':'Completion'},{'expression':'1 == 1'},
    {'logical_and':False},{'evaluation_operation':'ExpressionOrConstraint'}])
def test_nondefault_control_edges_are_preserved_and_blocked(edge):
    a,b=task('A'),task('B')
    p=plan(PackageIR(executables=[a,b],precedence_constraints=[PrecedenceConstraintIR(from_executable='A',to_executable='B',**edge)]))
    assert any(f['code']=='CONTROL_FLOW_NOT_PRESERVED' for f in p.unresolved)
    assert p.metadata['task_graph']['source_precedence'][0]['from']=='A'
    assert step_id('A') in next(s for s in p.steps if s.step_id==step_id('B')).dependencies


def test_cycle_is_a_blocker_not_an_arbitrary_executable_order():
    p=plan(PackageIR(executables=[task('A'),task('B')],precedence_constraints=[
        PrecedenceConstraintIR(from_executable='A',to_executable='B'),PrecedenceConstraintIR(from_executable='B',to_executable='A')]))
    assert any(f['code']=='CONTROL_FLOW_CYCLE' for f in p.unresolved)
    assert set(p.metadata['task_graph']['cycles'])=={'A','B'}


def test_disabled_container_skips_descendant_sql_and_dependencies():
    seq=ExecutableIR(ref_id='S',executable_type='STOCK:SEQUENCE',properties={'_attributes':{'Disabled':'True'}})
    child=task('S\\A','EXEC dbo.Missing',parent='S')
    p=plan(PackageIR(executables=[child,seq]))
    assert not p.unresolved
    assert all(i['disposition']=='DISABLED' for i in p.metadata['source_inventory'])
    assert not any('EXEC' in o['code'] for ops in p.metadata['operations_by_step'].values() for o in ops)


@pytest.mark.parametrize('props',[{'_event_handler':True},{'_property_expressions':[{'name':'Disabled'}]}, {'result_bindings':[{'ResultName':'0'}]}, {'parameter_bindings':[{'ParameterName':'0'}]}])
def test_special_scope_and_bindings_never_disappear(props):
    p=plan(PackageIR(executables=[task('A',**props)]))
    assert p.unresolved
    assert p.metadata['source_inventory'][0]['disposition']=='REVIEW_ONLY'


def test_explicit_mapping_handles_join_cte_and_native_dml():
    cfg=AssessmentConfig(object_mappings={'source|dbo.A':'cat.bronze.orders','target|dbo.B':{'target':'cat.silver.orders','layer':'silver'}})
    sql, uses=translate_sql('WITH c AS (SELECT id FROM dbo.A) SELECT * FROM c','source',cfg)
    assert '`cat`.`bronze`.`orders`' in sql and 'FROM c' in sql
    assert len(uses)==1
    other_sql, other_uses = translate_sql('SELECT * FROM dbo.A','other',cfg)
    assert 'orders' not in other_sql and '__pending_target_schema__' in other_sql
    assert other_uses[0]['resolution'] == 'PLACEHOLDER'
    cfg=AssessmentConfig(object_mappings={'dbo.A':'cat.bronze.A','dbo.B':'cat.silver.B'})
    emitted,_=translate_sql('INSERT INTO dbo.B (id) SELECT id FROM dbo.A',None,cfg)
    assert emitted.startswith('INSERT INTO `cat`.`silver`.`B`')
    assert 'FROM `cat`.`bronze`.`A`' in emitted


@pytest.mark.parametrize('sql',[
    'SELECT 1; SELECT 2','DECLARE @x int; SELECT @x','EXEC dbo.P','SELECT dbo.custom(1)',
    'SELECT * FROM dbo.A WITH(NOLOCK)','SELECT * INTO dbo.B FROM dbo.A','SELECT @x',
    'SELECT dbo.A.id FROM dbo.A','CREATE TABLE dbo.X (id int)'])
def test_unsupported_sql_is_rejected_before_emission(sql):
    with pytest.raises(ConversionGap):translate_sql(sql,None,AssessmentConfig(object_mappings={'dbo.A':'c.s.A','dbo.B':'c.s.B'}))


def test_mapping_targets_are_validated_and_do_not_accept_sql_injection():
    for target in ['dbo.T','c.s.T; DROP TABLE c.s.T','c.s.T AS t','c.s.T WHERE 1=1']:
        with pytest.raises(ValueError):AssessmentConfig(object_mappings={'dbo.T':target})


def output(ref,name,columns=(),**properties):
    return {'ref_id':ref,'name':name,'attributes':{},'properties':properties,'columns':list(columns)}


def routing_flow():
    source=ComponentIR(ref_id='S',detected_type='OLE_DB_SOURCE',properties={'OpenRowset':'dbo.Source'},
        outputs=[output('S:o','source',[{'name':'id','lineage_id':'1','data_type':'i4'},{'name':'status','lineage_id':'2','data_type':'wstr'}])])
    split=ComponentIR(ref_id='X',detected_type='CONDITIONAL_SPLIT',inputs=[{'ref_id':'X:i'}],outputs=[
        output('X:first','first',Expression='[status] == "A"',EvaluationOrder='0'),
        output('X:second','second',Expression='[id] > 0',EvaluationOrder='1'),
        output('X:default','default',IsDefaultOut='true')])
    destinations=[ComponentIR(ref_id=ref,detected_type='OLE_DB_DESTINATION',properties={'OpenRowset':f'dbo.{ref}'},inputs=[{'ref_id':ref+':i'}]) for ref in ['A','B','C']]
    edges=[{'start_id':'S:o','end_id':'X:i'}]+[{'start_id':port,'end_id':ref+':i'} for port,ref in zip(['X:first','X:second','X:default'],['A','B','C'])]
    return DataFlowIR(name='routing',ref_id='DF',components=[*destinations,split,source],paths=edges)


def routing_config():
    return AssessmentConfig(object_mappings={f'dbo.{name}':{'target':f'cat.s.{name}','layer':'silver'} for name in ['Source','A','B','C']})


def test_routing_uses_ports_and_evaluation_order_not_component_order():
    flow=routing_flow()
    ops,findings,graph=dataflow_operations(flow,routing_config())
    assert graph['order'].index('S')<graph['order'].index('X')<graph['order'].index('A')
    assert graph['edges'][1]['from_output']=='X:first'
    assert {f['code'] for f in findings}=={'LOAD_STRATEGY_REQUIRED'}
    split_code=next(o['code'] for o in ops if o['operation_id']=='X')
    assert variable('X:first') in split_code and variable('X:second') in split_code and variable('X:default') in split_code
    assert '.filter(remaining_' in split_code
    assert '.isNull()).limit(1).count()' in split_code
    assert not any('.write' in o['code'] for o in ops)
    assert len([o for o in ops if o['operation_type']=='WRITE_PENDING'])==3


def test_routing_executes_first_match_and_default_in_in_memory_contract_harness():
    # This validates emitted routing logic, not the Spark runtime implementation.
    import sqlite3
    class Expr:
        def __init__(self,fn):self.fn=fn
        def __and__(self,other):return Expr(lambda r:self.fn(r) and other.fn(r))
        def __invert__(self):return Expr(lambda r:not self.fn(r))
        def isNull(self):return Expr(lambda r:self.fn(r) is None)
    class Frame:
        def __init__(self,rows):self.rows=rows
        def filter(self,predicate):return Frame([r for r in self.rows if predicate.fn(r)])
        def select(self,*cols):return self
        def limit(self,count):return Frame(self.rows[:count])
        def count(self):return len(self.rows)
    class Functions:
        @staticmethod
        def lit(v):return Expr(lambda r:v)
        @staticmethod
        def col(v):return v
        @staticmethod
        def expr(sql):
            def evaluate(row):
                names=list(row)
                query='SELECT '+sql+' FROM (SELECT '+','.join('? AS "'+k+'"' for k in names)+')'
                with sqlite3.connect(':memory:') as db:return db.execute(query,[row[k] for k in names]).fetchone()[0]
            return Expr(evaluate)
    rows=[{'id':1,'status':'A'},{'id':2,'status':'B'},{'id':-1,'status':'B'}]
    from types import SimpleNamespace
    env={'F':Functions,'spark':SimpleNamespace(sql=lambda sql:Frame(rows))}
    ops,_,_=dataflow_operations(routing_flow(),routing_config())
    for op in ops:
        if op['operation_type']=='NATIVE_DATAFRAME':
            exec(op['code'].replace('from pyspark.sql import functions as F\n',''),env)
    assert [r['id'] for r in env[variable('A')].rows]==[1]
    assert [r['id'] for r in env[variable('B')].rows]==[2]
    assert [r['id'] for r in env[variable('C')].rows]==[-1]


def test_dangling_and_duplicate_input_edges_are_blockers():
    flow=routing_flow();flow.paths.append({'start_id':'missing','end_id':'A:i'})
    _,findings,_=dataflow_operations(flow,routing_config())
    assert findings[0]['code']=='DATAFLOW_PATH_UNRESOLVED'
    flow=routing_flow();flow.paths.append({'start_id':'X:second','end_id':'A:i'})
    _,findings,_=dataflow_operations(flow,routing_config())
    assert findings[0]['code']=='DATAFLOW_INPUT_AMBIGUOUS'


def test_split_without_rule_order_is_not_guessed():
    flow=routing_flow();split=next(c for c in flow.components if c.ref_id=='X')
    split.outputs[0]['properties'].pop('EvaluationOrder')
    _,findings,_=dataflow_operations(flow,routing_config())
    assert any(f['code']=='SPLIT_ORDER_UNRESOLVED' for f in findings)


def test_unsupported_transform_does_not_feed_downstream_first_source():
    flow=routing_flow();next(c for c in flow.components if c.ref_id=='X').detected_type='LOOKUP'
    ops,findings,_=dataflow_operations(flow,routing_config())
    assert any(f['code']=='TRANSFORMATION_UNSUPPORTED' for f in findings)
    assert not any(o['operation_id']=='A' and o['operation_type']=='NATIVE_DATAFRAME' for o in ops)


def test_expression_subset_resolves_lineage_and_rejects_casts_and_unknowns():
    assert 'status' in expression_sql('#2 == "A"',{'#2':'status'},['status'])
    assert 'IS NULL' in expression_sql('ISNULL([status])',{},['status'])
    for text in ['LEN([status])','(DT_I4)[status]','[unknown] == 1','[id] + 1','@[User::x]']:
        with pytest.raises(ConversionGap):expression_sql(text,{},['status','id'])


def test_scoped_same_named_procedures_do_not_cross_databases(tmp_path):
    from ssis_migration.database.connection_mapping import ConnectionMappingSet, ConnectionDatabaseMapping
    from ssis_migration.procedure_file_loader import load_scoped_procedure_files
    from ssis_migration.dependencies import ProcedureResolver, dependency_findings
    # Empty metadata contexts still have explicit, distinct database identities.
    metadata=tmp_path/'meta.json'
    metadata.write_text(json.dumps({'database':{'name':'db'},'objects':[],'tables':[],'schemas':[]}))
    mappings=ConnectionMappingSet([ConnectionDatabaseMapping('src',str(metadata),database_name='source'),ConnectionDatabaseMapping('dst',str(metadata),database_name='target')])
    a,b=tmp_path/'source.sql',tmp_path/'target.sql'
    a.write_text('CREATE PROC dbo.P AS SELECT 1')
    b.write_text('CREATE PROC dbo.P AS SELECT 2')
    scopes={'source.sql':'src','target.sql':'dst'}
    definitions,origins=load_scoped_procedure_files([a,b],scopes)
    ir=PackageIR(executables=[task('T','EXEC dbo.P',Connection='src')])
    context=build_migration_context(ir,{},connection_mapping=mappings,procedure_definitions=definitions,procedure_origins=origins,dependency_scopes=scopes)
    resolver=ProcedureResolver(context)
    assert 'SELECT 1' in resolver.resolve('dbo.P','src')[0]
    assert 'SELECT 2' in resolver.resolve('dbo.P','dst')[0]
    with pytest.raises(ConversionGap):resolver.resolve('dbo.P',None)
    with pytest.raises(ConversionGap):resolver.resolve('dbo.P','wrong')
    with pytest.raises(ConversionGap):resolver.resolve('other.dbo.P','src')
    assert not dependency_findings(context)


def test_unscoped_definition_is_not_broadcast_into_two_contexts(tmp_path):
    from ssis_migration.database.connection_mapping import ConnectionMappingSet, ConnectionDatabaseMapping
    path=tmp_path/'meta.json';path.write_text(json.dumps({'database':{},'objects':[],'tables':[],'schemas':[]}))
    mappings=ConnectionMappingSet([ConnectionDatabaseMapping('src',str(path)),ConnectionDatabaseMapping('dst',str(path))])
    context=build_migration_context(PackageIR(),{},connection_mapping=mappings,procedure_definitions={'dbo.p':'CREATE PROC dbo.P AS SELECT 1'})
    assert any(f['code']=='DEPENDENCY_SCOPE_REQUIRED' for f in context.metadata['input_findings'])
    assert all(not db.database_ir.objects for db in context.databases)


def test_real_xml_parser_preserves_owned_properties_ports_bindings_and_local_connections(tmp_path):
    from ssis_migration.parser.package_parser import SSISPackageParser
    from ssis_migration.ir.builder import IRBuilder
    path=tmp_path/'p.dtsx'
    path.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" xmlns:SQLTask="www.microsoft.com/sqlserver/dts/tasks/sqltask"
      DTS:refId="Package" DTS:ObjectName="P">
      <DTS:ConnectionManagers><DTS:ConnectionManager DTS:refId="Package.ConnectionManagers[Src]" DTS:DTSID="{ABC}"/></DTS:ConnectionManagers>
      <DTS:Executables><DTS:Executable DTS:refId="Package\\Q" DTS:ExecutableType="Microsoft.ExecuteSQLTask"><DTS:ObjectData>
        <SQLTask:SqlTaskData SQLTask:SqlStatementSource="SELECT 1" SQLTask:Connection="{abc}">
          <SQLTask:ResultBinding SQLTask:ResultName="0" SQLTask:DtsVariableName="User::x"/>
        </SQLTask:SqlTaskData></DTS:ObjectData></DTS:Executable>
      <DTS:Executable DTS:refId="Package\\DF" DTS:ExecutableType="Microsoft.Pipeline" DTS:CreationName="Microsoft.Pipeline"><DTS:ObjectData><pipeline><components>
        <component refId="S" componentClassID="Microsoft.OLEDBSource"><properties><property name="SqlCommand">SELECT 1 AS id</property></properties>
          <outputs><output refId="S:o"><properties><property name="SqlCommand">nested value must not replace component SQL</property><property name="IsDefaultOut">true</property></properties>
            <outputColumns><outputColumn refId="S:c" name="id" lineageId="1"><properties><property name="Expression">42</property></properties></outputColumn></outputColumns>
          </output></outputs></component></components><paths/></pipeline></DTS:ObjectData></DTS:Executable></DTS:Executables>
    </DTS:Executable>''')
    ir=IRBuilder().build(SSISPackageParser(path).parse())
    q=next(e for e in ir.executables if e.ref_id=='Package\\Q')
    assert q.properties['connection_manager_ref_id']=='Package.ConnectionManagers[Src]'
    assert q.properties['result_bindings'][0]['DtsVariableName']=='User::x'
    comp=ir.data_flows[0].components[0]
    assert comp.properties['SqlCommand']=='SELECT 1 AS id'
    assert comp.outputs[0]['properties']['IsDefaultOut']=='true'
    assert comp.outputs[0]['columns'][0]['properties']['Expression']=='42'
    assert ir.metadata['connection_aliases']['abc']=='Package.ConnectionManagers[Src]'
    graph=plan(ir)
    assert any(f['code']=='SQL_BINDINGS_UNSUPPORTED' for f in graph.unresolved)


def test_utf16_dtsx_and_invalid_encoding_are_not_silently_replaced(tmp_path):
    from ssis_migration.parser.package_parser import SSISPackageParser
    path=tmp_path/'u.dtsx'
    path.write_bytes('<?xml version="1.0" encoding="utf-16"?><Executable ObjectName="Valid"/>'.encode('utf-16'))
    assert SSISPackageParser(path).parse()['package']['attributes']['ObjectName']=='Valid'
    path.write_bytes(b'<Executable ObjectName="\xff"/>')
    with pytest.raises(UnicodeError):SSISPackageParser(path)


def test_graph_job_dependencies_are_not_replaced_by_name_sorting_or_chaining():
    from ssis_migration.generator.job_generator import JobGenerator
    p={'package_name':'P','metadata':{'planner':'source_graph'},'steps':[
       {'step_id':'a','name':'A','dependencies':['z']},{'step_id':'independent','name':'I','dependencies':[]},{'step_id':'z','name':'Z','dependencies':[]}]}
    job=JobGenerator().generate(p,'/Workspace/test')
    tasks={t['task_key']:t for t in job.tasks}
    assert tasks['a']['depends_on']==[{'task_key':'z'}]
    assert not tasks['independent'].get('depends_on')
    assert job.tasks[-1]['task_key']=='a'
    assert tasks['a']['notebook_task']['base_parameters']['schema']==''


def test_graph_job_rejects_cycles_and_missing_dependencies():
    from ssis_migration.generator.job_generator import JobGenerator
    generator=JobGenerator()
    for steps in [[{'step_id':'a','dependencies':['missing']}],[{'step_id':'a','dependencies':['a']}]]:
        with pytest.raises(ValueError):generator.generate({'metadata':{'planner':'source_graph'},'steps':steps},'/Workspace/test')


def test_boolean_expression_and_error_policies_are_not_approximated():
    with pytest.raises(ConversionGap):expression_sql('1',{},[],expect_boolean=True)
    with pytest.raises(ConversionGap):expression_sql('1; 2',{},[])
    flow=routing_flow()
    next(c for c in flow.components if c.ref_id=='X').properties['_attributes']={'errorRowDisposition':'RD_IgnoreFailure'}
    _,findings,_=dataflow_operations(flow,routing_config())
    assert any(f['code']=='ERROR_DISPOSITION_UNSUPPORTED' for f in findings)


def test_default_graph_cli_uses_mapping_file_and_exports_evidence(tmp_path):
    import subprocess,sys
    path=tmp_path/'p.dtsx'
    path.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" xmlns:SQLTask="www.microsoft.com/sqlserver/dts/tasks/sqltask" DTS:refId="Package" DTS:ObjectName="P"><DTS:Executables><DTS:Executable DTS:refId="Package\\T" DTS:ObjectName="unrelated name" DTS:ExecutableType="Microsoft.ExecuteSQLTask"><DTS:ObjectData><SQLTask:SqlTaskData SQLTask:SqlStatementSource="SELECT id FROM dbo.A"/></DTS:ObjectData></DTS:Executable></DTS:Executables></DTS:Executable>''')
    mapping=tmp_path/'objects.json';mapping.write_text(json.dumps({'dbo.A':'catalog.silver.A'}))
    result=subprocess.run([sys.executable,'-m','ssis_migration.migrate','--source',str(path),'--output',str(tmp_path/'out'),'--object-mappings',str(mapping),'--strict-mode'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr+result.stdout
    report=json.loads(result.stdout)
    item=report['packages'][0]
    assert item['status']=='ARTIFACTS_GENERATED'
    assert item['assessment']['verification']['legacy_match_percent'] is None
    assert Path(item['artifacts']['task_graph']).is_file()
    notebook=json.loads(Path(item['artifacts']['notebook']).read_text())
    code='\n'.join(''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='code')
    assert '`catalog`.`silver`.`A`' in code
    assert 'Integration.Lineage' not in code


def test_multilayer_outputs_and_schema_conflicts_are_explicit():
    ir=PackageIR(executables=[task('I','INSERT INTO dbo.B (id) SELECT id FROM dbo.A')])
    mappings={'dbo.A':{'target':'c.bronze.A','layer':'bronze'},'dbo.B':{'target':'c.gold.B','layer':'gold'}}
    p=plan(ir,AssessmentConfig(layer='multi-layer',object_mappings=mappings))
    assert not any(f['code']=='LAYER_MAPPING_REQUIRED' for f in p.unresolved)
    p=plan(ir,AssessmentConfig(layer='silver',target_schema='silver',object_mappings=mappings))
    codes={f['code'] for f in p.unresolved}
    assert {'TARGET_SCHEMA_CONTEXT_CONFLICT','LAYER_CONTEXT_CONFLICT'}<=codes
    mappings['dbo.B'].pop('layer')
    p=plan(ir,AssessmentConfig(layer='multi-layer',object_mappings=mappings))
    assert any(f['code']=='LAYER_MAPPING_REQUIRED' for f in p.unresolved)


def test_multiple_sources_are_kept_separate_when_join_is_unsupported():
    def source(ref,table):return ComponentIR(ref_id=ref,detected_type='OLE_DB_SOURCE',properties={'OpenRowset':table},outputs=[output(ref+':o','o',[{'name':'id','lineage_id':ref}])])
    a,b=source('A','dbo.A'),source('B','dbo.B')
    join=ComponentIR(ref_id='J',detected_type='MERGE_JOIN',inputs=[{'ref_id':'J:left'},{'ref_id':'J:right'}],outputs=[output('J:o','o')])
    flow=DataFlowIR(ref_id='DF',components=[join,b,a],paths=[{'start_id':'A:o','end_id':'J:left'},{'start_id':'B:o','end_id':'J:right'}])
    ops,findings,graph=dataflow_operations(flow,AssessmentConfig(object_mappings={'dbo.A':'c.s.A','dbo.B':'c.s.B'}))
    assert sum(o['operation_type']=='NATIVE_DATAFRAME' for o in ops)==2
    assert any(f['code']=='DATAFLOW_INPUT_UNSUPPORTED' for f in findings)
    assert len(graph['edges'])==2


def test_derived_copy_and_literal_have_no_udf_or_null_substitution():
    src=ComponentIR(ref_id='S',detected_type='OLE_DB_SOURCE',properties={'OpenRowset':'dbo.A'},outputs=[output('S:o','o',[{'name':'id','lineage_id':'1'}])])
    derived=ComponentIR(ref_id='D',detected_type='DERIVED_COLUMN',inputs=[{'ref_id':'D:i'}],outputs=[output('D:o','o',[
        {'name':'copy','lineage_id':'2','properties':{'Expression':'#1'}},
        {'name':'fixed','lineage_id':'3','properties':{'Expression':'42'}}])])
    flow=DataFlowIR(ref_id='DF',components=[derived,src],paths=[{'start_id':'S:o','end_id':'D:i'}])
    ops,findings,_=dataflow_operations(flow,AssessmentConfig(object_mappings={'dbo.A':'c.s.A'}))
    code=next(o['code'] for o in ops if o['operation_id']=='D')
    assert '.withColumn' in code and 'F.expr' in code and 'udf' not in code and 'None' not in code
    assert all(f['severity']=='WARNING' for f in findings)
    derived.outputs[0]['columns'][0]['properties']['Expression']='UNSUPPORTED(#1)'
    ops,findings,_=dataflow_operations(flow,AssessmentConfig(object_mappings={'dbo.A':'c.s.A'}))
    assert any(f['code']=='EXPRESSION_UNSUPPORTED' for f in findings)
    assert next(o for o in ops if o['operation_id']=='D')['operation_type']=='REVIEW_ONLY'


def test_multicast_preserves_separate_output_port_identities():
    src=ComponentIR(ref_id='S',detected_type='OLE_DB_SOURCE',properties={'OpenRowset':'dbo.A'},outputs=[output('S:o','o',[{'name':'id','lineage_id':'1'}])])
    multicast=ComponentIR(ref_id='M',detected_type='MULTICAST',inputs=[{'ref_id':'M:i'}],outputs=[output('M:a','a'),output('M:b','b')])
    flow=DataFlowIR(ref_id='DF',components=[multicast,src],paths=[{'start_id':'S:o','end_id':'M:i'}])
    ops,findings,_=dataflow_operations(flow,AssessmentConfig(object_mappings={'dbo.A':'c.s.A'}))
    assert not findings
    code=next(o['code'] for o in ops if o['operation_id']=='M')
    assert variable('M:a') in code and variable('M:b') in code


def test_independent_branches_require_pipeline_semantics_instead_of_silent_serialization():
    p=plan(PackageIR(package_name='P',executables=[task('A'),task('B')]))
    assert any(f['code']=='PARALLEL_CONTROL_REVIEW_REQUIRED' for f in p.unresolved)
    assert not next(s for s in p.steps if s.step_id==step_id('A')).dependencies
    assert not next(s for s in p.steps if s.step_id==step_id('B')).dependencies


def test_task_forced_result_and_package_checkpoints_are_blockers():
    ir=PackageIR(metadata={'package_attributes':{'CheckpointUsage':'Always'}},executables=[task('A',_attributes={'ForceExecutionResult':'0'})])
    codes={f['code'] for f in plan(ir).unresolved}
    assert {'CHECKPOINT_SEMANTICS_UNSUPPORTED','EXECUTION_OVERRIDE_UNSUPPORTED'}<=codes


def test_source_identifiers_with_embedded_dots_are_not_conflated():
    with pytest.raises(ConversionGap,match='containing dots'):
        translate_sql('SELECT * FROM dbo.[Order.Details]',None,AssessmentConfig(object_mappings={'dbo.[Order.Details]':'c.s.orders'}))


def test_graph_job_catalog_defaults_to_the_same_mapping_configuration():
    from ssis_migration.generator.job_generator import JobGenerator
    p={'package_name':'P','metadata':{'planner':'source_graph','catalog':'configured'},'steps':[{'step_id':'a','dependencies':[]}]}
    job=JobGenerator().generate(p,'/Workspace/test')
    assert job.tasks[0]['notebook_task']['base_parameters']['catalog']=='configured'
    assert next(x for x in job.parameters if x['name']=='catalog')['default']=='configured'


def test_real_relational_example_generates_mapped_code_and_verification_requirements(tmp_path):
    from ssis_migration.migrate import run_migration
    root=Path(__file__).resolve().parent.parent
    mappings=json.loads((root/'examples/graph/objects.json').read_text())
    result=run_migration(str(root/'examples/graph/relational.dtsx'),str(tmp_path),catalog='dev_catalog',layer='silver',object_mappings=mappings,strict_mode=True)
    assert result['status']=='ARTIFACTS_GENERATED'
    item=result['packages'][0]
    spec=json.loads(Path(item['artifacts']['verification_plan']).read_text())
    assert spec['status']=='NOT_EXECUTED' and spec['legacy_match_percent'] is None
    assert 'conditional_output_routing' in spec['required_checks']
    inventory=json.loads(Path(item['artifacts']['dependency_inventory']).read_text())
    assert inventory['connection_aliases']
    for cell in json.loads(Path(item['artifacts']['notebook']).read_text())['cells']:
        if cell['cell_type']=='code':compile(''.join(cell['source']),'<generated>','exec')


def test_mapping_does_not_silently_accept_a_new_write_strategy():
    with pytest.raises(ValueError,match='write strategy'):
        AssessmentConfig(object_mappings={'dbo.A':{'target':'c.s.A','mode':'overwrite'}})


def test_disabled_children_use_real_parent_scope_not_ref_string_prefix():
    seq=ExecutableIR(ref_id='S',executable_type='STOCK:SEQUENCE',properties={'_attributes':{'Disabled':'true'}})
    p=plan(PackageIR(executables=[seq,task('unstructured_child','EXEC dbo.Missing',parent='S'),task('disabled_sibling',_attributes={'Disabled':'true'})]))
    assert not p.unresolved
    assert all(i['disposition']=='DISABLED' for i in p.metadata['source_inventory'])


def test_duplicate_flow_identity_is_an_explicit_input_error():
    ir=PackageIR(executables=[ExecutableIR(ref_id='DF',executable_type='Microsoft.Pipeline')],data_flows=[DataFlowIR(ref_id='DF'),DataFlowIR(ref_id='DF')])
    with pytest.raises(ValueError,match='Duplicate data-flow'):plan(ir)
