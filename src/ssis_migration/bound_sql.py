"""Native SQL arguments and bounded result bindings; never interpolate runtime SQL."""
from __future__ import annotations

import json
import re
import sqlglot
from sqlglot import exp, ErrorLevel

from ssis_migration.mapping import ConversionGap, translate_sql
from ssis_migration.source_context import describe_variable
from ssis_migration.sql_text import mask_sql


def bindable_sql(sql):
    masked = mask_sql(sql)
    return re.sub(r'\[(?:[^\]]|\]\])*\]|"(?:[^"]|"")*"', lambda m:' ' * len(m[0]), masked)


class BindingContext:
    def __init__(self, package):
        self.package = package
        self.inputs = {}
        self.defaults = {}

    def fork(self):
        other = BindingContext(self.package)
        other.inputs, other.defaults = {k:dict(v) for k,v in self.inputs.items()}, dict(self.defaults)
        return other

    def adopt(self, other):
        self.inputs, self.defaults = other.inputs, other.defaults

    def variable(self, reference, owner, *, write=False):
        if not isinstance(reference, str) or not reference.strip():
            raise ConversionGap('VARIABLE_BINDING_UNRESOLVED', 'A variable binding needs its source reference.')
        reference = reference.strip()
        context = describe_variable(reference, owner, self.package)
        kind = 'scalar'
        key = reference
        if reference.startswith('User::'):
            if context['status'] in {'AMBIGUOUS','NOT_FOUND'}:
                raise ConversionGap('VARIABLE_BINDING_UNRESOLVED', f'Cannot resolve {reference} in its lexical scope.')
            matches = [v for v in self.package.variables if f'{v.namespace or "User"}::{v.name}' == reference]
            if len(matches) > 1:
                if not context.get('scope_ref_id'):
                    raise ConversionGap('VARIABLE_BINDING_UNRESOLVED', f'Cannot disambiguate {reference}.')
                key = context['scope_ref_id'] + '|' + reference
            kind = {'2':'int16','3':'int32','20':'int64','8':'string','7':'datetime','11':'bool',
                '4':'float','5':'float','6':'decimal','14':'decimal'}.get(str(context.get('data_type')), 'scalar')
            if not context.get('expression') and context.get('value') is not None and context['status'] != 'SENSITIVE_REDACTED':
                self.defaults[key] = context['value']
        elif reference.startswith('System::'):
            attrs = self.package.metadata.get('package_attributes', {})
            captured = {'System::PackageName':self.package.package_name,
                'System::PackageID':attrs.get('DTSID'), 'System::VersionGUID':attrs.get('VersionGUID'),
                'System::VersionMajor':attrs.get('VersionMajor'), 'System::VersionMinor':attrs.get('VersionMinor')}
            kind = 'datetime' if reference == 'System::StartTime' else 'int32' if reference in {'System::VersionMajor','System::VersionMinor'} else 'string'
            if captured.get(reference) is not None: self.defaults[key] = captured[reference]
        elif not reference.startswith(('$Package::','$Project::')):
            raise ConversionGap('VARIABLE_BINDING_UNRESOLVED', f'Unsupported variable namespace: {reference}')
        record = {'key':key,'reference':reference,'kind':kind,'scope_ref_id':context.get('scope_ref_id'),
            'read':not write, 'write':write}
        previous = self.inputs.get(key)
        if previous and previous['kind'] != kind:
            raise ConversionGap('VARIABLE_BINDING_UNRESOLVED', f'Conflicting types for {reference}')
        if previous:
            record.update(read=previous.get('read',False) or not write, write=previous.get('write',False) or write,
                numeric_fragment=previous.get('numeric_fragment',False))
        self.inputs[key] = record
        return key, kind

    def read(self, reference, owner, *, numeric=False):
        key, kind = self.variable(reference, owner)
        if numeric: self.inputs[key]['numeric_fragment'] = True
        value = f'_ssis_value({key!r}, {kind!r})'
        return f'_ssis_numeric({value})' if numeric else value


def require_targets(uses):
    if any(u['resolution'] == 'PLACEHOLDER' for u in uses):
        raise ConversionGap('TARGET_LOCATION_UNRESOLVED', 'Supply a target schema or override before binding executable SQL.')


def _named_sql(sql, bindings, owner, runtime):
    mapping = {}
    for b in bindings:
        if str(b.get('ParameterDirection','Input')) not in {'Input','1'}:
            raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Output/input-output parameters require separate translation.')
        name = str(b.get('ParameterName','')).lstrip('@').casefold()
        if not re.fullmatch(r'[A-Za-z_]\w*', name) or name in mapping:
            raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Expected unique named input bindings.')
        mapping[name] = b.get('DtsVariableName')
    matches = list(re.finditer(r'(?<!@)@([A-Za-z_]\w*)', bindable_sql(sql)))
    names, arguments, edits = {}, {}, []
    for match in matches:
        name = match[1].casefold()
        if name not in mapping:
            raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', f'No input binding for @{match[1]}')
        marker = names.setdefault(name, 'ssis_p' + str(len(names)))
        arguments[marker] = runtime.read(mapping[name], owner)
        edits.append((match.start(), match.end(), ':' + marker))
    if set(mapping) != set(names):
        raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Unused input bindings require source/provider review.')
    for a,b,replacement in reversed(edits): sql = sql[:a] + replacement + sql[b:]
    return sql, arguments


def _result_code(frame, results, owner, runtime):
    if not results: return []
    keys, positions, lines = set(), set(), [f'_ssis_rows = {frame}.limit(2).collect()',
        'if len(_ssis_rows) != 1:', "    raise RuntimeError('Single-row result binding requires exactly one row.')"]
    for binding in results:
        name = str(binding.get('ResultName',''))
        if not name.isdigit() or int(name) in positions:
            raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Only unique ordinal result bindings are supported.')
        positions.add(int(name))
        key, kind = runtime.variable(binding.get('DtsVariableName'), owner, write=True)
        if key in keys: raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Duplicate result variable binding.')
        keys.add(key)
        lines += [f'if len(_ssis_rows[0]) <= {int(name)}:',
            "    raise RuntimeError('Result binding ordinal is absent from the source row.')",
            f'_ssis_values[{key!r}] = _ssis_coerce(_ssis_rows[0][{int(name)}], {kind!r})']
    return lines


def sql_call(sql, arguments):
    args = ', '.join(f'{key!r}: {value}' for key,value in arguments.items())
    return f'spark.sql({sql!r}' + (', args={' + args + '}' if arguments else '') + ')'


def relational_batch(sql):
    try:
        return [s for s in sqlglot.parse(sql, read='tsql', error_level=ErrorLevel.RAISE) if s]
    except Exception as original:
        # SQL Server permits an omitted semicolon between INSERT VALUES and
        # a following SELECT. Recover only that bounded, fully parsed shape.
        clean = bindable_sql(sql)
        for match in re.finditer(r'\bSELECT\b', clean, re.I):
            prefix = clean[:match.start()]
            if prefix.count('(') != prefix.count(')'): continue
            try:
                head = sqlglot.parse_one(sql[:match.start()], read='tsql', error_level=ErrorLevel.RAISE)
                tail = [s for s in sqlglot.parse(sql[match.start():], read='tsql', error_level=ErrorLevel.RAISE) if s]
                if isinstance(head, exp.Insert) and isinstance(head.expression, exp.Values) and len(tail) == 1 and isinstance(tail[0],exp.Query):
                    return [head, tail[0]]
            except Exception: continue
        raise ConversionGap('SQL_PARSE_FAILED', str(original)) from original


def bound_task(properties, owner, connection, config, runtime):
    parent_runtime, runtime = runtime, runtime.fork()
    sql = properties.get('SqlStatementSource') or ''
    results = properties.get('result_bindings', [])
    result_type = str(properties.get('ResultType','ResultSetType_None'))
    if result_type not in {'','0','None','ResultSetType_None','1','ResultSetType_SingleRow'}:
        raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Full result sets and XML bindings require separate translation.')
    if bool(results) != (result_type in {'1','ResultSetType_SingleRow'}):
        raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Single-row result mode must have explicit ordinal bindings.')
    dynamic = re.fullmatch(r"\s*exec(?:ute)?\s*\(\s*'SELECT\s+Count\(\*\)\s+FROM\s+'\s*\+\s*@([A-Za-z_]\w*)\s*\)\s*;?\s*", sql, re.I)
    guards = []
    if dynamic:
        bindings = properties.get('parameter_bindings', [])
        if len(bindings) != 1 or str(bindings[0].get('ParameterName','')).lstrip('@').casefold() != dynamic[1].casefold():
            raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Dynamic row count requires one matching table-name binding.')
        ref = bindings[0].get('DtsVariableName')
        context = describe_variable(ref, owner, runtime.package)
        table = context.get('value')
        if context.get('expression') or not isinstance(table,str) or not table:
            raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Row-count table identity needs a captured non-expression declaration.')
        # Table names are parsed/mapped at generation time, never concatenated at runtime.
        try:
            parsed = sqlglot.parse_one(table, read='tsql', into=exp.Table)
            if parsed.args.get('alias') or not 1 <= len(parsed.parts) <= 3 or any(not isinstance(p,exp.Identifier) for p in parsed.parts): raise ValueError()
        except Exception as exc: raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Invalid row-count table identity.') from exc
        guards = [f'if {runtime.read(ref, owner)} != {table!r}:',
            "    raise RuntimeError('Row-count table identity changed; regenerate target naming.')"]
        sql = 'SELECT COUNT(*) AS source_count FROM ' + parsed.sql(dialect='tsql')
        arguments = {}
    else:
        sql, arguments = _named_sql(sql, properties.get('parameter_bindings', []), owner, runtime)
    statements = relational_batch(sql)
    if not 1 <= len(statements) <= 8:
        raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Expected a bounded relational statement batch.')
    if any(isinstance(s,exp.Query) for s in statements[:-1]) or (results and not isinstance(statements[-1],exp.Query)):
        raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Only one final query may supply bound result values.')
    code, uses, translated = list(guards), [], []
    for i, statement in enumerate(statements):
        parameters = {n.name for n in statement.find_all(exp.Placeholder)}
        emitted, mapped = translate_sql(statement.sql(dialect='tsql'), connection, config, parameter_names=arguments.keys())
        require_targets(mapped)
        uses.extend(mapped); translated.append(emitted)
        frame = '_ssis_frame_' + str(i)
        code.append(f'{frame} = ' + sql_call(emitted, {k:v for k,v in arguments.items() if k in parameters}))
        if i == len(statements)-1: code += _result_code(frame, results, owner, runtime)
    identity_review = len(statements)>1 and any(isinstance(s,exp.Insert) for s in statements[:-1]) and any(statements[-1].find_all(exp.Max))
    parent_runtime.adopt(runtime)
    return '\n'.join(code), uses, {'dynamic_row_count':bool(dynamic),'batch':len(statements)>1,
        'identity_review':bool(identity_review), 'statements':translated}


def variable_query(context, owner, connection, config, runtime):
    parent_runtime, runtime = runtime, runtime.fork()
    if context.get('evaluate_as_expression') is not True or context.get('status') != 'DECLARED_REQUIRES_RUNTIME_REVIEW':
        raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED', 'Variable query requires a scoped expression declaration with evaluation enabled.')
    text = context.get('expression') or ''
    token = re.compile(r'\s*("(?:[^"\\]|\\.)*"|@\[([^\]]+)\])\s*')
    pos, parts, arguments, expect_value = 0, [], {}, True
    while pos < len(text):
        if not expect_value:
            match = re.match(r'\s*\+\s*', text[pos:])
            if not match: raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED', 'Only literal/reference concatenation is supported.')
            pos += match.end(); expect_value=True; continue
        match = token.match(text,pos)
        if not match: raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED', 'Unsupported SSIS SQL expression token.')
        if match[1].startswith('"'):
            if re.search(r'\\(?!["\\nrtbf])', match[1][1:-1]):
                raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED', 'Unsupported SSIS string escape.')
            try: parts.append(json.loads(match[1], strict=False))
            except ValueError as exc: raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED', 'Unsupported SSIS string escape.') from exc
        else:
            marker = 'ssis_p' + str(len(arguments))
            arguments[marker] = runtime.read(match[2], owner, numeric=True)
            parts.append(':' + marker)
        pos=match.end(); expect_value=False
    if expect_value or not parts:
        raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED', 'Incomplete variable query expression.')
    query=''.join(parts)
    try: parsed=sqlglot.parse_one(query,read='tsql',error_level=ErrorLevel.RAISE)
    except Exception as exc:
        raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED', 'The variable SQL template cannot be parsed completely.') from exc
    actual={n.name for n in parsed.find_all(exp.Placeholder)}
    if actual != set(arguments):
        raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED', 'References inside SQL strings, identifiers, or comments need separate translation.')
    emitted,uses=translate_sql(query,connection,config,query_only=True,parameter_names=arguments.keys())
    require_targets(uses)
    parent_runtime.adopt(runtime)
    return sql_call(emitted,arguments),uses


def runtime_setup(defaults):
    return '''import json
import re
import math
from decimal import Decimal
from datetime import datetime
_ssis_values = ''' + repr(defaults) + '''
_ssis_supplied = json.loads(dbutils.widgets.get('ssis_runtime_values'))
if not isinstance(_ssis_supplied, dict):
    raise RuntimeError('SSIS runtime values must be a JSON object.')
_ssis_values.update(_ssis_supplied)

def _ssis_coerce(value, kind):
    if value is None:
        raise RuntimeError('NULL variable binding requires an explicit source nullability decision.')
    if kind in ('int16', 'int32', 'int64'):
        if isinstance(value, bool) or not re.fullmatch(r'[+-]?\\d+', str(value)):
            raise RuntimeError('Expected an integer SSIS variable value.')
        value = int(value)
        bits = {'int16':16, 'int32':32, 'int64':64}[kind]
        if not -(2**(bits-1)) <= value < 2**(bits-1):
            raise RuntimeError('SSIS variable integer overflow.')
    elif kind == 'string':
        if not isinstance(value,str): raise RuntimeError('Expected a string SSIS variable value.')
    elif kind == 'datetime':
        value = datetime.fromisoformat(value) if isinstance(value,str) else value
        if not isinstance(value,datetime) or value.tzinfo is not None:
            raise RuntimeError('Expected a naive datetime; confirm the source timezone policy.')
    elif kind == 'bool':
        if isinstance(value,str) and value.casefold() in ('true','false','1','0','-1'):
            value = value.casefold() in ('true','1','-1')
        if not isinstance(value,bool): raise RuntimeError('Expected a Boolean SSIS variable value.')
    elif kind in ('float','decimal'):
        value = Decimal(str(value)) if kind == 'decimal' else float(value)
        if not math.isfinite(value): raise RuntimeError('Non-finite variable binding is unsupported.')
    elif kind == 'scalar':
        if not isinstance(value,(str,int,float,bool,Decimal,datetime)):
            raise RuntimeError('Expected a scalar SSIS runtime value.')
    return value

def _ssis_value(key, kind):
    if key not in _ssis_values:
        raise RuntimeError('Missing SSIS runtime value: ' + key)
    return _ssis_coerce(_ssis_values[key], kind)

def _ssis_numeric(value):
    if isinstance(value,bool) or not re.fullmatch(r'[+-]?\\d+(?:\\.\\d+)?',str(value)):
        raise RuntimeError('A bare numeric SQL expression reference requires a numeric value.')
    return int(str(value)) if '.' not in str(value) else Decimal(str(value))
'''
