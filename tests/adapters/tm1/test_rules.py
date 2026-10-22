import copy
import hashlib

import pytest

from ssis_migration.adapters.tm1.rules import analyze_rules, rules_analysis_id, validate_rules_analysis
from ssis_migration.schemas import SchemaValidationError


def analyze(source):
    return analyze_rules(source,export_format='plain_rux_text',file_id='file-source')


@pytest.mark.parametrize('source',[
    "SKIPCHECK; ['Revenue']=N:['Units']*['Price']; FEEDERS; ['Units']=>['Revenue'];",
    "FEEDSTRINGS; UNDEFVALS; ['Label']=S:'Ready'; FEEDERS; ['Source']=>['Label'];",
    "['Total']=C:DB('Summary',!Year,'Value');",
    "[]=1;",
    "['Measures':'Revenue']=N:['Measures':'Units']*2;",
    "['Value']=N:IF(['Input']<>0, STET, CONTINUE);",
    "['Value']=N:ATTRN('Product',!Product,'Rate');",
    "['Value']=N:NUMBR(ATTRS('Product',!Product,'Code'));",
    "['Value']=N:IF(ELISCOMP('Dim','Parent',!Dim)=1,1,0);",
    "['Value']=N:(.5+1.2e-3)*-2;",
])
def test_complete_structure_never_claims_metadata_binding_or_acceptance(source):
    report=analyze(source)
    assert report['status']=='STRUCTURED',report['diagnostics']
    assert report['source']['text']==source
    assert report['source']['sha256']==hashlib.sha256(source.encode()).hexdigest()
    assert report['source_cube'] is None
    assert report['source_export_version'] is None
    assert report['ordering_scope']=='SOURCE_ORDER_ONLY'
    assert report['semantic_status']=='NOT_VERIFIED'
    assert report['migration_strategy']=='REVIEW_REQUIRED'
    assert report['migration_accepted'] is False and report['assumptions']==[]
    assert all(item['status']=='NOT_SUPPLIED' for item in report['dependencies'])
    validate_rules_analysis(report)


def test_order_and_overlapping_calculations_are_preserved_without_deduplication():
    report=analyze("['Revenue']=N:1; ['Revenue']=N:2; ['Revenue']=C:3; FEEDERS; ['Source']=>['Revenue'];")
    statements=report['statements']
    assert [s['source_order'] for s in statements]==[0,1,2,3,4]
    assert [s['ast']['value']['value'] for s in statements[:3]]==['1','2','3']
    assert [s['ast']['qualifier'] for s in statements[:3]]==['N','N','C']
    assert len({s['statement_id'] for s in statements})==5
    relation,=report['feeder_relations']
    assert relation['statement_id']==statements[-1]['statement_id']
    assert relation['kind']=='DECLARED_FEEDER' and relation['resolution']=='UNRESOLVED'
    assert 'RULE_ORDER_AND_OVERLAP' in {d['kind'] for d in report['dependencies']}


def test_db_literal_vs_dynamic_empty_targets_are_not_guessed():
    source="['Value']=N:DB('Rates',!Year,'Price'); FEEDERS; ['Units']=>DB(IF(['Flag']>0,'A','B'),!Year,'Value'); ['Units']=>DB('',!Year,'Value');"
    report=analyze(source)
    assert report['status']=='STRUCTURED'
    assert [r['value'] for r in report['references'] if r['kind']=='DB_CUBE_ARGUMENT']==['Rates',None,'']
    assert [d['declared_value'] for d in report['dependencies'] if d['kind']=='LOOKUP_CUBE_METADATA']==['Rates']
    assert len([d for d in report['dependencies'] if d['kind']=='DYNAMIC_OR_EMPTY_LOOKUP_TARGET'])==2
    assert all(r['resolution']=='UNRESOLVED' for r in report['references'])
    assert len(report['feeder_relations'])==2
    assert 'DYNAMIC_OR_EMPTY_DB_TARGET' in report['features']


def test_comments_strings_and_unicode_do_not_create_false_rule_dependencies():
    source="""# FEEDERS; ['Fake']=>DB('Ghost',!Dim,'X');
['日本']=S:'Café; FEEDERS; DB(''Ghost'',!Dim,''X'') # literal';
['Value']=N:DB('Ra''tes',!Year,'Value');"""
    report=analyze(source)
    assert report['status']=='STRUCTURED'
    assert len(report['statements'])==2 and not report['feeder_relations']
    assert [r['value'] for r in report['references'] if r['kind']=='DB_CUBE_ARGUMENT']==["Ra'tes"]
    assert report['source']['offset_unit']=='UNICODE_CODE_POINTS'
    for ref in report['references']:
        span=ref['span']
        assert 0<=span['start']<span['end']<=len(source)
        assert ref['evidence']['file_id']=='file-source'
        assert '#rux-chars='+str(span['start'])+':'+str(span['end']) in ref['evidence']['locator']


def test_control_and_division_operators_remain_distinct_and_unevaluated():
    report=analyze("['Result']=N:IF(['A'] @= 'x', CONTINUE, STET); ['Ratio']=N:['Numerator']/['Denominator'] + ['Other']\\0;")
    assert {'RULE_CONTROL:STET','RULE_CONTROL:CONTINUE','OPERATOR:/','OPERATOR:\\','OPERATOR:@='} <= set(report['features'])
    assert all(item['verification']=='NOT_VERIFIED' for item in report['statements'])


@pytest.mark.parametrize('source,status,code',[
    ("['A']=N:UnknownFunction(['B']);",'UNSUPPORTED','UNSUPPORTED_FUNCTION_SIGNATURE'),
    ("['A']=N:IF(1,2);",'UNSUPPORTED','UNSUPPORTED_FUNCTION_SIGNATURE'),
    ("['A']=N:1; #include 'other.rux'",'UNSUPPORTED','UNSUPPORTED_INCLUDE_DIRECTIVE'),
    ("['A']=N:1; Unexpected;",'UNSUPPORTED','UNSUPPORTED_STATEMENT'),
    ("['D':'H':'E']=N:1;",'UNSUPPORTED','UNSUPPORTED_HIERARCHY_SELECTOR'),
    ("['A']=N:Variable;",'UNSUPPORTED','UNRESOLVED_BARE_NAME'),
    ("['A']=N:DB('Cube',!'Spaced dimension','Value');",'UNSUPPORTED','UNSUPPORTED_DIMENSION_CONTEXT'),
    ("['A']=N:DB('Cube',!Spaced dimension,'Value');",'UNSUPPORTED','UNSUPPORTED_DIMENSION_CONTEXT'),
    ("['A']=>['B'];",'UNSUPPORTED','FEEDER_WITHOUT_SECTION'),
    ("FEEDERS; ['A']=N:1;",'UNSUPPORTED','CALCULATION_AFTER_FEEDERS'),
    ("SKIPCHECK; SKIPCHECK;",'UNSUPPORTED','REPEATED_DIRECTIVE'),
    ("FEEDERS; SKIPCHECK;",'UNSUPPORTED','DIRECTIVE_AFTER_FEEDERS'),
    ("FEEDERS; ['A']=>1;",'UNSUPPORTED','UNSUPPORTED_FEEDER_TARGET'),
    ("['A']=N:1",'INVALID','EXPECTED_TOKEN'),
    ("['A']=N:;",'INVALID','EXPECTED_EXPRESSION'),
    ("['A']=S:'unterminated",'INVALID','UNTERMINATED_STRING'),
    ('','INVALID','EMPTY_RULES'),
    ('# only a comment','INVALID','EMPTY_RULES'),
])
def test_unsupported_or_malformed_behavior_is_explicit_not_partial_success(source,status,code):
    report=analyze(source)
    assert report['status']==status,report['diagnostics']
    assert code in {d['code'] for d in report['diagnostics']}
    assert report['migration_strategy']=='UNSUPPORTED'
    assert report['source']['text']==source
    if code in {'UNSUPPORTED_INCLUDE_DIRECTIVE','UNSUPPORTED_STATEMENT','EXPECTED_TOKEN','UNTERMINATED_STRING'}:
        assert report['statements']==[] and report['references']==[] and report['dependencies']==[]


@pytest.mark.parametrize('source',[
    "['A']=N:"+'('*55+'1'+')'*55+';',
    "['A']=N:"+'+'.join('1' for _ in range(60))+';',
    '#'+'x'*1_048_576,
])
def test_limit_diagnostics_never_retain_a_parsed_prefix(source):
    report=analyze(source)
    assert report['status']=='LIMIT_EXCEEDED'
    assert report['statements']==[] and report['source']['text']==source


@pytest.mark.parametrize('change',['source','ast','file','span','order','feeder_missing','feeder_endpoint','accepted'])
def test_report_source_evidence_order_and_feeder_integrity_are_validated(change):
    report=copy.deepcopy(analyze("['A']=N:1; FEEDERS; ['A']=>['B'];"))
    if change=='source':report['source']['text']+=' changed'
    elif change=='ast':report['statements'][0]['ast']['value']['value']='99'
    elif change=='file':report['statements'][0]['evidence']['file_id']='other'
    elif change=='span':report['statements'][0]['span']['end']=99999
    elif change=='order':
        report['statements'][0],report['statements'][1]=report['statements'][1],report['statements'][0]
        for i,item in enumerate(report['statements']):item['source_order']=i
    elif change=='feeder_missing':report['feeder_relations']=[]
    elif change=='feeder_endpoint':report['feeder_relations'][0]['target_span']=report['feeder_relations'][0]['source_span']
    else:report['migration_accepted']=True
    if change not in {'source','ast','accepted'}:report['analysis_id']=rules_analysis_id(report)
    with pytest.raises(SchemaValidationError):validate_rules_analysis(report)


def test_rules_never_use_sql_or_mdx_fallback(monkeypatch):
    import sqlglot
    import ssis_migration.languages.mdx as mdx
    def forbidden(*args,**kwargs):raise AssertionError('Rules must use their own parser.')
    monkeypatch.setattr(sqlglot,'parse',forbidden);monkeypatch.setattr(sqlglot,'tokenize',forbidden)
    monkeypatch.setattr(mdx,'analyze_mdx',forbidden)
    assert analyze("['A']=N:1;")['status']=='STRUCTURED'
    with pytest.raises(ValueError):analyze_rules("['A']=N:1;",export_format='binary_rux',file_id='file-source')


def test_synthetic_rules_example_matches_portable_golden_summary():
    import json
    from pathlib import Path
    root=Path(__file__).resolve().parents[3]
    report=analyze((root/'examples/tm1/rules/sales_rules.rux').read_text())
    summary={'status':report['status'],'source_cube':report['source_cube'],'source_export_version':report['source_export_version'],
             'statement_kinds':[s['ast']['kind'] for s in report['statements']],
             'calculation_qualifiers':[s['ast']['qualifier'] for s in report['statements'] if s['ast']['kind']=='calculation'],
             'declared_db_cubes':[r['value'] for r in report['references'] if r['kind']=='DB_CUBE_ARGUMENT'],
             'feeder_count':len(report['feeder_relations']),'dependency_count':len(report['dependencies']),
             'features':report['features'],'semantic_status':report['semantic_status'],'migration_accepted':report['migration_accepted']}
    expected=json.loads((Path(__file__).parent/'fixtures/sales_rules_summary.json').read_text())
    assert summary==expected


def test_deep_supported_expression_validates_without_revisiting_unrelated_node_types():
    report=analyze("['Value']=N:"+'('*20+'1'+')'*20+';')
    assert report['status']=='STRUCTURED'
    for invalid_kind in ['unknown_node_type',None]:
        broken=copy.deepcopy(report)
        if invalid_kind is None:broken['statements'][0]['ast']['value'].pop('kind')
        else:broken['statements'][0]['ast']['value']['kind']=invalid_kind
        broken['analysis_id']=rules_analysis_id(broken)
        with pytest.raises(SchemaValidationError):validate_rules_analysis(broken)
