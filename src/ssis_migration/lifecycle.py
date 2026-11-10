"""Content-addressed artifact versions and reviewed-output protection."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from ssis_migration.database.connection_mapping_loader import load_connection_mapping


def file_record(path):
    path = Path(path)
    return {'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def input_manifest(package, procedures, ddls, mapping, base, config, *, ai, ai_provider):
    records = {'package': file_record(package),
               'procedures': sorted([file_record(p) for p in procedures], key=lambda x: (x['name'], x['sha256'])),
               'ddl': sorted([file_record(p) for p in ddls], key=lambda x: (x['name'], x['sha256'])),
               'connection_mapping': file_record(mapping) if mapping else None,
               'database_metadata': [], 'configuration': config.to_dict(),
               'ai': {'enabled': ai, 'provider': ai_provider}, 'engine_sha256': engine_hash()}
    if mapping:
        for item in load_connection_mapping(mapping).mappings:
            records['database_metadata'].append({'connection': item.connection_reference,
                    **file_record(Path(base) / item.metadata_file)})
    records['database_metadata'].sort(key=lambda x: x['connection'])
    fingerprint = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    return {'fingerprint': fingerprint, **records}


def engine_hash():
    digest = hashlib.sha256()
    root = Path(__file__).parent
    for path in sorted(root.rglob('*.py')):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def notebook_protection_reason(directory):
    directory = Path(directory)
    paths = sorted(directory.glob('*.ipynb'))
    if not paths: return None
    manifest = directory / 'migration_manifest.json'
    try:
        baseline = json.loads(manifest.read_text()).get('generated_notebooks', {}) if manifest.is_file() else {}
        if not isinstance(baseline, dict): return 'INVALID_GENERATED_BASELINE'
    except (ValueError, OSError, AttributeError):
        return 'UNREADABLE_MANIFEST'
    for path in paths:
        try:
            notebook = json.loads(path.read_text())
            if notebook.get('metadata', {}).get('human_reviewed') is True: return 'HUMAN_REVIEWED'
            if any('# HUMAN_REVIEWED: true' in ''.join(c.get('source', [])) for c in notebook.get('cells', [])): return 'HUMAN_REVIEWED'
            expected = baseline.get(path.name)
            if not isinstance(expected, str): return 'UNTRACKED_NOTEBOOK'
            if file_record(path)['sha256'] != expected: return 'NOTEBOOK_CONTENT_CHANGED'
        except (ValueError, OSError, AttributeError, TypeError):
            # An unreadable existing output must not be overwritten implicitly.
            return 'UNREADABLE_NOTEBOOK'
    return None


def reviewed_output(directory):
    return notebook_protection_reason(directory) is not None
