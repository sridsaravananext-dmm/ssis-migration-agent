"""Small, non-executing SQL text helpers. No inference from comments/literals."""
from __future__ import annotations
import re

IDENTIFIER = r'(?:\[(?:[^\]]|\]\])+\]|[A-Za-z_][\w$#]*)'
QUALIFIED = rf'{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER}){{0,2}}'
CALL = re.compile(rf'\bEXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?(?P<name>{QUALIFIED})', re.I)


def mask_sql(text: str) -> str:
    """Blank quoted strings and comments, retaining offsets and newlines."""
    out = list(text)
    i = 0
    while i < len(text):
        start = i
        if text.startswith('--', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
        elif text.startswith('/*', i):
            depth = 1
            i += 2
            while i < len(text) and depth:
                if text.startswith('/*', i): depth += 1; i += 2
                elif text.startswith('*/', i): depth -= 1; i += 2
                else: i += 1
        elif text[i] == "'":
            i += 1
            while i < len(text):
                if text[i] == "'":
                    i += 1
                    if i < len(text) and text[i] == "'": i += 1; continue
                    break
                i += 1
        else:
            i += 1
            continue
        for j in range(start, i):
            if out[j] != '\n': out[j] = ' '
    return ''.join(out)


def normalize_name(name: str, default_schema: str = 'dbo') -> str:
    parts = re.findall(IDENTIFIER, name)
    parts = [p[1:-1].replace(']]', ']') if p.startswith('[') else p for p in parts]
    if len(parts) == 1: parts.insert(0, default_schema)
    return '.'.join(parts).casefold()


def procedure_calls(sql: str) -> list[str]:
    return sorted({re.sub(r'\s*\.\s*', '.', m['name']).replace('[', '').replace(']', '')
                   for m in CALL.finditer(mask_sql(sql or ''))})


def has_dynamic_call(sql: str) -> bool:
    return bool(re.search(r'\bEXEC(?:UTE)?\s*(?:\(|@\w+(?!\w)\s*(?!\s*=))', mask_sql(sql), re.I))
