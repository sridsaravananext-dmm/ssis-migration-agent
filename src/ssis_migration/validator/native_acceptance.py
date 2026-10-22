"""Read-only acceptance inputs for replayable native Databricks candidates.

Generic review contracts are never upgraded. Every preserved graph obligation
needs its own assessed weight and evidence; local fixtures earn no source credit.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from ssis_migration.metadata.json_io import read_json
from ssis_migration.generator.tm1.contract import require_candidate
from ssis_migration.generator.tm1.emitter import render, validate_rendered
from ssis_migration.validator.readiness import summarize_readiness


def load_tm1_candidate(root):
    root = Path(root).resolve(strict=True)

    def safe(name):
        rel = PurePosixPath(name)
        path = root.joinpath(*rel.parts)
        if rel.is_absolute() or not rel.parts or any(p in {'.','..'} for p in rel.parts) or '\\' in name or ':' in name:
            raise ValueError('Candidate artifact paths must be portable and relative.')
        if not path.is_file() or not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path,*path.parents)):
            raise ValueError('Candidate artifact must be a contained regular file: '+name)
        return path

    def read(name):
        path = safe(name)
        if path.stat().st_size > 64*1048576:
            raise ValueError('Candidate JSON artifact exceeds the 64 MiB validation limit.')
        return read_json(path.read_bytes())

    contract = read('generation_contract.json')
    require_candidate(contract)
    expected, fixtures = render(contract)
    actual = {name: safe(name).read_text(encoding='utf-8') if isinstance(value,str) else read(name)
              for name,value in expected.items()}
    validate_rendered(contract, actual)
    graph = contract['source_ir']
    if read('normalized_ir.json') != graph or read('target_mapping.json') != contract['mapping_source']['document']:
        raise ValueError('Native graph or target mapping differs from its target contract.')
    from ssis_migration.adapters.tm1.pipeline import validate_tm1_review_result
    review_contracts = read('migration_contracts.json')['contracts']
    validate_tm1_review_result(read('source_envelope.json'),graph,read('migration_plan.json'),review_contracts)
    if read('plan_ir_binding.json') != read('migration_plan.json')['metadata']['normalized_ir_binding']:
        raise ValueError('Native plan binding artifact disagrees with its source graph.')
    if read('capability_report.json') != contract['capabilities'] or read('fixture_results.json') != fixtures:
        raise ValueError('Native capability or fixture artifact disagrees with contract replay.')
    index = read('source_evidence_index.json')
    records = {r['file_id']:r for r in graph['evidence_files']}
    if len(index) != len(records) or {i['file_id'] for i in index} != set(records):
        raise ValueError('Native source archive must cover each supplied source file once.')
    archive_paths = set()
    for item in index:
        record = records[item['file_id']]; path = safe(item['archived_path'])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path in archive_paths or digest != record['sha256'] or item['sha256'] != digest or path.stat().st_size != record['size_bytes']:
            raise ValueError('Native archived source evidence is missing, reused or changed.')
        archive_paths.add(path)
    inventory = read('artifact_inventory.json')
    if inventory['excludes'] != ['artifact_inventory.json']:
        raise ValueError('Native artifact inventory may exclude only itself.')
    actual_paths = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    if actual_paths != set(inventory['sha256']) | {'artifact_inventory.json'}:
        raise ValueError('Native artifact inventory omits or invents candidate files.')
    hashes = {}
    for name in sorted(actual_paths):
        digest = hashlib.sha256(safe(name).read_bytes()).hexdigest()
        if name != 'artifact_inventory.json' and inventory['sha256'][name] != digest:
            raise ValueError('Native candidate artifact hash changed: '+name)
        hashes[name] = digest
    # This is an acceptance view of the separate, validated target contract.
    # It does not mutate or unblock the original source review contracts.
    acceptance_contract = {
        'contract_id': contract['contract_id'],
        'required_behavior': {'obligations': [{'behavior_id':identity} for identity in contract['obligation_ids']]},
        'implementation_strategy': {'decision':'DETERMINISTIC'}, 'unresolved_items': [],
    }
    static = summarize_readiness([
        {'validator':'native_source_contract_artifact_replay','status':'PASS','scope':'STATIC_STRUCTURE'},
        {'validator':'local_target_fixtures','status':'PASS','scope':'TARGET_KERNEL_ONLY'},
    ],generated_artifact_count=2)
    return {'contracts':[acceptance_contract], 'artifact_hashes':hashes, 'static':static,
            'source_issues':[], 'source_platform':'tm1', 'validation_scope':'BOUNDED_PREVIEW_ONLY',
            'mandatory_gates':['tm1_semantic_profile','independent_source_observations'],
            'observation_binding': {'generation_contract_id':contract['contract_id'],
                                    'source_sha256':graph['extensions']['source_analysis']['manifest']['sha256'],
                                    'artifact_hashes':hashes},
            'unit_ids':[c['unit_id'] for c in contract['capabilities']],
            'limitations':['No Databricks or source workloads were executed by this offline package loader.',
                           'Synthetic fixture success earns no semantic, execution or data-equivalence coverage.',
                           'Acceptance evidence is operator-attested, not independently authenticated.']}
