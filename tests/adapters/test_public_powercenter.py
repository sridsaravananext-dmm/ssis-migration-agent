"""Regressions against unchanged, licensed upstream exports; no invented workload."""
import copy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.export_pipeline import validate_export_review
from ssis_migration.adapters.xml_export import validate_export_graph
from ssis_migration.intake import describe_file
from ssis_migration.ir.normalized import content_digest, normalized_ir_id
from ssis_migration.schemas import SchemaValidationError
from ssis_migration.ui import bundle_candidates, run_frontend_migration


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / 'examples/public/powercenter_scd1'
SOURCES = PUBLIC / 'upstream/SCD1'


def test_public_exports_match_pinned_upstream_bytes_and_license():
    provenance = json.loads((PUBLIC / 'provenance.json').read_text())
    assert provenance['commit'] == '43a58b9629b767454e5622913d12f53335866ea2'
    assert provenance['modifications'] == []
    assert provenance['license'] == 'MIT'
    assert len(provenance['files']) == 4
    for record in provenance['files']:
        data = (PUBLIC / record['path']).read_bytes()
        assert len(data) == record['size_bytes']
        assert hashlib.sha256(data).hexdigest() == record['sha256']
        assert provenance['commit'] in record['url']
    assert 'Copyright (c) 2025 Praveen Annadurai' in (PUBLIC / provenance['license_path']).read_text()


@pytest.mark.parametrize('name', ['m_scd1.XML', 'w_scd1.XML'])
def test_original_declared_encoding_inventory_and_graph_replay(name):
    path = SOURCES / name
    data = path.read_bytes()
    record = describe_file(path)
    assert record['platform'] == 'informatica'
    assert record['export_version'] == '189.98'
    result = get_adapter('powercenter').analyze(path, file_record=record)
    graph = result.normalized_ir
    snapshot = graph['extensions']['source_analysis']['source_snapshot']
    assert snapshot['encoding'] == 'windows-1252'
    assert snapshot['text'].encode(snapshot['encoding']) == data
    assert snapshot['sha256'] == record['sha256'] == hashlib.sha256(data).hexdigest()
    assert len(graph['nodes']) == sum(1 for _ in ET.fromstring(data).iter())
    assert graph['semantic_status'] == 'NOT_VERIFIED'
    assert graph['status'] == 'BLOCKED'
    assert any(d['code'] == 'UNSUPPORTED_CONSTRUCT' for d in graph['diagnostics'])
    assert result.package_ir is None
    validate_export_graph(json.loads(json.dumps(graph)))


def test_real_mapping_column_links_resolve_but_workflow_dependency_stays_missing():
    mapping = get_adapter('powercenter').analyze(SOURCES / 'm_scd1.XML').normalized_ir
    assert len([e for e in mapping['edges'] if e['kind'] == 'COLUMN_LINEAGE']) == 26
    assert all(e['resolution'] == 'RESOLVED' for e in mapping['edges'])
    assert {n['operation'] for n in mapping['nodes'] if not n['attributes']['structure_supported']} == {'GROUP', 'TARGETLOADORDER'}
    workflow = get_adapter('powercenter').analyze(SOURCES / 'w_scd1.XML').normalized_ir
    missing = [e for e in workflow['edges'] if e['resolution'] == 'UNRESOLVED']
    assert len(missing) == 1
    assert missing[0]['attributes'] == {'definition': 'm_scd1', 'role': 'SESSION_MAPPING'}
    assert missing[0]['target_id'] is None


@pytest.mark.parametrize('change', ['omit_encoding', 'change_encoding', 'unsupported_encoding', 'normalize_newlines'])
def test_rehashing_cannot_authorize_changed_original_snapshot_encoding(change):
    graph = copy.deepcopy(get_adapter('powercenter').analyze(SOURCES / 'm_scd1.XML').normalized_ir)
    snapshot = graph['extensions']['source_analysis']['source_snapshot']
    if change == 'omit_encoding':
        del snapshot['encoding']
    elif change == 'change_encoding':
        snapshot['encoding'] = 'utf-8'
    elif change == 'unsupported_encoding':
        snapshot['encoding'] = 'latin-1'
    else:
        snapshot['text'] = snapshot['text'].replace('\r\n', '\n')
    graph['provenance']['source_analysis_sha256'] = content_digest(graph['extensions']['source_analysis'])
    graph['ir_id'] = normalized_ir_id(graph)
    with pytest.raises(SchemaValidationError):
        validate_export_graph(graph)


@pytest.mark.parametrize('name', ['m_scd1.XML', 'w_scd1.XML'])
def test_workbench_real_exports_produce_conserved_review_artifacts_only(tmp_path, name):
    source = SOURCES / name
    assert source in bundle_candidates(SOURCES)
    report = run_frontend_migration(source, tmp_path, base_path=ROOT)
    assert report['source_platform'] == 'informatica'
    assert report['status'] == 'BLOCKED'
    assert report['execution_status'] == 'NOT_RUN'
    assert report['migration_accepted'] is False
    assert report['generated_artifact_count'] == 0
    assert report['coverage']['semantic']['verified_percent'] is None
    binding = report['preservation']
    assert binding['node_count'] == binding['assigned_node_count']
    assert binding['edge_count'] == binding['assigned_edge_count']
    artifacts = {key: json.loads(Path(path).read_text()) for key, path in report['artifacts'].items()}
    validate_export_review(artifacts['source_envelope'], artifacts['normalized_ir'],
                           artifacts['migration_plan'], artifacts['migration_contracts']['contracts'])
    assert not list(tmp_path.rglob('*.ipynb'))
