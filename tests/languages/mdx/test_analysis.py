import copy
import hashlib

import pytest

from ssis_migration.languages.mdx import analyze_mdx, mdx_analysis_id, validate_mdx_analysis
from ssis_migration.schemas import SchemaValidationError


def analyze(source):
    return analyze_mdx(source,dialect='tm1',file_id='file-source',locator='file:/')


@pytest.mark.parametrize('expression', [
    '{[Measures].[Revenue]}', '{}', '{[Time].&[2024], [Time].&[2025]}',
    '{([Region].[US], [Measures].[Revenue]), ([Region].[UK], [Measures].[Revenue])}',
    'CrossJoin({[Region].[US]}, {[Product].[A]})',
    'Union({[Region].[US]}, {[Region].[UK]})',
    'Intersect({[Region].[US]}, {[Region].[UK]})',
    'Except({[Region].[US]}, {[Region].[UK]})',
    'Hierarchize([Region].Members)', 'Head([Region].Children, 10)', 'Tail([Region].Members)',
    'TM1SubsetToSet([Product].[Product], "Active", "public")',
    'TM1FilterByLevel([Product].Members, 0)',
    '{[Time].[Jan]:[Time].[Dec]}',
    '({[Region].[US]} * {[Product].[A]}) + {[Region].[UK]}',
])
def test_complete_select_structure_is_separate_from_semantic_acceptance(expression):
    source='SELECT NON EMPTY '+expression+' ON COLUMNS FROM [Sales]'
    report=analyze(source)
    assert report['status']=='STRUCTURED',report['diagnostics']
    assert report['query']['axes'][0]['non_empty'] is True
    assert report['query']['cube']['name']=='Sales'
    assert report['source']['text']==source
    assert report['source']['sha256']==hashlib.sha256(source.encode()).hexdigest()
    assert report['semantic_status']=='NOT_VERIFIED'
    assert report['migration_strategy']=='REVIEW_REQUIRED'
    assert report['migration_accepted'] is False
    assert report['assumptions']==[]
    assert all(dep['status']=='NOT_SUPPLIED' for dep in report['dependencies'])
    validate_mdx_analysis(report)


def test_axes_slicer_and_member_properties_keep_multidimensional_context():
    source='''SELECT NON EMPTY {[Measures].[Revenue]} ON 0,
TM1SubsetToSet([Product].[Hierarchy], 'Active') ON ROWS
FROM [Finance] WHERE ([Version].[Actual], [Time].CurrentMember);'''
    report=analyze(source)
    assert report['status']=='STRUCTURED'
    assert [a['ordinal'] for a in report['query']['axes']]==[0,1]
    assert report['query']['slicer']['kind']=='tuple'
    assert 'SLICER_CONTEXT' in report['features']
    assert 'DYNAMIC_MEMBER_CONTEXT' in report['features']
    subset,=[d for d in report['dependencies'] if d['kind']=='SUBSET_DEFINITION']
    assert subset['declared_value']=='Active'
    assert subset['details']['declared_scope'] is None  # No guessed public/private scope.
    assert all(r['resolution']=='UNRESOLVED' for r in report['references'])


def test_escaping_comments_and_unicode_preserve_reference_evidence():
    source='''-- FROM [FakeCube] CellPutN(1);\nSELECT {[Measures].[Gross]]Margin], [Region].[日本]} ON COLUMNS
/* WHERE [FakeMember] */ FROM [Sales]]Archive]'''
    report=analyze(source)
    assert report['status']=='STRUCTURED'
    assert report['query']['cube']['name']=='Sales]Archive'
    assert report['source']['offset_unit']=='UNICODE_CODE_POINTS'
    assert [r['segments'][-1]['value'] for r in report['references'] if r['kind']=='MEMBER_PATH']==['Gross]Margin','日本']
    assert all('Fake' not in r['value'] for r in report['references'])
    for ref in report['references']:
        span=ref['span']
        assert 0<=span['start']<span['end']<=len(source)
        assert ref['evidence']['file_id']=='file-source'
        assert '#mdx-chars='+str(span['start'])+':'+str(span['end']) in ref['evidence']['locator']


def test_quoted_subset_string_does_not_create_false_dependencies():
    source='SELECT TM1SubsetToSet([Product], "SELECT [Fake] FROM [Ghost]") ON 0 FROM [Real]'
    report=analyze(source)
    assert report['status']=='STRUCTURED'
    assert [r['value'] for r in report['references'] if r['kind']=='CUBE']==['Real']
    assert [r['value'] for r in report['references'] if r['kind']=='MEMBER_PATH']==['[Product]']


@pytest.mark.parametrize('source,status,code', [
    ('SELECT {[A].[B]} ON 0 FROM [C]; SELECT {[X]} ON 0 FROM [Y]', 'UNSUPPORTED','UNSUPPORTED_REMAINDER'),
    ('WITH MEMBER [Measures].[X] AS 1 SELECT {[Measures].[X]} ON 0 FROM [C]', 'UNSUPPORTED','UNSUPPORTED_QUERY'),
    ('SELECT {[A]} ON 0 FROM (SELECT {[B]} ON 0 FROM [C])', 'UNSUPPORTED','UNSUPPORTED_SUBSELECT'),
    ('SELECT {[A]} ON 0, {[B]} ON COLUMNS FROM [C]','INVALID','DUPLICATE_AXIS'),
    ('SELECT {[A]} ON 0 FROM [C','INVALID','UNTERMINATED_LITERAL'),
    ('SELECT {[A]} ON 0 FROM [C] /*','INVALID','UNTERMINATED_COMMENT'),
    ('SELECT {[A]} ON 0 FROM','INVALID','EXPECTED_TOKEN'),
    ('SELECT {[A],} ON 0 FROM [C]','INVALID','EXPECTED_EXPRESSION'),
    ('SELECT {[A]} ON 999 FROM [C]','UNSUPPORTED','UNSUPPORTED_AXIS'),
    ('SELECT NamedSet ON 0 FROM [C]','UNSUPPORTED','UNRESOLVED_NAME'),
    ('SELECT {[A]} ON 0 FROM [C] CELL PROPERTIES VALUE','UNSUPPORTED','UNSUPPORTED_REMAINDER'),
    ('','INVALID','EMPTY_MDX'),
    ('SELECT [A].UnknownProperty ON 0 FROM [C]','UNSUPPORTED','UNKNOWN_MEMBER_PROPERTY'),
    ('SELECT Mystery({[A]}) ON 0 FROM [C]','UNSUPPORTED','UNSUPPORTED_FUNCTION_SIGNATURE'),
    ('SELECT {[A]} + 1 ON 0 FROM [C]','UNSUPPORTED','UNRESOLVED_OPERATOR_TYPES'),
    ('SELECT {[A]} ON 0 FROM [C] WHERE 1','UNSUPPORTED','UNSUPPORTED_SLICER'),
])
def test_unsupported_or_malformed_behavior_never_becomes_partial_success(source,status,code):
    report=analyze(source)
    assert report['status']==status
    assert code in {d['code'] for d in report['diagnostics']}
    assert report['migration_strategy']=='UNSUPPORTED'
    assert report['source']['text']==source
    if code in {'UNSUPPORTED_REMAINDER','UNSUPPORTED_SUBSELECT','UNTERMINATED_LITERAL','EXPECTED_TOKEN'}:
        assert report['query'] is None and report['references']==[]


@pytest.mark.parametrize('expression', [
    '(' * 55 + '{[A]}' + ')' * 55,
    '+'.join('{[A]}' for _ in range(60)),
])
def test_deep_or_left_associative_trees_have_explicit_limits(expression):
    report=analyze('SELECT '+expression+' ON 0 FROM [C]')
    assert report['status']=='LIMIT_EXCEEDED'
    assert report['query'] is None


def test_source_limit_preserves_full_evidence_without_parsing_a_prefix():
    source='--'+'x'*1_048_576
    report=analyze(source)
    assert report['status']=='LIMIT_EXCEEDED'
    assert report['source']['text']==source


@pytest.mark.parametrize('change',['text','ast','evidence','span','accepted'])
def test_source_content_and_evidence_validation_rejects_stale_or_forged_reports(change):
    report=copy.deepcopy(analyze('SELECT {[A].[B]} ON 0 FROM [C]'))
    if change=='text': report['source']['text']+=' extra'
    elif change=='ast': report['query']['cube']['name']='Other'
    elif change=='evidence':
        report['references'][0]['evidence']['file_id']='unrelated'
        report['analysis_id']=mdx_analysis_id(report)
    elif change=='span':
        report['references'][0]['span']['end']=9999
        report['analysis_id']=mdx_analysis_id(report)
    else: report['migration_accepted']=True
    with pytest.raises(SchemaValidationError):
        validate_mdx_analysis(report)


def test_mdx_never_calls_sql_or_ai(monkeypatch):
    import sqlglot
    def forbidden(*args,**kwargs):raise AssertionError('MDX must not use SQL parsing.')
    monkeypatch.setattr(sqlglot,'parse',forbidden)
    assert analyze('SELECT {[A]} ON 0 FROM [C]')['status']=='STRUCTURED'
    with pytest.raises(ValueError):
        analyze_mdx('SELECT 1',dialect='tsql',file_id='file-source')


def test_synthetic_example_matches_portable_golden_summary():
    import json
    from pathlib import Path
    root=Path(__file__).resolve().parents[3]
    report=analyze((root/'examples/tm1/mdx/sales_axes.mdx').read_text())
    summary={'status':report['status'],'cube':report['query']['cube']['name'],
             'axes':[{'ordinal':a['ordinal'],'non_empty':a['non_empty'],'expression_kind':a['expression']['kind']} for a in report['query']['axes']],
             'member_paths':sorted(r['value'] for r in report['references'] if r['kind']=='MEMBER_PATH'),
             'features':report['features'],'dependency_count':len(report['dependencies']),
             'semantic_status':report['semantic_status'],'migration_accepted':report['migration_accepted']}
    expected=json.loads((Path(__file__).parent/'fixtures/sales_axes_summary.json').read_text())
    assert summary==expected


def test_set_operator_precedence_and_range_endpoints_are_explicit():
    report=analyze('SELECT {[A]} + {[B]} * {[C]} ON 0 FROM [Cube]')
    expression=report['query']['axes'][0]['expression']
    assert expression['operator']=='+'
    assert expression['right']['operator']=='*'
    ranged=analyze('SELECT {[Time].[Jan]:[Time].[Dec]} ON 0 FROM [Cube]')['query']['axes'][0]['expression']['items'][0]
    assert ranged['operator']==':'
    assert ranged['left']['segments'][-1]['value']=='Jan'
    assert ranged['right']['segments'][-1]['value']=='Dec'


def test_nested_supported_sets_validate_with_explicit_node_tags():
    report=analyze('SELECT '+'{'*20+'[A]'+'}'*20+' ON 0 FROM [Cube]')
    assert report['status']=='STRUCTURED'
