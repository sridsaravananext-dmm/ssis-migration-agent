import json
import pytest
from ssis_migration.review_store import ReviewStore,revalidate_notebook


def test_reviews_are_versioned_append_only_and_use_revision_checks(tmp_path):
    store=ReviewStore(tmp_path/'reviews.sqlite');finding={'finding_id':'id','severity':'WARNING'}
    assert store.record('v1',finding,decision='OPEN',author='reviewer',note='Investigate')['revision']==1
    with pytest.raises(ValueError,match='Stale'):store.record('v1',finding,decision='OPEN',author='reviewer',note='Old edit')
    store.record('v1',finding,decision='ACCEPTED_WARNING',author='reviewer',note='Checked target types',expected_revision=1)
    assert len(store.history('v1'))==2 and store.history('v2')==[]
    assert store.record('v2',finding,decision='OPEN',author='reviewer',note='New source version')['revision']==1


def test_blocking_finding_cannot_be_accepted_or_resolved_without_implementation(tmp_path):
    store=ReviewStore(tmp_path/'reviews.sqlite');finding={'finding_id':'id','severity':'ERROR'}
    with pytest.raises(ValueError):store.record('v1',finding,decision='ACCEPTED_WARNING',author='r',note='Skip')
    with pytest.raises(ValueError):store.record('v1',finding,decision='RESOLVED',author='r',note='Skip')
    decision=store.record('v1',finding,decision='EVIDENCE_RECORDED',author='r',note='Attached analysis',evidence=['report.json'])
    assert decision['execution_gate_changed'] is False


def test_edited_notebook_checks_preserve_file_and_source_blockers(tmp_path):
    p=tmp_path/'draft.ipynb';raw=json.dumps({'cells':[{'cell_type':'code','source':['spark.sql("SELECT 1")']}]}).encode();p.write_bytes(raw)
    result=revalidate_notebook(p,findings=[{'finding_id':'missing','severity':'ERROR'}])
    assert result['status']=='REVALIDATION_REQUIRED' and result['blocking_source_findings']==['missing']
    assert p.read_bytes()==raw
    assert revalidate_notebook(p)['status']=='STATIC_CHECKS_PASSED'
    p.write_text(json.dumps({'cells':[{'cell_type':'code','source':['from pyspark.sql.functions import udf']}]}))
    assert revalidate_notebook(p)['status']=='REVALIDATION_REQUIRED'


def test_backend_cli_reads_real_package_version_and_preserves_edited_notebook(tmp_path,capsys):
    from ssis_migration.backend import main
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'fingerprint':'source-version'}))
    result=tmp_path/'result.json';result.write_text(json.dumps({'artifacts':{'manifest':str(manifest)},'findings':[{'finding_id':'id','severity':'ERROR'}]}))
    store=tmp_path/'reviews.sqlite'
    assert main(['review','--package-result',str(result),'--store',str(store),'--finding','id','--decision','NEEDS_IMPLEMENTATION','--author','reviewer','--note','Missing source body'])==0
    assert ReviewStore(store).history('source-version')[0]['finding_id']=='id'
    notebook=tmp_path/'draft.ipynb';notebook.write_text(json.dumps({'cells':[{'cell_type':'code','source':['spark.sql("SELECT 1")']}]}))
    with pytest.raises(ValueError,match='overwrite'):main(['revalidate','--notebook',str(notebook),'--package-result',str(result),'--output',str(notebook)])
    output=tmp_path/'revalidation.json'
    assert main(['revalidate','--notebook',str(notebook),'--package-result',str(result),'--output',str(output)])==1
    assert json.loads(output.read_text())['blocking_source_findings']==['id']
