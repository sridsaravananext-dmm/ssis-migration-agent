"""Validated extension points for source classification and target audit storage."""
import re
from ssis_migration.sql_text import QUALIFIED,normalize_name

RUN_TYPES={'notebook_key':'bigint','master_key':'bigint','notebook':'string','time_start':'timestamp',
    'time_end':'timestamp','duration_time':'decimal(10,1)','user_id':'string','status':'string',
    'source_file_path':'string','target_table':'string','message':'string'}
STATS_TYPES={'table_key':'bigint','master_key':'bigint','notebook_key':'bigint','time_start':'timestamp','time_end':'timestamp',
    'user_id':'string','caller_notebook':'string','schema_name':'string','table_name':'string','before_row_count':'bigint',
    'before_table_version':'bigint','before_storage':'bigint','after_row_count':'bigint','after_table_version':'bigint','after_storage':'bigint'}
OPTIONAL_RUN={'duration_time','user_id','source_file_path','target_table'}


def identifier(value,label):
    if not isinstance(value,str) or not value or len(value)>128 or any(ord(c)<32 for c in value):
        raise ValueError(label+' must be a nonempty identifier of at most 128 characters')
    return value


def reference(value,label):
    if not isinstance(value,str) or not value or len(value)>4096 or any(ord(c)<32 for c in value):
        raise ValueError(label+' must be a nonempty source reference')
    return value


def storage_contract(value):
    if not isinstance(value,dict) or set(value)-{'notebook_table','table_stats_table','notebook_columns','table_stats_columns','statuses'}:
        raise ValueError('Unknown target audit contract settings')
    result={'notebook_table':'audit_notebooks','table_stats_table':'audit_tables',
        'notebook_columns':{k:k for k in list(RUN_TYPES)+['job_id','job_name']},
        'table_stats_columns':{k:k for k in list(STATS_TYPES)+['job_id','job_name']},
        'statuses':{'Running':'Running','Success':'Success','Error':'Error'}}
    for name in ('notebook_table','table_stats_table'):
        if name in value:result[name]=identifier(value[name],name)
    if result['notebook_table'].casefold()==result['table_stats_table'].casefold():raise ValueError('Audit run and statistics tables must be distinct')
    for name,optional in [('notebook_columns',OPTIONAL_RUN|{'job_id','job_name'}),('table_stats_columns',{'job_id','job_name'})]:
        overrides=value.get(name,{})
        if not isinstance(overrides,dict) or set(overrides)-set(result[name]):raise ValueError('Unknown canonical roles in '+name)
        for role,physical in overrides.items():
            if physical is None:
                if role not in optional:raise ValueError('Required audit role cannot be omitted: '+role)
            else:identifier(physical,role)
            result[name][role]=physical
        names=[p.casefold() for p in result[name].values() if p is not None]
        if len(names)!=len(set(names)):raise ValueError('Audit column mappings must be one-to-one: '+name)
    states=value.get('statuses',{})
    if not isinstance(states,dict) or set(states)-set(result['statuses']):raise ValueError('Unknown audit lifecycle statuses')
    for role,text in states.items():identifier(text,'Audit status');result['statuses'][role]=text
    if len(set(result['statuses'].values()))!=3:raise ValueError('Audit lifecycle statuses must be distinct')
    return result


def source_rules(value,default_schema='dbo'):
    if not isinstance(value,dict) or set(value)-{'use_builtin_signatures','insert_signatures','objects','tasks'}:
        raise ValueError('Unknown source audit rule settings')
    if not isinstance(value.get('use_builtin_signatures',True),bool):raise ValueError('use_builtin_signatures must be boolean')
    result={'use_builtin_signatures':value.get('use_builtin_signatures',True),'insert_signatures':[],'objects':[],'tasks':[]}
    seen_objects=set();seen_tasks=set()
    for name in ('insert_signatures','objects','tasks'):
        items=value.get(name,[])
        if not isinstance(items,list) or len(items)>1024:raise ValueError('Source audit rules require bounded lists')
        for item in items:
            if not isinstance(item,dict):raise ValueError('Each audit rule must be an object')
            allowed={'role','columns'} if name=='insert_signatures' else {'role','name','connection'} if name=='objects' else {'role','source_id','package','outputs'}
            if set(item)-allowed:raise ValueError('Unknown source audit rule fields')
            role=identifier(item.get('role'),'Source audit role');record=dict(item,role=role)
            if name=='insert_signatures':
                columns=item.get('columns')
                if not isinstance(columns,list) or not columns or len(columns)>128:raise ValueError('Audit signatures require column names')
                record['columns']=[identifier(c,'Audit signature column').casefold() for c in columns]
                if len(record['columns'])!=len(set(record['columns'])):raise ValueError('Duplicate audit signature columns')
            elif name=='objects':
                object_name=item.get('name')
                if not isinstance(object_name,str) or not re.fullmatch(QUALIFIED,object_name.strip()):raise ValueError('Source audit object must be a qualified SQL Server identifier')
                if item.get('connection') is not None:reference(item['connection'],'Source audit connection')
                record['name']=normalize_name(object_name,default_schema)
                key=(item.get('connection'),record['name'])
                if key in seen_objects:raise ValueError('Duplicate source audit object rule')
                seen_objects.add(key)
            else:
                reference(item.get('source_id'),'Source audit task reference')
                if item.get('package') is not None:reference(item['package'],'Audit rule package')
                outputs=item.get('outputs',[])
                if not isinstance(outputs,list) or len(outputs)>1024 or any(not isinstance(v,str) or '::' not in v or not all(v.split('::',1)) for v in outputs):raise ValueError('Explicit audit outputs require bounded variable references')
                for output in outputs:reference(output,'Audit output variable')
                if len(set(outputs))!=len(outputs):raise ValueError('Duplicate audit output variable')
                key=(item.get('package'),item['source_id'])
                if key in seen_tasks or any(ref==key[1] and (pkg is None or key[0] is None) for pkg,ref in seen_tasks):raise ValueError('Overlapping source audit task rules')
                seen_tasks.add(key)
            result[name].append(record)
    return result
