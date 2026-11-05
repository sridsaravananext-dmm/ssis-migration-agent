"""Follow active source mode and lexical scope without executing SSIS expressions."""
from dataclasses import asdict
import json

import pytest

from ssis_migration.analyzer.sql_dependency import analyze_sql_dependency
from ssis_migration.analyzer.ssis_sql_dependencies import extract_ssis_sql_dependencies
from ssis_migration.analyzer.result_set import analyze_result_set
from ssis_migration.config import AssessmentConfig
from ssis_migration.context.builder import build_migration_context
from ssis_migration.generator.graph_native import dataflow_operations
from ssis_migration.ir.builder import IRBuilder
from ssis_migration.ir.models import ComponentIR, DataFlowIR, ExecutableIR, PackageIR, VariableIR
from ssis_migration.parser.package_parser import SSISPackageParser
from ssis_migration.planner.task_graph import build_graph_plan
from ssis_migration.source_context import describe_variable, source_selection


def source(properties):
    return ComponentIR(ref_id='Package\\DF\\S',name='Source',detected_type='OLE_DB_SOURCE',properties=properties,
        outputs=[{'ref_id':'S:o','attributes':{},'columns':[{'name':'id','lineage_id':'1'}]}])


def test_parser_retains_variable_scope_expression_flags_and_runtime_references(tmp_path):
    path=tmp_path/'scope.dtsx'
    path.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:refId="Package" DTS:ObjectName="Scope">
      <DTS:Variables><DTS:Variable DTS:ObjectName="query" DTS:Namespace="User" DTS:EvaluateAsExpression="True"
        DTS:Expression="&quot;SELECT * FROM dbo.Orders WHERE id=&quot; + @[$Package::Key]">
        <DTS:VariableValue DTS:DataType="8">old captured value</DTS:VariableValue></DTS:Variable></DTS:Variables>
      <DTS:Executables><DTS:Executable DTS:refId="Package\\DF" DTS:ExecutableType="Microsoft.Pipeline"/></DTS:Executables>
    </DTS:Executable>''')
    ir=IRBuilder().build(SSISPackageParser(path).parse())
    var=ir.variables[0]
    assert var.scope_ref_id=='Package' and var.evaluate_as_expression is True
    context=describe_variable('User::query','Package\\DF',ir)
    assert context['expression']=='"SELECT * FROM dbo.Orders WHERE id=" + @[$Package::Key]'
    assert context['references']==[{'reference':'$Package::Key','kind':'PARAMETER','status':'RUNTIME_BINDING_REQUIRED'}]
    assert context['value']=='old captured value'
    assert context['status']=='DECLARED_REQUIRES_RUNTIME_REVIEW'


def test_variable_mode_retains_actual_expression_and_ignores_inactive_sql():
    comp=source({'AccessMode':'3','SqlCommandVariable':'User::query','OpenRowset':'dbo.InactiveTable',
        'SqlCommand':'EXEC dbo.InactiveProcedure WITH RESULT SETS ((id int))'})
    flow=DataFlowIR(ref_id='Package\\DF',components=[comp])
    ir=PackageIR(executables=[ExecutableIR(ref_id=flow.ref_id,executable_type='Microsoft.Pipeline')],data_flows=[flow],
        variables=[VariableIR(name='query',namespace='User',scope_ref_id='Package',evaluate_as_expression=True,
            expression='"SELECT * FROM dbo.ActiveTable WHERE id=" + @[$Package::Key]')])
    ctx=build_migration_context(ir,{})
    cfg=AssessmentConfig(target_schema='silver')
    ctx.metadata.update(cfg.to_dict())
    plan=build_graph_plan(ctx,cfg)
    op=next(o for ops in plan.metadata['operations_by_step'].values() for o in ops if o.get('pending_item'))
    assert 'VARIABLE_SQL_REVIEW_REQUIRED' in op['pending_item']['reason_codes']
    assert 'dbo.ActiveTable' in op['pending_item']['source_variable_context']['expression']
    assert not any(f['code']=='MISSING_PROCEDURE_DEFINITION' for f in plan.unresolved)
    assert plan.metadata['mapping_uses']==[]
    dependency=analyze_sql_dependency(asdict(comp))
    assert dependency['sql'] is None and not dependency['objects'] and dependency['target_object'] is None
    assert not analyze_result_set(asdict(comp))['has_result_set']
    assert 'spark.sql(' not in op['code']


@pytest.mark.parametrize('mode,expected', [('0','ActualTable'),('2','ActualQuery')])
def test_static_modes_ignore_inactive_variable_and_command_properties(mode,expected):
    comp=source({'AccessMode':mode,'OpenRowset':'dbo.ActualTable','SqlCommand':'SELECT id FROM dbo.ActualQuery',
        'SqlCommandVariable':'User::unused'})
    ops,findings,graph=dataflow_operations(DataFlowIR(ref_id='DF',components=[comp]),AssessmentConfig(target_schema='silver'))
    assert not findings
    assert graph['mapping_uses'][0]['source']=='dbo.'+expected
    assert ops[0]['operation_type']=='NATIVE_DATAFRAME'


def test_nearest_scope_wins_and_other_container_variables_are_not_visible():
    ir=PackageIR(executables=[ExecutableIR(ref_id='DF',properties={'_parent_ref_id':'Container'}),
        ExecutableIR(ref_id='Container',properties={'_parent_ref_id':'Package'})],variables=[
            VariableIR(name='query',namespace='User',scope_ref_id='Package',expression='"outer"'),
            VariableIR(name='query',namespace='User',scope_ref_id='Container',expression='"inner"'),
            VariableIR(name='hidden',namespace='User',scope_ref_id='Other',expression='"inaccessible"')])
    assert describe_variable('User::query','DF',ir)['expression']=='"inner"'
    assert describe_variable('User::hidden','DF',ir)['status']=='NOT_FOUND'
    ir.variables.append(VariableIR(name='query',namespace='User',scope_ref_id='Container',expression='"duplicate"'))
    assert describe_variable('User::query','DF',ir)['status']=='AMBIGUOUS'


def test_sensitive_variable_context_is_redacted_and_literal_references_are_ignored():
    ir=PackageIR(variables=[VariableIR(name='secret',namespace='User',scope_ref_id='Package',sensitive=True,
        value='private-value',expression='"private-expression"'),
        VariableIR(name='query',namespace='User',scope_ref_id='Package',
            expression='"SELECT \'@[$Package::NotAReference]\'" + @[$Package::RealReference]')])
    secret=describe_variable('User::secret','Package',ir)
    assert secret['status']=='SENSITIVE_REDACTED'
    assert 'private' not in json.dumps(secret)
    context=describe_variable('User::query','Package',ir)
    assert [r['reference'] for r in context['references']]==['$Package::RealReference']


def test_legacy_unscoped_ir_retains_expression_without_claiming_scope_resolution():
    ir=PackageIR(variables=[VariableIR(name='query',namespace='User',expression='"SELECT 1"')])
    context=describe_variable('User::query','Package',ir)
    assert context['status']=='SCOPE_UNAVAILABLE'
    assert context['expression']=='"SELECT 1"'


def test_variable_table_mode_selects_its_own_reference():
    selected=source_selection({'AccessMode':'1','OpenRowsetVariable':'User::table','SqlCommandVariable':'User::unused'})
    assert selected=={'kind':'VARIABLE_TABLE','value':'User::table','access_mode':'1'}


def test_dependency_objects_keep_their_names_and_ignore_sql_literals_and_comments():
    comp=source({'AccessMode':'2','SqlCommand':"SELECT 'EXEC dbo.Fake FROM dbo.Decoy' FROM dbo.Orders -- EXEC dbo.CommentOnly"})
    ir=PackageIR(data_flows=[DataFlowIR(ref_id='DF',components=[comp])])
    dependency=extract_ssis_sql_dependencies(ir)
    assert dependency['database_objects']==['dbo.Orders']
    assert dependency['stored_procedures']==[]


def test_disabled_expression_is_retained_but_does_not_invent_runtime_references():
    ir=PackageIR(variables=[VariableIR(name='query',namespace='User',scope_ref_id='Package',
        evaluate_as_expression=False,value='SELECT 1',expression='"unused" + @[$Package::Unused]')])
    context=describe_variable('User::query','Package',ir)
    assert context['active_value_kind']=='VALUE'
    assert context['value']=='SELECT 1'
    assert context['references']==[]
