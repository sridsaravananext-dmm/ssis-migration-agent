import copy
import hashlib
import json
from pathlib import Path

import pytest

from ssis_migration.adapters.tm1.metadata import (
    bind_tm1_metadata, binding_report_id, metadata_snapshot, validate_metadata_snapshot,
    validate_tm1_binding_report,
)
from ssis_migration.adapters.tm1.rules import analyze_rules
from ssis_migration.adapters.tm1.ti import analyze_ti_sections
from ssis_migration.languages.mdx import analyze_mdx
from ssis_migration.schemas import SchemaValidationError

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def catalog():
    doc=json.loads((ROOT/'examples/tm1/metadata/synthetic_catalog.json').read_text())
    # A test-only declaration, never a claim of compatibility with a real server version.
    doc['source_export_version']='test-declared-version'
    return doc


def snapshot(doc, identity='metadata-1'):
    return metadata_snapshot(json.dumps(doc,ensure_ascii=False).encode(),file_id=identity)


def ti(prolog='',metadata='',data='',epilog='',name='SyntheticSalesLoad'):
    return analyze_ti_sections({key:{'text':value,'file_id':'source-'+key,'locator':'file:/'} for key,value in
                               [('prolog',prolog),('metadata',metadata),('data',data),('epilog',epilog)]},
                              export_format='ti_section_files_v1',process_name=name)


def rules(text="['Product':'A']=N:DB('Sales','B','Quantity');"):
    return analyze_rules(text,export_format='plain_rux_text',file_id='source-rules')


def mdx(text='SELECT {[Measures].[Measures].[Quantity]} ON 0 FROM [Sales] WHERE ([Product].[Product].[A]);'):
    return analyze_mdx(text,dialect='tm1',file_id='source-mdx')


def bindings(report,kind):
    return [b for b in report['bindings'] if b['kind']==kind]


def test_explicit_cube_order_members_and_original_reports_are_preserved(catalog):
    source=rules();original=copy.deepcopy(source);meta=snapshot(catalog);before=copy.deepcopy(meta)
    result=bind_tm1_metadata(source,[meta],source_cube='Sales')
    validate_tm1_binding_report(result)
    assert source==original and meta==before
    assert result['source_analysis']==source
    assert result['remaining_dependencies']==source['dependencies']
    assert result['status']=='BOUND_REVIEW_REQUIRED'
    assert result['semantic_status']=='NOT_VERIFIED' and result['migration_accepted'] is False
    assert all(d['status']=='NOT_SUPPLIED' for d in result['remaining_dependencies'])
    count,=bindings(result,'COORDINATE_COUNT')
    assert [a['dimension'] for a in count['details']['ordered_dimensions']]==['Product','Measures']
    assert [(b['requested']['hierarchy'],b['requested']['member']) for b in bindings(result,'MEMBER')]==[
        ('Product','B'),('Measures','Quantity')]
    assert 'critical-rule gates' in result['runtime_requirements'][-1]


def test_rules_source_cube_is_never_inferred_from_first_lookup_or_metadata(catalog):
    result=bind_tm1_metadata(rules(),[snapshot(catalog)])
    assert result['status']=='PARTIAL'
    source,=bindings(result,'RULE_SOURCE_CUBE')
    assert source['status']=='MISSING' and source['requested']['name'] is None
    assert bindings(result,'CALL_CUBE')[0]['status']=='MATCHED_DECLARATION'


def test_coordinate_count_mismatch_does_not_assign_partial_axes(catalog):
    result=bind_tm1_metadata(rules("['Product':'A']=N:DB('Sales','B');"),[snapshot(catalog)],source_cube='Sales')
    assert bindings(result,'COORDINATE_COUNT')[0]['status']=='CONFLICT'
    assert not bindings(result,'MEMBER')
    assert result['status']=='PARTIAL'


def test_reordered_cube_axes_change_coordinate_binding_without_changing_source(catalog):
    catalog['cubes'][0]['dimensions'].reverse()
    result=bind_tm1_metadata(rules(),[snapshot(catalog)],source_cube='Sales')
    assert [b['status'] for b in bindings(result,'MEMBER')]==['MISSING','MISSING']
    assert [b['requested']['dimension'] for b in bindings(result,'MEMBER')]==['Measures','Product']


def test_unqualified_area_member_ambiguity_is_preserved(catalog):
    catalog['dimensions'][1]['hierarchies'][0]['members'].append({'name':'A','type':'N','children':[]})
    result=bind_tm1_metadata(rules("['A']=N:1;"),[snapshot(catalog)],source_cube='Sales')
    area,=bindings(result,'AREA_MEMBER')
    assert area['status']=='AMBIGUOUS' and len(area['metadata_evidence'])==2


def test_repeated_dimension_in_area_is_a_conflict(catalog):
    result=bind_tm1_metadata(rules("['Product':'A','Product':'B']=N:1;"),[snapshot(catalog)],source_cube='Sales')
    assert [b['status'] for b in bindings(result,'AREA_MEMBER')]==['MATCHED_DECLARATION','CONFLICT']


def test_dynamic_cube_and_dimension_context_do_not_become_member_values(catalog):
    result=bind_tm1_metadata(rules("['Product':'A']=N:DB('Sales',!Product,'Quantity')+DB(DB('Sales','A','Label'),'A','Quantity');"),
                             [snapshot(catalog)],source_cube='Sales')
    assert bindings(result,'DIMENSION_CONTEXT')[0]['details']['member_value']=='DYNAMIC_NOT_EVALUATED'
    assert any(b['status']=='DYNAMIC' for b in bindings(result,'CALL_CUBE'))
    assert bindings(result,'COORDINATE')[0]['status']=='DYNAMIC'


def test_mdx_exact_path_bindings_do_not_award_semantic_acceptance(catalog):
    result=bind_tm1_metadata(mdx(),[snapshot(catalog)])
    assert result['status']=='BOUND_REVIEW_REQUIRED'
    assert [b['status'] for b in bindings(result,'MDX_PATH')]==['MATCHED_DECLARATION']*2
    assert result['migration_accepted'] is False


def test_mdx_hierarchy_member_and_alternate_hierarchy_ambiguity(catalog):
    dimension=catalog['dimensions'][0]
    dimension['hierarchies'].append({'name':'Alternate','default_member':None,'members':[{'name':'A','type':'N','children':[]}]})
    dimension['hierarchies'][0]['members'].append({'name':'Product','type':'N','children':[]})
    result=bind_tm1_metadata(mdx('SELECT {[Product].[A], [Product].[Product]} ON 0 FROM [Sales]'),[snapshot(catalog)])
    assert [b['status'] for b in bindings(result,'MDX_PATH')]==['AMBIGUOUS','AMBIGUOUS']
    assert all(len(b['metadata_evidence'])==2 for b in bindings(result,'MDX_PATH'))


@pytest.mark.parametrize('path,kind,status',[
    ('[Product].&[A]','MDX_PATH','UNSUPPORTED'),
    ('[Product].[Product].CurrentMember','MDX_PROPERTY_CONTEXT','DYNAMIC'),
    ('[Product].[Product].DefaultMember','MDX_DEFAULT_MEMBER','MATCHED_DECLARATION'),
    ('[Product].[Product].[Total].Children','MDX_PROPERTY_CONTEXT','UNSUPPORTED'),
])
def test_mdx_key_default_and_runtime_properties_are_distinct(catalog,path,kind,status):
    result=bind_tm1_metadata(mdx('SELECT {'+path+'} ON 0 FROM [Sales]'),[snapshot(catalog)])
    assert bindings(result,kind)[0]['status']==status


@pytest.mark.parametrize('scope,status',[(", 'public'",'MATCHED_DECLARATION'),('', 'DYNAMIC'),(", 'private'",'DYNAMIC')])
def test_subset_scope_and_private_owner_are_never_guessed(catalog,scope,status):
    source=mdx("SELECT TM1SubsetToSet([Product].[Product], 'Active'"+scope+") ON 0 FROM [Sales]")
    result=bind_tm1_metadata(source,[snapshot(catalog)])
    assert bindings(result,'MDX_SUBSET')[0]['status']==status


def test_dynamic_subset_definition_is_preserved_but_not_evaluated(catalog):
    catalog['subsets'][0].update(members=None,expression='{[Product].[Product].[A]}')
    result=bind_tm1_metadata(mdx("SELECT TM1SubsetToSet([Product].[Product], 'Active', 'public') ON 0 FROM [Sales]"),[snapshot(catalog)])
    subset,=bindings(result,'MDX_SUBSET')
    assert subset['status']=='DYNAMIC' and subset['metadata_evidence']
    assert result['metadata_inputs'][0]['document']['subsets'][0]['expression']=='{[Product].[Product].[A]}'


def test_ti_roles_and_columns_are_explicit_defaults_do_not_resolve_runtime_configuration(catalog):
    source=ti(prolog="DataSourceType='ASCII'; DataSourceNameForServer=pInputFile;",
              data="CellPutN(vQuantity,'Sales',vProduct,'Quantity'); nUnknown=0;")
    result=bind_tm1_metadata(source,[snapshot(catalog)])
    columns=bindings(result,'DATASOURCE_COLUMN')
    assert {b['requested']['column'] for b in columns}=={'Quantity','ProductCode'}
    assert all(b['status']=='MATCHED_DECLARATION' for b in columns)
    config=bindings(result,'DATASOURCE_CONFIGURATION')
    assert [b['status'] for b in config]==['MATCHED_DECLARATION','DYNAMIC']
    assert bindings(result,'COORDINATE')[0]['status']=='DYNAMIC'
    unknown=[b for b in bindings(result,'SYMBOL_DECLARATION') if b['requested']['name']=='nUnknown']
    assert unknown[0]['status']=='MISSING' and unknown[0]['details']['declared_roles']==[]
    assert result['source_analysis']['lifecycle_status']=='SOURCE_DEPENDENT_NOT_VERIFIED'


def test_empty_process_with_declared_none_datasource_still_requires_runtime_review(catalog):
    result=bind_tm1_metadata(ti(name='PrepareLoad'),[snapshot(catalog)])
    assert result['status']=='BOUND_REVIEW_REQUIRED'
    assert bindings(result,'PROCESS_DATASOURCE')[0]['status']=='MATCHED_DECLARATION'
    assert result['migration_accepted'] is False
    assert result['source_analysis']['sections'][1]['iteration_scope']=='SOURCE_DEPENDENT_SEPARATE_RECORD_PASS'


@pytest.mark.parametrize('body,status',[
    ("ExecuteProcess('PrepareLoad','pMode',pMode);",'MATCHED_DECLARATION'),
    ("ExecuteProcess('PrepareLoad','pMode',1);",'CONFLICT'),
    ("ExecuteProcess('PrepareLoad','pMode',pUnbound);",'DYNAMIC'),
    ("ExecuteProcess('PrepareLoad','missing','x');",'MISSING'),
])
def test_child_parameters_check_declared_types_without_running_processes(catalog,body,status):
    result=bind_tm1_metadata(ti(prolog=body),[snapshot(catalog)])
    assert bindings(result,'CHILD_PARAMETER')[0]['status']==status
    assert result['migration_accepted'] is False


def test_child_missing_duplicate_and_dynamic_parameter_names_remain_visible(catalog):
    source=ti(prolog="ExecuteProcess('PrepareLoad'); RunProcess('PrepareLoad','pMode','x','pMode','y'); ExecuteProcess('PrepareLoad',pMode,'x');")
    result=bind_tm1_metadata(source,[snapshot(catalog)])
    assert any(b['status']=='MISSING' for b in bindings(result,'OMITTED_CHILD_PARAMETER'))
    assert [b['status'] for b in bindings(result,'CHILD_PARAMETER')]==['MATCHED_DECLARATION','CONFLICT','DYNAMIC']
    assert any(b['status']=='DYNAMIC' for b in bindings(result,'OMITTED_CHILD_PARAMETER'))
    assert 'ASYNCHRONOUS_COMPLETION_REVIEW' in {d['kind'] for d in result['remaining_dependencies']}


def test_declared_process_and_explicit_context_conflict_is_not_overridden(catalog):
    result=bind_tm1_metadata(ti(),[snapshot(catalog)],process_name='PrepareLoad')
    assert bindings(result,'PROCESS')[0]['status']=='CONFLICT'
    assert bindings(result,'PROCESS_DATASOURCE')[0]['status']=='MISSING'


def test_process_context_is_required_when_source_process_name_is_unknown(catalog):
    missing=bind_tm1_metadata(ti(name=None),[snapshot(catalog)])
    assert bindings(missing,'PROCESS')[0]['status']=='MISSING'
    declared=bind_tm1_metadata(ti(name=None),[snapshot(catalog)],process_name='PrepareLoad')
    assert bindings(declared,'PROCESS')[0]['status']=='MATCHED_DECLARATION'


def test_exact_canonical_names_are_not_normalized_into_a_match(catalog):
    result=bind_tm1_metadata(mdx('SELECT {[product].[A]} ON 0 FROM [Sales]'),[snapshot(catalog)])
    assert bindings(result,'MDX_PATH')[0]['status']=='MISSING'


def test_dimension_mutations_are_bound_but_destructive_effects_stay_unverified(catalog):
    result=bind_tm1_metadata(ti(metadata="DimensionElementInsert('Product','',vProduct,'N');",epilog="CubeClearData('Sales');"),[snapshot(catalog)])
    assert bindings(result,'CALL_DIMENSION')[0]['status']=='MATCHED_DECLARATION'
    assert bindings(result,'CALL_CUBE')[0]['status']=='MATCHED_DECLARATION'
    assert result['source_analysis']['effects'][-1]['requires_redesign'] is True


@pytest.mark.parametrize('mutation,code',[
    ('cycle','CONSOLIDATION_CYCLE'),('missing_child','INVALID_CONSOLIDATION_CHILD'),
    ('leaf_children','NON_CONSOLIDATED_CHILDREN'),('bad_default','INVALID_DEFAULT_MEMBER'),
    ('missing_hierarchy','INVALID_CUBE_DIMENSION'),('repeated_axis','INVALID_CUBE_DIMENSION'),
    ('column_type','INVALID_DATASOURCE_COLUMN_BINDING'),('missing_column','INVALID_DATASOURCE_COLUMN_BINDING'),
    ('variable_role','INVALID_VARIABLE_COLUMN_ROLE'),('param_collision','PARAMETER_VARIABLE_COLLISION'),
    ('unknown_datasource','INVALID_PROCESS_DATASOURCE'),('none_with_columns','CONFLICTING_NONE_DATASOURCE'),
    ('missing_subset_member','INVALID_SUBSET_MEMBER'),('missing_subset_hierarchy','INVALID_SUBSET_HIERARCHY'),
    ('bad_cubeview','INVALID_CUBE_VIEW_DATASOURCE'),
])
def test_catalog_conflicts_block_binding_with_original_pointer_evidence(catalog,mutation,code):
    hierarchy=catalog['dimensions'][0]['hierarchies'][0]
    if mutation=='cycle':hierarchy['members'][2]['children'].append({'member':'Total','weight':'1'})
    elif mutation=='missing_child':hierarchy['members'][2]['children'].append({'member':'Ghost','weight':'1'})
    elif mutation=='leaf_children':hierarchy['members'][0]['children']=[{'member':'B','weight':'1'}]
    elif mutation=='bad_default':hierarchy['default_member']='Ghost'
    elif mutation=='missing_hierarchy':catalog['cubes'][0]['dimensions'][0]['hierarchy']='Ghost'
    elif mutation=='repeated_axis':catalog['cubes'][0]['dimensions'].append(catalog['cubes'][0]['dimensions'][0].copy())
    elif mutation=='column_type':catalog['datasources'][0]['columns'][1]['data_type']='STRING'
    elif mutation=='missing_column':catalog['processes'][0]['variables'][-1]['column']='Ghost'
    elif mutation=='variable_role':catalog['processes'][0]['variables'][-1]['role']='LOCAL'
    elif mutation=='param_collision':catalog['processes'][0]['parameters'].append({'name':'vQuantity','data_type':'NUMERIC','required':True})
    elif mutation=='unknown_datasource':catalog['processes'][0]['datasource']='Ghost'
    elif mutation=='none_with_columns':catalog['datasources'][1]['columns']=[{'name':'Ghost','data_type':'STRING'}]
    elif mutation=='missing_subset_member':catalog['subsets'][0]['members'].append('Ghost')
    elif mutation=='missing_subset_hierarchy':catalog['subsets'][0]['hierarchy']='Ghost'
    elif mutation=='bad_cubeview':catalog['datasources'][0]['kind']='CUBE_VIEW'
    result=bind_tm1_metadata(ti(),[snapshot(catalog)])
    assert result['status']=='BLOCKED'
    issue=next(i for i in result['metadata_issues'] if i['code']==code)
    assert issue['evidence'] and all(e['file_id']=='metadata-1' and e['locator'].startswith('json:/') for e in issue['evidence'])
    assert not any(b['status']=='MATCHED_DECLARATION' for b in result['bindings'])


def test_cross_file_duplicate_definitions_are_ambiguous_even_if_identical(catalog):
    result=bind_tm1_metadata(mdx(),[snapshot(catalog,'one'),snapshot(catalog,'two')])
    assert result['status']=='BLOCKED'
    cube,=bindings(result,'CUBE')
    assert cube['status']=='AMBIGUOUS' and len(cube['metadata_evidence'])==2
    assert {e['file_id'] for e in cube['metadata_evidence']}=={'one','two'}


@pytest.mark.parametrize('field,code',[('source_system','CONFLICTING_SOURCE_SYSTEMS'),('source_export_version','CONFLICTING_SOURCE_VERSIONS')])
def test_incompatible_catalogs_are_never_merged_silently(catalog,field,code):
    other=copy.deepcopy(catalog);other[field]='different-source-declaration'
    result=bind_tm1_metadata(mdx(),[snapshot(catalog,'one'),snapshot(other,'two')])
    assert result['status']=='BLOCKED'
    assert any(i['code']==code and len(i['evidence'])==2 for i in result['metadata_issues'])


def test_missing_version_and_empty_metadata_stay_partial(catalog):
    catalog['source_export_version']=None
    result=bind_tm1_metadata(mdx(),[snapshot(catalog)])
    assert bindings(result,'SOURCE_VERSION')[0]['status']=='MISSING'
    empty=bind_tm1_metadata(mdx(),[])
    assert empty['status']=='PARTIAL' and all(b['status']=='MISSING' for b in empty['bindings'])


def test_version_conflict_with_original_ti_declaration_is_explicit(catalog):
    from ssis_migration.adapters.tm1.ti import ti_analysis_id
    source=ti();source['source_export_version']='different-declared-version';source['analysis_id']=ti_analysis_id(source)
    result=bind_tm1_metadata(source,[snapshot(catalog)])
    assert bindings(result,'SOURCE_VERSION')[0]['status']=='CONFLICT'


def test_multiple_odbc_mappings_cannot_choose_first_or_infer_sql(catalog,monkeypatch):
    import sqlglot
    def forbidden(*args,**kwargs):raise AssertionError('Binding must not parse or execute SQL.')
    monkeypatch.setattr(sqlglot,'parse',forbidden)
    base=catalog['datasources'][0]
    for name in ['First','Second']:
        ds=copy.deepcopy(base);ds.update(name=name,kind='ODBC',connection_reference='warehouse',sql_dialect='plsql')
        catalog['datasources'].append(ds)
    result=bind_tm1_metadata(ti(prolog="ODBCOutput('warehouse', 'DELETE FROM T');"),[snapshot(catalog)])
    connection,=bindings(result,'CONNECTION_MAPPING')
    assert connection['status']=='AMBIGUOUS' and connection['details']['sql_not_parsed_or_executed']


@pytest.mark.parametrize('change',['binding','omit','pointer','source_pointer','status','dependency','runtime','accepted','metadata_document'])
def test_rehashed_changes_cannot_fabricate_binding_or_drop_unresolved_requirements(catalog,change):
    result=bind_tm1_metadata(rules(),[snapshot(catalog)],source_cube='Sales')
    if change=='binding':result['bindings'][1]['status']='MISSING'
    elif change=='omit':result['bindings'].pop()
    elif change=='pointer':result['bindings'][1]['metadata_evidence'][0]['locator']='json:/cubes/999'
    elif change=='source_pointer':next(b for b in result['bindings'] if b['source_evidence'])['source_evidence']['file_id']='invented'
    elif change=='status':result['status']='BLOCKED'
    elif change=='dependency':result['remaining_dependencies'].pop()
    elif change=='runtime':result['runtime_requirements']=['All verified']
    elif change=='accepted':result['migration_accepted']=True
    else:result['metadata_inputs'][0]['document']['cubes'][0]['name']='Invented'
    result['binding_report_id']=binding_report_id(result)
    with pytest.raises((SchemaValidationError,ValueError)):
        validate_tm1_binding_report(result)


def test_bom_and_decimal_weight_spelling_are_preserved(catalog):
    catalog['dimensions'][0]['hierarchies'][0]['members'][2]['children'][0]['weight']='1.000e+0'
    raw=b'\xef\xbb\xbf'+json.dumps(catalog,ensure_ascii=False).encode()
    s=metadata_snapshot(raw,file_id='bom')
    validate_metadata_snapshot(s)
    assert s['source']['sha256']==hashlib.sha256(raw).hexdigest()
    assert s['document']['dimensions'][0]['hierarchies'][0]['members'][2]['children'][0]['weight']=='1.000e+0'


@pytest.mark.parametrize('mutation',['format','unknown_field','parameter_type','private_owner','numeric_weight','dual_subset'])
def test_unsupported_or_invalid_metadata_shapes_are_rejected(catalog,mutation):
    if mutation=='format':catalog['export_format']='native_tm1_export'
    elif mutation=='unknown_field':catalog['invented_schema']={}
    elif mutation=='parameter_type':catalog['processes'][0]['parameters'][0]['default']=123
    elif mutation=='private_owner':catalog['subsets'][0]['scope']='private'
    elif mutation=='numeric_weight':catalog['dimensions'][0]['hierarchies'][0]['members'][2]['children'][0]['weight']=1.0
    else:catalog['subsets'][0]['expression']='{[A]}'
    with pytest.raises(SchemaValidationError):snapshot(catalog)


@pytest.mark.parametrize('raw',[b'{"dimensions":[],"dimensions":[]}',b'{"value":NaN}',b'{"value":Infinity}',b'{"value":1e9999}',b'\xff'])
def test_strict_json_never_chooses_duplicate_or_nonfinite_values(raw):
    with pytest.raises(ValueError):metadata_snapshot(raw,file_id='bad')


def test_metadata_limits_identity_collisions_and_wrong_context_are_rejected(catalog):
    with pytest.raises(ValueError,match='8 MiB'):metadata_snapshot(b' '*8_388_609,file_id='large')
    with pytest.raises(ValueError):bind_tm1_metadata(mdx(),[snapshot(catalog)]*33)
    with pytest.raises(ValueError,match='identity'):bind_tm1_metadata(mdx(),[snapshot(catalog,'source-mdx')])
    with pytest.raises(ValueError,match='identity'):bind_tm1_metadata(mdx(),[snapshot(catalog),snapshot(catalog)])
    with pytest.raises(ValueError):bind_tm1_metadata(mdx(),[],source_cube='Guess')
    with pytest.raises(ValueError):bind_tm1_metadata(rules(),[],process_name='Guess')


def test_invalid_source_analysis_cannot_be_laundered_by_complete_metadata(catalog):
    source=ti(data='n=;')
    result=bind_tm1_metadata(source,[snapshot(catalog)])
    assert result['status']=='BLOCKED'
    assert result['source_analysis']['status']=='INVALID'


def test_odbc_dsn_and_declared_dialect_bind_without_using_a_file_location(catalog):
    ds=catalog['datasources'][0]
    ds.update(kind='ODBC',location_reference=None,connection_reference='warehouse',sql_dialect='plsql')
    source=ti(prolog="DataSourceNameForServer='warehouse'; ODBCOutput('warehouse','DELETE FROM T');")
    result=bind_tm1_metadata(source,[snapshot(catalog)])
    assert bindings(result,'DATASOURCE_CONFIGURATION')[0]['status']=='MATCHED_DECLARATION'
    assert bindings(result,'DATASOURCE_MAPPING')[0]['status']=='MATCHED_DECLARATION'
    mapping,=bindings(result,'CONNECTION_MAPPING')
    assert mapping['status']=='MATCHED_DECLARATION' and mapping['details']['sql_dialects']==['plsql']
    assert all(d['status']=='NOT_SUPPLIED' for d in result['remaining_dependencies'])


@pytest.mark.parametrize('missing',['location_reference','columns'])
def test_missing_datasource_descriptor_fields_are_reported(catalog,missing):
    catalog['datasources'][0][missing]=None if missing!='columns' else []
    # A process with no row variables isolates the datasource descriptor check.
    catalog['processes'][0]['variables']=[]
    result=bind_tm1_metadata(ti(),[snapshot(catalog)])
    mapping,=bindings(result,'DATASOURCE_MAPPING')
    assert mapping['status']=='MISSING' and missing in mapping['details']['missing_fields']


def test_server_location_never_supplies_an_undeclared_client_location(catalog):
    catalog['processes'][0]['variables'].append({'name':'DataSourceNameForClient','data_type':'STRING','role':'SYSTEM','column':None})
    source=ti(prolog="DataSourceNameForClient='/legacy/input.csv';")
    result=bind_tm1_metadata(source,[snapshot(catalog)])
    assert bindings(result,'DATASOURCE_CONFIGURATION')[0]['status']=='MISSING'
    catalog['datasources'][0]['client_location_reference']='/legacy/input.csv'
    result=bind_tm1_metadata(source,[snapshot(catalog)])
    assert bindings(result,'DATASOURCE_CONFIGURATION')[0]['status']=='MATCHED_DECLARATION'


def test_valid_cube_view_mapping_still_requires_the_view_definition(catalog):
    catalog['datasources'][0].update(kind='CUBE_VIEW',location_reference=None,cube='Sales',view='InputView')
    result=bind_tm1_metadata(ti(),[snapshot(catalog)])
    mapping,=bindings(result,'DATASOURCE_MAPPING')
    assert result['status']=='PARTIAL' and mapping['status']=='DYNAMIC'
    assert mapping['details']['view_definition_not_supplied'] is True
