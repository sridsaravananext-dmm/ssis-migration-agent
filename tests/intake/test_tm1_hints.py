import pytest

from ssis_migration.intake.inventory import build_intake_manifest


@pytest.mark.parametrize('text', [
    "-- CellPutN(1, 'Cube');", "# CellPutS('value', 'Cube');", '/* SKIPCHECK; CellPutN(1); */',
    "var text = 'CellPutN(1, Cube)';", 'var text = "DatasourceType=ASCII";',
    "/* comment */ 'SKIPCHECK;'", '[CellPutN(1)]',
])
def test_comments_and_literals_cannot_confirm_tm1_platform(tmp_path,text):
    source=tmp_path/'notes.txt'; source.write_text(text)
    manifest=build_intake_manifest(source)
    assert manifest['files'][0]['platform']=='unknown'
    assert manifest['files'][0]['format'] not in {'tm1_ti','tm1_rules'}


@pytest.mark.parametrize('text,format', [
    ("# Ignore comment\nCellPutN(1, 'Sales', 'A');",'tm1_ti'),
    ('/* Header */\nSKIPCHECK;','tm1_rules'),
    ('// Header\nFEEDERS;','tm1_rules'),
])
def test_real_tm1_signatures_remain_detected_and_adapter_unimplemented(tmp_path,text,format):
    source=tmp_path/'export.txt'; source.write_text(text)
    manifest=build_intake_manifest(source)
    assert manifest['files'][0]['platform']=='tm1'
    assert manifest['files'][0]['format']==format
    assert 'UNSUPPORTED_PLATFORM' in {d['code'] for d in manifest['diagnostics']}


def test_mdx_with_ti_looking_member_is_language_hint_without_platform_confirmation(tmp_path):
    source=tmp_path/'query.mdx'; source.write_text('SELECT {[Measures].[CellPutN(1)]} ON 0 FROM [C]')
    manifest=build_intake_manifest(source)
    assert manifest['files'][0]['format']=='mdx'
    assert manifest['files'][0]['platform']=='unknown'


def test_plain_rux_extension_is_only_a_language_hint(tmp_path):
    source=tmp_path/'Sales.rux';source.write_text("['Revenue']=N:1;")
    record=build_intake_manifest(source)['files'][0]
    assert record['format']=='tm1_rules'
    assert record['platform']=='unknown' and record['export_version'] is None


@pytest.mark.parametrize('suffix,text,format', [
    ('.ti', 'n=1;', 'tm1_ti_section_hint'),
    ('.ti', '', 'tm1_ti_section_hint'),
    ('.pro', '572,0\n573,1\n', 'tm1_pro_unverified'),
])
def test_ti_extensions_do_not_confirm_platform_or_native_layout(tmp_path, suffix, text, format, monkeypatch):
    import sqlglot
    def forbidden(*args, **kwargs):raise AssertionError('TI hints must not enter SQL analysis.')
    monkeypatch.setattr(sqlglot, 'parse', forbidden)
    monkeypatch.setattr(sqlglot, 'tokenize', forbidden)
    source=tmp_path/('unconfirmed'+suffix);source.write_text(text)
    record=build_intake_manifest(source)['files'][0]
    assert record['platform']=='unknown' and record['export_version'] is None
    assert record['format']==format
