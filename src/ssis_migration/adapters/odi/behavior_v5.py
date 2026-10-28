"""Source topology declarations added without changing behavior versions 1–4."""
from __future__ import annotations

from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import validate_document, validate_evidence_references
from .behavior import MappingIndex
from .behavior_v4 import analyze as analyze_v4
from .topology import analyze_topology

PROFILE = 'odi_12c_mapping_requirements_v5'


def analyze(graph):
    document = analyze_v4(graph); document['profile'] = PROFILE
    topology = analyze_topology(MappingIndex(graph))
    root = next(r for r in document['requirements'] if r['kind'] == 'ODI_EXPORT_REVIEW')
    document['requirements'].append({'subject_node_id': root['subject_node_id'], 'kind': 'ODI_TOPOLOGY_REVIEW',
        'declarations': topology,
        'preservation_requirements': ['Preserve every original topology field, text/reference declaration, connection parent link and alternative context candidate without selecting a runtime context, inherited value or Databricks binding.'],
        'unresolved_items': list(topology['unresolved_items']), 'evidence': topology['evidence'], 'semantic_status': 'NOT_VERIFIED'})
    for requirement in document['requirements']:
        requirement['requirement_id'] = 'requirement-' + content_digest([PROFILE, {k: v for k, v in requirement.items() if k != 'requirement_id'}])[:24]
    validate_document(document, 'source_behavior_analysis'); validate_evidence_references(document)
    return document
