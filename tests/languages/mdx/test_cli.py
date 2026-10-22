import hashlib
import json

import pytest

from ssis_migration.languages.mdx.__main__ import main
from ssis_migration.languages.mdx import validate_mdx_analysis

SOURCE='SELECT {[Measures].[Sales]} ON COLUMNS FROM [Cube]'


def test_cli_preserves_bom_file_identity_without_sql_or_execution(tmp_path,monkeypatch,capsys):
    import sqlglot
    def forbidden(*args,**kwargs):raise AssertionError('MDX intake/analysis must not invoke SQL parsing/tokenization.')
    monkeypatch.setattr(sqlglot,'parse',forbidden); monkeypatch.setattr(sqlglot,'tokenize',forbidden)
    source=tmp_path/'query.mdx'; raw=b'\xef\xbb\xbf'+SOURCE.encode(); source.write_bytes(raw)
    output=tmp_path/'report.json'
    assert main(['--source',str(source),'--dialect','tm1','--output',str(output)])==0
    report=json.loads(output.read_text())
    validate_mdx_analysis(report)
    assert report['source_file']['sha256']==hashlib.sha256(raw).hexdigest()
    assert report['source']['sha256']==hashlib.sha256(SOURCE.encode()).hexdigest()
    assert report['source']['text']==SOURCE
    assert report['migration_accepted'] is False
    assert 'STRUCTURED' in capsys.readouterr().out


def test_cli_never_overwrites_source_or_existing_reports(tmp_path):
    source=tmp_path/'query.mdx'; source.write_text(SOURCE)
    for output in [source,tmp_path/'existing.json']:
        output.write_text(SOURCE if output==source else 'keep')
        before=output.read_bytes()
        with pytest.raises(SystemExit) as exc:
            main(['--source',str(source),'--dialect','tm1','--output',str(output)])
        assert exc.value.code==2
        assert output.read_bytes()==before


def test_unsupported_query_still_produces_explicit_report(tmp_path):
    source=tmp_path/'query.mdx'; source.write_text('SELECT UnknownSet ON 0 FROM [C]')
    output=tmp_path/'report.json'
    assert main(['--source',str(source),'--dialect','tm1','--output',str(output)])==1
    assert json.loads(output.read_text())['status']=='UNSUPPORTED'


def test_changed_source_prevents_report_emission(tmp_path,monkeypatch):
    import ssis_migration.languages.mdx.__main__ as module
    original=module.describe_file
    def changed(path):
        record=original(path)
        path.write_text(SOURCE+' changed')
        return record
    monkeypatch.setattr(module,'describe_file',changed)
    source=tmp_path/'query.mdx'; source.write_text(SOURCE)
    output=tmp_path/'report.json'
    with pytest.raises(SystemExit) as exc:
        main(['--source',str(source),'--dialect','tm1','--output',str(output)])
    assert exc.value.code==2
    assert not output.exists()


def test_symlink_and_unknown_dialect_are_rejected(tmp_path):
    source=tmp_path/'real.mdx'; source.write_text(SOURCE)
    link=tmp_path/'link.mdx'; link.symlink_to(source)
    for path,dialect in [(link,'tm1'),(source,'tsql')]:
        with pytest.raises(SystemExit) as exc:
            main(['--source',str(path),'--dialect',dialect,'--output',str(tmp_path/'out.json')])
        assert exc.value.code==2
