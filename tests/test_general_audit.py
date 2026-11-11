"""Synthetic packages exercise extension contracts rather than customer names."""
import json
from pathlib import Path
import pytest

from ssis_migration.audit_contract import RUN_TYPES, STATS_TYPES, storage_contract
from ssis_migration.config import AssessmentConfig
from ssis_migration.intake import stage_packages
from ssis_migration.ir.builder import IRBuilder
from ssis_migration.ir.models import ExecutableIR, PackageIR, VariableIR
from ssis_migration.migrate import generate_migration_artifacts, run_migration
from ssis_migration.parser.package_parser import SSISPackageParser
from ssis_migration.validator.native_spark import validate_native_spark
from tests.test_target_audit import cfg, task, plan, Frame, Spark, execute


def test_custom_signatures_work_when_builtin_profile_is_disabled():
    ir=PackageIR(package_name='Different',executables=[
        task('opaque-1',"INSERT INTO telemetry.History (FlowCode, StartedAt) VALUES ('x', CURRENT_TIMESTAMP)"),
        task('opaque-2',"INSERT INTO business.Rows (TableProcessKey,BranchName,ProcessingSummaryGroup) VALUES (1,'b','g')")])
    p=plan(ir,cfg(source_audit_rules={'use_builtin_signatures':False,
        'insert_signatures':[{'role':'execution','columns':['FlowCode','StartedAt']}]}))
    assert p.metadata['target_audit']['replaced_sources']==['opaque-1']
    assert any(i['source_id']=='opaque-2' and i['audit_disposition'] is None for i in p.metadata['source_inventory'])


def test_explicit_objects_support_update_only_layouts_with_connection_isolation():
    ir=PackageIR(package_name='Other',metadata={'connection_aliases':{'abc':'audit-connection'}},executables=[
        task('audit','UPDATE ops.RunState SET flag=1',Connection='abc'),
        task('business','UPDATE ops.RunState SET flag=2',Connection='business-connection')])
    p=plan(ir,cfg(source_audit_rules={'use_builtin_signatures':False,
        'objects':[{'role':'execution','name':'ops.RunState','connection':'audit-connection'}]}))
    assert p.metadata['target_audit']['replaced_sources']==['audit']
    assert p.metadata['target_audit']['audit_table_contracts'][0]['connection']=='audit-connection'
    assert not any(f['code']=='SOURCE_AUDIT_IN_BUSINESS_TASK' for f in p.unresolved)


def test_package_scoped_explicit_procedure_rule_keeps_business_output_bridge():
    ir=PackageIR(package_name='One',variables=[VariableIR(name='Key',namespace='User',scope_ref_id='Package',data_type='20',value='-1')],
        executables=[task('audit','EXEC ops.UnknownAudit',parameter_bindings=[
            {'ParameterName':'key','ParameterDirection':'Output','DtsVariableName':'User::Key'}]),
            task('main','SELECT @key AS result',parameter_bindings=[{'ParameterName':'key','DtsVariableName':'User::Key'}])])
    config=cfg(source_audit_rules={'tasks':[{'package':'One','source_id':'audit','role':'execution'}]})
    p=plan(ir,config)
    assert p.metadata['target_audit']['replaced_sources']==['audit']
    assert 'User::Key' not in p.metadata['runtime_defaults']
    assert any(f['code']=='TARGET_AUDIT_VALUE_BINDING_REQUIRED' for f in p.unresolved)
    assert not any(f['code']=='MISSING_PROCEDURE_DEFINITION' for f in p.unresolved)
    ir.package_name='Two'
    p=plan(ir,config)
    assert not p.metadata['target_audit']['replaced_sources']
    assert any(f['code']=='MISSING_PROCEDURE_DEFINITION' for f in p.unresolved)


def test_opaque_audit_task_requires_output_declaration_and_preserves_bridge():
    ir=PackageIR(package_name='P',variables=[VariableIR(name='Key',namespace='User',scope_ref_id='Package',data_type='20')],
        executables=[ExecutableIR(ref_id='opaque',executable_type='Microsoft.ScriptTask'),
            task('main','SELECT @key',parameter_bindings=[{'ParameterName':'key','DtsVariableName':'User::Key'}])])
    rule={'source_id':'opaque','role':'execution'}
    p=plan(ir,cfg(source_audit_rules={'tasks':[rule]}))
    assert not p.metadata['target_audit']['replaced_sources']
    assert any(f['code']=='SOURCE_AUDIT_RULE_OUTPUTS_REQUIRED' for f in p.unresolved)
    p=plan(ir,cfg(source_audit_rules={'tasks':[{**rule,'outputs':['User::Key']}]}))
    assert p.metadata['target_audit']['replaced_sources']==['opaque']
    assert any(f['code']=='TARGET_AUDIT_VALUE_BINDING_REQUIRED' for f in p.unresolved)
    assert p.metadata['target_audit']['replacements'][0]['output_variables']==['User::Key']


def test_rules_cannot_remove_containers_or_hide_misspelled_source_references():
    ir=PackageIR(executables=[ExecutableIR(ref_id='group',executable_type='STOCK:SEQUENCE'),
        task('child','SELECT 1',_parent_ref_id='group')])
    p=plan(ir,cfg(source_audit_rules={'tasks':[{'source_id':r,'role':'execution'} for r in ('group','missing')]}))
    assert not p.metadata['target_audit']['replaced_sources']
    assert {'SOURCE_AUDIT_RULE_NONLEAF','SOURCE_AUDIT_RULE_UNRESOLVED'} <= {f['code'] for f in p.unresolved}


CUSTOM={
    'notebook_table':'Run ledger', 'table_stats_table':'Table ledger',
    'notebook_columns':{**{r:'run '+r for r in RUN_TYPES},'duration_time':None,'source_file_path':None,'target_table':None,'job_id':None,'job_name':None},
    'table_stats_columns':{**{r:'stat '+r for r in STATS_TYPES},'job_id':None,'job_name':None},
    'statuses':{'Running':'IN_PROGRESS','Success':"DONE 'OK'",'Error':'FAILED'}
}


class MappedSpark(Spark):
    def table(self,name):
        contract=storage_contract(CUSTOM)
        roles=RUN_TYPES if 'Run ledger' in name else STATS_TYPES if 'Table ledger' in name else {}
        aliases=contract['notebook_columns'] if 'Run ledger' in name else contract['table_stats_columns']
        return Frame(types={aliases[k]:v for k,v in roles.items() if aliases[k] is not None},count=self.rows)
    def sql(self,sql,args=None):
        if sql.startswith('SELECT `run master_key`'):
            self.calls.append((sql,args))
            return Frame([{**self.parent,'status':CUSTOM['statuses']['Running']}])
        if sql.startswith('UPDATE') and 'Run ledger' in sql:
            self.calls.append((sql,args))
            if self.fail_audit:raise RuntimeError('mapped audit failed')
            return Frame()
        return super().sql(sql,args)


@pytest.mark.parametrize('attached',[False,True])
def test_custom_storage_columns_tables_statuses_and_optional_omissions(tmp_path,attached):
    ir=PackageIR(package_name='Another',executables=[task('main','INSERT INTO dbo.Business (id) VALUES (1)')])
    result=generate_migration_artifacts(ir,{},None,'.',tmp_path,config=cfg(
        object_mappings={'dbo.Business':'c.data.business'},audit_contract=CUSTOM))
    assert not [f for f in result['findings'] if f['severity']=='ERROR']
    n=json.loads(Path(result['notebook_file']).read_text())
    spark=MappedSpark();env,exits=execute(n,spark,execution_key='41' if attached else '')
    run_inserts=[(sql,args) for sql,args in spark.calls if sql.startswith('INSERT') and 'Run ledger' in sql]
    assert len(run_inserts)==(0 if attached else 1)
    if not attached:
        sql,args=run_inserts[0]
        assert '`run notebook_key`' in sql and args['status']=='IN_PROGRESS'
        assert 'duration_time' not in args and 'job_id' not in args
    updates=[(sql,args) for sql,args in spark.calls if sql.startswith('UPDATE')]
    assert updates[-1][1]['status']=="DONE 'OK'" and 'unix_millis' not in updates[-1][0]
    stats=next((sql,args) for sql,args in spark.calls if sql.startswith('INSERT') and 'Table ledger' in sql)
    assert '`stat before_row_count`' in stats[0] and stats[1]['after_row_count']==11
    assert env['_ta_finished_status']=='Success' and exits==(['[$OK][$STR]'] if attached else [])
    assert all(validate_native_spark(''.join(c['source']))['status']=='PASS' for c in n['cells'] if c['cell_type']=='code')


@pytest.mark.parametrize('contract',[
    {'notebook_columns':{'notebook_key':None}},
    {'notebook_columns':{'notebook_key':'same','master_key':'SAME'}},
    {'statuses':{'Running':'same','Success':'same'}},
    {'notebook_table':'audit_tables'},
    {'notebook_columns':{'unknown':'x'}},
])
def test_invalid_storage_contracts_are_rejected_before_generation(contract):
    with pytest.raises(ValueError):cfg(audit_contract=contract)


def test_overlapping_task_rules_and_invalid_top_level_api_contracts_fail_early(tmp_path):
    with pytest.raises(ValueError,match='Overlapping'):
        cfg(source_audit_rules={'tasks':[{'role':'execution','source_id':'T'},
            {'role':'execution','source_id':'T','package':'P'}]})
    with pytest.raises(ValueError,match='target audit contract'):
        run_migration(tmp_path/'not-needed.dtsx',tmp_path/'out',audit_contract=[])
    with pytest.raises(ValueError,match='source audit rule'):
        run_migration(tmp_path/'not-needed.dtsx',tmp_path/'out',source_audit_rules=[])


def xml(name='P',root='Package',task_id='T',sql='SELECT 1'):
    return f'''<D:Executable xmlns:D="www.microsoft.com/SqlServer/Dts" xmlns:S="sql" D:refId="{root}" D:ObjectName="{name}">
      <D:Executables><D:Executable D:refId="{task_id}" D:ExecutableType="Microsoft.ExecuteSQLTask"><D:ObjectData>
      <S:SqlTaskData S:SqlStatementSource="{sql}"/></D:ObjectData></D:Executable></D:Executables></D:Executable>'''.encode()


def test_nonstandard_root_and_opaque_event_ids_keep_local_variable_scope(tmp_path):
    path=tmp_path/'alternate.DTSX'
    raw=xml('Renamed','Root-42','main').decode()
    handler='''<D:EventHandlers><D:EventHandler D:refId="opaque-event" D:EventName="OnError">
      <D:Variables><D:Variable D:ObjectName="Local" D:Namespace="User"><D:VariableValue D:DataType="8">event value</D:VariableValue></D:Variable></D:Variables>
      <D:Executables><D:Executable D:refId="opaque-handler-task" D:ExecutableType="Microsoft.ExecuteSQLTask"><D:ObjectData>
      <S:SqlTaskData S:SqlStatementSource="SELECT @local" ><S:ParameterBinding S:ParameterName="local" S:DtsVariableName="User::Local" S:ParameterDirection="Input"/></S:SqlTaskData>
      </D:ObjectData></D:Executable></D:Executables></D:EventHandler></D:EventHandlers>'''
    raw=raw.removesuffix('</D:Executable>')+handler+'</D:Executable>'
    path.write_text(raw)
    ir=IRBuilder().build(SSISPackageParser(path).parse())
    assert ir.metadata['package_root_ref']=='Root-42'
    assert {e.ref_id for e in ir.executables}=={'main','opaque-handler-task'}
    p=plan(ir,AssessmentConfig(catalog='c',target_schema='data'))
    assert not [f for f in p.unresolved if f['severity']=='ERROR']
    assert p.metadata['package_error_handler']['scope']=='Root-42.EventHandlers[OnError]'
    assert any('event value'==v for v in p.metadata['runtime_defaults'].values())


def test_child_reusing_root_identity_is_not_silently_discarded(tmp_path):
    path=tmp_path/'duplicate.dtsx';path.write_bytes(xml(root='Root',task_id='Root'))
    ir=IRBuilder().build(SSISPackageParser(path).parse())
    assert len(ir.executables)==1
    p=plan(ir)
    assert any(f['code']=='SOURCE_ID_INVALID' for f in p.unresolved)


def test_batch_handles_duplicate_names_bad_xml_and_selection_isolation(tmp_path):
    uploads=[('folder/shared.dtsx',xml('First',sql='SELECT 1')),('shared.dtsx',xml('Second',sql='SELECT 2')),
        ('bad.DTSX',b'<invalid')]
    directory=stage_packages(uploads,tmp_path/'uploads')
    assert stage_packages(list(reversed(uploads)),tmp_path/'uploads')==directory
    report=run_migration(directory,tmp_path/'out',audit_mode='target',audit_schema='audit')
    assert report['package_count']==3 and report['failed_package_count']==1
    good=[p for p in report['packages'] if p['status']!='FAILED']
    assert len(good)==2 and len({p['artifacts']['notebook'] for p in good})==2
    assert all(p['assessment']['verification']['legacy_match_percent'] is None for p in good)
    second=stage_packages([uploads[0],uploads[0]],tmp_path/'uploads')
    assert second!=directory
    later=run_migration(second,tmp_path/'out',audit_mode='target',audit_schema='audit')
    assert later['package_count']==1 and later['failed_package_count']==0


def test_cli_passes_custom_source_and_storage_contracts_and_versions_them(tmp_path):
    import subprocess,sys
    path=tmp_path/'input.dtsx';path.write_bytes(xml(sql='EXEC ops.OldAudit'))
    rules=tmp_path/'rules.json';rules.write_text(json.dumps({'tasks':[{'source_id':'T','role':'execution'}]}))
    contract=tmp_path/'contract.json';contract.write_text(json.dumps(CUSTOM))
    result=subprocess.run([sys.executable,'-m','ssis_migration.migrate','--source',str(path),'--output',str(tmp_path/'out'),
        '--audit-mode','target','--audit-schema','audit','--source-audit-rules',str(rules),'--audit-contract',str(contract)],
        capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    item=json.loads(result.stdout)['packages'][0]
    audit=json.loads(Path(item['artifacts']['audit_replacement_plan']).read_text())
    assert audit['replaced_sources']==['T'] and audit['storage_contract']['statuses']['Running']=='IN_PROGRESS'
    changed=run_migration(path,tmp_path/'out',audit_mode='target',audit_schema='audit',
        source_audit_rules={'tasks':[{'source_id':'T','role':'execution'}]})
    assert changed['packages'][0]['artifacts']['notebook']!=item['artifacts']['notebook']


@pytest.mark.parametrize('kind',['STOCK:FORLOOP','Microsoft.FileSystemTask','Microsoft.ExecutePackageTask','Vendor.CustomTask'])
def test_unimplemented_task_families_remain_visible_and_blocked(kind):
    p=plan(PackageIR(package_name='Different',executables=[ExecutableIR(ref_id='opaque',executable_type=kind)]))
    assert any(i['source_id']=='opaque' and i['disposition']=='REVIEW_ONLY' for i in p.metadata['source_inventory'])
    assert any(f['severity']=='ERROR' for f in p.unresolved)
