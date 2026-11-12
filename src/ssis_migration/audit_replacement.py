"""Identify source audit operations by SQL contracts and preserve every disposition."""
from __future__ import annotations
import re
from sqlglot import exp

from ssis_migration.bound_sql import BindingContext,relational_batch
from ssis_migration.mapping import ConversionGap
from ssis_migration.sql_text import normalize_name,mask_sql,CALL
from ssis_migration.audit_contract import source_rules
from ssis_migration.source_context import output_references


SIGNATURES={
    'execution':{'pkgname','pkgguid','pkgversionguid','pkgusername','pkgmachinename','parentpkgexeckey'},
    'table_processing':{'pkgexeckey','tablename','tableinitialrowcnt'},
    'branch':{'tableprocesskey','branchname','processingsummarygroup'},
    'error':{'pkgexeckey','pkgerrorcode','pkgerrordescription','pkgsourcename','pkgsourcedescription'},
}


def variable_readers(package,disabled):
    from ssis_migration.source_context import describe_variable
    result={}
    def strings(value):
        if isinstance(value,str):yield value
        elif isinstance(value,list):
            for item in value:yield from strings(item)
        elif isinstance(value,dict):
            for name,item in value.items():
                if name!='result_bindings' and (not name.startswith('_') or name=='_property_expressions'):yield from strings(item)
    for exe in package.executables:
        if disabled(exe.ref_id):continue
        values=list(strings(exe.properties))
        for flow in package.data_flows:
            if flow.ref_id==exe.ref_id:
                for component in flow.components:values.extend(strings(component.properties))
        refs={ref for value in values for ref in re.findall(r'(?:User|System|\$Package|\$Project)::[A-Za-z_]\w*',value)}
        for value in values:
            refs.update(ref for ref in re.findall(r'@\[([^\]]+)\]',value) if '::' in ref)
            for item in value.split(','):
                item=item.strip()
                if item.split('::',1)[0] in {'User','System','$Package','$Project'}:refs.add(item)
        pending=list(refs)
        while pending:
            context=describe_variable(pending.pop(),exe.ref_id,package)
            for item in context.get('references',[]):
                if item['reference'] not in refs:refs.add(item['reference']);pending.append(item['reference'])
        result[exe.ref_id]=refs
    return result


def notification_call(sql):
    clean=mask_sql(sql or '').strip()
    if re.match(r'EXEC(?:UTE)?\s+@',clean,re.I):return False
    match=CALL.match(clean)
    if not match:return False
    tail=clean[match.end():].strip().removesuffix(';')
    return not re.search(r';|\b(?:SELECT|INSERT|UPDATE|DELETE|MERGE|EXEC|DROP|ALTER|CREATE)\b',tail,re.I)


def select_replacements(package,config,disabled):
    aliases=package.metadata.get('connection_aliases',{})
    runtime=BindingContext(package)
    def connection(exe):
        ref=exe.properties.get('connection_manager_ref_id') or exe.properties.get('Connection')
        return aliases.get(ref,aliases.get(str(ref).strip('{}').casefold(),ref))
    def identity(table,exe):return connection(exe),normalize_name(table.sql(dialect='tsql'),config.default_schema)
    def key(reference,owner):
        try:return runtime.fork().variable(reference,owner)[0]
        except ConversionGap:return None
    active=[e for e in package.executables if e.ref_id and not disabled(e.ref_id)]
    rules=source_rules(config.source_audit_rules,config.default_schema)
    signatures=list(SIGNATURES.items()) if rules['use_builtin_signatures'] else []
    signatures += [(item['role'],set(item['columns'])) for item in rules['insert_signatures']]
    parsed={};tables={};replaced={};outputs={};consumers={};issues=[]
    applicable=[r for r in rules['tasks'] if r.get('package') in (None,package.package_name)]
    task_rules={r['source_id']:r for r in applicable}
    for rule in applicable:
        if not any(e.ref_id==rule['source_id'] for e in package.executables):
            issues.append(('SOURCE_AUDIT_RULE_UNRESOLVED',rule['source_id'],'Explicit audit task reference does not exist in this package.'))
    readers=variable_readers(package,disabled)
    for exe in active:
        props=exe.properties
        outputs[exe.ref_id]={k for ref in output_references(exe,task_rules.get(exe.ref_id,{}).get('outputs',[])) if (k:=key(ref,exe.ref_id))}
        for ref in readers.get(exe.ref_id,[]):
            if k:=key(ref,exe.ref_id):consumers.setdefault(k,set()).add(exe.ref_id)
        sql=props.get('SqlStatementSource')
        if not sql or str(props.get('SqlStatementSourceType','DirectInput')) not in {'0','DirectInput'} or props.get('_property_expressions'):continue
        try:trees=relational_batch(sql)
        except ConversionGap:continue
        parsed[exe.ref_id]=trees
        for tree in trees:
            if not isinstance(tree,exp.Insert) or not isinstance(tree.this,exp.Schema) or not isinstance(tree.this.this,exp.Table):continue
            names={n.name.casefold() for n in tree.this.expressions}
            for role,signature in signatures:
                if signature.issubset(names):tables[identity(tree.this.this,exe)]=role
    for exe in active:
        for tree in parsed.get(exe.ref_id,[]):
            for table in tree.find_all(exp.Table):
                conn,name=identity(table,exe)
                for rule in rules['objects']:
                    ref=rule.get('connection')
                    ref=aliases.get(ref,aliases.get(str(ref).strip('{}').casefold(),ref)) if ref is not None else None
                    if name==rule['name'] and (ref is None or ref==conn):tables[(conn,name)]=rule['role']
    for exe in active:
        trees=parsed.get(exe.ref_id,[])
        used={identity(t,exe) for tree in trees for t in tree.find_all(exp.Table)}
        if trees and used and used.issubset(tables) and all(isinstance(t,(exp.Insert,exp.Update,exp.Query)) for t in trees):
            replaced[exe.ref_id]={'reason':'SQL_AUDIT_CONTRACT','roles':sorted({tables[t] for t in used}),
                'evidence':sorted(name for _,name in used)}
    for exe in active:
        rule=task_rules.get(exe.ref_id)
        if rule is None:continue
        kind=(exe.executable_type or exe.creation_name or '').casefold()
        children=[e for e in active if e.properties.get('_parent_ref_id')==exe.ref_id or e.ref_id.startswith(exe.ref_id+'\\')]
        if children or any(f.ref_id==exe.ref_id for f in package.data_flows) or 'sequence' in kind or 'loop' in kind:
            issues.append(('SOURCE_AUDIT_RULE_NONLEAF',exe.ref_id,'Explicit replacement of containers/data flows requires component/output review; select audit leaf tasks instead.'));continue
        if not any(t in kind for t in ('executesqltask','sendmailtask','expressiontask')) and 'outputs' not in rule and 'ReadWriteVariables' not in exe.properties:
            issues.append(('SOURCE_AUDIT_RULE_OUTPUTS_REQUIRED',exe.ref_id,'Declare outputs explicitly (including an empty list) for an opaque audit task.'));continue
        replaced[exe.ref_id]={'reason':'EXPLICIT_AUDIT_TASK_RULE','roles':[rule['role']],
            'evidence':['Explicit source/package-scoped rule; original operation remains in replacement evidence.']}
    # An error-notification chain is replaceable only when it contains verified
    # audit writes plus a fully recognized mail-only program. Unknown recovery
    # SQL/scripts are preserved for review rather than discarded.
    root=package.metadata.get('package_root_ref','Package')
    error_scope=root+'.EventHandlers[OnError]'
    def root_event(exe):
        scope=exe.properties.get('_event_handler_scope') or exe.properties.get('_event_handler_attributes',{}).get('refId')
        return scope==error_scope if scope else exe.ref_id.startswith(error_scope+'\\')
    events=[e for e in active if root_event(e)]
    if events and any(e.ref_id in replaced for e in events):
        mail=set();calls=set();unknown=set()
        for exe in events:
            if exe.ref_id in replaced:continue
            kind=(exe.executable_type or exe.creation_name or '').casefold()
            if 'scripttask' in kind or 'sendmailtask' in kind:
                try:
                    if 'scripttask' in kind:
                        from ssis_migration.script_mail import translate_script_mail
                        translate_script_mail(exe,package,runtime.fork(),inspect_only=True)
                    else:
                        # The typed SMTP task has notification-only effects.
                        # Old relay credentials/settings are not prerequisites
                        # for replacing this source notification lifecycle.
                        pass
                    mail.add(exe.ref_id)
                except ConversionGap:unknown.add(exe.ref_id)
            elif 'executesqltask' in kind and outputs[exe.ref_id] and notification_call(exe.properties.get('SqlStatementSource')):
                calls.add(exe.ref_id)
            else:unknown.add(exe.ref_id)
        # Require the unresolved calls' outputs to flow into these notifications,
        # not into a normal business task or another script.
        if mail and not unknown:
            script_references=set()
            for exe in events:
                if exe.ref_id in mail:
                    for source in exe.properties.get('script_sources',[]):
                        script_references.update(k for ref in re.findall(r'Dts\.Variables\["([^"]+)"\]',source.get('text') or '') if (k:=key(ref,exe.ref_id)))
                    for expression in exe.properties.get('_property_expressions',[]):
                        script_references.update(k for ref in re.findall(r'@\[([^\]]+)\]',expression.get('expression') or '') if (k:=key(ref,exe.ref_id)))
            all_event_refs={e.ref_id for e in events}
            if all(outputs[r] and outputs[r].issubset(script_references) and all(consumers.get(k,set()).issubset(all_event_refs) for k in outputs[r]) for r in calls):
                for exe in events:
                    replaced.setdefault(exe.ref_id,{'reason':'REPLACED_ERROR_NOTIFICATION','roles':['error_notification'],
                        'evidence':['Target notebook failure lifecycle replaces source audit/mail chain.']})
    # Only pure row-count helpers whose results have no business consumer can
    # disappear with audit writers. Names and sequence labels are not evidence.
    for exe in active:
        if exe.ref_id in replaced or not outputs[exe.ref_id]:continue
        sql=exe.properties.get('SqlStatementSource') or ''
        pure=bool(re.fullmatch(r"\s*exec(?:ute)?\s*\(\s*'SELECT\s+Count\(\*\)\s+FROM\s+'\s*\+\s*@\w+\s*\)\s*;?\s*",sql,re.I))
        trees=parsed.get(exe.ref_id,[])
        if len(trees)==1 and isinstance(trees[0],exp.Select):
            projections=trees[0].expressions
            pure=pure or len(projections)==1 and isinstance(projections[0].this if isinstance(projections[0],exp.Alias) else projections[0],exp.Count)
        if pure and all(consumers.get(k) and consumers[k].issubset(replaced) for k in outputs[exe.ref_id]):
            replaced[exe.ref_id]={'reason':'AUDIT_ONLY_ROW_COUNT','roles':['row_count'],'evidence':sorted(outputs[exe.ref_id])}
    records=[]
    for exe in active:
        if exe.ref_id in replaced:
            records.append({'source_id':exe.ref_id,**replaced[exe.ref_id],
                'original_sql':exe.properties.get('SqlStatementSource'),'parameter_bindings':exe.properties.get('parameter_bindings',[]),
                'result_bindings':exe.properties.get('result_bindings',[]),
                'output_variables':sorted(output_references(exe,task_rules.get(exe.ref_id,{}).get('outputs',[]))),
                'original_expression':exe.properties.get('Expression')})
    return {'mode':'target','replaced_sources':sorted(replaced),'replacements':records,
        'rule_findings':issues,'configured_rules':rules,
        'audit_table_contracts':[{'connection':c,'source_object':t,'role':role} for (c,t),role in sorted(tables.items(),key=str)],
        'note':'Intentional framework replacement, not legacy audit equivalence. Unknown business/recovery operations remain represented.'}


def bridge_values(package,replacements,config,runtime,disabled):
    removed=set(replacements['replaced_sources']);producers={};bridges={};issues=[]
    for exe in package.executables:
        if exe.ref_id not in removed:continue
        record=next(r for r in replacements['replacements'] if r['source_id']==exe.ref_id)
        for reference in record.get('output_variables',output_references(exe)):
            try:key,_=runtime.fork().variable(reference,exe.ref_id)
            except ConversionGap:continue
            producers.setdefault(key,[]).append(exe.ref_id)
    readers=variable_readers(package,disabled)
    for exe in package.executables:
        if exe.ref_id in removed or disabled(exe.ref_id):continue
        for reference in readers.get(exe.ref_id,[]):
            try:key,kind=runtime.fork().variable(reference,exe.ref_id)
            except ConversionGap:continue
            if key not in producers:continue
            runtime.variable(reference,exe.ref_id);runtime.defaults.pop(key,None)
            choice=config.audit_value_bindings.get(key)
            record={'key':key,'kind':kind,'source_producers':producers[key],'business_consumers':[], 'binding':choice}
            record=bridges.setdefault(key,record);record['business_consumers'].append(exe.ref_id)
            runtime.inputs[key]['origin']='target_audit_notebook_key' if choice=='notebook_key' and kind=='int64' else 'target_audit_bridge'
            if choice not in {'runtime_input','notebook_key'}:
                issues.append(('TARGET_AUDIT_VALUE_BINDING_REQUIRED',exe.ref_id,
                    f'{key} is produced by replaced source auditing but consumed by business SQL. Select an explicit value binding.'))
            elif choice=='notebook_key' and kind!='int64':
                issues.append(('TARGET_AUDIT_KEY_TYPE_REVIEW_REQUIRED',exe.ref_id,
                    f'{key} has source type {kind}; do not silently narrow the target 64-bit notebook key.'))
    return list(bridges.values()),issues
