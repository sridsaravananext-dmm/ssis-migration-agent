"""Version 3 extends native date/window declarations while preserving v1/v2."""
from __future__ import annotations

from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import validate_document, validate_evidence_references
from .behavior import MappingIndex
from .behavior_v2 import analyze as analyze_v2
from .expressions_v2 import analyze_expression

PROFILE = 'odi_12c_mapping_requirements_v3'


def analyze(graph):
    document = analyze_v2(graph); document['profile'] = PROFILE; index = MappingIndex(graph)
    for requirement in document['requirements']:
        if requirement['kind'] == 'ODI_MAPPING':
            for expression in requirement['declarations']['expressions']:
                expression['native_syntax'] = analyze_expression(index, index.nodes[expression['node_id']])
            requirement['preservation_requirements'].append('Preserve date format/index tokens, window partitions/order/frame and omitted defaults without inventing a dialect, runtime policy or unique ordering.')
        elif requirement['kind'] == 'ODI_EXPORT_REVIEW':
            requirement['declarations']['unscoped_expressions'] = [
                analyze_expression(index, index.nodes[e['expression_node_id']])
                for e in requirement['declarations']['unscoped_expressions']]
        requirement['requirement_id'] = 'requirement-' + content_digest([PROFILE, {k: v for k, v in requirement.items() if k != 'requirement_id'}])[:24]
    validate_document(document, 'source_behavior_analysis'); validate_evidence_references(document)
    return document
