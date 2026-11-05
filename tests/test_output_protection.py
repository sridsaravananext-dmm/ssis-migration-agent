"""Regeneration must preserve manual edits and keep bookkeeping out of coverage."""
import json
from pathlib import Path

import pytest

from ssis_migration.assessment import assess_operations, assessment_summary
from ssis_migration.drafts import pending_operation
from ssis_migration.migrate import run_migration


def run(tmp_path):
    source=tmp_path/'minimal.dtsx'
    source.write_text('''<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts"
      xmlns:SQLTask="www.microsoft.com/sqlserver/dts/tasks/sqltask" DTS:refId="Package" DTS:ObjectName="Minimal">
      <DTS:Executables><DTS:Executable DTS:refId="Package\\T" DTS:ExecutableType="Microsoft.ExecuteSQLTask">
      <DTS:ObjectData><SQLTask:SqlTaskData SQLTask:SqlStatementSource="SELECT 1"/></DTS:ObjectData>
      </DTS:Executable></DTS:Executables></DTS:Executable>''')
    report=run_migration(str(source),str(tmp_path/'out'))
    return report,report['packages'][0]


def test_unmarked_manual_edit_is_preserved_and_does_not_count_as_generation(tmp_path):
    _,item=run(tmp_path)
    path=Path(item['artifacts']['notebook'])
    nb=json.loads(path.read_text())
    nb['cells'].append({'cell_type':'markdown','metadata':{},'source':['Manually completed procedure section.']})
    path.write_text(json.dumps(nb))
    edited=path.read_bytes()
    report,again=run(tmp_path)
    assert path.read_bytes()==edited
    assert again['notebook_status']=='SKIPPED_HUMAN_REVIEWED'
    assert again['protection_reason']=='NOTEBOOK_CONTENT_CHANGED'
    assert again['execution_readiness']['status']=='REVALIDATION_REQUIRED'
    assert again['artifacts']['notebook']==str(path)
    assert Path(again['artifacts']['review']).is_file()
    assert report['preserved_package_count']==1
    assert report['generated_package_count']==0 and report['analyzed_package_count']==0


def test_unchanged_generated_notebook_is_reassessed_normally(tmp_path):
    _,first=run(tmp_path)
    manifest=json.loads(Path(first['artifacts']['manifest']).read_text())
    baseline=manifest['generated_notebooks'][Path(first['artifacts']['notebook']).name]
    assert len(baseline)==64
    report,second=run(tmp_path)
    assert second['notebook_status']=='GENERATED_FOR_REVIEW'
    assert report['generated_package_count']==1 and report['preserved_package_count']==0
    assert first['artifacts']['notebook']==second['artifacts']['notebook']


@pytest.mark.parametrize('change,reason', [('notebook','UNREADABLE_NOTEBOOK'),('manifest','UNREADABLE_MANIFEST'),('baseline','UNTRACKED_NOTEBOOK')])
def test_unreadable_or_untracked_output_is_preserved_conservatively(tmp_path,change,reason):
    _,item=run(tmp_path)
    notebook=Path(item['artifacts']['notebook'])
    manifest=Path(item['artifacts']['manifest'])
    if change=='notebook': notebook.write_text('unreadable notebook')
    elif change=='manifest': manifest.write_text('unreadable manifest')
    else:
        data=json.loads(manifest.read_text());data.pop('generated_notebooks');manifest.write_text(json.dumps(data))
    original=notebook.read_bytes()
    _,again=run(tmp_path)
    assert again['protection_reason']==reason
    assert notebook.read_bytes()==original


def test_bookkeeping_is_validated_but_excluded_from_operation_coverage():
    operations={'step':[
        {'operation_id':'container','operation_type':'STRUCTURAL','code':'_container_complete = True'},
        {'operation_id':'query','operation_type':'NATIVE_SQL','code':'result = spark.sql("SELECT 1")'},
        pending_operation('missing',['MISSING_PROCEDURE_DEFINITION'])]}
    validations,findings=assess_operations(operations,[])
    assert not findings
    assert next(v for v in validations if v['operation_id']=='container')['status']=='PASS'
    summary=assessment_summary([],validations,[])
    assert summary['generation']['structural_operation_count']==1
    assert summary['generation']['operation_count']==2
    assert summary['generation']['statically_valid_operations']==1
    assert summary['generation']['pending_operations']==1
    assert summary['generation']['operation_coverage_percent']==50
    assert summary['verification']['legacy_match_percent'] is None


def test_only_bookkeeping_has_no_business_operation_coverage():
    validations,_=assess_operations({'s':[{'operation_id':'c','operation_type':'STRUCTURAL','code':'_complete = True'}]},[])
    summary=assessment_summary([],validations,[])
    assert summary['generation']['operation_count']==0
    assert summary['generation']['operation_coverage_percent'] is None
