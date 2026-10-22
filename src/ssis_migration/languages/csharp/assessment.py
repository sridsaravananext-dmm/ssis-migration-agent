"""Conservative lexical triage of exported C# source.

Rules identify migration concerns, not a complete C# grammar. No rule grants
TRANSLATABLE status: that requires future syntax/semantic validation. Source
strings/comments cannot establish an API call; uncertain lexical forms block
assessment. Aliases, custom methods and external assemblies remain unresolved.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# Rules are bounded signals, not authoritative dependency or lineage extraction.
_RULES = (
    ('SSIS_VARIABLE_ACCESS', r'\bDts\s*\.\s*Variables\b', 'REVIEW'),
    ('SSIS_EVENT_HANDLING', r'\bDts\s*\.\s*Events\b', 'REVIEW'),
    ('SSIS_TASK_RESULT', r'\bDts\s*\.\s*TaskResult\b', 'REVIEW'),
    ('SSIS_CONNECTION_ACCESS', r'\bDts\s*\.\s*Connections\b', 'REDESIGN'),
    ('FILE_SYSTEM_IO', r'\b(?:System\s*\.\s*IO|File|Directory|FileStream|StreamReader|StreamWriter)\s*\.', 'REDESIGN'),
    ('PROCESS_EXECUTION', r'\b(?:Process\s*\.\s*Start|System\s*\.\s*Diagnostics)\b', 'REDESIGN'),
    ('DATABASE_CLIENT', r'\b(?:SqlConnection|SqlCommand|OleDbConnection|OdbcConnection|OracleConnection)\b', 'REDESIGN'),
    ('NETWORK_IO', r'\b(?:HttpClient|WebClient|WebRequest|Socket|TcpClient)\b', 'REDESIGN'),
    ('DYNAMIC_OR_NATIVE_CODE', r'\b(?:dynamic|unsafe|DllImport|Assembly\s*\.\s*Load|Activator\s*\.\s*CreateInstance)\b', 'REDESIGN'),
    ('TRANSACTION_BOUNDARY', r'\b(?:TransactionScope|BeginTransaction|Commit|Rollback)\b', 'REDESIGN'),
)


def _mask(source: str) -> tuple[str, list[str]]:
    """Mask comments and ordinary/verbatim literals, preserving offsets/lines.

    Interpolation, raw literals and preprocessor directives are deliberately
    unresolved: their contents/conditional compilation need a real parser.
    """
    chars = list(source)
    issues = []
    i = 0
    while i < len(source):
        start = i
        if source.startswith('//', i):
            end = source.find('\n', i)
            i = len(source) if end < 0 else end
        elif source.startswith('/*', i):
            end = source.find('*/', i + 2)
            if end < 0:
                issues.append('Unterminated block comment.')
            i = len(source) if end < 0 else end + 2
        elif source.startswith(('"""', '$"', '$@"', '@$"'), i):
            issues.append('Raw/interpolated string requires syntax-aware analysis.')
            # Do not inspect uncertain contents or credit later lexical signals.
            i = len(source)
        elif source[i] in ('"', "'") or source.startswith('@"', i):
            verbatim = source.startswith('@"', i)
            quote = '"' if verbatim else source[i]
            i += 2 if verbatim else 1
            closed = False
            while i < len(source):
                if verbatim and source.startswith('""', i):
                    i += 2
                elif not verbatim and source[i] == '\\':
                    i += 2
                elif source[i] == quote:
                    i += 1
                    closed = True
                    break
                else:
                    i += 1
            if not closed:
                issues.append('Unterminated string/character literal.')
        else:
            if source[i] == '#':
                issues.append('Preprocessor directives require compilation-context review.')
            i += 1
            continue
        for j in range(start, min(i, len(chars))):
            if chars[j] not in '\r\n':
                chars[j] = ' '
    return ''.join(chars), sorted(set(issues))


def assess_csharp(source: str, *, file_id: str, locator: str) -> dict[str, Any]:
    """Return evidence-bound triage of one decoded source export, never run it."""
    if not all(isinstance(value, str) and value for value in (file_id, locator)):
        raise ValueError('C# assessment requires a source file ID and locator.')
    if not isinstance(source, str):
        raise TypeError('C# source must be decoded text.')
    masked, issues = _mask(source)
    findings = []
    for code, pattern, concern in _RULES:
        for match in re.finditer(pattern, masked):
            findings.append({
                'code': code, 'concern': concern,
                'start_offset': match.start(), 'end_offset': match.end(),
                'line': source.count('\n', 0, match.start()) + 1,
                'evidence': {'file_id': file_id, 'locator': locator,
                             'rule': 'csharp_lexical_triage_v1'},
            })
    findings.sort(key=lambda item: (item['start_offset'], item['code']))
    classification = 'REVIEW_REQUIRED'
    if not issues and findings:
        classification = ('REQUIRES_REDESIGN' if any(f['concern'] == 'REDESIGN' for f in findings)
                          else 'PARTIALLY_TRANSLATABLE')
    unresolved = [
        'Complete C# syntax, control flow and semantic equivalence are not verified.',
        'Resolve referenced assemblies, aliases, custom calls, SSIS bindings and configuration from supplied exports.',
        'Source presence and lexical signals do not prove implementation or translation coverage.',
        *issues,
    ]
    if not source.strip():
        unresolved.append('Source text is empty; supply the original C# project/source export.')
    report = {
        'assessment_version': '1.0', 'language': 'csharp',
        'method': 'CONSERVATIVE_LEXICAL_TRIAGE',
        'classification': classification, 'semantic_status': 'NOT_VERIFIED',
        'source': {'text': source, 'sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                   'digest_scope': 'DECODED_UTF8_TEXT', 'file_id': file_id, 'locator': locator},
        'findings': findings, 'unresolved_items': unresolved,
        'validation_requirements': ['Review original source and all project references.',
                                    'Validate translated syntax independently.',
                                    'Verify error handling, side effects and outputs with controlled tests.'],
    }

    from .parser import parse_csharp
    report['syntax_analysis'] = parse_csharp(source, file_id=file_id, locator=locator)
    if report['syntax_analysis']['status'] != 'PARSED':
        report['classification'] = 'REVIEW_REQUIRED'
        report['unresolved_items'].append('Complete-file C# grammar parsing did not succeed.')
    from ssis_migration.schemas import validate_document
    validate_document(report, 'csharp_assessment')
    return report
