import copy
import hashlib

import pytest

from ssis_migration.adapters.tm1.ti import analyze_ti_sections, ti_analysis_id, validate_ti_analysis
from ssis_migration.schemas import SchemaValidationError

NAMES=('prolog','metadata','data','epilog')


def sections(**bodies):
    return {name:{'text':bodies.get(name,''),'file_id':'file-'+name,'locator':'file:/'} for name in NAMES}


def analyze(**bodies):return analyze_ti_sections(sections(**bodies),export_format='ti_section_files_v1')


def test_separate_section_lifecycle_and_missing_metadata_never_become_acceptance():
    report=analyze(prolog="DatasourceType='NONE';",metadata="n=1;",data="n=n+1;",epilog="nDone=1;")
    assert report['status']=='STRUCTURED'
    assert report['section_order']==list(NAMES)
    assert report['sections'][1]['iteration_scope']==report['sections'][2]['iteration_scope']=='SOURCE_DEPENDENT_SEPARATE_RECORD_PASS'
    assert report['lifecycle_status']=='SOURCE_DEPENDENT_NOT_VERIFIED'
    assert report['process_name'] is None and report['source_export_version'] is None
    assert report['incremental_logic']=='NOT_DETERMINED'
    assert report['semantic_status']=='NOT_VERIFIED' and report['migration_accepted'] is False
    assert report['assumptions']==[]
    assert all(dep['status']=='NOT_SUPPLIED' for dep in report['dependencies'])
    for section in report['sections']:
        assert section['source']['sha256']==hashlib.sha256(section['source']['text'].encode()).hexdigest()
        flow=section['control_flow']
        assert flow['scope']=='NOMINAL_INTRA_SECTION_ONLY'
        assert all(edge['target_id'] is None or ('ti-entry-' not in edge['target_id'] or edge['target_id']==flow['entry_id']) for edge in flow['edges'])


def test_missing_section_is_not_replaced_by_an_empty_pass():
    supplied=sections();supplied.pop('metadata')
    report=analyze_ti_sections(supplied,export_format='ti_section_files_v1')
    assert report['status']=='UNSUPPORTED' and report['missing_sections']==['metadata']
    assert report['sections'][1]['source'] is None and report['sections'][1]['status']=='MISSING'
    empty=analyze()
    assert empty['status']=='STRUCTURED'
    assert all(s['structure_complete'] and not s['ast'] for s in empty['sections'])


def test_ordered_if_elseif_else_guards_and_loop_back_edges_are_preserved():
    source='''n=0;
IF(pFlag=1); n=1; ELSEIF(pFlag=2); n=2; ELSE; n=3; ENDIF;
WHILE(n<5); n=n+1; END;'''
    report=analyze(data=source);section=report['sections'][2]
    assert report['status']=='STRUCTURED'
    assert [s['kind'] for s in section['statements']]==['assignment','if','assignment','assignment','assignment','while','assignment']
    assert [s['source_order'] for s in section['statements']]==list(range(7))
    branches=[e for e in section['control_flow']['edges'] if e['kind']=='BRANCH']
    assert [[g['expected'] for g in e['guards']] for e in branches]==[[True],[False,True],[False,False]]
    assert {'LOOP_BACK','LOOP_ENTER','LOOP_EXIT'}<={e['kind'] for e in section['control_flow']['edges']}
    assert 'LOOP_TERMINATION_REVIEW' in {d['kind'] for d in report['dependencies']}


@pytest.mark.parametrize('control',['ItemSkip','ProcessQuit','ProcessError','ProcessBreak',"ItemReject('bad')"])
def test_early_controls_have_unresolved_targets_and_do_not_complete_the_section(control):
    report=analyze(data=control+'; nAfter=1;')
    assert report['status']=='STRUCTURED'
    section=report['sections'][2]
    assert len(section['statements'])==2  # Later source is retained, not discarded.
    transfer,=[e for e in section['control_flow']['edges'] if e['kind']=='CONTROL_TRANSFER']
    assert transfer['target_id'] is None and transfer['resolution']=='UNRESOLVED_CONTROL_TARGET'
    first=section['statements'][0]['statement_id']
    assert not any(e['source_id']==first and e['kind']=='SEQUENCE' for e in section['control_flow']['edges'])


def test_cube_calls_and_sync_async_processes_keep_return_and_target_context():
    report=analyze(data="nValue=CellGetN('Input', vKey, 'Value'); CellPutN(nValue, 'Output', vKey, 'Value'); nStatus=ExecuteProcess('Child', 'pKey', vKey); nJob=RunProcess(pChild, 'pKey', vKey);")
    assert report['status']=='STRUCTURED'
    assert [(e['kind'],e['target_literal']) for e in report['effects']]==[
        ('CUBE_READ','Input'),('CUBE_WRITE','Output'),('SYNCHRONOUS_PROCESS_CALL','Child'),('ASYNCHRONOUS_PROCESS_CALL',None)]
    kinds={d['kind'] for d in report['dependencies']}
    assert {'PROCESS_RETURN_STATUS_REVIEW','ASYNCHRONOUS_COMPLETION_REVIEW','CHILD_PROCESS_DEFINITION'}<=kinds
    assert all(r['resolution']=='UNRESOLVED' for r in report['references'])
    assert any(d['code']=='UNRESOLVED_CALL_TARGET' for d in report['diagnostics'])


def test_external_and_destructive_effects_are_reported_without_execution_or_sql_inference():
    report=analyze(prolog="ODBCOpen('conn', 'user', 'secret'); ODBCOutput('conn', sSQL); ExecuteCommand(sCommand, 1); ViewZeroOut('Sales', 'View');",epilog="ASCIIOutput(sPath | '.txt', 'done'); ODBCClose('conn');")
    assert report['status']=='STRUCTURED'
    assert sum(e['requires_redesign'] for e in report['effects'])==2
    assert {e['kind'] for e in report['effects']}>={'EXTERNAL_COMMAND','EXTERNAL_SQL','DESTRUCTIVE_CUBE_ACTION','FILE_WRITE'}
    assert any(d['code']=='UNRESOLVED_SQL_DIALECT' for d in report['diagnostics'])
    assert [e['target_literal'] for e in report['effects'] if e['kind']=='FILE_WRITE']==[None]
    assert all(e['runtime_status']=='NOT_VERIFIED' for e in report['effects'])


def test_configuration_is_not_evaluated_and_names_do_not_infer_incremental_logic():
    report=analyze(prolog="DatasourceType='ASCII'; IF(pMode @= 'delta'); DatasourceNameForServer=pPath; ELSE; DatasourceNameForServer='all.csv'; ENDIF; pWatermark=0;")
    assert [(a['name'],a['literal_string']) for a in report['configuration_assignments']]==[
        ('DatasourceType','ASCII'),('DatasourceNameForServer',None),('DatasourceNameForServer','all.csv')]
    assert report['incremental_logic']=='NOT_DETERMINED'
    assert not any('WATERMARK' in d['kind'] for d in report['dependencies'])


def test_literals_and_comments_cannot_create_effects_or_section_markers():
    report=analyze(data="""# ProcessQuit; CellPutN(1,'Ghost','X');
s='CellPutN(1,''Ghost'',''X''); # Epilog';
n=1;""")
    assert report['status']=='STRUCTURED' and report['effects']==[]
    assert len(report['sections'][2]['statements'])==2


@pytest.mark.parametrize('source,status,code',[
    ('601,100\n602,"NativeProcess"','UNSUPPORTED','UNSUPPORTED_TOKEN'),
    ('601,100','UNSUPPORTED','UNSUPPORTED_SERIALIZED_OR_STATEMENT'),
    ('n=UnknownFunction(1);','UNSUPPORTED','UNSUPPORTED_FUNCTION_SIGNATURE'),
    ("ExecuteProcess('Child','unpaired');",'UNSUPPORTED','UNSUPPORTED_FUNCTION_SIGNATURE'),
    ('n=1; HiddenStatement;','UNSUPPORTED','UNSUPPORTED_STATEMENT'),
    ('IF(1); n=1;','INVALID','EXPECTED_TOKEN'),
    ('WHILE(1); n=1;','INVALID','EXPECTED_TOKEN'),
    ('ENDIF;','INVALID','UNEXPECTED_BLOCK_TERMINATOR'),
    ("n=['RulesArea'];",'UNSUPPORTED','UNSUPPORTED_TI_EXPRESSION'),
    ('n=!RuleDimension;','UNSUPPORTED','UNSUPPORTED_TI_EXPRESSION'),
    ('n=1; #include other','UNSUPPORTED','UNSUPPORTED_INCLUDE_DIRECTIVE'),
    ('n=;','INVALID','EXPECTED_EXPRESSION'),
])
def test_unsupported_or_invalid_sections_never_get_partial_control_flow(source,status,code):
    report=analyze(data=source)
    assert report['status']==status,report['diagnostics']
    assert code in {d['code'] for d in report['diagnostics']}
    record=report['sections'][2]
    assert record['source']['text']==source
    assert report['migration_strategy']=='UNSUPPORTED'
    if code!='UNSUPPORTED_FUNCTION_SIGNATURE':
        assert record['ast']==[] and record['control_flow'] is None and not record['structure_complete']


@pytest.mark.parametrize('change',['source','flow','guard','index','file','missing','lifecycle','accepted'])
def test_source_identity_control_flow_and_index_omissions_are_rejected(change):
    report=copy.deepcopy(analyze(data='IF(p=1); n=1; ELSE; n=2; ENDIF;'))
    section=report['sections'][2]
    if change=='source':section['source']['text']+=' extra'
    elif change=='flow':section['control_flow']['edges'].pop()
    elif change=='guard':next(e for e in section['control_flow']['edges'] if e['guards'])['guards'][0]['expected']=False
    elif change=='index':section['statements'].pop()
    elif change=='file':report['references'][0]['evidence']['file_id']='other'
    elif change=='missing':report['missing_sections']=['data']
    elif change=='lifecycle':report['lifecycle_status']='ONE_COMBINED_ROW_LOOP'
    else:report['migration_accepted']=True
    if change not in {'source','lifecycle','accepted'}:report['analysis_id']=ti_analysis_id(report)
    with pytest.raises(SchemaValidationError):validate_ti_analysis(report)


def test_unknown_section_roles_are_not_silently_ignored():
    supplied=sections();supplied['extra']=supplied['data']
    with pytest.raises(ValueError):analyze_ti_sections(supplied,export_format='ti_section_files_v1')
    with pytest.raises(ValueError):analyze_ti_sections(sections(),export_format='native_pro')


def test_rules_and_sql_fallbacks_are_not_used(monkeypatch):
    import sqlglot
    import ssis_migration.adapters.tm1.rules as rules
    def forbidden(*a,**k):raise AssertionError('TI must not fall through to another language.')
    monkeypatch.setattr(sqlglot,'parse',forbidden);monkeypatch.setattr(rules,'analyze_rules',forbidden)
    assert analyze(data='n=1;')['status']=='STRUCTURED'


@pytest.mark.parametrize('change',['iteration','completion','ast_order'])
def test_rehashed_derived_fields_cannot_bypass_lifecycle_or_source_order(change):
    from ssis_migration.adapters.tm1.ti.control_flow import flatten, build_control_flow
    report=analyze(data='n=1; m=2;');section=report['sections'][2]
    if change=='iteration':section['iteration_scope']='NOMINAL_PROCESS_STAGE'
    elif change=='completion':
        section['structure_complete']=False;section['ast']=[];section['statements']=[];section['control_flow']=None
    else:
        section['ast'].reverse()
        section['statements']=flatten('data',section['ast']);section['control_flow']=build_control_flow('data',section['ast'])
    report['analysis_id']=ti_analysis_id(report)
    with pytest.raises(SchemaValidationError):validate_ti_analysis(report)


@pytest.mark.parametrize('body',[
    'IF(1);'*20+'n=1;'+'ENDIF;'*20,
    'n='+'('*55+'1'+')'*55+';',
    'n='+'+'.join('1' for _ in range(60))+';',
    'n=1;'*13_000,
    '#'+'x'*1_048_576,
])
def test_parser_limits_do_not_keep_a_valid_prefix(body):
    report=analyze(data=body)
    assert report['status']=='LIMIT_EXCEEDED'
    assert report['sections'][2]['ast']==[]


def test_empty_branch_and_empty_loop_retain_conditional_edges():
    report=analyze(data='IF(p=1); ELSE; ENDIF; WHILE(p=1); END;')
    assert report['status']=='STRUCTURED'
    edges=report['sections'][2]['control_flow']['edges']
    assert len([e for e in edges if e['kind']=='BRANCH'])==2
    loop=next(e for e in edges if e['kind']=='LOOP_ENTER')
    assert loop['source_id']==loop['target_id']


def test_nested_if_loop_paths_preserve_back_edges_and_unresolved_item_skip():
    report=analyze(data='WHILE(n<5); IF(p=1); ItemSkip; ELSE; n=n+1; ENDIF; END; nDone=1;')
    section=report['sections'][2]
    loop,branch,skip,increment,done=section['statements']
    edges=section['control_flow']['edges']
    assert report['status']=='STRUCTURED'
    assert any(e['source_id']==loop['statement_id'] and e['target_id']==branch['statement_id'] and e['kind']=='LOOP_ENTER' for e in edges)
    assert any(e['source_id']==increment['statement_id'] and e['target_id']==loop['statement_id'] and e['kind']=='LOOP_BACK' for e in edges)
    assert any(e['source_id']==loop['statement_id'] and e['target_id']==done['statement_id'] and e['kind']=='LOOP_EXIT' for e in edges)
    assert [e['target_id'] for e in edges if e['source_id']==skip['statement_id']]==[None]


@pytest.mark.parametrize('arguments,status', [
    ("'Product','',vProduct,'N'",'STRUCTURED'),
    ("'Product','',vProduct",'UNSUPPORTED'),
])
def test_dimension_element_insert_requires_explicit_element_type(arguments,status):
    report=analyze(metadata='DimensionElementInsert('+arguments+');')
    assert report['status']==status
    assert report['effects'][0]['kind']=='DIMENSION_MUTATION'


def test_missing_label_cannot_conceal_an_explicitly_supplied_empty_source():
    report=analyze()
    section=report['sections'][1]
    section.update(status='MISSING',structure_complete=False,control_flow=None)
    report.update(status='UNSUPPORTED',migration_strategy='UNSUPPORTED',missing_sections=['metadata'])
    report['analysis_id']=ti_analysis_id(report)
    with pytest.raises(SchemaValidationError):validate_ti_analysis(report)


def test_invalid_section_keeps_other_complete_sections_and_their_evidence():
    report=analyze(prolog='n=;',data="CellPutN(vAmount,'Sales',vProduct,'Amount');")
    assert report['status']=='INVALID'
    assert report['sections'][0]['source']['text']=='n=;'
    assert report['sections'][0]['ast']==[]
    assert report['sections'][2]['structure_complete']
    assert report['sections'][2]['status']=='STRUCTURED'
    effect,=report['effects']
    assert effect['kind']=='CUBE_WRITE' and effect['section']=='data'
    assert effect['evidence']['file_id']=='file-data'
