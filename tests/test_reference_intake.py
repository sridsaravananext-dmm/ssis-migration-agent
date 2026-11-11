"""Reference exports remain evidence, separate from executable source dependencies."""
import base64
import io
import json
from pathlib import Path
from urllib.parse import quote
import zipfile
import pytest

from ssis_migration.intake import classify_dependency
from ssis_migration.migrate import run_migration
from ssis_migration.references import inspect_references,review_text,cells


def archive(path,files):
    path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path,'w') as z:
        for name,value in files.items():z.writestr(name,value)
    return path


def html(commands):
    payload=base64.b64encode(quote(json.dumps({'language':'python','commands':[{'command':s} for s in commands]})).encode()).decode()
    return '<html><script>var __DATABRICKS_NOTEBOOK_MODEL = '+repr(payload)+';</script></html>'


def source(path,sql='SELECT 1 AS result'):
    path.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" xmlns:S="sql" DTS:refId="Package" DTS:ObjectName="Synthetic">
    <DTS:Executables><DTS:Executable DTS:refId="Package\\Task" DTS:ExecutableType="Microsoft.ExecuteSQLTask"><DTS:ObjectData>
    <S:SqlTaskData S:SqlStatementSource="'''+sql+'''"/></DTS:ObjectData></DTS:Executable></DTS:Executables></DTS:Executable>''')
    return path


def test_same_named_cfg_archives_keep_definitions_and_configuration_separate(tmp_path):
    ddl='# Databricks notebook source\n# MAGIC %sql\n# MAGIC CREATE TABLE ${app}_${env}.settings (id BIGINT NOT NULL, description STRING);'
    config='# Databricks notebook source\n# MAGIC %sql\n# MAGIC INSERT INTO ${app}_${env}.settings (id, description) VALUES (1, \'sample\');'
    a=archive(tmp_path/'definitions/02-cfg.zip',{'02-cfg/defs.py':ddl})
    b=archive(tmp_path/'configuration/02-cfg.zip',{'02-cfg/load.py':config})
    inventory=inspect_references([a,b])
    assert inventory['summary']['input_count']==2
    assert len({i['sha256'] for i in inventory['inputs']})==2
    assert {f['kind'] for f in inventory['files']}=={'definition_notebook','configuration_notebook'}
    assert len({f['reference_id'] for f in inventory['files']})==2
    assert inventory==inspect_references([b,a])


def test_html_models_and_python_sql_templates_expose_column_mismatches_without_execution(tmp_path):
    a=archive(tmp_path/'schema.zip',{'audit.html':html(['%sql\nCREATE TABLE ${app}_${env}.execution_log (execution_id BIGINT NOT NULL);'])})
    program='''# Databricks notebook source
from pathlib import Path
Path("SHOULD_NOT_BE_CREATED").write_text("side effect")
class Recorder:
    def start(self, env, execution_id):
        spark.sql(f"INSERT INTO {app}_{env}.execution_log (execution_id, job_id) VALUES ({execution_id}, 1)")
'''
    b=archive(tmp_path/'libraries.zip',{'libs/recorder.py':program})
    r=inspect_references([a,b])
    mismatch=next(f for f in r['findings'] if f['code']=='REFERENCE_INSERT_COLUMN_MISMATCH')
    assert mismatch['missing_columns']==['job_id']
    library=next(f for f in r['files'] if f['kind']=='library_notebook')
    assert any(s.get('qualified_name')=='Recorder.start' for s in library['symbols'])
    assert not Path('SHOULD_NOT_BE_CREATED').exists()
    assert not r['executed'] and not r['applied_as_legacy_definitions']
    assert 'Missing columns: job_id' in review_text(r)


@pytest.mark.parametrize('name',['../bad.py','/absolute.py','C:/outside.py'])
def test_unsafe_archive_paths_are_rejected_before_any_extraction(tmp_path,name):
    with pytest.raises(ValueError,match='unsafe member path'):
        inspect_references([archive(tmp_path/'unsafe.zip',{name:'print("not run")'})])


def test_duplicate_casefolded_paths_symlinks_and_nested_archives_are_rejected(tmp_path):
    duplicate=archive(tmp_path/'duplicate.zip',{'A.py':'pass','a.py':'pass'})
    with pytest.raises(ValueError,match='duplicate'):inspect_references([duplicate])
    nested=archive(tmp_path/'nested.zip',{'inner.zip':b'not relevant'})
    with pytest.raises(ValueError,match='Nested'):inspect_references([nested])
    link=tmp_path/'link.zip'
    with zipfile.ZipFile(link,'w') as z:
        info=zipfile.ZipInfo('link.py');info.create_system=3;info.external_attr=0o120777<<16
        z.writestr(info,'/outside')
    with pytest.raises(ValueError,match='Symlink'):inspect_references([link])


def test_function_notebooks_are_recorded_without_installing_udfs(tmp_path):
    p=tmp_path/'functions.py';p.write_text('# Databricks notebook source\n# MAGIC %sql\n# MAGIC CREATE OR REPLACE FUNCTION app.example(x INT) RETURNS INT RETURN x + 1;')
    r=inspect_references([p])
    assert r['files'][0]['kind']=='function_notebook'
    assert r['files'][0]['udfs']
    assert any(f['code']=='REFERENCE_UDF_NOT_APPLIED' for f in r['findings'])


def test_python_udfs_and_unbounded_collections_are_not_treated_as_native_helpers(tmp_path):
    p=tmp_path/'library.py'
    p.write_text('''# Databricks notebook source
def business_value(value):
    return value
row_function = udf(business_value)
def export_rows(df):
    return df.collect()
''')
    r=inspect_references([p])
    codes={f['code'] for f in r['findings']}
    assert 'REFERENCE_UDF_POLICY_VIOLATION' in codes
    assert 'REFERENCE_UNBOUNDED_DRIVER_COLLECTION' in codes


def test_notebook_outputs_and_markdown_are_not_dependency_evidence(tmp_path):
    p=tmp_path/'ref.ipynb'
    p.write_text(json.dumps({'cells':[{'cell_type':'markdown','source':['CREATE TABLE ignored.bad (id INT);']},
        {'cell_type':'code','source':['%sql\nCREATE TABLE reference.good (id INT);'],'outputs':[{'text':'CREATE TABLE ignored.output (id INT);'}]}]}))
    r=inspect_references([p])
    assert [t['name'] for t in r['files'][0]['tables']]==['good']


def test_literal_values_defaults_and_run_arguments_are_not_copied_into_inventory(tmp_path):
    p=tmp_path/'reference.py'
    p.write_text('''# Databricks notebook source
def helper(password="never-copy-this-default"):
    spark.sql("INSERT INTO app.config (setting) VALUES ('never-copy-this-row')")
# COMMAND ----------
# MAGIC %run ../shared/setup $password="never-copy-this-argument"
''')
    r=inspect_references([p]);encoded=json.dumps(r)
    assert 'never-copy-this' not in encoded
    assert r['files'][0]['includes'][0]['path']=='../shared/setup'
    assert r['files'][0]['includes'][0]['has_arguments']


def test_following_add_column_cells_are_included_in_reference_contracts(tmp_path):
    p=tmp_path/'definitions.py'
    p.write_text('''# Databricks notebook source
# MAGIC %sql
# MAGIC CREATE TABLE app.execution_log (id BIGINT);
# COMMAND ----------
# MAGIC %sql
# MAGIC ALTER TABLE app.execution_log ADD COLUMN job_id BIGINT;
# COMMAND ----------
spark.sql("INSERT INTO app.execution_log (id, job_id) VALUES (1,2)")
''')
    r=inspect_references([p])
    assert {c['name'] for c in r['files'][0]['tables'][0]['columns']}=={'id','job_id'}
    assert not any(f['code']=='REFERENCE_INSERT_COLUMN_MISMATCH' for f in r['findings'])


def test_invalid_notebook_payload_remains_uninspected_evidence(tmp_path):
    p=tmp_path/'bad.html';p.write_text('<html>No export model</html>')
    r=inspect_references([p])
    assert r['files'][0]['kind']=='uninspected'
    assert r['findings'][0]['code']=='REFERENCE_FORMAT_REVIEW'


def test_references_participate_in_versions_but_do_not_supply_legacy_procedures(tmp_path):
    package=source(tmp_path/'sample.dtsx','EXEC dbo.Unavailable')
    reference=tmp_path/'target.sql';reference.write_text('CREATE PROC dbo.Unavailable AS SELECT 1;')
    result=run_migration(package,tmp_path/'out',references_paths=[reference])
    item=result['packages'][0]
    assert result['failed_package_count']==0
    assert any(f['code']=='MISSING_PROCEDURE_DEFINITION' for f in item['findings'])
    assert Path(item['artifacts']['reference_inventory']).is_file()
    assert Path(item['artifacts']['reference_review']).is_file()
    manifest=json.loads(Path(item['artifacts']['manifest']).read_text())
    assert manifest['references'][0]['name']=='target.sql' and not manifest['procedures']
    assert classify_dependency('target.py',b'not run')=='reference'
    first=Path(item['artifacts']['manifest']).parent
    reference.write_text('CREATE PROC dbo.Unavailable AS SELECT 2;')
    updated=run_migration(package,tmp_path/'out',references_paths=[reference])
    assert Path(updated['packages'][0]['artifacts']['manifest']).parent!=first


def test_dry_run_writes_reference_review_without_a_final_notebook(tmp_path):
    p=tmp_path/'target.py';p.write_text('# Databricks notebook source\nraise RuntimeError("not executed")')
    result=run_migration(source(tmp_path/'sample.dtsx'),tmp_path/'out',references_paths=[p],dry_run=True)
    item=result['packages'][0]
    assert item['notebook_status']=='DRY_RUN' and 'notebook' not in item['artifacts']
    assert Path(item['artifacts']['reference_review']).is_file()
