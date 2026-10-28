"""Bind expression syntax review to unselected source topology declarations."""
from __future__ import annotations

from ssis_migration.schemas import SchemaValidationError, validate_document
from .expressions_v2 import _build_candidates

PROFILE = 'odi_databricks_expression_candidates_v4'


def build_candidates(graph, analysis, contracts):
    from ssis_migration.adapters.odi.behavior_v5 import PROFILE as SOURCE_PROFILE
    return _build_candidates(graph, analysis, contracts, source_profile=SOURCE_PROFILE,
                             profile=PROFILE, candidate_version='4.0', schema='odi_expression_candidates_v4')


def validate_candidates(report, envelope, graph, plan, contracts, analysis):
    from ssis_migration.adapters.export_pipeline import validate_export_review
    validate_document(report, 'odi_expression_candidates_v4')
    validate_export_review(envelope, graph, plan, contracts, behavior_analysis=analysis)
    if report != build_candidates(graph, analysis, contracts):
        raise SchemaValidationError('Expression review differs from original topology requirements or contracts.')
