import hashlib
import json

import pytest

from ssis_migration.adapters.tm1.rules.__main__ import main
from ssis_migration.adapters.tm1.rules import validate_rules_analysis

SOURCE="['Measure':'Revenue']=N:1; FEEDERS; ['Measure':'Units']=>['Measure':'Revenue'];"


def test_bom_cli_uses_original_file_evidence_without_sql_fallback(tmp_path,monkeypatch,capsys):
    import sqlglot
    def forbidden(*args,**kwargs):raise AssertionError('Rules intake/analysis must not invoke SQL.')
    monkeypatch.setattr(sqlglot,'parse',forbidden);monkeypatch.setattr(sqlglot,'tokenize',forbidden)
    source=tmp_path/'unconfirmed_cube_name.rux';raw=b'\xef\xbb\xbf'+SOURCE.encode();source.write_bytes(raw)
    output=tmp_path/'report.json'
    assert main(['--source',str(source),'--format','plain-rux-text','--output',str(output)])==0
    report=json.loads(output.read_text());validate_rules_analysis(report)
    assert report['source_file']['sha256']==hashlib.sha256(raw).hexdigest()
    assert report['source']['sha256']==hashlib.sha256(SOURCE.encode()).hexdigest()
    assert report['source_cube'] is None
    assert len(report['feeder_relations'])==1
    assert report['migration_accepted'] is False
    assert 'STRUCTURED' in capsys.readouterr().out


def test_cli_never_overwrites_source_or_previous_output(tmp_path):
    source=tmp_path/'query.rux';source.write_text(SOURCE)
    for output in [source,tmp_path/'existing.json']:
        if output!=source:output.write_text('keep')
        before=output.read_bytes()
        with pytest.raises(SystemExit) as exc:main(['--source',str(source),'--format','plain-rux-text','--output',str(output)])
        assert exc.value.code==2 and output.read_bytes()==before


def test_unsupported_source_produces_a_complete_explicit_report(tmp_path):
    source=tmp_path/'rules.rux';source.write_text("['A']=N:CustomFunction(1);")
    output=tmp_path/'report.json'
    assert main(['--source',str(source),'--format','plain-rux-text','--output',str(output)])==1
    assert json.loads(output.read_text())['status']=='UNSUPPORTED'


def test_source_changed_after_inventory_prevents_output(tmp_path,monkeypatch):
    import ssis_migration.adapters.tm1.rules.__main__ as module
    original=module.describe_file
    def changed(path):
        record=original(path);path.write_text(SOURCE+' changed');return record
    monkeypatch.setattr(module,'describe_file',changed)
    source=tmp_path/'rules.rux';source.write_text(SOURCE);output=tmp_path/'report.json'
    with pytest.raises(SystemExit) as exc:main(['--source',str(source),'--format','plain-rux-text','--output',str(output)])
    assert exc.value.code==2 and not output.exists()


def test_symlink_and_unimplemented_formats_are_rejected(tmp_path):
    source=tmp_path/'rules.rux';source.write_text(SOURCE);link=tmp_path/'linked.rux';link.symlink_to(source)
    for path,format in [(link,'plain-rux-text'),(source,'binary-rux')]:
        with pytest.raises(SystemExit) as exc:main(['--source',str(path),'--format',format,'--output',str(tmp_path/'out.json')])
        assert exc.value.code==2
