"""TM1 file bundle → native IR → review plan/contracts and a blocked report."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ssis_migration.adapters import get_adapter
from ssis_migration.migration.native_review import build_native_review_plan, validate_native_review_plan
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ssis_migration.validator.readiness import summarize_readiness
from .normalizer import validate_tm1_normalized_ir


def validate_tm1_review_result(envelope, graph, plan, contracts):
    validate_document(envelope, 'source_envelope_v2')
    validate_evidence_references(envelope)
    validate_tm1_normalized_ir(graph)
    if (envelope['native_analysis'] != graph['extensions']['source_analysis'] or envelope['source'] != graph['source']
        or envelope['evidence_files'] != graph['evidence_files'] or envelope['diagnostics'] != graph['diagnostics']
        or envelope['status'] != graph['status']):
        raise SchemaValidationError('TM1 envelope, source analyses and neutral graph disagree.')
    if plan['package_name'] != envelope['native_analysis']['manifest']['document']['name']:
        raise SchemaValidationError('TM1 review plan does not identify its supplied source bundle.')
    validate_native_review_plan(graph, plan, contracts)


def run_tm1_review(source, output):
    result = get_adapter('tm1').analyze(source)
    graph = result.normalized_ir; envelope = result.source_envelope
    plan, contracts = build_native_review_plan(graph, result.parsed['name'])
    validate_tm1_review_result(envelope, graph, plan, contracts)
    # Verify freshness after all source/plan/contract processing, before creating output.
    for record in envelope['evidence_files']:
        path = Path(record['path'])
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('TM1 source evidence changed before review output: '+str(path))
    validations = [{'validator':'native_ir_plan_contract_integrity', 'status':'PASS', 'scope':'SOURCE_STRUCTURE'},
                   {'validator':'target_translation', 'status':'NOT_IMPLEMENTED', 'scope':'SEMANTIC_IMPLEMENTATION',
                    'issues':['TM1 target translation is not implemented; no code, notebooks, jobs or DAB was generated.']}]
    report = {'report_version':'2.0', 'source_platform':'tm1', 'source_format':'tm1_source_bundle_v1',
              'name':result.parsed['name'], 'status':'BLOCKED', 'source_analysis_status':graph['status'], 'scope':'REVIEW_ONLY',
              'analyzed_package_count':1, 'migrated_package_count':0, 'generated_artifact_count':0,
              'execution_status':'NOT_RUN', 'migration_accepted':False,
              'parser_status':{u['unit_id']:u['analysis']['status'] for u in envelope['native_analysis']['units']},
              'metadata_binding_status':{u['unit_id']:u['metadata_binding']['status'] for u in envelope['native_analysis']['units']},
              'source_files':envelope['evidence_files'], 'source_diagnostics':graph['diagnostics'],
              'strategies':{s['step_id']:s['metadata']['migration_strategy'] for s in plan['steps']},
              'preservation':plan['metadata']['normalized_ir_binding'],
              'stages':{'source_analysis':'COMPLETED', 'native_ir':'COMPLETED', 'plan_contract_binding':'COMPLETED',
                        'target_generation':'NOT_IMPLEMENTED', 'execution':'NOT_RUN'},
              'required_human_actions':list(dict.fromkeys(issue for c in contracts for issue in c['unresolved_items']))}
    report.update(summarize_readiness(validations, generated_artifact_count=0,
                                      unresolved_items=['Source semantics, criticality and behavior weights remain unverified.']))
    directory = Path(output).expanduser().absolute() / ('tm1-review-'+uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    artifacts = {'source_envelope':'source_envelope.json','normalized_ir':'normalized_ir.json',
                 'plan':'migration_plan.json','contracts':'migration_contracts.json',
                 'plan_ir_binding':'plan_ir_binding.json','validation':'validation_results.json','report':'migration_report.json'}
    report['artifacts'] = {key:str(directory/name) for key,name in artifacts.items()}
    documents = {'source_envelope':envelope, 'normalized_ir':graph, 'plan':plan,
                 'contracts':{'contract_bundle_version':'1.0','contracts':contracts},
                 'plan_ir_binding':plan['metadata']['normalized_ir_binding'], 'validation':validations,'report':report}
    for key, document in documents.items():
        with (directory/artifacts[key]).open('x',encoding='utf-8') as stream:
            json.dump(document,stream,indent=2,ensure_ascii=False,allow_nan=False)
            stream.write('\n')
    return report
