"""Bounded target cell-snapshot kernel. No source parsing, eval, IO or Spark.

This exact utility source is embedded in generated Databricks notebooks so a
reviewed candidate needs no package installation or imports from this checkout.
"""
from decimal import Decimal
import math

MAX_EXACT_INTEGER = (1 << 53) - 1


def exact_integer(value):
    if type(value) is int:
        result = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        result = int(value)
    elif isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        result = int(value)
    else:
        raise ValueError('Numeric input/result is not a finite exact integer.')
    if abs(result) > MAX_EXACT_INTEGER:
        raise ValueError('Numeric input/intermediate result exceeds the exact IEEE754 integer domain.')
    return result


def target_expression(expression, environment):
    operation = expression['op']
    if operation == 'literal':
        return expression['value']
    if operation == 'variable':
        return environment[expression['name']]
    if operation == 'negate':
        return exact_integer(-target_expression(expression['value'], environment))
    if operation == 'positive':
        return exact_integer(target_expression(expression['value'], environment))
    left = target_expression(expression['left'], environment)
    right = target_expression(expression['right'], environment)
    if operation == '+':return exact_integer(left + right)
    if operation == '-':return exact_integer(left - right)
    if operation == '*':return exact_integer(left * right)
    if operation == '=':return left == right
    if operation == '<>':return left != right
    if operation == '>':return left > right
    if operation == '<':return left < right
    if operation == '>=':return left >= right
    if operation == '<=':return left <= right
    raise ValueError('Unknown target expression operation.')


def _typed(value, kind):
    if kind == 'NUMERIC':return exact_integer(value)
    if kind == 'STRING' and type(value) is str:return value
    raise ValueError('Input does not match its explicitly declared source type.')


def _coordinate(values, model):
    if len(values) != len(model['coordinates']):
        raise ValueError('Coordinate arity does not match the source cube.')
    result = []
    for value, coordinate in zip(values, model['coordinates']):
        if type(value) is not str or value not in coordinate['members']:
            raise ValueError('Coordinate is not an exact supplied canonical leaf member.')
        result.append(value)
    return tuple(result)


def _cell_state(cells, model, limit):
    if len(cells) > limit:raise ValueError('Input exceeds the declared preview bound; no prefix was processed.')
    columns = [c['column'] for c in model['coordinates']]
    expected_fields = set(columns + [model['value_column']])
    state = {}
    for cell in cells:
        if set(cell) != expected_fields:raise ValueError('Cell input schema differs from the explicit mapping.')
        key = _coordinate([cell[c] for c in columns], model)
        if key in state:raise ValueError('Duplicate cube coordinates; no first/last input value was guessed.')
        state[key] = exact_integer(cell[model['value_column']])
    domain_size = math.prod(len(c['members']) for c in model['coordinates'])
    if domain_size > limit:raise ValueError('Declared coordinate domain exceeds the preview bound.')
    if len(state) != domain_size:
        raise ValueError('This capability requires a complete explicit leaf matrix; sparse/empty-cell semantics are unsupported.')
    return state


def _statements(statements, environment, state, model):
    for statement in statements:
        operation = statement['op']
        if operation == 'assign':
            environment[statement['name']] = _typed(target_expression(statement['value'], environment), statement['type'])
        elif operation == 'skip_record':
            return True  # Earlier writes stay applied; this is not rollback.
        elif operation == 'put':
            key = _coordinate([target_expression(v, environment) for v in statement['coordinates']], model)
            state[key] = exact_integer(target_expression(statement['value'], environment))
        elif operation == 'branch':
            for branch in statement['branches']:
                condition = branch['condition']
                if condition is None or target_expression(condition, environment):
                    if _statements(branch['body'], environment, state, model):return True
                    break
        else:raise ValueError('Unknown target statement; nothing was silently skipped.')
    return False


def execute_program(program, cells, rows, parameters):
    """Return a fresh snapshot, preserving source order within the declared domain."""
    limit = program['max_rows'];model = program['model']
    state = _cell_state(cells, model, limit)
    if len(rows) > limit:raise ValueError('Datasource exceeds the preview bound; no prefix was processed.')
    kind = program['kind']
    if kind == 'row_process':
        declarations = program['parameters']
        if set(parameters) != set(declarations):raise ValueError('Supply exactly the declared process parameters; defaults are not substituted.')
        parameters = {n:_typed(parameters[n], t) for n,t in declarations.items()}
        columns = program['source_columns'];order = program['order_column']
        normalized = []
        seen = set()
        for row in rows:
            if set(row) != set(columns):raise ValueError('Datasource schema differs from supplied metadata.')
            row = {n:_typed(row[n], t) for n,t in columns.items()}
            ordinal = exact_integer(row[order])
            if ordinal in seen:raise ValueError('Source record ordinals must be unique; row order was not guessed.')
            seen.add(ordinal);normalized.append(row)
        for row in sorted(normalized, key=lambda r:r[order]):
            environment = {**parameters, **{name:row[column] for name,column in program['row_bindings'].items()}}
            _statements(program['statements'], environment, state, model)
    elif kind == 'constant_rules':
        if rows or parameters:raise ValueError('Rules have no row/parameter inputs in this capability.')
        for rule in program['rules']:
            state[_coordinate(rule['coordinates'],model)] = exact_integer(target_expression(rule['value'],{}))
    elif kind == 'cell_query':
        if rows or parameters:raise ValueError('Literal cell queries have no row/parameter inputs.')
        requests = program['requests']
        if len(requests) > limit:raise ValueError('Query cellset exceeds the preview bound.')
        result = []
        for request in requests:
            key = _coordinate(request['coordinates'],model)
            result.append({**request['axis_positions'], **{c['column']:v for c,v in zip(model['coordinates'],key)},
                           model['value_column']:state[key]})
        return result
    else:raise ValueError('Unknown target program kind.')
    return [{**{c['column']:v for c,v in zip(model['coordinates'],key)},model['value_column']:state[key]} for key in sorted(state)]
