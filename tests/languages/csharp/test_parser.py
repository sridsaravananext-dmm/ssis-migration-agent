import hashlib

import pytest

from ssis_migration.languages.csharp import assess_csharp, parse_csharp
from ssis_migration.schemas import validate_document


@pytest.mark.parametrize('source', [
    'class C { public void Main() { var x = $"{1+2}"; } }',
    'class C { string s = """raw { text }"""; }',
    'class C { void M() { try { foreach(var x in xs) { Call(x); } } catch { throw; } } }',
    '#if DEBUG\nclass C {}\n#else\nclass D {}\n#endif',
])
def test_complete_grammar_recognizes_broad_syntax_without_claiming_translation(source):
    result = parse_csharp(source, file_id='file-test', locator='xml:/0')
    assert result['status'] == 'PARSED'
    assert result['semantic_status'] == 'NOT_VERIFIED'
    assert result['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()
    assert result['size_bytes'] == len(source.encode())
    validate_document(result, 'csharp_syntax')


@pytest.mark.parametrize('source', [
    'class C { void M() { int x = ; } }',
    'class C { void M() { return; }',
    'class C {} unexpected trailing @@@',
])
def test_malformed_or_trailing_source_never_passes_on_a_valid_prefix(source):
    result = parse_csharp(source, file_id='file-test', locator='xml:/0')
    assert result['status'] == 'INVALID'
    assert result['diagnostics']
    assert assess_csharp(source, file_id='file-test', locator='xml:/0')['classification'] == 'REVIEW_REQUIRED'


def test_error_offsets_are_utf8_bytes_and_bound_to_complete_source():
    source = '// café\nclass C { void M() { int n = ; } }'
    result = parse_csharp(source, file_id='f', locator='xml:/1')
    assert result['offset_unit'] == 'UTF8_BYTES'
    assert result['status'] == 'INVALID'
    assert all(d['line'] == 2 for d in result['diagnostics'])
    assert result['evidence']['file_id'] == 'f'


def test_size_limit_reports_whole_source_without_accepting_a_prefix():
    source = '// ' + 'x' * 1_048_576
    result = parse_csharp(source, file_id='f', locator='xml:/1')
    assert result['status'] == 'LIMIT_EXCEEDED'
    assert result['construct_counts'] == {}
    assert result['size_bytes'] == len(source.encode())
