"""Select active source properties and describe variable SQL without evaluating it."""
from __future__ import annotations

import re


def source_selection(properties):
    mode = str(properties.get('AccessMode', ''))
    if mode == '':
        if properties.get('SqlCommandVariable'): mode = '3'
        elif properties.get('OpenRowsetVariable'): mode = '1'
        elif properties.get('SqlCommand'): mode = '2'
        else: mode = '0'
    if mode == '0': return {'kind':'TABLE', 'value':properties.get('OpenRowset'), 'access_mode':mode}
    if mode == '1': return {'kind':'VARIABLE_TABLE', 'value':properties.get('OpenRowsetVariable'), 'access_mode':mode}
    if mode == '2': return {'kind':'SQL', 'value':properties.get('SqlCommand'), 'access_mode':mode}
    if mode == '3': return {'kind':'VARIABLE_SQL', 'value':properties.get('SqlCommandVariable'), 'access_mode':mode}
    return {'kind':'UNSUPPORTED', 'value':None, 'access_mode':mode}


def active_source_sql(properties):
    selected = source_selection(properties)
    if selected['kind'] == 'SQL': return selected['value']
    if selected['kind'] == 'TABLE' and selected['value']:
        return 'SELECT * FROM ' + selected['value']
    return None


def describe_variable(reference, owner_ref, package):
    """Nearest lexical declaration wins; unavailable scope is never guessed."""
    reference = (reference or '').strip()
    qualified = reference if '::' in reference else 'User::' + reference
    result = {'reference':qualified, 'owner_ref':owner_ref, 'status':'NOT_FOUND',
        'expression':None, 'value':None, 'references':[]}
    candidates = [v for v in package.variables if f'{v.namespace or "User"}::{v.name}' == qualified]
    parents = {e.ref_id:e.properties.get('_parent_ref_id') for e in package.executables}
    lexical={}
    for exe in package.executables:
        chain=exe.properties.get('_lexical_scopes',[])
        for child,parent in zip(chain,chain[1:]):lexical[child]=parent
    scopes, seen = [], set()
    scope = owner_ref
    while scope and scope not in seen:
        scopes.append(scope); seen.add(scope)
        if scope in lexical:scope=lexical[scope]
        elif re.search(r'\.EventHandlers\[[^\]]+\]$',scope):scope=scope.rsplit('.EventHandlers[',1)[0]
        else:
            inferred=scope.rsplit('\\',1)[0] if '\\' in scope else None
            scope=inferred if inferred and '.EventHandlers[' in inferred and inferred not in seen else parents.get(scope) or inferred
    if 'Package' not in scopes: scopes.append('Package')
    selected = []
    for scope in scopes:
        selected = [v for v in candidates if v.scope_ref_id == scope]
        if selected: break
    if not selected:
        unknown = [v for v in candidates if v.scope_ref_id is None]
        if len(unknown) == 1:
            selected = unknown
            result['status'] = 'SCOPE_UNAVAILABLE'
        elif unknown:
            result['status'] = 'AMBIGUOUS'
            return result
        else: return result
    if len(selected) != 1:
        result['status'] = 'AMBIGUOUS'
        return result
    var = selected[0]
    result.update(scope_ref_id=var.scope_ref_id, data_type=var.data_type,
        evaluate_as_expression=var.evaluate_as_expression,
        active_value_kind='EXPRESSION' if var.evaluate_as_expression is True else 'VALUE' if var.evaluate_as_expression is False else 'UNCONFIRMED',
        status='DECLARED_REQUIRES_RUNTIME_REVIEW' if var.scope_ref_id else 'SCOPE_UNAVAILABLE')
    if var.sensitive:
        result.update(status='SENSITIVE_REDACTED', expression='[REDACTED]', value='[REDACTED]')
        return result
    result.update(expression=var.expression, value=var.value)
    # Ignore references embedded inside SSIS string literals. No eval or SQL execution.
    expression = re.sub(r'"(?:[^"\\]|\\.)*"', '', (var.expression or '') if var.evaluate_as_expression is not False else '')
    names = sorted(set(a or b for a,b in re.findall(r'@(?:\[([^\]]+)\]|([A-Za-z_]\w*::[A-Za-z_]\w*))', expression)))
    result['references'] = [{'reference':name, 'kind':'PARAMETER' if name.startswith(('$Package::','$Project::')) else 'VARIABLE',
        'status':'RUNTIME_BINDING_REQUIRED'} for name in names]
    return result
