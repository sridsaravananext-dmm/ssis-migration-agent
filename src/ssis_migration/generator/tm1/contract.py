"""Separate, replayable target contracts. Existing native review contracts stay blocked."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from ssis_migration.adapters.tm1.metadata.catalog import read_json
from ssis_migration.adapters.tm1.normalizer import validate_tm1_normalized_ir
from ssis_migration.adapters.tm1.translation import analyze_capabilities
from ssis_migration.intake import describe_file
from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import SchemaValidationError, validate_document

REQUIREMENTS = [
    'User must verify the supplied dense-leaf semantic declarations against the source system.',
    'Run Validation.ipynb in Databricks and review fixture reconciliation before table-mode preview.',
    'Obtain independent source-engine observations for every critical rule, record lifecycle and numeric boundary.',
    'Define business criticality and weights; require at least 80% weighted verified semantic coverage and all mandatory gates.',
    'Review unresolved source diagnostics and confirm there are no hidden rules, feeders, aliases, security filters or dependencies.',
    'Generation and passing synthetic fixtures do not establish migration acceptance or source equivalence.',
]


def load_mapping(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1048576:
        raise ValueError('Supply a regular target mapping JSON file up to 8 MiB.')
    raw = path.read_bytes(); record = describe_file(path)
    if record['sha256'] != hashlib.sha256(raw).hexdigest() or path.read_bytes() != raw:
        raise ValueError('Target mapping changed during inventory.')
    document = read_json(raw)
    validate_document(document, 'tm1_databricks_mapping')
    return {'record':record,'text':raw.decode('utf-8'),'document':document}


def contract_id(contract):
    return 'tm1-target-' + content_digest({k:v for k,v in contract.items() if k != 'contract_id'})


def _build(graph, mapping_source, workspace_config=None):
    mapping = mapping_source['document']; bundle = graph['extensions']['source_analysis']
    if mapping['source_bundle_sha256'] != bundle['manifest']['sha256']:
        raise ValueError('Target mapping is bound to a different source bundle SHA-256.')
    capabilities = analyze_capabilities(bundle, mapping)
    contract = {'contract_version':'1.0','source_platform':'tm1','target_platform':'databricks',
                'source_ir':copy.deepcopy(graph),'mapping_source':copy.deepcopy(mapping_source),
                'capabilities':capabilities,
                'obligation_ids':[n['node_id'] for n in graph['nodes']] + [e['edge_id'] for e in graph['edges']],
                'status':'CANDIDATE_READY' if all(c['decision']=='DETERMINISTIC' for c in capabilities) else 'BLOCKED',
                'semantic_status':'NOT_VERIFIED','migration_accepted':False,'execution_mode':'BOUNDED_PREVIEW',
                'validation_requirements':REQUIREMENTS[:]}
    if workspace_config is not None:
        from ssis_migration.workspace import validate_workspace_config
        contract['workspace_config'] = validate_workspace_config(workspace_config)
        if set(workspace_config['output_tables']) - {c['unit_id'] for c in capabilities}:
            raise ValueError('Output table settings reference an unknown source unit.')
    contract['contract_id'] = contract_id(contract)
    return contract


def build_contract(graph, mapping_source, *, workspace_config=None):
    validate_tm1_normalized_ir(graph)
    contract = _build(graph, mapping_source, workspace_config)
    validate_contract(contract)
    return contract


def validate_contract(contract):
    validate_document(contract, 'tm1_databricks_contract')
    validate_tm1_normalized_ir(contract['source_ir'])
    source = contract['mapping_source']; raw = source['text'].encode('utf-8'); record = source['record']
    if (record['sha256'] != hashlib.sha256(raw).hexdigest() or record['size_bytes'] != len(raw)
        or read_json(raw) != source['document'] or record['status'] != 'AVAILABLE'):
        raise SchemaValidationError('Target mapping bytes, document or file inventory disagree.')
    if _build(contract['source_ir'], source, contract.get('workspace_config')) != contract:
        raise SchemaValidationError('Target contract does not reproduce complete source capability analysis; no edited/omitted code is trusted.')


def require_candidate(contract):
    validate_contract(contract)
    if contract['status'] != 'CANDIDATE_READY':
        raise ValueError('Unsupported source behavior blocks all executable candidate generation.')
