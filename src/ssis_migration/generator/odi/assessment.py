"""Explain why ODI mappings cannot yet be generated faithfully on Databricks.

This module assesses source/contract evidence; it does not emit placeholder
notebooks or treat a direct logical connector as a complete migration strategy.
"""
from __future__ import annotations

from ssis_migration.ir.normalized import content_digest
from ssis_migration.migration.contract_bridge import validate_contract
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references

PROFILE = 'odi_databricks_capabilities_v1'


def build_assessment(graph, analysis, contracts):
    """Build a report after native source/plan/contract validation.

    Publishing callers use validate_assessment for original-source replay.
    No code generation or target execution is enabled by this report.
    """
    validate_document(analysis, 'source_behavior_analysis'); validate_evidence_references(analysis)
    if (graph['source']['platform'] != 'odi' or analysis['ir_id'] != graph['ir_id']
            or analysis['source_analysis_sha256'] != graph['provenance']['source_analysis_sha256']
            or analysis['evidence_files'] != graph['evidence_files']):
        raise SchemaValidationError('ODI assessment needs its bound mapping analysis.')
    required = []
    for contract in contracts:
        validate_contract(contract)
        scope = contract['source_behavior']['legacy_step']['metadata'].get('normalized_source', {})
        if (contract['source_platform'] != 'odi' or scope.get('ir_id') != graph['ir_id']
                or scope.get('source_analysis_sha256') != graph['provenance']['source_analysis_sha256']):
            raise SchemaValidationError('ODI assessment contract belongs to a different original source graph.')
        required.extend(o['source_value'] for o in contract['required_behavior']['obligations'] if o['kind'] == 'source_requirement')
    if required != analysis['requirements']:
        raise SchemaValidationError('ODI generation assessment needs all original mapping review requirements in its contracts.')
    mappings = []
    for requirement in analysis['requirements']:
        if requirement['kind'] != 'ODI_MAPPING': continue
        declarations = requirement['declarations']; identity = requirement['subject_node_id']
        blockers = [
            {'code': 'NATIVE_COMPONENT_EXPRESSION_SEMANTICS_UNIMPLEMENTED',
             'message': 'Component, expression, datatype and null semantics lack a verified Databricks translation rule.',
             'source_node_ids': [c['node_id'] for c in declarations['components']]},
            {'code': 'PHYSICAL_KM_WRITE_SEMANTICS_UNIMPLEMENTED',
             'message': 'Physical design selection, Knowledge Module commands/options and target write/error/transaction behavior are not implemented.',
             'source_node_ids': [d['node_id'] for d in declarations['physical_designs']]},
            {'code': 'DATABRICKS_BINDINGS_NOT_ESTABLISHED',
             'message': 'Source-to-Databricks physical/table bindings and runtime parameters have not been established.',
             'source_node_ids': [identity]},
        ]
        missing = [r for r in declarations['external_references'] if r['resolution'] != 'RESOLVED']
        if missing:
            blockers.append({'code': 'EXPORTED_DEFINITIONS_UNRESOLVED',
                'message': 'Referenced object definitions are missing or ambiguous under class/key/GUID binding.',
                'source_node_ids': [r['node_id'] for r in missing]})
        destructive = [p['node_id'] for p in declarations['properties'] if p['name'] == 'TRUNCATE' and p['value'] == 'true']
        if destructive:
            blockers.append({'code': 'DESTRUCTIVE_OPTION_REVIEW_REQUIRED',
                'message': 'TRUNCATE=true is declared. Its selected scope and faithful target write strategy require review; append is not established as equivalent.',
                'source_node_ids': destructive})
        mappings.append({'mapping_node_id': identity, 'requirement_id': requirement['requirement_id'],
            'name': declarations['name'], 'strategy': 'UNSUPPORTED', 'requires_ai': False,
            'component_types': list(dict.fromkeys(c['component_type'] for c in declarations['components'])),
            'expression_count': len(declarations['expressions']), 'physical_design_count': len(declarations['physical_designs']),
            'selected_physical_design': None, 'unresolved_external_references': missing, 'blockers': blockers,
            'semantic_status': 'NOT_VERIFIED'})
    report = {'assessment_version': '1.0', 'profile': PROFILE, 'target_platform': 'databricks',
        'scope': 'CAPABILITY_ASSESSMENT_ONLY', 'ir_id': graph['ir_id'],
        'behavior_analysis_sha256': content_digest(analysis), 'contracts_sha256': content_digest(contracts),
        'source_contract_ids': [c['contract_id'] for c in contracts], 'mappings': mappings,
        'status': 'BLOCKED', 'generation_languages': [], 'generated_notebooks': [],
        'execution_status': 'NOT_RUN', 'semantic_status': 'NOT_VERIFIED', 'migration_accepted': False}
    validate_document(report, 'odi_databricks_assessment')
    return report


def validate_assessment(report, envelope, graph, plan, contracts, analysis):
    from ssis_migration.adapters.export_pipeline import validate_export_review
    validate_document(report, 'odi_databricks_assessment')
    validate_export_review(envelope, graph, plan, contracts, behavior_analysis=analysis)
    if report != build_assessment(graph, analysis, contracts):
        raise SchemaValidationError('ODI Databricks assessment differs from original source/contracts and capability rules.')
