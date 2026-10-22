from __future__ import annotations

import copy
import json
from pathlib import Path

from ssis_migration.migrate import run_migration
from ssis_migration.ir.normalized import normalized_ir_id, validate_normalized_ir
from ssis_migration.validator.acceptance import load_package

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'examples/wwi/DailyETLMain.dtsx'


def test_pipeline_saves_and_validates_neutral_graph_before_generation(tmp_path, monkeypatch):
    from ssis_migration.generator.dispatcher import GeneratorDispatcher
    original = GeneratorDispatcher.generate
    seen = []
    def generate(self, step, *args, **kwargs):
        path = tmp_path / 'DailyETLMain/normalized_ir.json'
        assert path.is_file()
        doc = json.loads(path.read_text())
        legacy = json.loads((path.parent / 'package_ir.json').read_text())
        validate_normalized_ir(doc, legacy_ir=legacy)
        seen.append(doc['ir_id'])
        return original(self, step, *args, **kwargs)
    monkeypatch.setattr(GeneratorDispatcher, 'generate', generate)
    report = run_migration(str(SOURCE), str(tmp_path), base_path=str(ROOT))
    assert report['migrated_package_count'] == 1
    package = report['packages'][0]
    doc = json.loads(Path(package['artifacts']['normalized_ir']).read_text())
    assert len(set(seen)) == 1
    assert package['normalization']['node_count'] == len(doc['nodes'])
    assert package['normalization']['edge_count'] == len(doc['edges'])
    assert package['normalization']['semantic_status'] == 'NOT_VERIFIED'
    assert package['stages']['normalized_ir_validation'] == 'SUCCEEDED'
    manifest = json.loads(Path(package['artifacts']['manifest']).read_text())
    assert manifest['normalization'] == package['normalization']
    assert manifest['artifacts']['normalized_ir'] == package['artifacts']['normalized_ir']
    current = load_package(Path(package['artifacts']['normalized_ir']).parent)
    assert 'normalized_ir.json' in current['artifact_hashes']
    assert 'package_ir.json' in current['artifact_hashes']
    assert 'source_envelope.json' in current['artifact_hashes']
    assert current['static']['blocking_issues']
    assert package['acceptance']['accepted'] is False


def test_invalid_neutral_graph_blocks_before_target_generation(tmp_path, monkeypatch):
    from ssis_migration.adapters.ssis import normalizer
    from ssis_migration.generator.dispatcher import GeneratorDispatcher
    original = normalizer.normalize_ssis_ir
    def invalid(*args, **kwargs):
        doc = original(*args, **kwargs)
        doc['nodes'].append(copy.deepcopy(doc['nodes'][0]))
        doc['ir_id'] = normalized_ir_id(doc)
        return doc
    def forbidden(*args, **kwargs):
        raise AssertionError('Generation occurred before normalized IR validation')
    monkeypatch.setattr(normalizer, 'normalize_ssis_ir', invalid)
    monkeypatch.setattr(GeneratorDispatcher, 'generate', forbidden)
    report = run_migration(str(SOURCE), str(tmp_path), base_path=str(ROOT))
    assert report['failed_package_count'] == 1
    assert report['migrated_package_count'] == 0
    package = report['packages'][0]
    assert package['lifecycle_status'] == 'BLOCKED'
    assert 'Duplicate normalized graph identity' in package['error']
    assert 'notebook' not in package['artifacts']
