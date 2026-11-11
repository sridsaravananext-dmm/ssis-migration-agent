"""Native adapter for the existing notebook/table audit storage contract."""
from __future__ import annotations
import textwrap
from ssis_migration.mapping import target_table
from ssis_migration.audit_contract import RUN_TYPES,STATS_TYPES,storage_contract


def column(role,aliases):
    actual=aliases[role]
    return role if actual==role else '`'+actual.replace('`','``')+'`'


def _insert_lines(table,types,values,indent,aliases):
    # Optional job columns are chosen through fixed, generated SQL variants.
    # SQL text and named argument keys remain statically inspectable.
    lines=[]
    for i,optional in enumerate((('job_id','job_name'),('job_id',),('job_name',),())):
        prefix='if' if i==0 else 'elif' if i<3 else 'else'
        condition=' and '.join(repr(k)+' in _ta_fields' for k in optional)
        lines.append(' '*indent+prefix+(' '+condition if condition else '')+':')
        columns=[c for c in list(types)+list(optional) if aliases.get(c) is not None]
        sql=f'INSERT INTO {table} ('+', '.join(column(c,aliases) for c in columns)+') VALUES ('+', '.join(':'+c for c in columns)+')'
        args='{'+', '.join(repr(k)+': '+values+'['+repr(k)+']' for k in columns)+'}'
        lines.append(' '*(indent+4)+f'spark.sql({sql!r}, args={args})')
    return '\n'.join(lines)


def setup_code(plan):
    meta=plan['metadata']['target_audit']
    contract=storage_contract(meta.get('storage_contract',{}))
    run=target_table('.'.join('`'+v.replace('`','``')+'`' for v in (meta['catalog'],meta['schema'],contract['notebook_table']))).sql(dialect='databricks')
    stats=target_table('.'.join('`'+v.replace('`','``')+'`' for v in (meta['catalog'],meta['schema'],contract['table_stats_table']))).sql(dialect='databricks')
    run_aliases=contract['notebook_columns'];stats_aliases=contract['table_stats_columns']
    run_types={k:v for k,v in RUN_TYPES.items() if run_aliases[k] is not None}
    stats_types={k:v for k,v in STATS_TYPES.items() if stats_aliases[k] is not None}
    targets=meta['stats_targets'] if meta['capture_table_stats'] else []
    run_insert=_insert_lines(run,run_types,'_ta_values',4,run_aliases)
    stats_insert=_insert_lines(stats,stats_types,'_ta_values',4,stats_aliases)
    duration=(f", {column('duration_time',run_aliases)}=CAST((unix_millis(CURRENT_TIMESTAMP())-unix_millis({column('time_start',run_aliases)}))/1000.0 AS DECIMAL(10,1))" if run_aliases['duration_time'] is not None else '')
    update=f"UPDATE {run} SET {column('time_end',run_aliases)}=CURRENT_TIMESTAMP(){duration}, {column('status',run_aliases)}=:status, {column('message',run_aliases)}=:message WHERE {column('notebook_key',run_aliases)}=:key"
    parent='SELECT '+', '.join(column(k,run_aliases)+((' AS '+k) if run_aliases[k]!=k else '') for k in ('master_key','time_start','status'))+f" FROM {run} WHERE {column('notebook_key',run_aliases)}=:key"
    code=textwrap.dedent('''\
        import uuid
        import json
        from decimal import Decimal
        _target_audit_errors = []
        _ta_terminal = False
        _ta_finished_status = None
        _ta_started = False
        _ta_before = {}
        _ta_stats_recorded = set()

        def _ta_single(frame):
            rows = frame.limit(2).collect()
            if len(rows) != 1:
                raise RuntimeError('Target audit lookup requires exactly one row.')
            return rows[0].asDict()

        def _ta_key():
            return (uuid.uuid4().int & ((1 << 63) - 1)) or 1

        def _ta_contract(table, required):
            if not spark.catalog.tableExists(table):
                raise RuntimeError('Existing target audit table is missing: ' + table)
            physical = {field.name:field for field in spark.table(table).schema.fields}
            aliases = _ta_column_aliases[table]
            fields = {role:physical[name] for role,name in aliases.items() if name in physical}
            for name, kind in required.items():
                if name not in fields or fields[name].dataType.simpleString() != kind:
                    raise RuntimeError('Target audit schema/type contract differs: ' + table + '.' + name)
            for name, kind in {'job_id':'bigint','job_name':'string'}.items():
                if name in fields and fields[name].dataType.simpleString() != kind:
                    raise RuntimeError('Target optional job column has an incompatible type: ' + name)
            if any(not field.nullable and name not in set(aliases.values()) for name,field in physical.items()):
                raise RuntimeError('Unknown required target audit column; review its default/identity semantics.')
            return set(fields)

        _ta_job = json.loads(dbutils.widgets.get('target_audit_job'))
        if not isinstance(_ta_job, dict) or set(_ta_job) - {'job_id','job_name','job_run_id','master_key','execution_key','source_file_path'}:
            raise RuntimeError('Target audit job context must contain only documented keys.')
        def _ta_integer(value):
            if isinstance(value,bool) or not isinstance(value,int) or not 0 < value < (1 << 63):
                raise RuntimeError('Target audit identifiers must be positive signed 64-bit integers.')
            return value
        try:
            _ta_context = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        except Exception:
            _ta_context = None
        if _ta_context is not None:
            if 'job_id' not in _ta_job:
                try:
                    _ta_job['job_id'] = _ta_integer(int(_ta_context.jobId().get()))
                except Exception:
                    pass
            if 'job_name' not in _ta_job:
                try:
                    _ta_job['job_name'] = _ta_context.jobName().get()
                except Exception:
                    pass
            if 'job_run_id' not in _ta_job:
                try:
                    _ta_job['job_run_id'] = _ta_integer(int(_ta_context.parentRunId().get()))
                except Exception:
                    pass
        _ta_job_id = _ta_integer(_ta_job['job_id']) if _ta_job.get('job_id') is not None else None
        _ta_job_name = _ta_job.get('job_name','')
        if not isinstance(_ta_job_name,str):
            raise RuntimeError('Target audit job name must be a string.')
        _ta_existing = dbutils.widgets.get('execution_key').strip()
        if _ta_existing:
            if not _ta_existing.isdecimal():
                raise RuntimeError('Existing target execution key must be a positive integer.')
            _ta_existing = _ta_integer(int(_ta_existing))
            if _ta_job.get('execution_key') is not None and _ta_existing != _ta_job['execution_key']:
                raise RuntimeError('Conflicting target execution keys were supplied.')
            _ta_job['execution_key'] = _ta_existing
    ''')
    code+=f"\n_ta_column_aliases = {{{run!r}:{run_aliases!r}, {stats!r}:{stats_aliases!r}}}\n"
    code+=f"_ta_status_values = {contract['statuses']!r}\n"
    code+=f"_ta_run_fields = _ta_contract({run!r}, {run_types!r})\n"
    code+=f"_ta_provider = _ta_single(spark.sql({('DESCRIBE TABLE EXTENDED '+run)!r}).filter(\"col_name = 'Provider'\"))['data_type']\n"
    code+="if _ta_provider.casefold() != 'delta':\n    raise RuntimeError('Target audit storage requires existing Delta tables.')\n"
    if targets:code+=f"_ta_stats_fields = _ta_contract({stats!r}, {stats_types!r})\n"
    else:code+='_ta_stats_fields = set()\n'
    if targets:
        code+=f"_ta_provider = _ta_single(spark.sql({('DESCRIBE TABLE EXTENDED '+stats)!r}).filter(\"col_name = 'Provider'\"))['data_type']\n"
        code+="if _ta_provider.casefold() != 'delta':\n    raise RuntimeError('Target audit statistics storage requires an existing Delta table.')\n"
    code+=textwrap.dedent(f'''\
        _ta_notebook = {plan['package_name'] or 'package'!r}
        try:
            _ta_notebook = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        except Exception:
            pass
        _ta_source_file = _ta_job.get('source_file_path')
        if _ta_source_file is not None and not isinstance(_ta_source_file,str):
            raise RuntimeError('Target source file path must be a string or null.')
        _ta_target_label = {', '.join(meta['stats_targets'])!r}
        _ta_user = _ta_single(spark.sql('SELECT current_user() AS user_id'))['user_id']
        _ta_start = _ta_single(spark.sql('SELECT current_timestamp() AS time_start'))['time_start']
        _target_audit_execution_key = _ta_key()
        _ta_master_key = _ta_integer(_ta_job['master_key']) if _ta_job.get('master_key') is not None else _ta_integer(_ta_job['job_run_id']) if _ta_job.get('job_run_id') is not None else _target_audit_execution_key
        _ta_attached = _ta_job.get('execution_key') is not None
        if _ta_job.get('execution_key') is not None:
            _ta_parent_key = _ta_integer(_ta_job['execution_key'])
            _ta_parent = _ta_single(spark.sql({parent!r}, args={{'key':_ta_parent_key}}))
            if _ta_parent['status'] != _ta_status_values['Running']:
                raise RuntimeError('Existing target execution must be a unique Running record; do not reuse completed keys.')
            if _ta_job.get('master_key') is not None and _ta_parent['master_key'] != _ta_master_key:
                raise RuntimeError('Target audit parent/master keys disagree.')
            _ta_master_key = _ta_integer(_ta_parent['master_key'])
            _target_audit_execution_key = _ta_parent_key
            _ta_start = _ta_parent['time_start']

        def _ta_insert_run(_ta_values):
            _ta_fields = _ta_run_fields
    ''')+run_insert+'\n'
    code+=textwrap.dedent('''\
        def _ta_insert_stats(_ta_values):
            _ta_fields = _ta_stats_fields
    ''')+stats_insert+'\n'
    # Each table and SQL argument is emitted literally. Only two metadata rows
    # can reach the driver, and row counts use a distributed aggregate.
    code+='def _ta_snapshot():\n    result = {}\n'
    for target in targets:
        parsed=target_table(target);quoted=parsed.sql(dialect='databricks')
        code+=f'    _ta_detail = _ta_single(spark.sql({("DESCRIBE DETAIL "+quoted)!r}))\n'
        code+=f'    _ta_history = _ta_single(spark.sql({("DESCRIBE HISTORY "+quoted+" LIMIT 1")!r}))\n'
        code+=f'    result[{quoted!r}] = {{"rows":spark.table({quoted!r}).count(), "version":int(_ta_history["version"]), "storage":int(_ta_detail["sizeInBytes"]), "schema":{parsed.db!r}, "table":{parsed.name!r}}}\n'
    code+='    return result\n'
    code+=textwrap.dedent(f'''\
        def _target_audit_finish(status, message):
            global _ta_terminal, _ta_finished_status
            if _ta_finished_status is not None or not _ta_started:
                return
            _ta_terminal = True
            spark.sql({update!r}, args={{'status':_ta_status_values[status],'message':message,'key':_target_audit_execution_key}})
            _ta_finished_status = status

        def _target_audit_failure(original):
            global _ta_terminal
            _ta_terminal = True
            try:
                _ta_write_statistics()
            except Exception as audit_error:
                _target_audit_errors.append(str(audit_error))
            try:
                _target_audit_finish('Error', str(original))
            except Exception as audit_error:
                _target_audit_errors.append(str(audit_error))

        def _ta_write_statistics():
            remaining = {{table:before for table,before in _ta_before.items() if table not in _ta_stats_recorded}}
            if not remaining:
                return
            after = _ta_snapshot()
            end = _ta_single(spark.sql('SELECT current_timestamp() AS time_end'))['time_end']
            for table, before in remaining.items():
                current = after[table]
                _ta_insert_stats({{'table_key':_ta_key(),'master_key':_ta_master_key,'notebook_key':_target_audit_execution_key,
                    'time_start':_ta_start,'time_end':end,'user_id':_ta_user,'caller_notebook':_ta_notebook,
                    'schema_name':before['schema'],'table_name':before['table'],
                    'before_row_count':before['rows'],'before_table_version':before['version'],'before_storage':before['storage'],
                    'after_row_count':current['rows'],'after_table_version':current['version'],'after_storage':current['storage'],
                    'job_id':_ta_job_id,'job_name':_ta_job_name}})
                _ta_stats_recorded.add(table)

        def _target_audit_success():
            if _ta_terminal:
                if _ta_finished_status != 'Success':
                    raise RuntimeError('Target audit execution has failed; a success exit is not allowed.')
                return
            try:
                _ta_write_statistics()
                _target_audit_finish('Success','')
            except Exception as audit_error:
                _target_audit_failure(audit_error)
                raise

        if not _ta_attached:
            _ta_insert_run({{'notebook_key':_target_audit_execution_key,'master_key':_ta_master_key,'notebook':_ta_notebook,
                'time_start':_ta_start,'time_end':None,'duration_time':None,'user_id':_ta_user,'status':_ta_status_values['Running'],
                'source_file_path':_ta_source_file,'target_table':_ta_target_label,'message':'',
                'job_id':_ta_job_id,'job_name':_ta_job_name}})
        _ta_started = True
        try:
            _ta_before = _ta_snapshot()
        except Exception as audit_error:
            _target_audit_failure(audit_error)
            raise
    ''')
    for binding in meta['value_bindings']:
        if binding['binding']=='notebook_key' and binding['kind']=='int64':
            code+=f"_ssis_values[{binding['key']!r}] = _ssis_coerce(_target_audit_execution_key, 'int64')\n"
    return code
