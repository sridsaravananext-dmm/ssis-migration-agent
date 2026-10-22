import hashlib
import json
from pathlib import Path

import pytest

from ssis_migration.adapters.tm1.ti import validate_ti_analysis
from ssis_migration.adapters.tm1.ti.__main__ import analyze_manifest, main


def make_manifest(tmp_path, *, bodies=None):
    bodies = bodies if bodies is not None else {
        'prolog': "DataSourceType='ASCII';",
        'metadata': '',
        'data': "CellPutN(vAmount,'Sales',vProduct,'Amount');",
        'epilog': "ASCIIOutput(pLog,'Finished');",
    }
    manifest = {
        'manifest_version': '1.0', 'export_format': 'ti_section_files_v1',
        'process_name': None, 'source_export_version': None, 'sections': {},
    }
    for role, text in bodies.items():
        raw = text if isinstance(text, bytes) else text.encode('utf-8')
        path = tmp_path / (role + '.ti')
        path.write_bytes(raw)
        manifest['sections'][role] = {'path': path.name, 'sha256': hashlib.sha256(raw).hexdigest()}
    path = tmp_path / 'process.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


def test_bom_files_preserve_raw_and_decoded_evidence_without_language_fallback(tmp_path, monkeypatch, capsys):
    import sqlglot
    import ssis_migration.adapters.tm1.rules.analysis as rules
    import ssis_migration.languages.mdx.analysis as mdx

    def forbidden(*args, **kwargs):
        raise AssertionError('TI analysis must not invoke SQL, rules or MDX analysis.')

    monkeypatch.setattr(sqlglot, 'parse', forbidden)
    monkeypatch.setattr(sqlglot, 'tokenize', forbidden)
    monkeypatch.setattr(rules, 'analyze_rules', forbidden)
    monkeypatch.setattr(mdx, 'analyze_mdx', forbidden)
    text = "# Unicode: café\nCellPutN(vAmount,'Sales',vProduct,'Amount');"
    raw = b'\xef\xbb\xbf' + text.encode('utf-8')
    path = make_manifest(tmp_path, bodies={'prolog': '', 'metadata': '', 'data': raw, 'epilog': ''})
    path.write_bytes(b'\xef\xbb\xbf' + path.read_bytes())
    output = tmp_path / 'report.json'
    assert main(['--manifest', str(path), '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    validate_ti_analysis(report)
    section = report['sections'][2]
    assert section['source_file']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert section['source']['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert section['source']['text'] == text
    assert report['manifest_file']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report['process_name'] is None and report['source_export_version'] is None
    assert report['migration_accepted'] is False
    assert 'STRUCTURED' in capsys.readouterr().out


def test_missing_role_reports_missing_but_missing_referenced_file_is_an_intake_error(tmp_path):
    path = make_manifest(tmp_path, bodies={'data': "n=1;"})
    output = tmp_path / 'report.json'
    assert main(['--manifest', str(path), '--output', str(output)]) == 1
    report = json.loads(output.read_text())
    assert report['missing_sections'] == ['prolog', 'metadata', 'epilog']
    assert report['sections'][0]['source'] is None
    (tmp_path / 'data.ti').unlink()
    with pytest.raises(SystemExit) as exc:
        main(['--manifest', str(path), '--output', str(tmp_path / 'missing.json')])
    assert exc.value.code == 2
    assert not (tmp_path / 'missing.json').exists()


@pytest.mark.parametrize('mutation', ['stale_hash', 'unknown_role', 'unsupported_format', 'empty_version', 'bad_utf8'])
def test_invalid_manifest_or_source_is_rejected(tmp_path, mutation):
    path = make_manifest(tmp_path)
    manifest = json.loads(path.read_text())
    if mutation == 'stale_hash':
        (tmp_path / 'data.ti').write_text('changed;')
    elif mutation == 'unknown_role':
        manifest['sections']['guessed'] = manifest['sections']['data']
    elif mutation == 'unsupported_format':
        manifest['export_format'] = 'native_pro'
    elif mutation == 'empty_version':
        manifest['source_export_version'] = ''
    else:
        raw = b'\xff'
        (tmp_path / 'data.ti').write_bytes(raw)
        manifest['sections']['data']['sha256'] = hashlib.sha256(raw).hexdigest()
    path.write_text(json.dumps(manifest))
    output = tmp_path / 'report.json'
    with pytest.raises(SystemExit) as exc:
        main(['--manifest', str(path), '--output', str(output)])
    assert exc.value.code == 2 and not output.exists()


@pytest.mark.parametrize('text', [
    '{"sections":{},"sections":{}}',
    '{"source_export_version":NaN}',
    '{"source_export_version":Infinity}',
])
def test_duplicate_keys_and_nonfinite_json_are_not_silently_accepted(tmp_path, text):
    path = tmp_path / 'process.json'
    path.write_text(text)
    with pytest.raises(ValueError):
        analyze_manifest(path)


def test_cli_exclusively_creates_output_and_preserves_all_inputs(tmp_path):
    path = make_manifest(tmp_path)
    existing = tmp_path / 'existing.json'
    existing.write_text('keep')
    for output in [path, tmp_path / 'data.ti', existing]:
        original = output.read_bytes()
        with pytest.raises(SystemExit) as exc:
            main(['--manifest', str(path), '--output', str(output)])
        assert exc.value.code == 2 and output.read_bytes() == original


@pytest.mark.parametrize('role', ['manifest', 'data'])
def test_symlink_inputs_are_rejected(tmp_path, role):
    path = make_manifest(tmp_path)
    source = path if role == 'manifest' else tmp_path / 'data.ti'
    original = source.with_suffix('.original')
    source.rename(original)
    source.symlink_to(original)
    with pytest.raises(ValueError, match='symlink|non-regular'):
        analyze_manifest(path)


@pytest.mark.parametrize('role', ['manifest', 'data'])
def test_changed_input_after_analysis_prevents_output(tmp_path, monkeypatch, role):
    import ssis_migration.adapters.tm1.ti.__main__ as module
    path = make_manifest(tmp_path)
    analyze = module.analyze_ti_sections

    def changed(*args, **kwargs):
        report = analyze(*args, **kwargs)
        target = path if role == 'manifest' else tmp_path / 'data.ti'
        target.write_bytes(target.read_bytes() + b' changed')
        return report

    monkeypatch.setattr(module, 'analyze_ti_sections', changed)
    output = tmp_path / 'report.json'
    with pytest.raises(SystemExit) as exc:
        main(['--manifest', str(path), '--output', str(output)])
    assert exc.value.code == 2 and not output.exists()


def test_native_serialized_section_is_explicitly_unsupported(tmp_path):
    path = make_manifest(tmp_path, bodies={'data': '572,0\n573,1\n'})
    output = tmp_path / 'report.json'
    assert main(['--manifest', str(path), '--output', str(output)]) == 1
    report = json.loads(output.read_text())
    assert report['status'] == 'UNSUPPORTED'
    assert report['sections'][2]['source']['text'] == '572,0\n573,1\n'
    assert not report['sections'][2]['structure_complete']


def test_synthetic_example_matches_portable_golden_behavior_summary():
    root=Path(__file__).resolve().parents[3]
    report=analyze_manifest(root/'examples/tm1/ti/manifest.json')
    summary={
        'status':report['status'], 'process_name':report['process_name'],
        'source_export_version':report['source_export_version'],
        'sections':[{'name':s['name'],'statement_kinds':[n['kind'] for n in s['statements']],
                     'edge_kinds':[e['kind'] for e in s['control_flow']['edges']]} for s in report['sections']],
        'effects':[[e['section'],e['kind'],e['target_literal']] for e in report['effects']],
        'dependency_count':len(report['dependencies']),
        'semantic_status':report['semantic_status'],'migration_accepted':report['migration_accepted'],
    }
    expected=Path(__file__).parent/'fixtures/ti_sales_summary.json'
    assert summary==json.loads(expected.read_text())
