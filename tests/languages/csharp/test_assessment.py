import hashlib

import pytest

from ssis_migration.languages.csharp import assess_csharp
from ssis_migration.schemas import SchemaValidationError, validate_document


def assess(source):
    return assess_csharp(source, file_id='file-test', locator='xml:/0/1')


@pytest.mark.parametrize('source,classification,code', [
    ('Dts.Variables["User::Count"].Value = 1;', 'PARTIALLY_TRANSLATABLE', 'SSIS_VARIABLE_ACCESS'),
    ('Dts.TaskResult = (int)ScriptResults.Success;', 'PARTIALLY_TRANSLATABLE', 'SSIS_TASK_RESULT'),
    ('File.Delete(path);', 'REQUIRES_REDESIGN', 'FILE_SYSTEM_IO'),
    ('new SqlConnection(connection);', 'REQUIRES_REDESIGN', 'DATABASE_CLIENT'),
    ('Process.Start(command);', 'REQUIRES_REDESIGN', 'PROCESS_EXECUTION'),
    ('using(var tx = new TransactionScope()) {}', 'REQUIRES_REDESIGN', 'TRANSACTION_BOUNDARY'),
])
def test_triage_is_evidence_bound_and_never_verification(source, classification, code):
    result = assess(source)
    assert result['classification'] == classification
    assert result['semantic_status'] == 'NOT_VERIFIED'
    assert result['source']['text'] == source
    assert result['source']['sha256'] == hashlib.sha256(source.encode()).hexdigest()
    finding = next(f for f in result['findings'] if f['code'] == code)
    assert finding['evidence']['locator'] == 'xml:/0/1'
    assert source[finding['start_offset']:finding['end_offset']]
    assert result['unresolved_items']


@pytest.mark.parametrize('source', [
    '// File.Delete(path);\n/* Process.Start(x); */',
    'var x = "File.Delete(path)";',
    'var x = @"Dts.Variables[""x""].Value";',
    '', 'public void Main() { CustomBusinessRule(); }',
])
def test_comments_literals_empty_and_unknown_do_not_grant_support(source):
    result = assess(source)
    assert result['findings'] == []
    assert result['classification'] == 'REVIEW_REQUIRED'


@pytest.mark.parametrize('source', [
    'Dts.TaskResult=0; /*', 'Dts.TaskResult=0; "unterminated',
    'Dts.TaskResult=0; var x = $"{File.Delete(path)}";',
    'Dts.TaskResult=0; var x = """raw""";',
    '#if DEBUG\nDts.TaskResult=0;\n#endif',
])
def test_uncertain_lexical_forms_require_review(source):
    assert assess(source)['classification'] == 'REVIEW_REQUIRED'


def test_offsets_preserve_lines_and_non_ascii():
    source = '// café\nvar x = "\\\"File.Delete(x)";\nDts . Variables["x"].Value=1;'
    finding, = assess(source)['findings']
    assert finding['line'] == 3
    assert source[finding['start_offset']:finding['end_offset']] == 'Dts . Variables'


def test_schema_rejects_claim_of_verified_translation():
    report = assess('Dts.TaskResult=0;')
    report['classification'] = 'TRANSLATABLE'
    with pytest.raises(SchemaValidationError):
        validate_document(report, 'csharp_assessment')
