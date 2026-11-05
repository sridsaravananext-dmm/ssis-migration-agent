"""Draft generation must preserve gaps without certifying or executing them."""
import json
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import quoteattr

import pytest

from ssis_migration.config import AssessmentConfig
from ssis_migration.context.builder import build_migration_context
from ssis_migration.drafts import pending_operation
from ssis_migration.generator.graph_native import dataflow_operations
from ssis_migration.ir.models import (ComponentIR, DataFlowIR, ExecutableIR, PackageIR, PrecedenceConstraintIR)
from ssis_migration.mapping import resolve_target
from ssis_migration.migrate import run_migration
from ssis_migration.planner.task_graph import build_graph_plan


def source_task(ref='Package\\T', sql='EXEC dbo.LoadOrders @Date', **props):
    return ExecutableIR(ref_id=ref, name='Load', executable_type='Microsoft.ExecuteSQLTask',
        properties={'SqlStatementSource':sql, 'Connection':'Legacy', **props})


def graph(ir, cfg=None, definitions=None):
    cfg = cfg or AssessmentConfig()
    ctx = build_migration_context(ir, {}, procedure_definitions=definitions)
    ctx.metadata.update(cfg.to_dict())
    return build_graph_plan(ctx, cfg)


def xml_package(tmp_path, sql='EXEC dbo.LoadOrders'):
    path = tmp_path / 'draft.dtsx'
    path.write_text(f'''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts"
      xmlns:SQLTask="www.microsoft.com/sqlserver/dts/tasks/sqltask" DTS:refId="Package" DTS:ObjectName="Draft">
      <DTS:Executables><DTS:Executable DTS:refId="Package\\T" DTS:ObjectName="Load" DTS:ExecutableType="Microsoft.ExecuteSQLTask">
      <DTS:ObjectData><SQLTask:SqlTaskData SQLTask:SqlStatementSource={quoteattr(sql)} SQLTask:Connection="Legacy"/>
      </DTS:ObjectData></DTS:Executable></DTS:Executables></DTS:Executable>''')
    return path


def test_missing_procedure_has_named_stable_pending_section_with_bindings():
    ir = PackageIR(executables=[source_task(parameter_bindings=[{'ParameterName':'@Date', 'DtsVariableName':'User::Date'}])])
    p = graph(ir)
    op = next(iter(p.metadata['operations_by_step'].values()))[0]
    pending = op['pending_item']
    assert pending['procedures'][0]['name'] == 'dbo.LoadOrders'
    assert pending['procedures'][0]['definition_status'] == 'MISSING'
    assert pending['connection_reference'] == 'Legacy'
    assert pending['parameter_bindings'][0]['DtsVariableName'] == 'User::Date'
    assert '# Original SQL' in op['code'] and '# EXEC dbo.LoadOrders @Date' in op['code']
    with pytest.raises(RuntimeError, match=pending['pending_id']): exec(op['code'], {})
    supplied = graph(ir, definitions={'dbo.loadorders':'CREATE PROC dbo.LoadOrders @Date int AS SELECT @Date'})
    replacement = next(iter(supplied.metadata['operations_by_step'].values()))[0]['pending_item']
    assert replacement['pending_id'] == pending['pending_id']
    assert replacement['procedures'][0]['definition_status'] == 'AVAILABLE'
    assert 'PROCEDURE_BINDINGS_UNSUPPORTED' in replacement['reason_codes']


def test_strict_mode_writes_draft_and_preflight_stops_before_any_spark_call(tmp_path):
    report = run_migration(str(xml_package(tmp_path)), str(tmp_path/'out'), strict_mode=True)
    item = report['packages'][0]
    assert report['failed_package_count'] == 0
    assert item['notebook_status'] == 'DRAFT_REQUIRES_REVIEW'
    assert item['assessment']['execution_readiness']['status'] == 'BLOCKED_BY_REVIEW'
    assert item['assessment']['generation']['pending_operations'] == 1
    assert item['assessment']['generation']['statically_valid_operations'] == 0
    pending = json.loads(Path(item['artifacts']['pending_items']).read_text())
    notebook = json.loads(Path(item['artifacts']['notebook']).read_text())
    tagged = [c for c in notebook['cells'] if c['metadata'].get('ssis_migration', {}).get('pending_ids')]
    assert tagged[0]['metadata']['ssis_migration']['pending_ids'] == [pending[0]['pending_id']]
    calls = []
    env = {'dbutils':SimpleNamespace(widgets=SimpleNamespace(text=lambda *a:None,
        get=lambda key: {'step_id':'all','catalog':'main','schema':''}[key])),
        'spark':SimpleNamespace(sql=lambda sql:calls.append(sql))}
    with pytest.raises(RuntimeError, match='unresolved findings'):
        for cell in notebook['cells']:
            if cell['cell_type'] == 'code': exec(''.join(cell['source']), env)
    assert not calls


def test_definition_arrival_replaces_supported_procedure_placeholder(tmp_path):
    source = xml_package(tmp_path)
    before = run_migration(str(source), str(tmp_path/'out'), strict_mode=True)['packages'][0]
    dependency = tmp_path/'procedures.sql'
    dependency.write_text('CREATE PROCEDURE dbo.LoadOrders AS BEGIN SELECT 42 AS answer; END\nGO')
    after = run_migration(str(source), str(tmp_path/'out'), procedures_path=str(dependency), strict_mode=True)['packages'][0]
    assert Path(before['artifacts']['notebook']).is_file()
    assert before['artifacts']['notebook'] != after['artifacts']['notebook']
    assert after['status'] == 'ARTIFACTS_GENERATED'
    assert json.loads(Path(after['artifacts']['pending_items']).read_text()) == []
    nb = json.loads(Path(after['artifacts']['notebook']).read_text())
    code = '\n'.join(''.join(c['source']) for c in nb['cells'] if c['cell_type'] == 'code')
    assert 'spark.sql(' in code and 'SELECT 42 AS answer' in code
    assert 'EXEC dbo.LoadOrders' not in code
    assert after['assessment']['verification']['legacy_match_percent'] is None


@pytest.mark.parametrize('definition,call', [
    ('CREATE PROC dbo.LoadOrders @x int AS SELECT @x', 'EXEC dbo.LoadOrders 1'),
    ('CREATE PROC dbo.LoadOrders AS SELECT 1; SELECT 2', 'EXEC dbo.LoadOrders'),
    ('CREATE PROC dbo.LoadOrders WITH EXECUTE AS OWNER AS SELECT 1', 'EXEC dbo.LoadOrders'),
    ('CREATE PROC dbo.LoadOrders AS BEGIN TRY SELECT 1 END TRY BEGIN CATCH SELECT 2 END CATCH', 'EXEC dbo.LoadOrders'),
    ('CREATE PROC dbo.LoadOrders AS SELECT 1', 'EXEC @rc = dbo.LoadOrders'),
])
def test_definition_presence_does_not_certify_unsupported_procedure_semantics(definition, call):
    p = graph(PackageIR(executables=[source_task(sql=call)]), definitions={'dbo.loadorders':definition})
    ops = next(iter(p.metadata['operations_by_step'].values()))
    assert ops[0].get('pending_item')
    assert 'spark.sql(' not in ops[0]['code']
    assert ops[0]['pending_item']['procedures'][0]['definition_status'] == 'AVAILABLE'


def test_catalog_schema_convention_and_scoped_overrides():
    cfg = AssessmentConfig(catalog='dev_catalog', target_schema='dev_silver',
        object_mappings={'Legacy|dbo.Orders':'prod.gold.special_orders'})
    explicit, a = resolve_target('dbo.Orders','Legacy',cfg,role='source')
    implicit, b = resolve_target('dbo.Orders','Other',cfg,role='source')
    assert explicit.sql(dialect='databricks') == '`prod`.`gold`.`special_orders`'
    assert a['resolution'] == 'EXPLICIT'
    assert implicit.sql(dialect='databricks') == '`dev_catalog`.`dev_silver`.`orders`'
    assert b['resolution'] == 'CONVENTION' and b['mapping_key'] is None


def test_unknown_schema_retains_target_placeholder_and_no_active_query(tmp_path):
    report = run_migration(str(xml_package(tmp_path,'SELECT * FROM dbo.Orders')), str(tmp_path/'out'), strict_mode=True)
    item = report['packages'][0]
    assert 'TARGET_LOCATION_UNRESOLVED' in {f['code'] for f in item['findings']}
    nb = json.loads(Path(item['artifacts']['notebook']).read_text())
    code = '\n'.join(''.join(c['source']) for c in nb['cells'] if c['cell_type']=='code')
    assert '# SELECT * FROM dbo.Orders' in code
    assert 'spark.sql(' not in code
    plan = json.loads(Path(item['artifacts']['plan']).read_text())
    assert plan['metadata']['mapping_uses'][0]['resolution'] == 'PLACEHOLDER'


def test_no_mapping_json_is_needed_with_catalog_and_schema(tmp_path):
    report = run_migration(str(xml_package(tmp_path,'SELECT * FROM dbo.Orders')), str(tmp_path/'out'),
        catalog='dev_catalog', target_schema='dev_silver', strict_mode=True)
    item = report['packages'][0]
    assert item['assessment']['execution_readiness']['status'] == 'STATIC_CHECKS_PASSED'
    assert {f['code'] for f in item['findings']} == {'CONVENTION_TARGET_REVIEW'}
    nb = json.loads(Path(item['artifacts']['notebook']).read_text())
    assert any('`dev_catalog`.`dev_silver`.`orders`' in ''.join(c['source']) for c in nb['cells'])


def test_different_legacy_schemas_or_connections_cannot_silently_collide():
    cfg = AssessmentConfig(target_schema='silver')
    for sql_a, sql_b, conn_b in [('SELECT * FROM sales.Orders','SELECT * FROM audit.Orders','Legacy'),
                                ('SELECT * FROM dbo.Orders','SELECT * FROM dbo.Orders','Other')]:
        ir = PackageIR(executables=[source_task('A',sql_a),source_task('B',sql_b,Connection=conn_b)],
            precedence_constraints=[PrecedenceConstraintIR(from_executable='A',to_executable='B')])
        assert 'TARGET_NAME_COLLISION' in {f['code'] for f in graph(ir,cfg).unresolved}
    same = PackageIR(executables=[source_task('A','SELECT * FROM Orders'),source_task('B','SELECT * FROM dbo.Orders')],
        precedence_constraints=[PrecedenceConstraintIR(from_executable='A',to_executable='B')])
    assert 'TARGET_NAME_COLLISION' not in {f['code'] for f in graph(same,cfg).unresolved}


def test_event_handlers_do_not_appear_as_parallel_normal_branches():
    normal = source_task('Package\\Normal','SELECT 1')
    error = source_task('Package.EventHandlers[OnError]\\Error','SELECT 2',_event_handler=True)
    ir = PackageIR(executables=[normal,error])
    p = graph(ir)
    codes = {f['code'] for f in p.unresolved}
    assert 'EVENT_HANDLER_UNSUPPORTED' in codes
    assert 'PARALLEL_CONTROL_REVIEW_REQUIRED' not in codes
    nodes = p.metadata['task_graph']['nodes']
    assert next(n for n in nodes if n['id']==error.ref_id)['event_handler_scope'] == 'Package.EventHandlers[OnError]'


def test_nested_handler_scope_is_inherited_and_cannot_link_to_main_flow():
    error = ExecutableIR(ref_id='Package.EventHandlers[OnError]\\Container', executable_type='STOCK:SEQUENCE',
        properties={'_event_handler':True})
    child = source_task('handler_child','SELECT 2',_parent_ref_id=error.ref_id)
    normal = source_task('normal','SELECT 1')
    p = graph(PackageIR(executables=[error,child,normal], precedence_constraints=[
        PrecedenceConstraintIR(from_executable=child.ref_id,to_executable=normal.ref_id)]))
    assert 'CONTROL_SCOPE_UNSUPPORTED' in {f['code'] for f in p.unresolved}
    step = next(s for s in p.steps if s.metadata['source_ref_id']==child.ref_id)
    assert step.metadata['event_handler_scope'] == 'Package.EventHandlers[OnError]'


def test_override_resolves_a_convention_collision():
    ir = PackageIR(executables=[source_task('A','SELECT * FROM sales.Orders'),source_task('B','SELECT * FROM audit.Orders')],
        precedence_constraints=[PrecedenceConstraintIR(from_executable='A',to_executable='B')])
    cfg = AssessmentConfig(target_schema='silver',object_mappings={'audit.Orders':'main.audit.orders'})
    p = graph(ir,cfg)
    assert 'TARGET_NAME_COLLISION' not in {f['code'] for f in p.unresolved}


def test_strict_cli_reports_review_but_retains_notebook_draft(tmp_path):
    import subprocess
    import sys
    result = subprocess.run([sys.executable,'-m','ssis_migration.migrate','--source',str(xml_package(tmp_path)),
        '--output',str(tmp_path/'out'),'--strict-mode'],text=True,capture_output=True)
    assert result.returncode == 1
    item = json.loads(result.stdout)['packages'][0]
    assert item['notebook_status'] == 'DRAFT_REQUIRES_REVIEW'
    assert Path(item['artifacts']['notebook']).is_file()


@pytest.mark.parametrize('value', ['FailComponent','RD_FailComponent','NotUsed','RD_NotUsed'])
def test_ssis_error_disposition_spellings_do_not_create_false_findings(value):
    comp = ComponentIR(ref_id='S',detected_type='OLE_DB_SOURCE',properties={'OpenRowset':'dbo.Orders'},
        outputs=[{'ref_id':'S:o','attributes':{},'columns':[{'name':'id','attributes':{'errorRowDisposition':value}}]}])
    _, findings, _ = dataflow_operations(DataFlowIR(ref_id='DF',components=[comp]), AssessmentConfig(target_schema='silver'))
    assert 'ERROR_DISPOSITION_UNSUPPORTED' not in {f['code'] for f in findings}


@pytest.mark.parametrize('value', ['IgnoreFailure','RD_IgnoreFailure','RedirectRow','RD_RedirectRow','unknown'])
def test_ignore_redirect_and_unknown_error_dispositions_remain_pending(value):
    comp = ComponentIR(ref_id='S',detected_type='OLE_DB_SOURCE',properties={'OpenRowset':'dbo.Orders','_attributes':{'errorRowDisposition':value}})
    ops, findings, _ = dataflow_operations(DataFlowIR(ref_id='DF',components=[comp]), AssessmentConfig(target_schema='silver'))
    assert 'ERROR_DISPOSITION_UNSUPPORTED' in {f['code'] for f in findings}
    assert ops[0]['pending_item']


def test_reference_sql_remains_comment_only_even_when_multiline():
    op = pending_operation('Task', ['MISSING_PROCEDURE_DEFINITION'], sql="EXEC dbo.P\nspark.sql('DROP TABLE dangerous')")
    called = []
    with pytest.raises(RuntimeError): exec(op['code'], {'spark':SimpleNamespace(sql=called.append)})
    assert not called


@pytest.mark.parametrize('schema', ['silver - schema name','catalog.silver','silver; DROP TABLE x'])
def test_schema_configuration_rejects_descriptions_and_sql(schema):
    with pytest.raises(ValueError, match='single identifier'):
        AssessmentConfig(target_schema=schema)
