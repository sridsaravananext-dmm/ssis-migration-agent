"""Contract-bound Databricks SQL expression review fragments, never notebooks."""
from __future__ import annotations

import copy

from sqlglot import exp

from ssis_migration.ir.normalized import content_digest
from ssis_migration.languages.sql.analysis import parse_sql
from ssis_migration.migration.contract_bridge import validate_contract
from ssis_migration.schemas import SchemaValidationError, validate_document

PROFILE = 'odi_databricks_expression_candidates_v1'


def _syntax_entries(analysis):
    entries = []
    for requirement in analysis['requirements']:
        if requirement['kind'] == 'ODI_MAPPING':
            entries.extend((requirement, item['native_syntax']) for item in requirement['declarations']['expressions'])
        elif requirement['kind'] == 'ODI_EXPORT_REVIEW':
            entries.extend((requirement, item) for item in requirement['declarations'].get('unscoped_expressions', []))
    return entries


def _render(ast, bindings):
    if ast['kind'] == 'ATTRIBUTE_REFERENCE':
        candidates = [b for b in bindings if b['reference_key'] == ast['reference_key']
                      and b['attribute_node_id'] == ast['attribute_node_id'] and b['resolution'] == 'BOUND']
        if len(candidates) != 1: raise SchemaValidationError('Target expression lacks a unique source attribute binding.')
        binding = candidates[0]
        return exp.Column(this=exp.Identifier(this=binding['column'], quoted=True),
                          table=exp.Identifier(this=binding['alias'], quoted=True))
    if ast['kind'] == 'COMPARE':
        return exp.EQ(this=_render(ast['left'], bindings), expression=_render(ast['right'], bindings))
    if ast['kind'] == 'CALL':
        return (exp.Sum if ast['name'] == 'SUM' else exp.Year)(this=_render(ast['argument'], bindings))
    raise SchemaValidationError('Target expression kind has no registered candidate rule.')


def build_candidates(graph, analysis, contracts):
    """Use previously validated source/plan/contracts; validate_candidates replays.

    Syntax fragments retain source component/attribute names as logical symbols.
    No physical table, type coercion, aggregate grouping or runtime is invented.
    """
    from ssis_migration.adapters.odi.behavior_v2 import PROFILE as SOURCE_PROFILE
    validate_document(analysis, 'source_behavior_analysis')
    if (analysis['profile'] != SOURCE_PROFILE or graph['source']['platform'] != 'odi'
            or analysis['ir_id'] != graph['ir_id']
            or analysis['source_analysis_sha256'] != graph['provenance']['source_analysis_sha256']
            or analysis['evidence_files'] != graph['evidence_files']):
        raise SchemaValidationError('ODI expression candidates require their bound version-2 source analysis.')
    requirements = []
    for contract in contracts:
        validate_contract(contract)
        scope = contract['source_behavior']['legacy_step']['metadata'].get('normalized_source', {})
        if scope.get('ir_id') != graph['ir_id'] or scope.get('source_analysis_sha256') != graph['provenance']['source_analysis_sha256']:
            raise SchemaValidationError('Expression candidate contract belongs to a different source graph.')
        requirements.extend(o['source_value'] for o in contract['required_behavior']['obligations'] if o['kind'] == 'source_requirement')
    if requirements != analysis['requirements']:
        raise SchemaValidationError('Target expression candidates must retain every bound source requirement.')
    entries = []
    for requirement, syntax in _syntax_entries(analysis):
        validate_document(syntax, 'odi_expression_analysis')
        code = None; validation_status = 'NOT_GENERATED'
        if syntax['status'] == 'PARSED':
            code = _render(syntax['ast'], syntax['bindings']).sql(dialect='databricks')
            # Parse the actual generated fragment independently. This is target
            # syntax validation only, never a source SQL dialect assumption.
            parsed = parse_sql('SELECT ' + code, dialect='databricks')
            if not parsed.supported or len(parsed.expressions) != 1 or not isinstance(parsed.expressions[0], exp.Select):
                raise SchemaValidationError('Generated Databricks expression failed independent SQL validation.')
            select = parsed.expressions[0]
            if len(select.expressions) != 1 or select.args.get('from_') or select.args.get('joins'):
                raise SchemaValidationError('Target candidate must contain exactly one expression without table access.')
            actual_columns = [(c.table, c.name) for c in select.find_all(exp.Column)]
            expected_columns = [(c.table, c.name) for c in _render(syntax['ast'], syntax['bindings']).find_all(exp.Column)]
            if actual_columns != expected_columns:
                raise SchemaValidationError('Generated expression changed its exact declared attribute bindings.')
            validation_status = 'PASS_SYNTAX_ONLY'
        entries.append({'expression_node_id': syntax['expression_node_id'], 'requirement_id': requirement['requirement_id'],
            'syntax_sha256': content_digest(syntax), 'status': 'CANDIDATE' if code is not None else 'NO_CANDIDATE',
            'databricks_sql_expression': code, 'validation_status': validation_status,
            'logical_bindings': copy.deepcopy(syntax['bindings']), 'evidence': copy.deepcopy(syntax['evidence']),
            'unresolved_items': [*syntax['unresolved_items'], 'Logical aliases need explicit target bindings; a syntax fragment does not implement a complete mapping, KM, write strategy or migration.'],
            'semantic_status': 'NOT_VERIFIED'})
    known = {n['node_id'] for n in graph['nodes'] if n['attributes'].get('object_class') == 'SnpMapExpr'}
    if len(entries) != len(known) or {e['expression_node_id'] for e in entries} != known:
        raise SchemaValidationError('Target expression review omitted or duplicated source expression records.')
    report = {'candidate_version': '1.0', 'profile': PROFILE, 'scope': 'EXPRESSION_SYNTAX_REVIEW_ONLY',
        'ir_id': graph['ir_id'], 'behavior_analysis_sha256': content_digest(analysis), 'contracts_sha256': content_digest(contracts),
        'entries': entries, 'candidate_count': sum(e['status'] == 'CANDIDATE' for e in entries),
        'status': 'REVIEW_REQUIRED', 'execution_status': 'NOT_RUN', 'generated_notebooks': [],
        'semantic_status': 'NOT_VERIFIED', 'migration_accepted': False}
    validate_document(report, 'odi_expression_candidates')
    return report


def validate_candidates(report, envelope, graph, plan, contracts, analysis):
    from ssis_migration.adapters.export_pipeline import validate_export_review
    validate_document(report, 'odi_expression_candidates')
    validate_export_review(envelope, graph, plan, contracts, behavior_analysis=analysis)
    if report != build_candidates(graph, analysis, contracts):
        raise SchemaValidationError('ODI expression review candidates do not replay from original source/contracts.')
