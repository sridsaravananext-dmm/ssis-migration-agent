"""Window SQL review fragments that preserve every omitted source clause."""
from __future__ import annotations

from collections import Counter
import copy

from sqlglot import exp

from ssis_migration.ir.normalized import content_digest
from ssis_migration.languages.sql.analysis import parse_sql
from ssis_migration.migration.contract_bridge import validate_contract
from ssis_migration.schemas import SchemaValidationError, validate_document
from .expressions import _render, _syntax_entries

PROFILE = 'odi_databricks_expression_candidates_v2'


def _reference_sql(node, bindings):
    return _render(node, bindings).sql(dialect='databricks')


def _window_sql(ast, bindings):
    # Do not render WindowSpec through a formatter: some versions expand a
    # single PRECEDING bound with an explicit CURRENT ROW end not in the source.
    argument = _reference_sql(ast['argument'], bindings)
    parts = ['PARTITION BY ' + ', '.join(_reference_sql(n, bindings) for n in ast['partition_by'])]
    if ast['order_by']:
        parts.append('ORDER BY ' + ', '.join(_reference_sql(n['expression'], bindings) for n in ast['order_by']))
    if ast['frame'] is not None:
        parts.append('ROWS ' + ast['frame']['start']['token'] + ' PRECEDING')
    return ast['function'] + '(' + argument + ') OVER (' + ' '.join(parts) + ')'


def _pair(node, bindings):
    column = _render(node, bindings)
    return column.table, column.name


def _check_window(parsed, ast, bindings):
    functions = {'LAG': exp.Lag, 'MAX': exp.Max, 'SUM': exp.Sum}
    if not isinstance(parsed, exp.Window) or not isinstance(parsed.this, functions[ast['function']]):
        raise SchemaValidationError('Generated window changed its native function.')
    argument = parsed.this.this
    if not isinstance(argument, exp.Column) or (argument.table, argument.name) != _pair(ast['argument'], bindings):
        raise SchemaValidationError('Generated window changed its argument binding.')
    if ast['function'] == 'LAG' and (parsed.this.args.get('offset') is not None or parsed.this.args.get('default') is not None):
        raise SchemaValidationError('Generated LAG invented an undeclared offset/default.')
    partitions = parsed.args.get('partition_by') or []
    if not all(isinstance(c, exp.Column) for c in partitions) or [(c.table, c.name) for c in partitions] != [_pair(n, bindings) for n in ast['partition_by']]:
        raise SchemaValidationError('Generated window changed partition bindings/order.')
    order = parsed.args.get('order'); terms = order.expressions if order else []
    if (not all(isinstance(term.this, exp.Column) for term in terms)
            or [(term.this.table, term.this.name) for term in terms] != [_pair(n['expression'], bindings) for n in ast['order_by']]):
        raise SchemaValidationError('Generated window changed its declared ordering columns.')
    # Parser-internal default sort/null flags are not source declarations.
    spec = parsed.args.get('spec'); frame = ast['frame']
    if frame is None:
        if spec is not None: raise SchemaValidationError('Generated window invented an undeclared frame.')
    elif (spec is None or spec.args.get('kind') != 'ROWS' or not isinstance(spec.args.get('start'), exp.Literal)
          or spec.args['start'].this != frame['start']['token'] or spec.args.get('start_side') != 'PRECEDING'
          or spec.args.get('end') is not None or spec.args.get('end_side') is not None):
        raise SchemaValidationError('Generated window changed its exact single-bound frame or invented an endpoint.')


def build_candidates(graph, analysis, contracts):
    from ssis_migration.adapters.odi.behavior_v3 import PROFILE as SOURCE_PROFILE
    validate_document(analysis, 'source_behavior_analysis')
    if (analysis['profile'] != SOURCE_PROFILE or graph['source']['platform'] != 'odi'
            or analysis['ir_id'] != graph['ir_id'] or analysis['source_analysis_sha256'] != graph['provenance']['source_analysis_sha256']
            or analysis['evidence_files'] != graph['evidence_files']):
        raise SchemaValidationError('Window candidates require their original bound version-3 source analysis.')
    requirements = []
    for contract in contracts:
        validate_contract(contract)
        scope = contract['source_behavior']['legacy_step']['metadata'].get('normalized_source', {})
        if scope.get('ir_id') != graph['ir_id'] or scope.get('source_analysis_sha256') != graph['provenance']['source_analysis_sha256']:
            raise SchemaValidationError('Window candidate contract belongs to another source graph.')
        requirements.extend(o['source_value'] for o in contract['required_behavior']['obligations'] if o['kind'] == 'source_requirement')
    if requirements != analysis['requirements']:
        raise SchemaValidationError('Window candidates require every original source requirement.')
    entries = []
    for requirement, syntax in _syntax_entries(analysis):
        validate_document(syntax, 'odi_expression_analysis_v2')
        code = None; issues = list(syntax['unresolved_items']); ast = syntax['ast']
        if syntax['status'] == 'PARSED':
            if ast['kind'] == 'DATE_CONVERSION':
                issues.append('Date SQL is not generated: source dialect/calendar, invalid-input policy, substring indexing and target format behavior are not established.')
            else:
                code = _window_sql(ast, syntax['bindings']) if ast['kind'] == 'WINDOW' else _render(ast, syntax['bindings']).sql(dialect='databricks')
                parsed = parse_sql('SELECT ' + code, dialect='databricks')
                if (not parsed.supported or len(parsed.expressions) != 1 or not isinstance(parsed.expressions[0], exp.Select)
                        or len(parsed.expressions[0].expressions) != 1 or parsed.expressions[0].args.get('from_') or parsed.expressions[0].args.get('joins')):
                    raise SchemaValidationError('Generated target fragment is not an independently valid single expression.')
                target = parsed.expressions[0].expressions[0]
                if ast['kind'] == 'WINDOW':
                    _check_window(target, ast, syntax['bindings'])
                elif Counter((c.table, c.name) for c in target.find_all(exp.Column)) != Counter((c.table, c.name) for c in _render(ast, syntax['bindings']).find_all(exp.Column)):
                    raise SchemaValidationError('Target fragment changed its declared column bindings.')
        entries.append({'expression_node_id': syntax['expression_node_id'], 'requirement_id': requirement['requirement_id'],
            'syntax_sha256': content_digest(syntax), 'status': 'CANDIDATE' if code is not None else 'NO_CANDIDATE',
            'databricks_sql_expression': code, 'validation_status': 'PASS_SYNTAX_ONLY' if code is not None else 'NOT_GENERATED',
            'logical_bindings': copy.deepcopy(syntax['bindings']), 'evidence': copy.deepcopy(syntax['evidence']),
            'unresolved_items': [*issues, 'Logical aliases, runtime defaults, ordering uniqueness and source/target semantics require validation before complete migration generation.'],
            'semantic_status': 'NOT_VERIFIED'})
    known = {n['node_id'] for n in graph['nodes'] if n['attributes'].get('object_class') == 'SnpMapExpr'}
    if len(entries) != len(known) or {e['expression_node_id'] for e in entries} != known:
        raise SchemaValidationError('Extended candidate review omitted or duplicated source expressions.')
    report = {'candidate_version': '2.0', 'profile': PROFILE, 'scope': 'EXPRESSION_SYNTAX_REVIEW_ONLY', 'ir_id': graph['ir_id'],
        'behavior_analysis_sha256': content_digest(analysis), 'contracts_sha256': content_digest(contracts), 'entries': entries,
        'candidate_count': sum(e['status'] == 'CANDIDATE' for e in entries), 'status': 'REVIEW_REQUIRED', 'execution_status': 'NOT_RUN',
        'generated_notebooks': [], 'semantic_status': 'NOT_VERIFIED', 'migration_accepted': False}
    validate_document(report, 'odi_expression_candidates_v2')
    return report


def validate_candidates(report, envelope, graph, plan, contracts, analysis):
    from ssis_migration.adapters.export_pipeline import validate_export_review
    validate_document(report, 'odi_expression_candidates_v2')
    validate_export_review(envelope, graph, plan, contracts, behavior_analysis=analysis)
    if report != build_candidates(graph, analysis, contracts):
        raise SchemaValidationError('Date/window review differs from original source/contracts or omits undeclared-default limitations.')
