"""Content-based intake shared by the frontend and unit tests."""
import json
import re
import hashlib
from pathlib import Path
from ssis_migration.database.connection_mapping_loader import validate_connection_mapping
from ssis_migration.sql_text import mask_sql


def stage_packages(uploads,root):
    """Stage only the selected batch; old uploads never enter a later run."""
    selected={}
    for filename,raw in uploads:
        name=Path(filename.replace('\\','/')).name
        if Path(name).suffix.casefold()!='.dtsx':raise ValueError('Package uploads must be .dtsx files')
        if not isinstance(raw,bytes):raise ValueError('Package content must be bytes')
        fingerprint=hashlib.sha256(raw).hexdigest()
        selected[(name,fingerprint)]=raw
    if not selected:raise ValueError('Select at least one SSIS package')
    if len(selected)>512:raise ValueError('A frontend batch supports at most 512 packages')
    batch=hashlib.sha256(json.dumps(sorted(selected)).encode()).hexdigest()
    directory=Path(root)/batch
    for (name,fingerprint),raw in sorted(selected.items()):
        path=directory/fingerprint/name
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return directory


def classify_dependency(filename, content):
    if filename.casefold().endswith(('.zip','.py','.html','.ipynb')):return 'reference'
    try:
        text = content.decode('utf-16' if content.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    except UnicodeError:
        return 'unknown'
    if filename.casefold().endswith('.json'):
        try: validate_connection_mapping(json.loads(text))
        except (ValueError, TypeError): return 'unknown'
        return 'connection_mapping'
    if not filename.casefold().endswith(('.sql', '.txt')): return 'unknown'
    clean = mask_sql(text)
    procedure = bool(re.search(r'\b(?:CREATE\s+(?:OR\s+ALTER\s+)?|ALTER\s+)PROC(?:EDURE)?\b', clean, re.I))
    ddl = bool(re.search(r'\bCREATE\s+TABLE\b', clean, re.I))
    return 'mixed_sql' if procedure and ddl else 'procedures' if procedure else 'ddl' if ddl else 'sql_other'
