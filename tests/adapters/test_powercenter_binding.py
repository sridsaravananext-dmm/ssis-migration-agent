"""Dependency binding on real exports and hostile review artifacts, not invented jobs."""
import copy
import json
from pathlib import Path

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.base import SourceEvidenceError, UnsupportedSourceError
from ssis_migration.adapters.export_pipeline import validate_export_review
from ssis_migration.adapters.informatica.bundle import _combine
from ssis_migration.adapters.xml_export import snapshot_bytes, validate_export_graph
from ssis_migration.ir.normalized import content_digest, normalized_ir_id
from ssis_migration.migrate import run_migration
from ssis_migration.schemas import SchemaValidationError
from ssis_migration.ui import run_frontend_migration

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / 'examples/public/powercenter_scd1/upstream/SCD1'
WORKFLOW = SOURCES / 'w_scd1.XML'
MAPPING = SOURCES / 'm_scd1.XML'


def binding(graph):
    return next(e for e in graph['edges'] if e['attributes'].get('role') == 'SESSION_MAPPING')


def bundle():
    return get_adapter('powercenter').analyze_bundle(WORKFLOW, dependency_files=[MAPPING]).normalized_ir


def rehash(graph):
    graph['provenance']['source_analysis_sha256'] = content_digest(graph['extensions']['source_analysis'])
    graph['ir_id'] = normalized_ir_id(graph)


def test_real_workflow_binds_unique_mapping_with_both_files_scope_evidence():
    graph = bundle()
    validate_export_graph(graph)
    edge = binding(graph)
    nodes = {n['node_id']: n for n in graph['nodes']}
    assert len(graph['nodes']) == 320 and len(graph['edges']) == 382
    assert len(graph['evidence_files']) == 2
    assert edge['resolution'] == 'RESOLVED'
    assert edge['attributes']['resolution_reason'] == 'UNIQUE_DECLARED_SCOPE'
    assert nodes[edge['source_id']]['operation'] == 'SESSION'
    assert nodes[edge['target_id']]['operation'] == 'MAPPING'
    assert {r['file_id'] for r in edge['evidence']} == {f['file_id'] for f in graph['evidence_files']}
    assert edge['attributes']['scope_identity'] == {
        'export_version': '189.98', 'repository_name': 'REP', 'repository_version': '189',
        'repository_codepage': 'MS1252', 'repository_databasetype': 'Oracle',
        'folder_name': 'scd', 'folder_uuid': 'e1e81ffc-cbf0-4742-bae8-f28639a6849a'}
    assert not [e for e in graph['edges'] if e['resolution'] == 'UNRESOLVED']
    assert len([d for d in graph['diagnostics'] if d['code'] == 'UNSUPPORTED_CONSTRUCT']) == 21
    assert graph['status'] == 'BLOCKED' and graph['semantic_status'] == 'NOT_VERIFIED'


def test_duplicate_original_exports_are_ambiguous_and_order_cannot_pick_a_winner(tmp_path):
    duplicate = tmp_path / MAPPING.name
    duplicate.write_bytes(MAPPING.read_bytes())
    adapter = get_adapter('powercenter')
    graph = adapter.analyze_bundle(WORKFLOW, dependency_files=[MAPPING, duplicate]).normalized_ir
    assert graph == adapter.analyze_bundle(WORKFLOW, dependency_files=[duplicate, MAPPING]).normalized_ir
    assert len(graph['nodes']) == 477
    assert binding(graph)['resolution'] == 'UNRESOLVED'
    assert binding(graph)['attributes']['resolution_reason'] == 'AMBIGUOUS_MAPPING_DEFINITION'
    assert binding(graph)['target_id'] is None
    validate_export_graph(graph)


@pytest.mark.parametrize('operation,attribute', [
    ('FOLDER', 'UUID'), ('FOLDER', 'NAME'), ('REPOSITORY', 'NAME'),
    ('REPOSITORY', 'VERSION'), ('REPOSITORY', 'CODEPAGE'), ('REPOSITORY', 'DATABASETYPE'),
    ('POWERMART', 'REPOSITORY_VERSION'),
])
def test_missing_scope_in_tampered_artifacts_cannot_authorize_cross_file_binding(operation, attribute):
    adapter = get_adapter('powercenter')
    originals = [adapter.analyze(path).normalized_ir for path in (WORKFLOW, MAPPING)]
    altered = copy.deepcopy(originals)
    node = next(n for n in altered[1]['nodes'] if n['operation'] == operation)
    del node['attributes']['export_attributes'][attribute]
    graph = _combine(altered)
    assert binding(graph)['resolution'] == 'UNRESOLVED'
    assert binding(graph)['attributes']['resolution_reason'] == 'MISSING_SCOPE_EVIDENCE'
    with pytest.raises(SchemaValidationError):
        validate_export_graph(graph)


def test_conflicting_export_version_in_tampered_artifact_cannot_bind():
    adapter = get_adapter('powercenter')
    originals = [adapter.analyze(path).normalized_ir for path in (WORKFLOW, MAPPING)]
    altered = copy.deepcopy(originals)
    root = next(n for n in altered[1]['nodes'] if n['operation'] == 'POWERMART')
    root['attributes']['export_attributes']['REPOSITORY_VERSION'] = '189'
    graph = _combine(altered)
    assert binding(graph)['resolution'] == 'UNRESOLVED'
    assert binding(graph)['attributes']['resolution_reason'] == 'CONFLICTING_EXPORT_SCOPE'
    with pytest.raises(SchemaValidationError):
        validate_export_graph(graph)


@pytest.mark.parametrize('change', ['drop_snapshot', 'change_encoding', 'wrong_mapping', 'drop_unsupported', 'change_scope', 'change_record_version'])
def test_rehashed_bundle_tampering_fails_original_evidence_replay(change):
    graph = bundle()
    if change == 'drop_snapshot':
        graph['extensions']['source_analysis']['source_snapshots'].pop()
    elif change == 'change_encoding':
        graph['extensions']['source_analysis']['source_snapshots'][1]['snapshot'].pop('encoding')
    elif change == 'wrong_mapping':
        binding(graph)['target_id'] = next(n['node_id'] for n in graph['nodes'] if n['operation'] == 'TARGET_DEFINITION')
    elif change == 'change_scope':
        binding(graph)['attributes']['scope_identity']['folder_uuid'] = None
    elif change == 'change_record_version':
        graph['evidence_files'][1]['export_version'] = '189'
    else:
        node = next(n for n in graph['nodes'] if not n['attributes']['structure_supported'])
        graph['nodes'].remove(node)
        graph['edges'] = [e for e in graph['edges'] if node['node_id'] not in (e['source_id'], e['target_id'])]
    rehash(graph)
    with pytest.raises(SchemaValidationError):
        validate_export_graph(graph)


def test_workbench_bundle_artifacts_replay_without_original_paths(tmp_path):
    workflow, mapping = [tmp_path / path.name for path in (WORKFLOW, MAPPING)]
    workflow.write_bytes(WORKFLOW.read_bytes()); mapping.write_bytes(MAPPING.read_bytes())
    report = run_frontend_migration(workflow, tmp_path / 'output', dependencies=[mapping], base_path=ROOT)
    artifacts = {key: json.loads(Path(path).read_text()) for key, path in report['artifacts'].items()}
    workflow.unlink(); mapping.unlink()
    validate_export_review(artifacts['source_envelope'], artifacts['normalized_ir'],
                           artifacts['migration_plan'], artifacts['migration_contracts']['contracts'])
    graph = artifacts['normalized_ir']
    for item, record in zip(graph['extensions']['source_analysis']['source_snapshots'], graph['evidence_files']):
        original = WORKFLOW if Path(record['path']).name == WORKFLOW.name else MAPPING
        assert snapshot_bytes(item['snapshot'], record) == original.read_bytes()
    assert not report['missing_dependencies']
    assert report['preservation']['node_count'] == report['preservation']['assigned_node_count'] == 320
    assert report['preservation']['edge_count'] == report['preservation']['assigned_edge_count'] == 382
    assert artifacts['migration_plan']['metadata']['execution_order'] == 'NOT_ESTABLISHED'
    assert report['status'] == 'BLOCKED' and report['migration_accepted'] is False
    assert report['execution_status'] == 'NOT_RUN'
    assert report['coverage']['semantic']['verified_percent'] is None
    assert not list(tmp_path.rglob('*.ipynb'))


@pytest.mark.parametrize('bad', ['missing', 'directory', 'primary', 'repeat', 'non_xml', 'wrong_platform'])
def test_invalid_dependency_selection_is_rejected_before_output(tmp_path, bad):
    options = {'missing': [tmp_path / 'absent.xml'], 'directory': [SOURCES],
               'primary': [WORKFLOW], 'repeat': [MAPPING, MAPPING],
               'non_xml': [SOURCES / 'w_scd1_log.txt'], 'wrong_platform': [ROOT / 'examples/odi/structural_export.xml']}
    output = tmp_path / 'output'
    with pytest.raises((ValueError, OSError)):
        run_migration(str(WORKFLOW), str(output), dependency_files=options[bad])
    assert not output.exists()


def test_dependency_change_before_publication_cannot_write_review(tmp_path, monkeypatch):
    from ssis_migration.adapters import export_pipeline
    mapping = tmp_path / MAPPING.name; mapping.write_bytes(MAPPING.read_bytes())
    original_validate = export_pipeline.validate_export_review

    def validate_then_remove(*args):
        original_validate(*args)
        mapping.unlink()

    monkeypatch.setattr(export_pipeline, 'validate_export_review', validate_then_remove)
    output = tmp_path / 'output'
    with pytest.raises((SourceEvidenceError, OSError)):
        run_migration(str(WORKFLOW), str(output), dependency_files=[mapping])
    assert not output.exists()


def test_combined_element_limit_applies_to_all_original_exports(monkeypatch):
    from ssis_migration.adapters.informatica import bundle as module
    monkeypatch.setattr(module, 'MAX_ELEMENTS', 319)
    with pytest.raises(UnsupportedSourceError, match='combined element'):
        bundle()
