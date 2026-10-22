import copy
import hashlib
import json
from pathlib import Path

import pytest

from ssis_migration.adapters import get_adapter
from ssis_migration.adapters.base import SourceEvidenceError, UnsupportedSourceError
from ssis_migration.adapters.export_pipeline import validate_export_review
from ssis_migration.adapters.xml_export import validate_export_graph
from ssis_migration.ir.normalized import normalized_ir_id
from ssis_migration.migrate import run_migration
from ssis_migration.schemas import SchemaValidationError
from ssis_migration.validator.acceptance import load_package

ROOT = Path(__file__).resolve().parents[2]
POWER = ROOT/'examples/informatica/simple_mapping.xml'
BODS = ROOT/'examples/bods/structural_job.xml'
ODI = ROOT/'examples/odi/structural_export.xml'


def test_powercenter_native_graph_resolves_scoped_links_and_retains_columns():
    result = get_adapter('powercenter').analyze(POWER)
    graph = result.normalized_ir
    validate_export_graph(graph)
    assert result.package_ir is None
    assert graph['status'] == 'REVIEW_REQUIRED'
    assert graph['source']['export_version'] == '188.95'
    assert len([e for e in graph['edges'] if e['kind'] == 'DATA_FLOW']) == 1
    assert len([e for e in graph['edges'] if e['kind'] == 'CONTROL_FLOW']) == 1
    assert all(e['resolution'] == 'RESOLVED' for e in graph['edges'])
    assert len([n for n in graph['nodes'] if n['operation'] == 'COLUMN']) == 2
    assert graph['extensions']['source_analysis']['source_snapshot']['sha256'] == hashlib.sha256(POWER.read_bytes()).hexdigest()
    assert any(n['attributes'].get('condition') == 'TRUE' for n in graph['nodes'])


def test_powercenter_missing_port_and_definition_block_without_dropping_connector(tmp_path):
    path = tmp_path/'broken.xml'
    path.write_text(POWER.read_text().replace('TOFIELD="CUSTOMER_ID"', 'TOFIELD="MISSING"').replace('TRANSFORMATION_NAME="SRC_CUSTOMER"', 'TRANSFORMATION_NAME="ABSENT"'))
    graph = get_adapter('informatica').analyze(path).normalized_ir
    assert graph['status'] == 'BLOCKED'
    assert {'UNRESOLVED_PORT','UNRESOLVED_GRAPH_REFERENCE'} <= {d['code'] for d in graph['diagnostics']}
    assert len([n for n in graph['nodes'] if n['operation'] == 'COLUMN_CONNECTOR']) == 1


def test_powercenter_duplicate_instance_is_ambiguous(tmp_path):
    path = tmp_path/'duplicate.xml'
    path.write_text(POWER.read_text().replace('<CONNECTOR ', '<INSTANCE NAME="src" TYPE="SOURCE" TRANSFORMATION_NAME="SRC_CUSTOMER"/><CONNECTOR '))
    graph = get_adapter('informatica').analyze(path).normalized_ir
    assert any(e['kind'] == 'DATA_FLOW' and e['source_id'] is None for e in graph['edges'])


def test_powercenter_unknown_construct_is_a_retained_blocker(tmp_path):
    path = tmp_path/'unknown.xml'
    path.write_text(POWER.read_text().replace('</MAPPING>', '<SHORTCUT NAME="remote" REFOBJECTNAME="hidden"/></MAPPING>'))
    graph = get_adapter('informatica').analyze(path).normalized_ir
    assert graph['status'] == 'BLOCKED'
    assert any(n['operation'] == 'SHORTCUT' for n in graph['nodes'])
    validate_export_graph(graph)


@pytest.mark.parametrize('change', ['remove_column','remove_link','change_condition','upgrade_status','change_snapshot'])
def test_structural_replay_rejects_self_rehashed_tampering(change):
    graph = copy.deepcopy(get_adapter('informatica').analyze(POWER).normalized_ir)
    if change == 'remove_column':
        column = next(n for n in graph['nodes'] if n['operation'] == 'COLUMN')
        graph['nodes'].remove(column)
        graph['edges'] = [e for e in graph['edges'] if column['node_id'] not in (e['source_id'],e['target_id'])]
    elif change == 'remove_link': graph['edges'] = [e for e in graph['edges'] if e['kind'] != 'CONTROL_FLOW']
    elif change == 'change_condition': next(e for e in graph['edges'] if e['kind'] == 'CONTROL_FLOW')['attributes']['condition'] = 'FALSE'
    elif change == 'upgrade_status': graph['status'] = 'ACCEPTED'
    else: graph['extensions']['source_analysis']['source_snapshot']['text'] += ' '
    graph['ir_id'] = normalized_ir_id(graph)
    with pytest.raises((SchemaValidationError, ValueError)): validate_export_graph(graph)


def test_xml_pipeline_preserves_all_obligations_and_never_accepts(tmp_path):
    report = run_migration(str(POWER),str(tmp_path))
    assert report['source_platform'] == 'informatica' and report['status'] == 'BLOCKED'
    assert report['execution_status'] == 'NOT_RUN' and not report['migration_accepted']
    assert report['coverage']['semantic']['verified_percent'] is None
    artifacts = {key: json.loads(Path(path).read_text()) for key,path in report['artifacts'].items()}
    validate_export_review(artifacts['source_envelope'],artifacts['normalized_ir'],artifacts['migration_plan'],artifacts['migration_contracts']['contracts'])
    binding = report['preservation']
    assert binding['node_count'] == binding['assigned_node_count'] > 0
    assert binding['edge_count'] == binding['assigned_edge_count'] > 0
    assert not list(tmp_path.rglob('*.ipynb'))
    with pytest.raises(ValueError,match='cannot be accepted'): load_package(Path(report['artifacts']['migration_report']).parent)


@pytest.mark.parametrize('option',[{'ai': True},{'execute': True},{'dependency_files':['absent.json']},{'procedures_path':'absent.sql'},{'target_mapping':'absent.json'}])
def test_native_xml_does_not_silently_ignore_unsupported_options(tmp_path,option):
    with pytest.raises(ValueError): run_migration(str(POWER),str(tmp_path),**option)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('text', ['<POWERMART><!ENTITY bad "x"></POWERMART>', '<!DOCTYPE POWERMART [<!ENTITY x "abc">]><POWERMART/>', '<SunopsisExport/>', '<POWERMART>'])
def test_powercenter_rejects_wrong_format_entities_and_malformed_xml(tmp_path,text):
    path = tmp_path/'input.xml'; path.write_text(text)
    with pytest.raises(UnsupportedSourceError): get_adapter('informatica').analyze(path)


def test_xml_evidence_rejects_symlinks_and_stale_hash(tmp_path):
    path = tmp_path/'link.xml'; path.symlink_to(POWER)
    with pytest.raises(SourceEvidenceError): get_adapter('informatica').analyze(path)
    with pytest.raises(SourceEvidenceError): get_adapter('informatica').analyze(POWER,file_record={'sha256':'0'*64})


def test_native_adapter_reuses_confirmed_intake_identity_without_reinterpreting_roles():
    from ssis_migration.intake import build_intake_manifest
    intake=build_intake_manifest(POWER,role_overrides={str(POWER):'package'})
    record=intake['files'][0]
    result=get_adapter('informatica').analyze(POWER,file_record=record)
    assert result.source_envelope['evidence_files']==[record]
    validate_export_graph(result.normalized_ir)


def test_intake_does_not_expand_entities_before_native_adapter_dispatch(tmp_path,monkeypatch):
    from ssis_migration.intake import describe_file
    import ssis_migration.intake.inventory as inventory
    def forbidden(*args,**kwargs):raise AssertionError('Entity-bearing XML reached the inventory parser.')
    monkeypatch.setattr(inventory.ET,'fromstring',forbidden)
    path=tmp_path/'source.xml'
    path.write_text('<!DOCTYPE POWERMART [<!ENTITY text "export">]><POWERMART>&text;</POWERMART>')
    record=describe_file(path)
    assert record['platform']=='unknown'


def test_declaration_like_comments_and_cdata_remain_source_text(tmp_path):
    from ssis_migration.intake import describe_file
    path=tmp_path/'source.xml'
    path.write_text('<POWERMART><!-- <!ENTITY sample> --><MAPPING NAME="m"><ATTRIBUTE NAME="literal"><![CDATA[<!DOCTYPE example [ <!ENTITY sample>]]></ATTRIBUTE></MAPPING></POWERMART>')
    assert describe_file(path)['platform']=='informatica'
    graph=get_adapter('informatica').analyze(path).normalized_ir
    validate_export_graph(graph)
    assert any('<!DOCTYPE example [' in n['attributes']['export_text'] for n in graph['nodes'])


def test_bods_resolves_only_explicit_identifiers_and_preserves_script_language():
    graph = get_adapter('sap_bods').analyze(BODS).normalized_ir
    validate_export_graph(graph)
    assert graph['status'] == 'REVIEW_REQUIRED'
    assert len([e for e in graph['edges'] if e['kind'] == 'DECLARED_RELATION']) == 2
    assert len([e for e in graph['edges'] if e['kind'] == 'USES_CONNECTION']) == 2
    assert not [e for e in graph['edges'] if e['kind'] == 'CONTROL_FLOW']
    assert next(n for n in graph['nodes'] if n['operation'] == 'QUERY')['attributes']['language'] == 'SQL'


@pytest.mark.parametrize('change', ['missing','duplicate','unknown','link_encoding'])
def test_bods_unresolved_and_unsupported_behavior_is_retained(tmp_path,change):
    text = BODS.read_text()
    if change == 'missing': text = text.replace('datastoreId="warehouse"','datastoreId="absent"')
    elif change == 'duplicate': text = text.replace('</DataServices>','<DIDatastore id="warehouse" name="Other"/></DataServices>')
    elif change == 'unknown': text = text.replace('</DIDataflow>','<DIMagicTransform name="magic"/></DIDataflow>')
    else: text = text.replace('sourceId="src"','source="CustomerSource"')
    path = tmp_path/'source.xml';path.write_text(text)
    graph = get_adapter('bods').analyze(path).normalized_ir
    assert graph['status'] == 'BLOCKED'
    validate_export_graph(graph)


def test_bods_atl_is_not_misparsed_as_xml(tmp_path):
    path = tmp_path/'source.atl';path.write_text('CREATE WORKFLOW Example() BEGIN END')
    with pytest.raises(UnsupportedSourceError):get_adapter('bods').analyze(path)


def test_bods_pipeline_returns_neutral_review_not_ssis(tmp_path):
    report = run_migration(str(BODS),str(tmp_path),source_platform='sap_bods')
    assert report['source_platform'] == 'bods' and report['status'] == 'BLOCKED'
    assert report['preservation']['node_count'] == report['preservation']['assigned_node_count']


def test_odi_declared_class_keys_resolve_without_sql_or_execution_inference(monkeypatch):
    import sqlglot
    def forbidden(*args,**kwargs):raise AssertionError('ODI field text has no confirmed SQL dialect.')
    monkeypatch.setattr(sqlglot,'parse',forbidden)
    graph = get_adapter('oracle_data_integrator').analyze(ODI).normalized_ir
    validate_export_graph(graph)
    assert graph['status'] == 'REVIEW_REQUIRED'
    assert graph['source']['export_version'] == '05.02.02.08'
    refs = [e for e in graph['edges'] if e['kind'] == 'REQUIRES']
    assert len(refs) == 2 and all(e['resolution']=='RESOLVED' for e in refs)
    assert not [e for e in graph['edges'] if e['kind'] in {'CONTROL_FLOW','DATA_FLOW'}]
    assert any(n['attributes'].get('value') == 'SELECT CUSTOMER_ID FROM CUSTOMER' for n in graph['nodes'])


@pytest.mark.parametrize('change',['missing','duplicate','unknown','duplicate_field','missing_key'])
def test_odi_missing_ambiguous_and_unknown_objects_remain_blockers(tmp_path,change):
    text = ODI.read_text()
    if change == 'missing':text = text.replace('>10</Field>\n    <Field name="PopName"','>99</Field>\n    <Field name="PopName"')
    elif change == 'duplicate':text = text.replace('</SunopsisExport>','<Object class="com.sunopsis.dwg.dbobj.SnpFolder"><Field name="IFolder">10</Field></Object></SunopsisExport>')
    elif change == 'unknown':text = text.replace('SnpPop"','SnpUnverifiedMapping"')
    elif change == 'duplicate_field':text = text.replace('<Field name="ProjectName"','<Field name="IProject">2</Field><Field name="ProjectName"')
    else:text = text.replace('name="IProject" type="java.lang.Number">1','name="OtherKey" type="java.lang.Number">1',1)
    path = tmp_path/'source.xml';path.write_text(text)
    graph = get_adapter('odi').analyze(path).normalized_ir
    assert graph['status'] == 'BLOCKED'
    validate_export_graph(graph)


def test_odi_pipeline_preserves_all_source_obligations(tmp_path):
    report = run_migration(str(ODI),str(tmp_path))
    assert report['source_platform'] == 'odi' and not report['migration_accepted']
    assert report['preservation']['edge_count'] == report['preservation']['assigned_edge_count']
