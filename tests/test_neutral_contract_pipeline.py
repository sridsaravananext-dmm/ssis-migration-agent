from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from ssis_migration.migrate import run_migration
from ssis_migration.migration import validate_plan_ir_binding
from ssis_migration.validator.acceptance import load_package
from ssis_migration.schemas import SchemaValidationError

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'examples/wwi/DailyETLMain.dtsx'


def test_package_preserves_all_neutral_records_before_any_target_generation(tmp_path,monkeypatch):
    from ssis_migration.generator.dispatcher import GeneratorDispatcher
    original=GeneratorDispatcher.generate
    seen=[]
    def generate(self,step,*args,**kwargs):
        folder=tmp_path/'DailyETLMain'
        graph=json.loads((folder/'normalized_ir.json').read_text())
        plan=json.loads((folder/'migration_plan.json').read_text())
        validate_plan_ir_binding(plan,graph)
        bundle=json.loads((folder/'migration_contracts.json').read_text())
        actual={'normalized_node':[],'normalized_edge':[]}
        for contract in bundle['contracts']:
            for item in contract['required_behavior']['obligations']:
                if item['kind'] in actual:
                    record=item['source_value']['record']
                    actual[item['kind']].append(record.get('node_id') or record['edge_id'])
        assert Counter(actual['normalized_node'])==Counter(n['node_id'] for n in graph['nodes'])
        assert Counter(actual['normalized_edge'])==Counter(e['edge_id'] for e in graph['edges'])
        assert (folder/'plan_ir_binding.json').is_file()
        seen.append(step['step_id'])
        return original(self,step,*args,**kwargs)
    monkeypatch.setattr(GeneratorDispatcher,'generate',generate)
    report=run_migration(str(SOURCE),str(tmp_path),base_path=str(ROOT))
    assert report['migrated_package_count']==1, report['packages'][0].get('error')
    package=report['packages'][0]
    assert len(seen)==13
    assert package['stages']['normalized_plan_binding']=='SUCCEEDED'
    summary=json.loads(Path(package['artifacts']['plan_ir_binding']).read_text())
    assert summary['node_count']==summary['assigned_node_count']==192
    assert summary['edge_count']==summary['assigned_edge_count']==487
    assert summary['unplanned_node_ids']  # Previously omitted package-level tasks.
    assert not package['acceptance']['accepted']
    current=load_package(Path(package['artifacts']['plan']).parent)
    assert 'plan_ir_binding.json' in current['artifact_hashes']
    assert current['static']['blocking_issues']
    # Even if a caller edits the scope before rehashing contracts, package
    # binding validation still compares it against the complete source graph.
    plan_path=Path(package['artifacts']['plan'])
    plan=json.loads(plan_path.read_text())
    plan['metadata']['normalized_ir_binding']['assigned_node_count']=0
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(SchemaValidationError,match='binding report'):
        validate_plan_ir_binding(plan,json.loads(Path(package['artifacts']['normalized_ir']).read_text()))
