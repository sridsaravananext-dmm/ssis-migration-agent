"""Optional leaf-rule kernel extension; existing candidate utility bytes stay stable."""
from pathlib import Path

from . import runtime
from .runtime import (_cell_state, _coordinate, exact_integer, target_expression,
                      execute_program as _base_execute_program)


def kernel_source(programs):
    """Embed trusted extension definitions only for the explicit leaf-rule profile."""
    source = Path(runtime.__file__).read_text(encoding='utf-8')
    if not any(p['kind']=='leaf_rules' for p in programs.values()): return source
    extension = Path(__file__).read_text(encoding='utf-8').split('\n# BEGIN_EMBEDDED_LEAF_KERNEL\n',1)[1]
    return source+'\n\n_base_execute_program = execute_program\n'+extension


# BEGIN_EMBEDDED_LEAF_KERNEL
def _resolve_leaf_reads(expression, state, model):
    """Replace cell reads with exact evaluated values; reuse the base arithmetic."""
    if expression['op']=='cell_value':
        key = _coordinate(expression['coordinates'],model)
        return {'op':'literal','value':exact_integer(state[key])}
    return {key:_resolve_leaf_reads(value,state,model) if isinstance(value,dict) and 'op' in value else value
            for key,value in expression.items()}


def execute_program(program, cells, rows, parameters):
    if program['kind']!='leaf_rules':
        return _base_execute_program(program,cells,rows,parameters)
    if rows or parameters:
        raise ValueError('Leaf rules have no row/parameter inputs in this capability.')
    model = program['model']; state = _cell_state(cells,model,program['max_rows'])
    for rule in program['rules']:
        expression = _resolve_leaf_reads(rule['value'],state,model)
        state[_coordinate(rule['coordinates'],model)] = exact_integer(target_expression(expression,{}))
    return [{**{c['column']:v for c,v in zip(model['coordinates'],key)},model['value_column']:state[key]}
            for key in sorted(state)]
