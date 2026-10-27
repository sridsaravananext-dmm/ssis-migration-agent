"""Native XML source → neutral IR → conserved review contracts and reports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ssis_migration.migration.native_review import build_native_review_plan, validate_native_review_plan
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ssis_migration.validator.readiness import summarize_readiness
from .registry import get_adapter
from .xml_export import validate_export_graph


def validate_export_review(envelope, graph, plan, contracts, *, behavior_analysis=None):
    validate_document(envelope, 'source_envelope_v2'); validate_evidence_references(envelope)
    validate_export_graph(graph)
    for key in ('source', 'evidence_files', 'diagnostics', 'status'):
        if envelope[key] != graph[key]:
            raise SchemaValidationError('Export envelope and normalized graph disagree: '+key)
    if envelope['native_analysis'] != graph['extensions']['source_analysis']:
        raise SchemaValidationError('Export source snapshot and normalized graph disagree.')
    if plan['package_name'] != Path(graph['evidence_files'][0]['path']).stem:
        raise SchemaValidationError('Export review plan name does not identify its source file.')
    if (graph['source']['platform'] == 'odi'
            and graph['extensions']['source_analysis']['profile'] == getattr(get_adapter('odi'), 'native_profile', None)
            and 'behavior_analysis_binding' not in plan['metadata']):
        raise SchemaValidationError('Native ODI mapping reviews require their source behavior requirements in the plan/contracts.')
    if behavior_analysis is not None or 'behavior_analysis_binding' in plan['metadata']:
        if graph['source']['platform'] == 'odi':
            from .odi.behavior import analyze_behavior, validate_behavior_analysis
        else:
            from .informatica.behavior import analyze_behavior, validate_behavior_analysis
        if behavior_analysis is None:
            binding = plan['metadata']['behavior_analysis_binding']
            if not isinstance(binding, dict):
                raise SchemaValidationError('Invalid source behavior analysis binding.')
            behavior_analysis = analyze_behavior(graph, profile=binding.get('profile'))
        else:
            validate_behavior_analysis(behavior_analysis, graph)
    validate_native_review_plan(graph, plan, contracts, behavior_analysis=behavior_analysis)


def run_export_review(source, output, platform, *, workspace_config=None, dependency_files=None):
    adapter = get_adapter(platform)
    if dependency_files:
        if platform != 'informatica':
            raise ValueError('Cross-export dependencies are implemented only for PowerCenter XML.')
        result = adapter.analyze_bundle(source, dependency_files=dependency_files)
    else:
        result = adapter.analyze(source)
    graph = result.normalized_ir; envelope = result.source_envelope
    behavior_analysis = None
    if platform == 'informatica':
        from .informatica.behavior import analyze_behavior
        behavior_analysis = analyze_behavior(graph)
    elif platform == 'odi' and graph['extensions']['source_analysis']['profile'] == getattr(adapter, 'native_profile', None):
        from .odi.behavior import analyze_behavior
        behavior_analysis = analyze_behavior(graph)
    plan, contracts = build_native_review_plan(graph, result.parsed['name'], behavior_analysis=behavior_analysis)
    validate_export_review(envelope, graph, plan, contracts)
    target_assessment = None
    expression_candidates = None
    physical_analysis = None
    if platform == 'odi' and behavior_analysis is not None:
        from ssis_migration.generator.odi.assessment import build_assessment
        target_assessment = build_assessment(graph, behavior_analysis, contracts)
        from .odi.behavior import LATEST_PROFILE, SYNTAX_PROFILE, WINDOW_PROFILE
        if behavior_analysis['profile'] == SYNTAX_PROFILE:
            from ssis_migration.generator.odi.expressions import build_candidates
            expression_candidates = build_candidates(graph, behavior_analysis, contracts)
        elif behavior_analysis['profile'] == WINDOW_PROFILE:
            from ssis_migration.generator.odi.expressions_v2 import build_candidates
            expression_candidates = build_candidates(graph, behavior_analysis, contracts)
        elif behavior_analysis['profile'] == LATEST_PROFILE:
            from ssis_migration.generator.odi.expressions_v3 import build_candidates
            from .odi.physical import extract_physical_analysis
            expression_candidates = build_candidates(graph, behavior_analysis, contracts)
            physical_analysis = extract_physical_analysis(behavior_analysis)
    for record in envelope['evidence_files']:
        path = Path(record['path'])
        if any(p.is_symlink() for p in (path, *path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Export source evidence changed before publishing review artifacts.')
    validation = [{'validator': 'export_ir_contract_integrity', 'status': 'PASS', 'scope': 'SOURCE_STRUCTURE'},
                  {'validator': 'target_translation', 'status': 'NOT_IMPLEMENTED', 'scope': 'SEMANTIC_IMPLEMENTATION',
                   'issues': ['Native vendor behavior is not translated by the structural review adapter.']}]
    report = {'report_version': '2.2', 'name': result.parsed['name'], 'source_platform': platform,
              'source_format': graph['source']['format'], 'export_version': graph['source']['export_version'],
              'status': 'BLOCKED', 'scope': 'REVIEW_ONLY', 'source_analysis_status': graph['status'],
              'analyzed_package_count': 1, 'migrated_package_count': 0, 'generated_artifact_count': 0,
              'execution_status': 'NOT_RUN', 'migration_accepted': False,
              'parser_status': 'STRUCTURED_WITH_GAPS' if graph['status']=='BLOCKED' else 'STRUCTURED',
              'ir_status': graph['status'], 'export_profile': graph['extensions']['source_analysis']['profile'],
              'source_files': envelope['evidence_files'], 'source_diagnostics': graph['diagnostics'],
              'missing_dependencies': [e for e in graph['edges'] if e['resolution']=='UNRESOLVED'],
              'unsupported_constructs': [d for d in graph['diagnostics'] if d['code'].startswith('UNSUPPORTED')],
              'assumptions': [],
              'tasks': [{'node_id': n['node_id'], 'name': n['name'], 'operation': n['operation']} for n in graph['nodes'] if n['kind'] in {'TASK','DATA_FLOW','TRANSFORMATION'}],
              'dependency_graph': graph['edges'], 'lineage': [e for e in graph['edges'] if e['kind'] in {'DATA_FLOW','COLUMN_LINEAGE'}],
              'strategies': {s['step_id']: s['metadata']['migration_strategy'] for s in plan['steps']},
              'preservation': plan['metadata']['normalized_ir_binding'],
              'stages': {'source_analysis': 'COMPLETED', 'native_ir': 'COMPLETED', 'plan_contract_binding': 'COMPLETED',
                         'target_generation': 'NOT_IMPLEMENTED', 'execution': 'NOT_RUN'},
              'implementation_checkpoint': 'CONTINUE_OFFLINE_IMPLEMENTATION',
              'required_human_actions': list(dict.fromkeys(i for c in contracts for i in c['unresolved_items']))}
    report.update(summarize_readiness(validation, generated_artifact_count=0,
                                     unresolved_items=['Native target translation and source equivalence remain unverified.']))
    report['coverage']['structural']['ir_obligations'] = {
        'retained': len(graph['nodes'])+len(graph['edges']), 'total': len(graph['nodes'])+len(graph['edges']),
        'scope': 'PARSED_RECORD_PRESERVATION_ONLY'}
    directory = Path(output).expanduser().absolute() / (platform+'-review-'+uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    documents = {'source_envelope': envelope, 'normalized_ir': graph, 'migration_plan': plan,
                 'migration_contracts': {'contract_bundle_version': '1.0', 'contracts': contracts},
                 'plan_ir_binding': plan['metadata']['normalized_ir_binding'], 'validation_results': validation}
    if behavior_analysis is not None:
        documents['source_behavior_analysis'] = behavior_analysis
        report['behavior_analysis'] = {
            'profile': behavior_analysis['profile'], 'semantic_status': 'NOT_VERIFIED',
            'requirement_count': len(behavior_analysis['requirements']),
            'unresolved_items': list(dict.fromkeys(value for r in behavior_analysis['requirements'] for value in r['unresolved_items']))}
    if target_assessment is not None:
        documents['target_generation_assessment'] = target_assessment
        report['target_generation_assessment'] = {
            'profile': target_assessment['profile'], 'status': target_assessment['status'],
            'mapping_count': len(target_assessment['mappings']),
            'blocked_mapping_count': sum(m['strategy'] == 'UNSUPPORTED' for m in target_assessment['mappings'])}
        report['source_element_preservation'] = graph['extensions']['source_analysis']['metadata']
        report['stages']['target_capability_assessment'] = 'COMPLETED'
        report['required_human_actions'].extend(dict.fromkeys(
            b['message'] for m in target_assessment['mappings'] for b in m['blockers']
            if b['message'] not in report['required_human_actions']))
    if expression_candidates is not None:
        documents['target_expression_candidates'] = expression_candidates
        report['target_expression_review'] = {
            'profile': expression_candidates['profile'], 'scope': expression_candidates['scope'],
            'record_count': len(expression_candidates['entries']), 'candidate_count': expression_candidates['candidate_count'],
            'semantic_status': 'NOT_VERIFIED', 'execution_status': 'NOT_RUN'}
        report['stages']['target_expression_syntax_review'] = 'COMPLETED'
    if physical_analysis is not None:
        documents['source_physical_analysis'] = physical_analysis
        report['physical_analysis'] = {
            'profile': physical_analysis['profile'], **physical_analysis['summary'],
            'semantic_status': 'NOT_VERIFIED', 'execution_status': 'NOT_RUN'}
        report['stages']['physical_dependency_analysis'] = 'COMPLETED'
    if workspace_config is not None: documents['workspace_config'] = workspace_config
    report['artifacts'] = {key: str(directory/(key+'.json')) for key in (*documents, 'migration_report')}
    documents['migration_report'] = report
    for name, doc in documents.items():
        (directory/(name+'.json')).write_text(json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')
    return report
