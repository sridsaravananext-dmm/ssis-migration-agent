import json
from pathlib import Path

import pytest

from ssis_migration.adapters.tm1.metadata import load_tm1_metadata, validate_tm1_binding_report
from ssis_migration.adapters.tm1.metadata.__main__ import main
from ssis_migration.adapters.tm1.ti.__main__ import analyze_manifest

ROOT=Path(__file__).resolve().parents[3]


def inputs(tmp_path):
    source=analyze_manifest(ROOT/'examples/tm1/ti/manifest.json')
    analysis=tmp_path/'analysis.json';analysis.write_text(json.dumps(source))
    metadata=tmp_path/'metadata.json'
    metadata.write_bytes((ROOT/'examples/tm1/metadata/synthetic_catalog.json').read_bytes())
    return analysis,metadata


def args(analysis,metadata,output):
    return ['--analysis',str(analysis),'--metadata',str(metadata),'--output',str(output)]


def test_offline_cli_preserves_bom_and_reports_partial_binding_without_execution(tmp_path,monkeypatch,capsys):
    import socket
    import subprocess
    import sqlglot
    analysis,metadata=inputs(tmp_path)
    metadata.write_bytes(b'\xef\xbb\xbf'+metadata.read_bytes())
    def forbidden(*a,**kw):raise AssertionError('Metadata binding must not connect, parse SQL or execute a workload.')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(sqlglot,'parse',forbidden);monkeypatch.setattr(sqlglot,'tokenize',forbidden)
    output=tmp_path/'report.json'
    assert main(args(analysis,metadata,output))==1
    result=json.loads(output.read_text());validate_tm1_binding_report(result)
    assert result['status']=='PARTIAL' and result['metadata_issues']==[]
    assert len(result['bindings'])==30
    assert sum(b['status']!='MATCHED_DECLARATION' for b in result['bindings'])==3
    assert result['metadata_inputs'][0]['source']['text'].startswith('\ufeff')
    assert result['migration_accepted'] is False
    assert 'PARTIAL' in capsys.readouterr().out


def test_output_never_overwrites_analysis_metadata_or_an_existing_report(tmp_path):
    analysis,metadata=inputs(tmp_path)
    existing=tmp_path/'keep.json';existing.write_text('keep')
    for output in [analysis,metadata,existing]:
        before=output.read_bytes()
        with pytest.raises(SystemExit) as exc:main(args(analysis,metadata,output))
        assert exc.value.code==2 and output.read_bytes()==before


@pytest.mark.parametrize('role',['analysis','metadata'])
def test_symlink_inputs_are_rejected(tmp_path,role):
    analysis,metadata=inputs(tmp_path)
    path=analysis if role=='analysis' else metadata
    saved=path.with_suffix('.original');path.rename(saved);path.symlink_to(saved)
    with pytest.raises(SystemExit) as exc:main(args(analysis,metadata,tmp_path/'out.json'))
    assert exc.value.code==2


@pytest.mark.parametrize('role',['analysis','metadata'])
def test_changed_input_during_binding_prevents_output(tmp_path,monkeypatch,role):
    import ssis_migration.adapters.tm1.metadata.__main__ as module
    analysis,metadata=inputs(tmp_path);bind=module.bind_tm1_metadata
    def changed(*a,**kw):
        report=bind(*a,**kw)
        target=analysis if role=='analysis' else metadata
        target.write_bytes(target.read_bytes()+b' changed')
        return report
    monkeypatch.setattr(module,'bind_tm1_metadata',changed)
    output=tmp_path/'out.json'
    with pytest.raises(SystemExit) as exc:main(args(analysis,metadata,output))
    assert exc.value.code==2 and not output.exists()


def test_changed_metadata_during_inventory_is_not_given_a_fresh_identity(tmp_path,monkeypatch):
    import ssis_migration.adapters.tm1.metadata.catalog as module
    _,metadata=inputs(tmp_path);describe=module.describe_file
    def changed(path):
        path.write_bytes(path.read_bytes()+b' ')
        return describe(path)
    monkeypatch.setattr(module,'describe_file',changed)
    with pytest.raises(ValueError,match='changed during inventory'):load_tm1_metadata(metadata)


@pytest.mark.parametrize('text',['{"dimensions":[],"dimensions":[]}','{"value":NaN}','not json'])
def test_invalid_metadata_never_emits_a_success_report(tmp_path,text):
    analysis,metadata=inputs(tmp_path);metadata.write_text(text);output=tmp_path/'out.json'
    with pytest.raises(SystemExit) as exc:main(args(analysis,metadata,output))
    assert exc.value.code==2 and not output.exists()


def test_cli_zero_exit_only_means_complete_declaration_binding(tmp_path):
    from ssis_migration.languages.mdx import analyze_mdx
    analysis,metadata=inputs(tmp_path)
    source=analyze_mdx('SELECT {[Product].[Product].[A]} ON 0 FROM [Sales]',dialect='tm1',file_id='mdx-source')
    analysis.write_text(json.dumps(source))
    document=json.loads(metadata.read_text());document['source_export_version']='synthetic-test-declaration'
    metadata.write_text(json.dumps(document))
    output=tmp_path/'out.json'
    assert main(args(analysis,metadata,output))==0
    report=json.loads(output.read_text())
    assert report['status']=='BOUND_REVIEW_REQUIRED' and not report['migration_accepted']
    assert report['semantic_status']=='NOT_VERIFIED'


def test_repeated_metadata_arguments_preserve_all_candidates(tmp_path):
    analysis,metadata=inputs(tmp_path)
    duplicate=tmp_path/'duplicate.json';duplicate.write_bytes(metadata.read_bytes())
    output=tmp_path/'out.json'
    assert main(args(analysis,metadata,output)+['--metadata',str(duplicate)])==1
    report=json.loads(output.read_text())
    assert report['status']=='BLOCKED' and len(report['metadata_inputs'])==2
    assert any(b['status']=='AMBIGUOUS' for b in report['bindings'])


def test_synthetic_example_matches_portable_golden_summary():
    from ssis_migration.adapters.tm1.metadata import bind_tm1_metadata
    analysis=analyze_manifest(ROOT/'examples/tm1/ti/manifest.json')
    snapshot=load_tm1_metadata(ROOT/'examples/tm1/metadata/synthetic_catalog.json')
    report=bind_tm1_metadata(analysis,[snapshot])
    summary={'status':report['status'],'binding_count':len(report['bindings']),
             'matched_declarations':sum(b['status']=='MATCHED_DECLARATION' for b in report['bindings']),
             'unresolved':[[b['kind'],b['status']] for b in report['bindings'] if b['status']!='MATCHED_DECLARATION'],
             'original_dependency_count':len(report['remaining_dependencies']),
             'metadata_issue_count':len(report['metadata_issues']),
             'semantic_status':report['semantic_status'],'migration_accepted':report['migration_accepted']}
    expected=Path(__file__).parent/'fixtures/ti_metadata_summary.json'
    assert summary==json.loads(expected.read_text())
