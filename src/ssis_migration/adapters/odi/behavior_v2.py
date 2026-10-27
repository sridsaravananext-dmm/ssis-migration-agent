"""Version 2 adds native syntax and procedure/KM declarations to frozen v1."""
from __future__ import annotations

from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import validate_document, validate_evidence_references
from ..base import UnsupportedSourceError
from .behavior import MappingIndex, _analyze_v1
from .commands import command_requirements
from .expressions import analyze_expression, _evidence, MAX_EXPRESSIONS

PROFILE = 'odi_12c_mapping_requirements_v2'


def analyze(graph):
    document = _analyze_v1(graph); document['profile'] = PROFILE
    index = MappingIndex(graph)
    if len(index.classes['SnpMapExpr']) > MAX_EXPRESSIONS:
        raise UnsupportedSourceError('ODI expression analysis count limit exceeded.')
    processed = set()
    for requirement in document['requirements']:
        if requirement['kind'] != 'ODI_MAPPING': continue
        for expression in requirement['declarations']['expressions']:
            node = index.nodes[expression['node_id']]
            expression['native_syntax'] = analyze_expression(index, node)
            processed.add(node['node_id'])
        requirement['preservation_requirements'].append('Preserve native reference bindings, both text representations and unsupported syntax; SQL syntax candidates provide no semantic credit.')
    root_requirement = next(r for r in document['requirements'] if r['kind'] == 'ODI_EXPORT_REVIEW')
    root_requirement['declarations']['unscoped_expressions'] = [
        analyze_expression(index, node) for node in index.classes['SnpMapExpr'] if node['node_id'] not in processed]
    root_requirement['declarations']['unassociated_expression_reference_node_ids'] = [
        node['node_id'] for node in index.classes['SnpMapExprRef'] if index.target(node, 'IOwnerMapExpr') is None]
    associated = set()
    for node, kind, declarations, unresolved, related in command_requirements(index):
        associated.update(b['node_id'] for b in declarations.get('option_associations', []))
        document['requirements'].append({'subject_node_id': node['node_id'], 'kind': kind, 'declarations': declarations,
            'preservation_requirements': ['Preserve original procedure/KM commands, text, options, condition/rank/error/commit/encryption declarations and every unresolved dependency without executing or expanding source code.'],
            'unresolved_items': unresolved, 'evidence': _evidence([node, *related]), 'semantic_status': 'NOT_VERIFIED'})
    root_requirement['declarations']['unassociated_command_option_node_ids'] = [
        n['node_id'] for n in index.classes['SnpLineTrtUe'] if n['node_id'] not in associated]
    for requirement in document['requirements']:
        requirement['requirement_id'] = 'requirement-' + content_digest([PROFILE, {k: v for k, v in requirement.items() if k != 'requirement_id'}])[:24]
    validate_document(document, 'source_behavior_analysis'); validate_evidence_references(document)
    return document
