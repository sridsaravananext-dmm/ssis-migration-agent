"""Registered native TM1 analysis adapter, separate from opt-in target generation."""
from __future__ import annotations

import copy

from ssis_migration.schemas import validate_document, validate_evidence_references
from ..base import SourceAdapterResult, UnsupportedSourceError
from .bundle import read_bundle
from .normalizer import normalize_tm1_bundle


class TM1Adapter:
    platform = 'tm1'
    supported_export_formats = frozenset({'tm1_source_bundle_v1'})

    def analyze(self, source_path, *, procedures_path=None, file_record=None):
        if procedures_path is not None:
            raise UnsupportedSourceError('TM1 inputs require explicit bundle roles; SSIS procedure arguments are not accepted.')
        bundle, files, reader = read_bundle(source_path, file_record)
        graph = normalize_tm1_bundle(bundle, files)
        envelope = {'envelope_version':'2.0', 'source':copy.deepcopy(graph['source']),
                    'evidence_files':files, 'diagnostics':copy.deepcopy(graph['diagnostics']),
                    'status':graph['status'], 'native_analysis':bundle}
        validate_document(envelope, 'source_envelope_v2')
        validate_evidence_references(envelope)
        reader.verify()
        return SourceAdapterResult(parsed=copy.deepcopy(bundle['manifest']['document']), package_ir=None,
                                   analysis={'platform':'tm1','status':graph['status'],'scope':'SOURCE_STRUCTURE_ONLY',
                                             'unit_count':len(bundle['units']),'semantic_status':'NOT_VERIFIED','migration_accepted':False},
                                   source_envelope=envelope, normalized_ir=graph)
