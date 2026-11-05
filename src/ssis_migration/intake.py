"""Content-based intake shared by the frontend and unit tests."""
import json
import re
from ssis_migration.database.connection_mapping_loader import validate_connection_mapping
from ssis_migration.sql_text import mask_sql


def classify_dependency(filename, content):
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
