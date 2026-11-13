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
        self.derived = {}
        self.deriving = set()

    def fork(self):
        other = BindingContext(self.package)
        other.inputs, other.defaults = {k:dict(v) for k,v in self.inputs.items()}, dict(self.defaults)
        other.derived,other.deriving=dict(self.derived),set(self.deriving)
        return other

    def adopt(self, other):
        self.inputs, self.defaults = other.inputs, other.defaults
        self.derived,self.deriving=other.derived,other.deriving

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
            if (not context.get('expression') or context.get('evaluate_as_expression') is False) and context.get('value') is not None and context['status'] != 'SENSITIVE_REDACTED':
                self.defaults[key] = context['value']
        elif reference.startswith('System::'):
            attrs = self.package.metadata.get('package_attributes', {})
            captured = {'System::PackageName':self.package.package_name,
                'System::PackageID':attrs.get('DTSID'), 'System::VersionGUID':attrs.get('VersionGUID'),
                'System::VersionMajor':attrs.get('VersionMajor'), 'System::VersionMinor':attrs.get('VersionMinor')}
            kind = 'datetime' if reference == 'System::StartTime' else 'int32' if reference in {'System::VersionMajor','System::VersionMinor','System::ErrorCode'} else 'string'
            if captured.get(reference) is not None: self.defaults[key] = captured[reference]
        elif reference.startswith('$Package::'):
            parameters=[p for p in self.package.metadata.get('package_parameters',[]) if p.get('name')==reference.split('::',1)[1]]
            if len(parameters)>1:raise ConversionGap('VARIABLE_BINDING_UNRESOLVED','Duplicate package parameter declaration.')
            if parameters:
                parameter=parameters[0]
                kind={'2':'int16','3':'int32','20':'int64','8':'string','7':'datetime','11':'bool','4':'float','5':'float','6':'decimal','14':'decimal'}.get(str(parameter.get('data_type')),'scalar')
                if not parameter.get('required') and not parameter.get('sensitive') and parameter.get('default_value') is not None:
                    self.defaults[key]=parameter['default_value']
        elif not reference.startswith('$Project::'):
            raise ConversionGap('VARIABLE_BINDING_UNRESOLVED', f'Unsupported variable namespace: {reference}')
        record = {'key':key,'reference':reference,'kind':kind,'scope_ref_id':context.get('scope_ref_id'),
            'read':not write, 'write':write}
        previous = self.inputs.get(key)
        if previous and previous['kind'] != kind:
            raise ConversionGap('VARIABLE_BINDING_UNRESOLVED', f'Conflicting types for {reference}')
        if previous:
            record.update(read=previous.get('read',False) or not write, write=previous.get('write',False) or write,
                numeric_fragment=previous.get('numeric_fragment',False))
            if 'fixed_sql_text' in previous:record['fixed_sql_text']=previous['fixed_sql_text']
        self.inputs[key] = record
        if reference.startswith('User::') and context['status']!='SENSITIVE_REDACTED' and context.get('expression') and context.get('evaluate_as_expression') is not False:
            if context.get('evaluate_as_expression') is not True:
                raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Expression evaluation flag is unavailable; do not substitute a runtime override for computed source logic.')
            if write:raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Result binding into an expression variable requires explicit source review.')
            if key in self.deriving or len(self.deriving)>16:raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Cyclic or excessive variable-expression dependencies.')
            if key not in self.derived:
                from ssis_migration.scalar_expressions import compile_scalar
                self.deriving.add(key)
                try:
                    scope=context.get('scope_ref_id') or owner
                    def resolve(ref):
                        expression=self.read(ref,scope)
                        dependency_key,_=self.variable(ref,scope)
                        return expression,self.inputs[dependency_key]['kind']
                    expression,_=compile_scalar(context['expression'],resolve,clock=self.source_clock)
                    self.derived[key]=expression
                finally:self.deriving.discard(key)
            self.inputs[key]['derived']=True
        return key, kind

    def read(self, reference, owner, *, numeric=False):
        key, kind = self.variable(reference, owner)
        if numeric: self.inputs[key]['numeric_fragment'] = True
        value = f'_ssis_value({key!r}, {kind!r})'
        if key in self.derived:value=f'_ssis_coerce({self.derived[key]}, {kind!r})'
        return f'_ssis_numeric({value})' if numeric else value

    def source_clock(self,utc=False):
        self.inputs['$Runtime::SourceClock']={'key':'$Runtime::SourceClock','reference':'$Runtime::SourceClock',
            'kind':'string','read':False,'write':False,'clock_override':True,'origin':'optional_source_clock'}
        if not utc:
            self.inputs['$Runtime::SourceTimeZone']={'key':'$Runtime::SourceTimeZone','reference':'$Runtime::SourceTimeZone',
                'kind':'string','read':True,'write':False,'timezone':True,'origin':'source_clock'}
        return "_ssis_source_now('UTC')" if utc else '_ssis_source_now()'


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


def result_bindings(properties):
    results = properties.get('result_bindings', [])
    result_type = str(properties.get('ResultType','ResultSetType_None'))
    if result_type not in {'','0','None','ResultSetType_None','1','ResultSetType_SingleRow'}:
        raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Full result sets and XML bindings require separate translation.')
    if bool(results) != (result_type in {'1','ResultSetType_SingleRow'}):
        raise ConversionGap('SQL_BINDINGS_UNSUPPORTED', 'Single-row result mode must have explicit ordinal bindings.')
    return results


def bound_task(properties, owner, connection, config, runtime):
    parent_runtime, runtime = runtime, runtime.fork()
    sql = properties.get('SqlStatementSource') or ''
    results = result_bindings(properties)
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
            arguments[marker] = runtime.read(match[2], context.get('scope_ref_id') or owner, numeric=True)
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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import calendar
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

def _ssis_substring(value, start, length):
    if start < 1 or length < 0:
        raise RuntimeError('Invalid SSIS substring bounds.')
    return value.encode('utf-16-le', 'surrogatepass')[(start-1)*2:(start-1+length)*2].decode('utf-16-le', 'surrogatepass')

def _ssis_left(value, length):
    return _ssis_substring(value, 1, length)

def _ssis_right(value, length):
    if length < 0: raise RuntimeError('Invalid SSIS right-string length.')
    if length == 0: return ''
    size = len(value.encode('utf-16-le', 'surrogatepass')) // 2
    return _ssis_substring(value, max(1, size-length+1), length)

def _ssis_source_now(zone=None):
    zone = zone or _ssis_value('$Runtime::SourceTimeZone', 'string')
    if '$Runtime::SourceClock' in _ssis_values:
        return _ssis_source_clock_override().astimezone(ZoneInfo(zone)).replace(tzinfo=None)
    return datetime.now(ZoneInfo(zone)).replace(tzinfo=None)

def _ssis_source_clock_override():
    value = _ssis_values['$Runtime::SourceClock']
    if not isinstance(value,str): raise RuntimeError('Source clock must be an ISO timestamp with offset.')
    value = datetime.fromisoformat(value)
    if value.tzinfo is None: raise RuntimeError('Replay clock requires an explicit UTC offset.')
    return value

def _ssis_datepart(part, value):
    value = _ssis_coerce(value, 'datetime')
    parts = {'year':value.year, 'quarter':(value.month-1)//3+1, 'month':value.month,
        'dayofyear':value.timetuple().tm_yday, 'day':value.day, 'hour':value.hour,
        'minute':value.minute, 'second':value.second, 'millisecond':value.microsecond//1000}
    if part not in parts: raise RuntimeError('Unsupported date part.')
    return parts[part]

def _ssis_dateadd(part, number, value):
    value = _ssis_coerce(value, 'datetime')
    number = _ssis_coerce(number, 'int32')
    if part in ('year','quarter','month'):
        months = number * {'year':12,'quarter':3,'month':1}[part]
        year, month = divmod(value.year*12+value.month-1+months, 12)
        if not 1 <= year <= 9999: raise RuntimeError('Date arithmetic overflow.')
        return value.replace(year=year, month=month+1, day=min(value.day,calendar.monthrange(year,month+1)[1]))
    units = {'day':'days','dayofyear':'days','hour':'hours','minute':'minutes','second':'seconds','millisecond':'milliseconds'}
    if part not in units: raise RuntimeError('Unsupported date part.')
    return value + timedelta(**{units[part]:number})
'''
