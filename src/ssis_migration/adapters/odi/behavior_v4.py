"""Physical declarations added to frozen source behavior profiles 1 through 3."""
from __future__ import annotations

import copy

from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import validate_document, validate_evidence_references
from .behavior import MappingIndex
from .behavior_v3 import analyze as analyze_v3
from .physical import analyze_physical

PROFILE = 'odi_12c_mapping_requirements_v4'


def analyze(graph):
    document = analyze_v3(graph); document['profile'] = PROFILE
    physical = analyze_physical(MappingIndex(graph))
    root = next(r for r in document['requirements'] if r['kind'] == 'ODI_EXPORT_REVIEW')
    root['declarations']['physical_analysis_manifest'] = {k: copy.deepcopy(v) for k, v in physical.items() if k != 'designs'}
    for record in physical['unscoped_records']:
        for evidence in record['evidence']:
            if evidence not in root['evidence']: root['evidence'].append(copy.deepcopy(evidence))
    for design in physical['designs']:
        document['requirements'].append({'subject_node_id': design['design_node_id'], 'kind': 'ODI_PHYSICAL_DESIGN',
            'declarations': copy.deepcopy(design),
            'preservation_requirements': [
                'Preserve every alternative physical design, original typed field, exact scoped dependency and property-parent chain without choosing execution order, effective options or a target binding.'],
            'unresolved_items': list(design['unresolved_items']), 'evidence': copy.deepcopy(design['evidence']),
            'semantic_status': 'NOT_VERIFIED'})
    for requirement in document['requirements']:
        requirement['requirement_id'] = 'requirement-' + content_digest([PROFILE, {k: v for k, v in requirement.items() if k != 'requirement_id'}])[:24]
    validate_document(document, 'source_behavior_analysis'); validate_evidence_references(document)
    return document
