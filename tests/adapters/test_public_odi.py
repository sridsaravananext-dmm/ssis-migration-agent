"""Compatibility checks on Oracle's unchanged public export, without new data."""
import copy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.base import SourceEvidenceError, UnsupportedSourceError
from ssis_migration.adapters.odi import preflight
from ssis_migration.adapters.xml_export import _decode_xml
from ssis_migration.intake import describe_file
from ssis_migration.schemas import SchemaValidationError
from ssis_migration.ui import bundle_candidates, extract_bundle

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / 'examples/public/oracle_odi_etl_offload'
ARCHIVE = PUBLIC / 'upstream/CloudAtCustomerODIETLOffload/ODISmartExport_ETLOffload_BigDataCloud@Customer.zip'


@pytest.fixture(scope='module')
def original(tmp_path_factory):
    directory = extract_bundle(ARCHIVE.read_bytes(), tmp_path_factory.mktemp('odi') / 'source')
    source, = bundle_candidates(directory)
    return source


@pytest.fixture(scope='module')
def report(original):
    return preflight.inspect_export(original)


def test_oracle_archive_documentation_license_and_member_match_pinned_originals():
    provenance = json.loads((PUBLIC / 'provenance.json').read_text())
    assert provenance['commit'] == '4f67a1ccf84c6a4ef02720690cdee61af3027f25'
    assert provenance['license'] == 'UPL-1.0' and provenance['modifications'] == []
    assert len(provenance['files']) == 3
    for record in provenance['files']:
        raw = (PUBLIC / record['path']).read_bytes()
        assert len(raw) == record['size_bytes']
        assert hashlib.sha256(raw).hexdigest() == record['sha256']
        assert provenance['commit'] in record['url']
    license_text = (PUBLIC / provenance['license_path']).read_text()
    assert 'Copyright © 2018, Oracle' in license_text
    assert 'Universal Permissive License (UPL), Version 1.0' in license_text
    assert len(provenance['archive_members']) == 1
    member = provenance['archive_members'][0]
    with ZipFile(PUBLIC / member['archive_path']) as archive:
        assert archive.namelist() == [member['path']]
        raw = archive.read(member['path'])
    assert len(raw) == member['size_bytes'] == 3363246
    assert hashlib.sha256(raw).hexdigest() == member['sha256'] == '672b864a051b636da920565e80531e8d20b01f3b7566170bc16ac454254bea2b'


def test_real_archive_discovery_versions_counts_and_two_independent_parser_blockers(original, report):
    record = describe_file(original)
    assert record['platform'] == 'odi' and record['format'] == 'odi_xml'
    assert report['source']['sha256'] == record['sha256']
    assert report['version_declarations']['admin'] == [{
        'locator': 'xml:/SunopsisExport[1]/Admin[1]',
        'declarations': {'RepositoryVersion': '05.02.02.07', 'OdiVersion': '12.2.1', 'IsSmartExportFile': 'true'},
    }]
    inventory = report['inventory']
    assert (inventory['element_count'], inventory['object_count'], inventory['field_count'], inventory['maximum_depth']) == (41411, 2495, 38911, 3)
    assert len(inventory['object_classes']) == 45
    parser = report['structural_parser']
    assert parser['eligibility'] == 'BLOCKED'
    assert parser['blockers'] == ['ELEMENT_LIMIT_EXCEEDED', 'UNSUPPORTED_STRUCTURAL_ENCODING']
    assert parser['unsupported_object_count'] == 2405 and parser['unsupported_element_count'] == 3
    assert parser['max_elements'] == 10000 and report['encoding'] == 'iso-8859-1'
    assert parser['dependency_status'] == 'NOT_ANALYZED'
    assert report['status'] == 'BLOCKED' and report['execution_status'] == 'NOT_RUN'
    assert report['semantic_status'] == 'NOT_VERIFIED' and report['migration_accepted'] is False
    # The larger inventory budget and ISO declaration do not alter shared IR.
    with pytest.raises(UnsupportedSourceError, match='encoding'):
        _decode_xml(original.read_bytes())
    with pytest.raises(UnsupportedSourceError, match='encoding'):
        get_adapter('odi').analyze(original)


def test_every_inventory_count_and_representative_locator_agrees_with_original_xml(original, report):
    root = ET.fromstring(original.read_bytes())
    objects = list(root.iter('Object'))
    assert sum(item['count'] for item in report['inventory']['element_tags']) == sum(1 for _ in root.iter())
    assert sum(item['count'] for item in report['inventory']['field_types']) == len(list(root.iter('Field')))
    for item in report['inventory']['object_classes']:
        assert sum(obj.get('class') == item['class_name'] for obj in objects) == item['count']
        locator = item['representative_locator']
        assert locator.startswith('xml:/SunopsisExport[1]/Object[')
        found = root.find(locator.removeprefix('xml:/SunopsisExport[1]/'))
        assert found is not None and found.get('class') == item['class_name']
    classes = {item['class_name'].rsplit('.', 1)[-1]: item for item in report['inventory']['object_classes']}
    assert classes['SnpMapping']['count'] == 7 and not classes['SnpMapping']['structure_supported']
    assert classes['SnpMapComp']['count'] == 19 and not classes['SnpMapComp']['structure_supported']
    assert classes['SnpCol']['count'] == 55 and classes['SnpCol']['structure_supported']


def test_report_omits_field_values_and_encryption_key_material(original, report):
    serialized = json.dumps(report)
    for key in ('source_snapshot', 'exportKeyHash', 'keyVect', 'exportKeySalt', 'field_name'):
        assert key not in serialized
    root = ET.fromstring(original.read_bytes())
    for element in root.iter('Encryption'):
        for name in ('exportKeyHash', 'keyVect', 'exportKeySalt'):
            assert element.get(name) and element.get(name) not in serialized
    for element in root.iter('Field'):
        if element.get('name') in {'Pass', 'JndiPwd'} and (element.text or '').strip():
            assert element.text not in serialized
    assert report['encryption_declarations'] == [{
        'locator': 'xml:/SunopsisExport[1]/Encryption[1]', 'contains_cipher_text': 'false',
    }]


@pytest.mark.parametrize('change', ['class_count', 'drop_class', 'class_support', 'product_version', 'eligibility', 'field_count', 'source_hash', 'acceptance', 'remove_action'])
def test_replay_rejects_altered_counts_versions_support_eligibility_and_acceptance(original, report, change):
    document = copy.deepcopy(report)
    if change == 'class_count': document['inventory']['object_classes'][0]['count'] += 1
    elif change == 'drop_class': document['inventory']['object_classes'].pop()
    elif change == 'class_support':
        item = document['inventory']['object_classes'][0]
        item['structure_supported'] = not item['structure_supported']
    elif change == 'product_version': document['version_declarations']['admin'][0]['declarations']['OdiVersion'] = '19'
    elif change == 'eligibility': document['structural_parser'].update(eligibility='WITHIN_PREFLIGHT_LIMITS', blockers=[])
    elif change == 'field_count': document['inventory']['field_count'] -= 1
    elif change == 'source_hash': document['source']['sha256'] = '0' * 64
    elif change == 'acceptance': document['migration_accepted'] = True
    else: document['required_actions'].pop(0)
    with pytest.raises(SchemaValidationError):
        preflight.validate_preflight(document, original)


def test_report_replays_with_identical_original_bytes_at_a_different_path(original, report, tmp_path):
    relocated = tmp_path / 'original.xml'
    relocated.write_bytes(original.read_bytes())
    preflight.validate_preflight(json.loads(json.dumps(report)), relocated)


@pytest.mark.parametrize('limit,value', [('MAX_BYTES', 1000), ('MAX_PREFLIGHT_ELEMENTS', 10000), ('MAX_DEPTH', 2), ('MAX_DISTINCT_VALUES', 2)])
def test_preflight_limits_fail_before_writing_any_report(original, tmp_path, monkeypatch, limit, value):
    monkeypatch.setattr(preflight, limit, value)
    output = tmp_path / 'report.json'
    with pytest.raises(SystemExit) as exc:
        preflight.main(['--source', str(original), '--output', str(output)])
    assert exc.value.code == 2 and not output.exists()


@pytest.mark.parametrize('ancestor', [False, True])
def test_preflight_rejects_links_without_following_source(original, tmp_path, ancestor):
    link = tmp_path / 'link'
    link.symlink_to(original.parent if ancestor else original)
    with pytest.raises(SourceEvidenceError):
        preflight.inspect_export(link / original.name if ancestor else link)


def test_preflight_rejects_unsafe_declarations_before_xml_parser(original, monkeypatch):
    monkeypatch.setattr(preflight, 'has_unsafe_xml_declarations', lambda _: True)
    def forbidden(*args, **kwargs): raise AssertionError('Unsafe source reached XML parser.')
    monkeypatch.setattr(preflight.ET, 'iterparse', forbidden)
    with pytest.raises(UnsupportedSourceError, match='entities'):
        preflight.inspect_export(original)


def test_corrupted_original_is_not_a_partial_compatibility_success(original, tmp_path):
    truncated = tmp_path / original.name
    truncated.write_bytes(original.read_bytes()[:100])
    with pytest.raises(UnsupportedSourceError, match='well-formed'):
        preflight.inspect_export(truncated)


def test_source_change_during_inspection_is_rejected(original, monkeypatch):
    read = preflight._read_source
    calls = []
    def changing_source(path):
        calls.append(path)
        return read(path) if len(calls) == 1 else read(path)[:-1]
    monkeypatch.setattr(preflight, '_read_source', changing_source)
    with pytest.raises(SourceEvidenceError, match='changed'):
        preflight.inspect_export(original)


def test_existing_small_odi_export_stays_eligible_and_wrong_vendor_is_rejected():
    source = ROOT / 'examples/odi/structural_export.xml'
    report = preflight.inspect_export(source)
    assert report['structural_parser']['eligibility'] == 'WITHIN_PREFLIGHT_LIMITS'
    assert report['structural_parser']['blockers'] == []
    assert report['structural_parser']['unsupported_object_count'] == 0
    preflight.validate_preflight(report, source)
    with pytest.raises(UnsupportedSourceError, match='SunopsisExport'):
        preflight.inspect_export(ROOT / 'examples/public/powercenter_scd1/upstream/SCD1/m_scd1.XML')


def test_cli_writes_complete_report_without_overwriting_existing_output(original, tmp_path, capsys):
    output = tmp_path / 'compatibility.json'
    assert preflight.main(['--source', str(original), '--output', str(output)]) == 0
    raw = output.read_bytes()
    preflight.validate_preflight(json.loads(raw), original)
    assert 'migration BLOCKED' in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        preflight.main(['--source', str(original), '--output', str(output)])
    assert exc.value.code == 2 and output.read_bytes() == raw
