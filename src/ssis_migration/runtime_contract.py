"""Initial-value requirements from emitted reads/writes and task dependencies."""
import ast


def annotate_initial_values(plan, runtime):
    reads={};writes={}
    steps={s.step_id:s for s in plan.steps}
    for sid,operations in plan.metadata['operations_by_step'].items():
        for operation in operations:
            try:tree=ast.parse(operation.get('code',''))
            except SyntaxError:continue  # Static validation retains this blocker.
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='_ssis_value':
                    if node.args and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str):
                        reads.setdefault(node.args[0].value,set()).add(sid)
                if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name) and node.value.id=='_ssis_values' and isinstance(node.ctx,ast.Store):
                    if isinstance(node.slice,ast.Constant) and isinstance(node.slice.value,str):
                        writes.setdefault(node.slice.value,set()).add(sid)
    def ancestors(sid):
        seen=set();pending=list(steps[sid].dependencies)
        while pending:
            dependency=pending.pop()
            if dependency in seen:continue
            seen.add(dependency)
            if dependency in steps:pending.extend(steps[dependency].dependencies)
        return seen
    prior={sid:ancestors(sid) for sid in steps}
    deferred={'source_result','failure_context','error_code_mapping','target_audit_notebook_key'}
    for key,record in runtime.inputs.items():
        producers=writes.get(key,set())
        consumers=reads.get(key,set())
        if producers and record.get('origin') not in {'target_audit_bridge','target_audit_notebook_key'}:
            record['origin']='source_result'
            record['source_producer_ids']=sorted(set(record.get('source_producer_ids',[]))|
                {steps[sid].metadata['source_ref_id'] for sid in producers})
        if record.get('derived') or record.get('origin') in {'failure_context','error_code_mapping','target_audit_notebook_key'}:
            initial=False
        elif consumers and producers:
            # A producer must precede every read through source dependencies.
            # A read and write in one task still needs its initial value.
            initial=any(not (producers & prior[sid]) for sid in consumers)
        else:
            # Missing implementations remain globally blocked; their declared
            # result values are not reclassified as manual input substitutes.
            initial=bool(record.get('read') and record.get('origin') not in deferred)
        record['initial_value_required']=initial
        record['required_external_input']=initial and key not in runtime.defaults


def preflight_code(variables,derived=None):
    required=[{'key':r['key'],'kind':r['kind'],'numeric':bool(r.get('numeric_fragment'))}
        for r in variables if r.get('initial_value_required',r.get('required_external_input'))]
    fixed={r['key']:r['fixed_sql_text'] for r in variables if 'fixed_sql_text' in r}
    for r in required:
        if r['key'] in fixed:r['fixed_sql_text']=fixed[r['key']]
        if any(v['key']==r['key'] and v.get('timezone') for v in variables):r['timezone']=True
    initial={r['key'] for r in required};checks=[]
    for record in variables:
        expression=(derived or {}).get(record['key'])
        if not expression:continue
        tree=ast.parse(expression,mode='eval')
        refs={n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_ssis_value'
            and n.args and isinstance(n.args[0],ast.Constant)}
        if refs.issubset(initial):checks.append((record,expression))
    if not required and not checks and not any(v.get('clock_override') for v in variables):return ''
    code='''# Validate initial SSIS values before audit setup or business mutations.
_ssis_input_errors = []
for _ssis_input in '''+repr(required)+''':
    _ssis_key = _ssis_input['key']
    if _ssis_key not in _ssis_values:
        _ssis_input_errors.append(_ssis_key + ': missing required initial value')
        continue
    try:
        _ssis_checked_input = _ssis_value(_ssis_key, _ssis_input['kind'])
        if _ssis_input['numeric']:
            _ssis_numeric(_ssis_checked_input)
        if _ssis_input.get('timezone'):
            ZoneInfo(_ssis_checked_input)
        if 'fixed_sql_text' in _ssis_input and _ssis_checked_input != _ssis_input['fixed_sql_text']:
            _ssis_input_errors.append(_ssis_key + ': captured SQL changed; regenerate before execution')
            continue
    except Exception:
        _ssis_input_errors.append(_ssis_key + ': invalid ' + _ssis_input['kind'] + (' / numeric SQL value' if _ssis_input['numeric'] else ' value'))
'''
    for record,expression in checks:
        value=f'_ssis_coerce({expression}, {record["kind"]!r})'
        if record.get('numeric_fragment'):value=f'_ssis_numeric({value})'
        code+='if not _ssis_input_errors:\n    try:\n        _ssis_checked_derived = '+value+'\n    except Exception:\n'
        code+='        _ssis_input_errors.append('+repr(record['key']+': invalid initial derived '+record['kind']+' value')+')\n'
    return code+'''
if '$Runtime::SourceClock' in _ssis_values:
    try:
        _ssis_source_clock_override()
    except Exception:
        _ssis_input_errors.append('$Runtime::SourceClock: invalid timestamp or missing UTC offset')
if _ssis_input_errors:
    raise RuntimeError('SSIS input preflight failed; set ssis_runtime_values and rerun before any writes: ' + '; '.join(_ssis_input_errors))
'''
