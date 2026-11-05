"""Read procedure definitions from multiple files; reject conflicting definitions."""
from __future__ import annotations
import re
from pathlib import Path
from ssis_migration.sql_text import QUALIFIED, mask_sql, normalize_name

CREATE = re.compile(rf'\b(?:CREATE\s+(?:OR\s+ALTER\s+)?|ALTER\s+)PROC(?:EDURE)?\s+(?P<name>{QUALIFIED})', re.I)


def read_sql_file(path):
    raw = Path(path).read_bytes()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    return raw.decode('utf-8-sig')


def parse_procedures(text: str, default_schema='dbo'):
    matches = list(CREATE.finditer(mask_sql(text)))
    result = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # A SQL Server GO batch boundary ends the definition, not subsequent DDL.
        batch = re.search(r'^\s*GO\s*(?:--[^\n]*)?$', mask_sql(text)[match.end():end], re.I | re.M)
        if batch: end = match.end() + batch.start()
        name = normalize_name(match['name'], default_schema)
        body = text[match.start():end].strip()
        if name in result and result[name] != body:
            raise ValueError(f"Conflicting procedure definitions: {name}")
        result[name] = body
    return result


def load_procedures(source, default_schema='dbo'):
    if isinstance(source, Path):
        text = read_sql_file(source)
    else:
        try:
            path = Path(source)
            text = read_sql_file(path) if '\n' not in source and path.is_file() else source
        except OSError:
            text = str(source)
    return parse_procedures(text, default_schema)


def load_procedure_files(paths, default_schema='dbo'):
    definitions, origins = {}, {}
    for path in paths:
        for name, body in load_procedures(Path(path), default_schema).items():
            if name in definitions and definitions[name] != body:
                raise ValueError(f"Conflicting procedure definitions for {name}: {origins[name]} and {path}")
            definitions[name] = body
            origins.setdefault(name, []).append(str(path))
    return definitions, origins


def list_procedures(source):
    return sorted(load_procedures(source))


def load_scoped_procedure_files(paths, scopes=None, default_schema='dbo'):
    """Keep same-named procedures separate when files have explicit connection scopes."""
    scopes = scopes or {}
    definitions, origins = {}, {}
    for path in paths:
        path = Path(path)
        scope = scopes.get(str(path)) or scopes.get(path.name)
        for name, body in load_procedures(path, default_schema).items():
            key = f'{scope}|{name}' if scope else name
            if key in definitions and definitions[key] != body:
                raise ValueError(f'Conflicting procedure definitions for {key}: {origins[key]} and {path}')
            definitions[key] = body
            origins.setdefault(key, []).append(str(path))
    return definitions, origins
