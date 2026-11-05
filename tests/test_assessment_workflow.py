import json
from pathlib import Path
import pytest
from ssis_migration.migrate import run_migration
from ssis_migration.procedure_file_loader import load_procedure_files, parse_procedures
from ssis_migration.sql_text import procedure_calls, has_dynamic_call
from ssis_migration.intake import classify_dependency
from ssis_migration.validator.native_spark import validate_native_spark
from ssis_migration.context.builder import build_migration_context
from ssis_migration.ir.models import PackageIR, ExecutableIR


def package(tmp_path, sql='SELECT 1', task_type='Microsoft.ExecuteSQLTask', source_type='DirectInput'):
    p = tmp_path / 'example.dtsx'
    # A real parsed top-level task outside STOCK:SEQUENCE must be accounted for.
    from xml.sax.saxutils import quoteattr
    p.write_text(f'''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts"
        xmlns:SQLTask="www.microsoft.com/sqlserver/dts/tasks/sqltask"
        DTS:refId="Package" DTS:ObjectName="Example" DTS:ExecutableType="Microsoft.Package">
        <DTS:Executables><DTS:Executable DTS:refId="Package\\Task" DTS:ObjectName="Task"
        DTS:ExecutableType="{task_type}"><DTS:ObjectData>
        <SQLTask:SqlTaskData SQLTask:SqlStatementSource={quoteattr(sql)} SQLTask:SqlStatementSourceType="{source_type}"/>
        </DTS:ObjectData></DTS:Executable></DTS:Executables></DTS:Executable>''')
    return p


def run(tmp_path, **kwargs):
    p = package(tmp_path, kwargs.pop('sql', 'SELECT 1'))
    result = run_migration(str(p), str(tmp_path / 'out'), **kwargs)
    assert result['failed_package_count'] == 0, result
    return result, result['packages'][0]


def test_standalone_work_is_retained_without_equivalence_claim(tmp_path):
    result, entry = run(tmp_path, catalog='test_catalog')
    assert result['status'] == 'ARTIFACTS_GENERATED'
    assert result['verification']['legacy_match_percent'] is None
    assert not entry['findings']
    inventory = json.loads(Path(entry['artifacts']['source_inventory']).read_text())
    assert any(i['source_id'] == 'Package\\Task' for i in inventory)
    notebook = json.loads(Path(entry['artifacts']['notebook']).read_text())
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code': compile(''.join(cell['source']), '<cell>', 'exec')
    assert 'test_catalog' in ''.join(notebook['cells'][1]['source'])
    assert any('raise RuntimeError' in ''.join(c['source']) for c in notebook['cells'])


def test_strict_blocks_notebook_and_preserves_diagnostics(tmp_path):
    _, entry = run(tmp_path, sql='EXEC dbo.Missing', strict_mode=True)
    assert entry['notebook_status'] == 'BLOCKED'
    assert 'notebook' not in entry['artifacts']
    assert not list((tmp_path / 'out').rglob('*.ipynb'))
    assert Path(entry['artifacts']['assessment']).is_file()


def test_dry_run_has_plan_and_never_calls_ai(tmp_path, monkeypatch):
    import ssis_migration.migrate as m
    def forbidden(*args, **kwargs): raise AssertionError('AI called during dry-run')
    monkeypatch.setattr(m, '_run_ai_assisted_generation', forbidden)
    _, entry = run(tmp_path, dry_run=True, ai=True)
    assert 'notebook' not in entry['artifacts']
    assert Path(entry['artifacts']['plan']).is_file()
    assert entry['assessment']['generation']['operation_coverage_percent'] is None


def test_no_procedure_call_has_no_missing_definition_warning(tmp_path):
    _, entry = run(tmp_path, sql="SELECT 'EXEC dbo.fake' AS value -- EXEC dbo.fake")
    assert not any(f['code'] == 'MISSING_PROCEDURE_DEFINITION' for f in entry['findings'])


def test_missing_and_dynamic_procedure_findings(tmp_path):
    _, entry = run(tmp_path, sql='EXEC missing; EXEC(@sql)')
    codes = {f['code'] for f in entry['findings']}
    assert {'MISSING_PROCEDURE_DEFINITION', 'DYNAMIC_PROCEDURE_REFERENCE'} <= codes


def test_variable_sql_is_incomplete_not_dependency_free(tmp_path):
    p = package(tmp_path, 'User::Sql', source_type='Variable')
    r = run_migration(str(p), str(tmp_path / 'out'))
    assert any(f['code'] == 'SQL_EXTRACTION_INCOMPLETE' for f in r['packages'][0]['findings'])


def test_multiple_procedures_reach_planner_and_nested_dependencies(tmp_path):
    a, b = tmp_path / 'a.sql', tmp_path / 'b.txt'
    a.write_text('CREATE PROCEDURE dbo.a AS BEGIN EXEC dbo.b; END;')
    b.write_text('CREATE PROCEDURE dbo.b AS BEGIN SELECT 1; END;')
    _, entry = run(tmp_path, sql='EXEC dbo.a', procedures_paths=[a, b])
    assert not any(f['code'] == 'MISSING_PROCEDURE_DEFINITION' for f in entry['findings'])
    ir = PackageIR(package_name='P', executables=[ExecutableIR(ref_id='t', executable_type='Microsoft.ExecuteSQLTask', properties={'SqlStatementSource':'EXEC dbo.a'})])
    definitions, origins = load_procedure_files([a, b])
    context = build_migration_context(ir, {}, procedure_definitions=definitions, procedure_origins=origins)
    names = {o['name'] for o in context.databases[0].relevant_objects}
    assert {'a', 'b'} <= names


def test_conflicting_procedures_are_not_silently_selected(tmp_path):
    files = [tmp_path / 'a.sql', tmp_path / 'b.sql']
    files[0].write_text('CREATE PROC dbo.P AS SELECT 1')
    files[1].write_text('CREATE PROC dbo.p AS SELECT 2')
    with pytest.raises(ValueError, match='Conflicting procedure'): load_procedure_files(files)


def test_sql_loader_ignores_false_headers_and_supports_default_schema():
    defs = parse_procedures("-- CREATE PROC dbo.fake AS SELECT 0\nCREATE PROC Real AS SELECT 'CREATE PROC dbo.other';\nGO\nCREATE TABLE dbo.T (id int)", 'custom')
    assert set(defs) == {'custom.real'}
    assert 'CREATE TABLE' not in defs['custom.real']
    assert procedure_calls('EXEC @return = [dbo].[P]') == ['dbo.P']
    assert not has_dynamic_call('EXEC @return = dbo.P')


def test_input_classifier_mixed_sql_and_json():
    mixed = b'CREATE PROC dbo.P AS SELECT 1\nGO\nCREATE TABLE dbo.T (id int)'
    assert classify_dependency('mixed.sql', mixed) == 'mixed_sql'
    assert classify_dependency('data.json', b'{"anything":true}') == 'unknown'
    assert classify_dependency('map.json', b'{"mappings": [{"connection_reference":"x", "metadata_file":"a.json"}]}') == 'connection_mapping'


@pytest.mark.parametrize('code', [
    'from pyspark.sql.functions import udf as make\nf = make(lambda x: x)',
    'from pyspark.sql import functions as F\nmake = F.udf\nf = make(lambda x: x)',
    'from pyspark.sql.functions import pandas_udf\n@pandas_udf("int")\ndef f(x): return x',
    'spark.udf.register("f", lambda x: x)',
    'sql = "CREATE OR REPLACE FUNCTION f(x INT) RETURNS INT RETURN x"\nspark.sql(sql)',
    'rows = df.collect()', 'df.toPandas()',
    'spark.sql("EXEC dbo.P")', 'spark.sql("SELECT :missing")',
    'exec("print(1)")', '# REVIEW_REQUIRED: pending',
])
def test_native_policy_rejects_unsafe_or_incomplete_output(code):
    assert validate_native_spark(code)['status'] == 'REJECTED'


def test_native_helpers_and_bounded_collection_are_allowed():
    assert validate_native_spark('def f():\n    return spark.sql("SELECT 1")')['status'] == 'PASS'
    assert validate_native_spark('rows = spark.sql("SELECT id FROM t LIMIT 1").collect()')['status'] == 'PASS'


def test_fingerprint_changes_on_dependency_changes_and_findings_are_stable(tmp_path):
    f = tmp_path / 'p.sql';f.write_text('CREATE PROC dbo.P AS SELECT 1')
    r1, e1 = run(tmp_path, sql='EXEC dbo.P', procedures_paths=[f])
    r2, e2 = run(tmp_path, sql='EXEC dbo.P', procedures_paths=[f])
    assert r1['run_id'] != r2['run_id']
    assert e1['artifacts']['manifest'] == e2['artifacts']['manifest']
    assert [f['finding_id'] for f in e1['findings']] == [f['finding_id'] for f in e2['findings']]
    f.write_text('CREATE PROC dbo.P AS SELECT 2')
    _, e3 = run(tmp_path, sql='EXEC dbo.P', procedures_paths=[f])
    assert e3['artifacts']['manifest'] != e2['artifacts']['manifest']


def test_human_reviewed_notebook_is_preserved(tmp_path):
    _, entry = run(tmp_path)
    path = Path(entry['artifacts']['notebook'])
    notebook = json.loads(path.read_text());notebook['metadata']['human_reviewed'] = True
    path.write_text(json.dumps(notebook));original = path.read_bytes()
    _, again = run(tmp_path)
    assert again['notebook_status'] == 'SKIPPED_HUMAN_REVIEWED'
    assert path.read_bytes() == original


def test_layer_context_is_recorded_without_silent_schema_rewrite(tmp_path):
    _, entry = run(tmp_path, layer='multi-layer', target_schema='target_silver')
    manifest = json.loads(Path(entry['artifacts']['manifest']).read_text())
    assert manifest['configuration']['layer'] == 'multi-layer'
    codes = {f['code'] for f in entry['findings']}
    assert {'LAYER_MAPPING_REQUIRED','TARGET_MAPPING_REVIEW_REQUIRED'} <= codes


def test_multiple_ddl_files_enrich_package_context(tmp_path):
    a, b = tmp_path / 'a.sql', tmp_path / 'b.sql'
    a.write_text('CREATE TABLE dbo.A ([id] int NOT NULL, PRIMARY KEY (id));')
    b.write_text('CREATE TABLE dbo.B ([id] bigint NULL);')
    ir = PackageIR(package_name='P')
    context = build_migration_context(ir, {}, ddl_paths=[a, b])
    tables = {t.name: t for t in context.databases[0].database_ir.tables}
    assert set(tables) == {'A','B'}
    assert tables['A'].columns[0]['nullable'] is False
    assert tables['A'].columns[0]['is_primary_key'] is True


def test_ddl_enriches_external_metadata_path(tmp_path):
    from ssis_migration.database.connection_mapping import ConnectionMappingSet, ConnectionDatabaseMapping
    # Use the actual metadata fixture to verify the path not used by DTSX fallback.
    root = Path(__file__).resolve().parent.parent
    ddl = tmp_path / 'extra.sql'
    ddl.write_text('CREATE TABLE dbo.NewTable ([id] int NOT NULL);')
    mapping = ConnectionMappingSet([ConnectionDatabaseMapping(connection_reference='c', metadata_file=str(root/'examples/wwi/database_metadata_wwi.json'))])
    context = build_migration_context(PackageIR(package_name='P'), {}, connection_mapping=mapping, ddl_paths=[ddl])
    assert any(t.name == 'NewTable' for t in context.databases[0].database_ir.tables)


def test_conflicting_ddl_is_explicit(tmp_path):
    a, b = tmp_path/'a.sql', tmp_path/'b.sql'
    a.write_text('CREATE TABLE dbo.T (id int);');b.write_text('CREATE TABLE dbo.T (id bigint);')
    with pytest.raises(ValueError, match='Conflicting supplied DDL'):
        build_migration_context(PackageIR(package_name='P'), {}, ddl_paths=[a,b])


def test_configured_default_schema_resolves_unqualified_call(tmp_path):
    p = tmp_path/'p.sql';p.write_text('CREATE PROC custom.P AS SELECT 1')
    _, entry = run(tmp_path, sql='EXEC P', default_schema='custom', procedures_paths=[p])
    assert not any(f['code']=='MISSING_PROCEDURE_DEFINITION' for f in entry['findings'])


def test_partial_failure_is_not_hidden_by_successful_package(tmp_path):
    good = package(tmp_path)
    bad = tmp_path/'bad.dtsx';bad.write_text('not xml')
    result = run_migration(str(tmp_path), str(tmp_path/'out'))
    assert result['status']=='PARTIAL_FAILURE'
    assert result['failed_package_count']==1
    assert result['analyzed_package_count']==1


def test_same_name_packages_get_distinct_output_names(tmp_path):
    for name in ['a','b']:
        folder = tmp_path/name;folder.mkdir();package(folder)
    result = run_migration(str(tmp_path), str(tmp_path/'out'))
    paths = [p['artifacts']['manifest'] for p in result['packages']]
    assert len(set(paths))==2


def test_fingerprint_changes_with_mapping_metadata_and_engine(tmp_path, monkeypatch):
    from ssis_migration.lifecycle import input_manifest
    from ssis_migration.config import AssessmentConfig
    import ssis_migration.lifecycle as lifecycle
    p=package(tmp_path);metadata=tmp_path/'db.json';metadata.write_text('{"value":1}')
    mapping=tmp_path/'map.json';mapping.write_text(json.dumps({'mappings':[{'connection_reference':'c','metadata_file':'db.json'}]}))
    def fp():return input_manifest(p,[],[],mapping,tmp_path,AssessmentConfig(),ai=False,ai_provider='mock')['fingerprint']
    first=fp();metadata.write_text('{"value":2}');second=fp()
    assert first != second
    monkeypatch.setattr(lifecycle,'engine_hash',lambda:'changed')
    assert second != fp()


def test_ai_provider_failure_is_preserved_as_review_finding(tmp_path, monkeypatch):
    import ssis_migration.migrate as migration
    def fail(*a, **k): raise RuntimeError('provider unavailable')
    monkeypatch.setattr(migration, '_run_ai_assisted_generation', fail)
    _, entry = run(tmp_path, ai=True)
    assert any(f['code'] == 'AI_REVIEW_REQUIRED' for f in entry['findings'])
    results = json.loads(Path(entry['artifacts']['validation']).read_text())
    assert any(v.get('error') == 'provider unavailable' for v in results)


def test_rejected_operation_code_is_not_embedded_in_notebook(tmp_path, monkeypatch):
    from ssis_migration.ir.models import ExecutableIR
    from ssis_migration.planner.models import MigrationPlan, MigrationStep
    from ssis_migration.generator.models import GeneratedArtifact
    import ssis_migration.migrate as migration
    from ssis_migration.planner.planner import MigrationPlanner
    from ssis_migration.generator.dispatcher import GeneratorDispatcher
    from ssis_migration.generator.operations import MigrationOperationBuilder
    from ssis_migration.generator.databricks_operations import DatabricksOperationGenerator
    from types import SimpleNamespace
    plan = MigrationPlan(package_name='P', steps=[MigrationStep(step_id='s', name='S', step_type='DATA_FLOW')], metadata={'source_inventory':[]})
    monkeypatch.setattr(MigrationPlanner, 'build_plan', lambda *a: plan)
    artifact = GeneratedArtifact(artifact_id='a', name='a', artifact_type='CODE', target_platform='databricks', language='pyspark', code='x=1', source_step_id='s')
    monkeypatch.setattr(GeneratorDispatcher, 'generate', lambda *a: artifact)
    monkeypatch.setattr(MigrationOperationBuilder, 'build', lambda *a: [SimpleNamespace(operation_id='op', operation_type='EXTRACT', order=1, to_dict=lambda:{})])
    unsafe='from pyspark.sql.functions import udf\nf = udf(lambda x: x)'
    monkeypatch.setattr(DatabricksOperationGenerator, 'generate_operation', lambda *a: unsafe)
    result = migration.generate_migration_artifacts(PackageIR(package_name='P'), {}, None, tmp_path, tmp_path, package_name='P')
    assert result['assessment']['status'] == 'REVIEW_REQUIRED'
    nb=json.loads(Path(result['notebook_file']).read_text())
    code='\n'.join(''.join(c['source']) for c in nb['cells'] if c['cell_type']=='code')
    assert 'import udf' not in code
    assert 'raise RuntimeError' in code
    assert any(f['code']=='UDF_POLICY_VIOLATION' for f in result['findings'])


def test_missing_nested_procedure_is_identified(tmp_path):
    p=tmp_path/'p.sql';p.write_text('CREATE PROC dbo.P AS EXEC dbo.Missing;')
    _, entry=run(tmp_path, sql='EXEC dbo.P', procedures_paths=[p])
    assert any(f['code']=='MISSING_PROCEDURE_DEFINITION' and f['source_id']=='dbo.p' for f in entry['findings'])


def test_cli_strict_exit_status_and_dry_run_artifacts(tmp_path):
    import subprocess, sys
    p = package(tmp_path, sql='EXEC dbo.Missing')
    result = subprocess.run([sys.executable, '-m', 'ssis_migration.migrate', '--source', str(p),
        '--output', str(tmp_path/'cli-out'), '--dry-run', '--strict-mode'], text=True, capture_output=True)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report['status'] == 'REVIEW_REQUIRED'
    assert not list((tmp_path/'cli-out').rglob('*.ipynb'))


def test_ddl_preserves_bracketed_names_and_inline_primary_key():
    from ssis_migration.database.ddl_parser import parse_ddl_text
    ir=parse_ddl_text('CREATE TABLE [Fact].[Stock Holding] ([Stock Key] int PRIMARY KEY);')
    assert ir.tables[0].name=='Stock Holding'
    assert ir.tables[0].columns[0]['is_primary_key']
    assert ir.tables[0].columns[0]['nullable'] is False


def test_job_schema_context_is_not_silently_ignored():
    from ssis_migration.generator.notebook import NotebookAssembler
    from types import SimpleNamespace
    plan={'package_name':'P','steps':[],'unresolved':[],'metadata':{'catalog':'dev'}}
    nb=NotebookAssembler().assemble(plan,[])
    widgets=SimpleNamespace(text=lambda *a:None, get=lambda key:'target_schema' if key=='schema' else 'all' if key=='step_id' else 'dev')
    with pytest.raises(RuntimeError, match='mapping review'):
        exec(''.join(nb['cells'][1]['source']), {'dbutils':SimpleNamespace(widgets=widgets)})
