"""Bounded package OnError dispatch and captured SMTP task translation."""
from __future__ import annotations
import json
import re
import sqlglot
from sqlglot import exp

from ssis_migration.mapping import ConversionGap


def condition(expression,owner,runtime):
    from ssis_migration.generator.graph_native import expression_sql
    bindings={}
    def replace(match):
        name='event_value_'+str(len(bindings))
        bindings[name]=runtime.read(match[1],owner)
        return '['+name+']'
    # References inside literals are retained as literal text.
    parts=re.split(r'("(?:\\.|[^"\\])*")',expression or '')
    rewritten=''.join(re.sub(r'@\[([^\]]+)\]',replace,part) if i%2==0 else part for i,part in enumerate(parts))
    tree=sqlglot.parse_one(expression_sql(rewritten,{},list(bindings),expect_boolean=True),read='databricks')
    def emit(node):
        if isinstance(node,exp.Column):return bindings[node.name]
        if isinstance(node,exp.Literal):return repr(node.this) if node.is_string else node.this
        if isinstance(node,exp.Boolean):return str(node.this is True or str(node.this).casefold()=='true')
        if isinstance(node,exp.Null):return 'None'
        if isinstance(node,exp.Paren):return '('+emit(node.this)+')'
        if isinstance(node,exp.Neg):return '(-'+emit(node.this)+')'
        if isinstance(node,exp.Not):return '(not '+emit(node.this)+')'
        operators={exp.And:'and',exp.Or:'or',exp.EQ:'==',exp.NEQ:'!=',exp.GT:'>',exp.GTE:'>=',exp.LT:'<',exp.LTE:'<=',exp.Is:'is'}
        for cls,operator in operators.items():
            if isinstance(node,cls):return '('+emit(node.this)+' '+operator+' '+emit(node.expression)+')'
        raise ConversionGap('EVENT_EXPRESSION_UNSUPPORTED','Unsupported event expression.')
    return emit(tree)


def mail_code(exe,package,runtime):
    props=exe.properties
    aliases=package.metadata.get('connection_aliases',{})
    ref=props.get('SMTPConnection')
    ref=aliases.get(ref,aliases.get(str(ref).strip('{}').casefold(),ref))
    profile=package.metadata.get('smtp_connections',{}).get(ref)
    if not profile or not profile.get('host'):
        raise ConversionGap('SMTP_METADATA_REQUIRED','Capture the source SMTP connection and Send Mail Task properties.')
    if str(profile.get('windows_authentication','False')).casefold() not in {'false','0'}:
        raise ConversionGap('SMTP_AUTHENTICATION_REVIEW_REQUIRED','Windows SMTP authentication needs an explicit target credential design.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',profile['host']) or not str(profile['port']).isdigit() or not 1<=int(profile['port'])<=65535:
        raise ConversionGap('SMTP_METADATA_REQUIRED','Invalid captured SMTP host/port.')
    ssl=str(profile.get('enable_ssl','False')).casefold()
    if ssl not in {'true','false','1','0'}:raise ConversionGap('SMTP_METADATA_REQUIRED','Unknown SMTP TLS setting.')
    if props.get('Attachments'):raise ConversionGap('SMTP_ATTACHMENT_MAPPING_REQUIRED','Mail attachments need target file-path mappings.')
    expressions={e.get('name'):e.get('expression') for e in props.get('_property_expressions',[])}
    allowed={'FromLine','ToLine','CCLine','BCCLine','Subject','MessageSource'}
    if set(expressions)-allowed:raise ConversionGap('SMTP_EXPRESSION_UNSUPPORTED','Unsupported Send Mail Task property expression.')
    def value(name):
        if name in expressions:
            match=re.fullmatch(r'\s*@\[([^\]]+)\]\s*',expressions[name] or '')
            if not match:raise ConversionGap('SMTP_EXPRESSION_UNSUPPORTED','Mail expressions currently require one explicit variable reference.')
            return runtime.read(match[1],exe.ref_id)
        return repr(props.get(name,''))
    body_type=str(props.get('MessageSourceType','DirectInput'))
    if body_type in {'Variable','2'} and 'MessageSource' not in expressions:
        body=runtime.read(props.get('MessageSource'),exe.ref_id)
    elif body_type in {'DirectInput','0'}:body=value('MessageSource')
    else:raise ConversionGap('SMTP_BODY_MAPPING_REQUIRED','File-backed mail bodies need a target file mapping.')
    if not props.get('FromLine') and 'FromLine' not in expressions:
        raise ConversionGap('SMTP_METADATA_REQUIRED','A captured sender or sender expression is required.')
    if not any(props.get(k) or k in expressions for k in ['ToLine','CCLine','BCCLine']):
        raise ConversionGap('SMTP_METADATA_REQUIRED','A captured recipient or recipient expression is required.')
    priority=str(props.get('Priority','Normal'))
    if priority not in {'Normal','High','Low'}:raise ConversionGap('SMTP_METADATA_REQUIRED','Unknown source mail priority.')
    lines=['import smtplib','import ssl','from email.message import EmailMessage','from email.utils import getaddresses',
        '_ssis_message = EmailMessage()',f'_ssis_sender = {value("FromLine")}',
        f'_ssis_to = {value("ToLine")}',f'_ssis_cc = {value("CCLine")}',f'_ssis_bcc = {value("BCCLine")}',
        "_ssis_message['From'] = _ssis_sender", "_ssis_message['To'] = _ssis_to.replace(';', ',')",
        "if _ssis_cc: _ssis_message['Cc'] = _ssis_cc.replace(';', ',')",
        f'_ssis_message["Subject"] = {value("Subject")}',
        f'_ssis_message["X-Priority"] = { {"High":"1","Normal":"3","Low":"5"}[priority]!r}',
        f'_ssis_message.set_content({body})',
        "_ssis_recipients = [address for _,address in getaddresses([s.replace(';', ',') for s in [_ssis_to,_ssis_cc,_ssis_bcc]]) if address]",
        "if not _ssis_recipients: raise RuntimeError('Source mail task has no recipients.')",
        f'with smtplib.SMTP({profile["host"]!r}, {int(profile["port"])}, timeout=30) as _ssis_smtp:']
    if ssl in {'true','1'}:lines.append('    _ssis_smtp.starttls(context=ssl.create_default_context())')
    lines.append('    _ssis_smtp.send_message(_ssis_message, from_addr=_ssis_sender, to_addrs=_ssis_recipients)')
    return '\n'.join(lines)


def edge_expression(edge,runtime,owner):
    operation=edge.get('evaluation_operation') or 'Constraint'
    operation={'0':'Constraint','1':'Expression','2':'ExpressionAndConstraint','3':'ExpressionOrConstraint'}.get(operation,operation)
    expected=edge.get('value') or 'Success'
    expected={'0':'Success','1':'Failure','2':'Completion'}.get(expected,expected)
    if expected not in {'Success','Failure','Completion'}:raise ConversionGap('EVENT_CONSTRAINT_UNSUPPORTED','Unknown event constraint result.')
    status=f'_ssis_event_status.get({edge["from"]!r})'
    executed=f'({status} in ("Success", "Failure"))'
    match=executed if expected=='Completion' else f'({status} == {expected!r})'
    if operation=='Constraint':return match
    expr=condition(edge.get('expression'),owner,runtime)
    if operation=='Expression':return f'({executed} and {expr})'
    if operation=='ExpressionAndConstraint':return f'({match} and {expr})'
    if operation=='ExpressionOrConstraint':return f'({executed} and ({match} or {expr}))'
    raise ConversionGap('EVENT_CONSTRAINT_UNSUPPORTED','Unknown event constraint operation.')


def handler_setup(plan,operations):
    refs=plan['metadata']['package_error_handler']['source_ids']
    steps={s['metadata'].get('source_ref_id'):s for s in plan['steps'] if s['metadata'].get('source_ref_id') in refs}
    incoming={ref:[] for ref in refs}
    for edge in plan['metadata']['package_error_handler']['edges']:incoming[edge['to']].append(edge)
    lines=["_ssis_error_codes = json.loads(dbutils.widgets.get('ssis_error_codes'))",
        "if not isinstance(_ssis_error_codes, dict): raise RuntimeError('SSIS error-code mapping must be a JSON object.')",
        '_ssis_event_errors = []',
        'def _ssis_run_package_error(_ssis_failed_name, _ssis_failed_description, _ssis_original):',
        "    _ssis_values['System::SourceName'] = _ssis_failed_name",
        "    _ssis_values['System::SourceDescription'] = _ssis_failed_description",
        "    _ssis_values['System::ErrorDescription'] = str(_ssis_original)",
        "    _ssis_error_class = (_ssis_original.getCondition() if hasattr(_ssis_original, 'getCondition') else _ssis_original.getErrorClass() if hasattr(_ssis_original, 'getErrorClass') else None) or type(_ssis_original).__name__",
        "    _ssis_values.pop('System::ErrorCode', None)",
        "    if _ssis_error_class in _ssis_error_codes: _ssis_values['System::ErrorCode'] = _ssis_error_codes[_ssis_error_class]",
        '    _ssis_event_status = {}']
    for ref in refs:
        edges=incoming[ref]
        kinds={e.get('logical_and') is not False for e in edges}
        joiner=' and ' if kinds!={False} else ' or '
        predicate=joiner.join('('+e['compiled_condition']+')' for e in edges) or 'True'
        lines += ['    if '+predicate+':','        try:']
        bodies=[o['code'] for o in operations.get(steps[ref]['step_id'],[])]
        body='\n'.join(bodies) or "raise RuntimeError('Event task implementation is unavailable.')"
        lines += ['            '+line if line.strip() else '' for line in body.splitlines()]
        lines += [f'            _ssis_event_status[{ref!r}] = "Success"',
            '        except Exception as _ssis_handler_error:',f'            _ssis_event_status[{ref!r}] = "Failure"',
            f'            _ssis_event_errors.append({{"source":{ref!r},"error":str(_ssis_handler_error)}})',
            '    else:',f'        _ssis_event_status[{ref!r}] = "Skipped"']
    lines.append('    return _ssis_event_status')
    return '\n'.join(lines)
