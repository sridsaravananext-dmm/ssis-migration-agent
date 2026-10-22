"""Fail-closed TM1 semantics → portable preview programs, never vendor text execution."""
from __future__ import annotations

import copy
from decimal import Decimal
from itertools import product
import math

from .metadata.catalog import Catalog

MAX_INTEGER = (1 << 53) - 1


class CapabilityError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise CapabilityError(message)


def one(candidates, label):
    require(len(candidates) == 1, 'Missing/ambiguous declaration: ' + label)
    return candidates[0]['value']


def scalar(expression, symbols):
    kind = expression['kind']
    if kind == 'group':
        return scalar(expression['value'], symbols)
    if kind == 'literal':
        if expression['literal_type'] == 'STRING':
            return {'op':'literal', 'value':expression['value']}, 'STRING'
        number = Decimal(expression['value'])
        require(number.is_finite() and number == number.to_integral_value() and abs(number) <= MAX_INTEGER,
                'Only exact integers within the IEEE754 safe integer domain are supported.')
        return {'op':'literal', 'value':int(number)}, 'NUMERIC'
    if kind == 'variable':
        name = expression['name']
        require(name in symbols, 'Variable has no definite per-record value: ' + name)
        return {'op':'variable', 'name':name}, symbols[name]
    if kind == 'unary':
        value, data_type = scalar(expression['value'], symbols)
        require(data_type == 'NUMERIC' and expression['operator'] in {'+', '-'}, 'Unsupported unary expression.')
        return {'op':'positive' if expression['operator'] == '+' else 'negate', 'value':value}, data_type
    if kind == 'binary':
        left, lt = scalar(expression['left'], symbols); right, rt = scalar(expression['right'], symbols)
        operator = expression['operator']
        require(lt == rt == 'NUMERIC', 'Only numeric arithmetic/comparisons are supported; string comparison semantics require review.')
        require(operator in {'+', '-', '*', '=', '<>', '>', '<', '>=', '<='}, 'Unsupported scalar operator: ' + operator)
        return {'op':operator, 'left':left, 'right':right}, 'NUMERIC' if operator in {'+', '-', '*'} else 'BOOLEAN'
    raise CapabilityError('Unsupported scalar construct: ' + kind)


def typed_expression(expression, symbols, expected):
    target, actual = scalar(expression, symbols)
    require(actual == expected, 'Expression does not match the declared ' + expected + ' type.')
    return target


def numeric_model(catalog, declaration, limit):
    cube = one(catalog.find('cube', declaration['cube']), declaration['cube'])
    require(declaration['coordinates'] and
            [{k:c[k] for k in ('dimension','hierarchy')} for c in declaration['coordinates']] == cube['dimensions'],
            'Coordinate mappings must match the supplied cube dimension order exactly.')
    model = copy.deepcopy(declaration)
    columns = [c['column'] for c in model['coordinates']] + [model['value_column']]
    require(len({c.casefold() for c in columns + ['axis_0','axis_1']}) == len(columns) + 2,
            'Mapped target columns collide, including reserved MDX axis columns.')
    for coordinate in model['coordinates']:
        hierarchy = one(catalog.hierarchy(coordinate['dimension'], coordinate['hierarchy']), 'coordinate hierarchy')
        members = hierarchy['members']
        require(members and all(m['type'] == 'N' and not m['children'] for m in members),
                'This capability requires complete numeric leaf-only hierarchies; consolidations/string members require review.')
        coordinate['members'] = [m['name'] for m in members]
        require(len({name.casefold().replace(' ', '') for name in coordinate['members']}) == len(members),
                'Member names collide under TM1 case/space folding; canonical identity requires review.')
    require(math.prod(len(c['members']) for c in model['coordinates']) <= limit,
            'Complete coordinate domain exceeds the bounded preview limit.')
    return model


def statements(nodes, symbols, declarations, model):
    result = []; assigned = dict(symbols)
    for node in nodes:
        kind = node['kind']
        if kind == 'assignment':
            name = node['name']
            require(name in declarations and declarations[name]['role'] == 'LOCAL',
                    'Only explicitly declared local variables may be assigned: ' + name)
            data_type = declarations[name]['data_type']
            value = typed_expression(node['value'], assigned, data_type)
            result.append({'op':'assign','name':name,'type':data_type,'value':value}); assigned[name] = data_type
        elif kind == 'control' and node['name'] == 'ITEMSKIP':
            result.append({'op':'skip_record'})
            # Continue checking unreachable syntax as well; never discard unsupported behavior.
        elif kind == 'if':
            branches = []; outcomes = []
            for branch in node['branches']:
                condition = None if branch['condition'] is None else typed_expression(branch['condition'], assigned, 'BOOLEAN')
                body, outcome = statements(branch['body'], assigned, declarations, model)
                branches.append({'condition':condition,'body':body}); outcomes.append(outcome)
            if not any(b['condition'] is None for b in node['branches']):outcomes.append(assigned)
            assigned = {n:t for n,t in outcomes[0].items() if all(o.get(n) == t for o in outcomes)}
            result.append({'op':'branch','branches':branches})
        elif kind == 'invoke':
            call = node['call']; arguments = call['arguments']
            require(call['name'].upper() == 'CELLPUTN' and len(arguments) == len(model['coordinates']) + 2,
                    'Only CellPutN with explicit cube coordinates is supported.')
            require(arguments[1]['kind'] == 'literal' and arguments[1]['literal_type'] == 'STRING'
                    and arguments[1]['value'] == model['cube'], 'CellPutN cube must be a literal matching the explicit model.')
            value = typed_expression(arguments[0], assigned, 'NUMERIC')
            coords = [typed_expression(a, assigned, 'STRING') for a in arguments[2:]]
            require(all(c['op'] != 'literal' or c['value'] in axis['members']
                        for c,axis in zip(coords,model['coordinates'])),
                    'CellPutN references a missing canonical leaf member.')
            result.append({'op':'put','value':value,'coordinates':coords})
        else:
            raise CapabilityError('Unsupported TI statement: ' + kind + '/' + str(node.get('name','')))
    return result, assigned


def lower_ti(analysis, config, model, catalog, limit):
    require(model['value_semantics'] == 'STORED_WITHOUT_RULES', 'TI requires stored cells with no rules/feeders.')
    sections = {s['name']:s for s in analysis['sections']}
    require(set(sections) == {'prolog','metadata','data','epilog'} and
            all(s['structure_complete'] for s in sections.values()), 'All four TI sections must be supplied and fully parsed.')
    require(all(not sections[n]['ast'] for n in ('prolog','metadata','epilog')),
            'Nonempty prolog/metadata/epilog lifecycle behavior is outside this preview capability.')
    require(sections['data']['ast'], 'Empty TI data section has no supported row transformation.')
    process = one(catalog.find('process', analysis['process_name']), 'TI process')
    datasource = one(catalog.find('datasource', process['datasource']), 'TI datasource')
    require(datasource['kind'] == 'ASCII', 'Only explicitly mapped ASCII row snapshots are supported; no source connection is opened.')
    columns = {c['name']:c['data_type'] for c in datasource['columns']}
    require(len({name.casefold() for name in columns}) == len(columns), 'Datasource column names collide in Spark.')
    order = config['order_column']
    require(order in columns and columns[order] == 'NUMERIC', 'Supply an explicit numeric source-record ordinal column.')
    require(all(c['data_type'] in {'NUMERIC','STRING'} for c in datasource['columns']), 'Unsupported datasource type.')
    parameters = {p['name']:p['data_type'] for p in process['parameters']}
    require(set(parameters) == set(config['parameters']), 'Supply every process parameter explicitly; defaults are never substituted.')
    for name, data_type in parameters.items():
        value = config['parameters'][name]
        require((data_type == 'STRING' and type(value) is str) or
                (data_type == 'NUMERIC' and type(value) is int and abs(value) <= MAX_INTEGER), 'Invalid explicit parameter: ' + name)
    variables = {v['name']:v for v in process['variables']}
    require(not (set(parameters) & set(variables)), 'Parameter/variable names conflict.')
    names = [*parameters, *variables]
    require(len({name.casefold() for name in names}) == len(names), 'TI symbols collide under case folding.')
    bindings = {}; symbols = dict(parameters)
    for name, declaration in variables.items():
        role = declaration['role']; data_type = declaration['data_type']
        require(role in {'LOCAL','DATASOURCE_COLUMN'}, 'System/persistent variable lifecycle requires review: ' + name)
        if role == 'DATASOURCE_COLUMN':
            column = declaration['column']
            require(column in columns and columns[column] == data_type, 'Datasource variable/column types disagree.')
            bindings[name] = column; symbols[name] = data_type
    body, _ = statements(sections['data']['ast'], symbols, variables, model)
    return {'kind':'row_process','model':model,'max_rows':limit,'parameters':parameters,
            'default_parameters':config['parameters'],'source_columns':columns,'row_bindings':bindings,
            'order_column':order,'datasource_table':config['datasource_table'],'statements':body}


def lower_rules(analysis, config, model):
    require(model['value_semantics'] == 'STORED_BEFORE_SUPPLIED_CONSTANT_RULES', 'Rules require an explicit pre-rule stored snapshot.')
    require(config['datasource_table'] is None and config['order_column'] is None and not config['parameters'],
            'Rule mapping contains unused datasource/parameter declarations.')
    rules = []; seen = set(); coordinates = model['coordinates']
    require(analysis['statements'], 'No constant leaf rules were supplied.')
    for statement in analysis['statements']:
        node = statement['ast']
        require(node['kind'] == 'calculation' and node['qualifier'] == 'N',
                'Only N-qualified full-coordinate constant rules are supported; directives/feeders are blocked.')
        selectors = node['area']['selectors']
        by_dimension = {s['dimension']:s['element'] for s in selectors}
        require(len(by_dimension) == len(selectors) == len(coordinates) and
                set(by_dimension) == {c['dimension'] for c in coordinates}, 'Rule areas must bind every named dimension exactly once.')
        values = [by_dimension[c['dimension']] for c in coordinates]
        require(all(v in c['members'] for v,c in zip(values,coordinates)), 'Rule area references a missing/non-leaf member.')
        require(tuple(values) not in seen, 'Overlapping rules require source precedence analysis.')
        seen.add(tuple(values))
        rules.append({'coordinates':values,'value':typed_expression(node['value'], {}, 'NUMERIC')})
    return {'kind':'constant_rules','model':model,'rules':rules}


def member_tuple(expression, model):
    if expression['kind'] == 'group':return member_tuple(expression['expression'], model)
    items = expression['items'] if expression['kind'] == 'tuple' else [expression]
    result = {}
    for item in items:
        require(item['kind'] == 'member_path' and item['property'] is None and len(item['segments']) == 3
                and all(s['kind'] == 'NAME' for s in item['segments']), 'MDX requires explicit canonical [dimension].[hierarchy].[member] literals.')
        dimension, hierarchy, member = [s['value'] for s in item['segments']]
        matches = [c for c in model['coordinates'] if c['dimension'] == dimension and c['hierarchy'] == hierarchy]
        require(len(matches) == 1 and member in matches[0]['members'] and dimension not in result,
                'MDX tuple has a missing/duplicate dimension or unknown leaf member.')
        result[dimension] = member
    return result


def axis_set(expression, model, limit):
    """Lower bounded literal sets and static CrossJoin/Head/Tail expressions.

    All function inputs are checked before selection: a Head/Tail cannot hide
    unsupported expressions, duplicate tuples or inconsistent dimensionality.
    """
    kind = expression['kind']
    if kind == 'group': return axis_set(expression['expression'],model,limit)
    if kind == 'set':
        tuples = [member_tuple(item,model) for item in expression['items']]
    elif kind == 'function' and expression['name'].upper() in {'CROSSJOIN','HEAD','TAIL'}:
        name = expression['name'].upper();args = expression['arguments']
        if name == 'CROSSJOIN':
            require(len(args)==2,'CrossJoin requires exactly two explicit sets.')
            left = axis_set(args[0],model,limit);right = axis_set(args[1],model,limit)
            require(not set(left[0]) & set(right[0]),'CrossJoin operands cannot overlap dimensions.')
            require(len(left)*len(right)<=limit,'CrossJoin exceeds the bounded preview limit.')
            tuples = [{**l,**r} for l in left for r in right]
        else:
            require(len(args) in {1,2},name+' requires a set and optional literal positive count.')
            tuples = axis_set(args[0],model,limit);count = 1
            if len(args)==2:
                count_node = args[1]
                require(count_node['kind']=='literal' and count_node['category']=='INTEGER' and count_node['value'].isascii()
                        and count_node['value'].isdecimal() and len(count_node['value'])<=6,
                        name+' requires a bounded positive integer literal count.')
                count = int(count_node['value'])
            require(0<count<=limit,name+' requires a positive count within the preview bound; empty axes are unsupported.')
            tuples = tuples[:count] if name=='HEAD' else tuples[-count:]
    else:
        raise CapabilityError('MDX axis requires a literal set or bounded static CrossJoin/Head/Tail.')
    require(tuples and len(tuples)<=limit,'MDX axis must be nonempty and fit the preview bound.')
    require(all(set(t)==set(tuples[0]) for t in tuples),'MDX set dimensionality is inconsistent.')
    require(len({tuple(sorted(t.items())) for t in tuples})==len(tuples),'Duplicate axis tuples require explicit cellset semantics review.')
    return tuples


def lower_mdx(analysis, config, model, limit):
    require(model['value_semantics'] == 'EVALUATED_WITHOUT_RULES', 'MDX requires evaluated cells without hidden rules/feeders.')
    require(config['datasource_table'] is None and config['order_column'] is None and not config['parameters'],
            'MDX mapping contains unused datasource/parameter declarations.')
    query = analysis['query']
    require(query is not None and query['kind'] == 'select' and query['cube']['name'] == model['cube'], 'MDX cube does not match its model.')
    axes = sorted(query['axes'], key=lambda a:a['ordinal'])
    require(1 <= len(axes) <= 2 and [a['ordinal'] for a in axes] == list(range(len(axes))), 'Only axis 0 and optional axis 1 are supported.')
    slicer = {} if query['slicer'] is None else member_tuple(query['slicer'],model)
    choices = []; used = set(slicer)
    count = 1
    for axis in axes:
        expression = axis['expression']
        require(not axis['non_empty'],'NON EMPTY requires source empty-cell semantics and is unsupported.')
        tuples = axis_set(expression,model,limit)
        require(all(set(t) == set(tuples[0]) for t in tuples) and not (used & set(tuples[0])), 'MDX axis dimensionality is inconsistent or overlaps another axis/slicer.')
        require(len({tuple(sorted(t.items())) for t in tuples}) == len(tuples), 'Duplicate axis tuples require explicit cellset semantics review.')
        used.update(tuples[0]); choices.append(tuples); count *= len(tuples)
    require(used == {c['dimension'] for c in model['coordinates']}, 'All cube dimensions must be explicit; no default members are invented.')
    require(count <= limit, 'MDX cellset exceeds the bounded preview limit.')
    requests = []
    # MDX cell ordinals vary axis 0 fastest. Positions remain explicit as well.
    for reversed_positions in product(*(range(len(t)) for t in reversed(choices))):
        positions = tuple(reversed(reversed_positions))
        coordinates = dict(slicer)
        for a,p in enumerate(positions):coordinates.update(choices[a][p])
        requests.append({'coordinates':[coordinates[c['dimension']] for c in model['coordinates']],
                         'axis_positions':{'axis_'+str(a):p for a,p in enumerate(positions)}})
    return {'kind':'cell_query','model':model,'requests':requests}


def analyze_capabilities(bundle, mapping):
    """Every unit gets a decision; a blocker anywhere prevents executable output."""
    catalog = Catalog(bundle['metadata_inputs'])
    require(type(mapping['max_rows']) is int, 'Preview max_rows must be an integer.')
    models = {m['model_id']:m for m in mapping['models']}; configs = {u['unit_id']:u for u in mapping['units']}
    require(len(models) == len(mapping['models']) and len(configs) == len(mapping['units']), 'Duplicate model/unit mapping IDs.')
    unit_ids = {u['unit_id'] for u in bundle['units']}
    require(set(configs) == unit_ids, 'Mappings must cover exactly all supplied source units.')
    require(set(models) == {c['model_id'] for c in configs.values()}, 'Missing/unused target models are not allowed.')
    cases = mapping['fixture_cases']
    require(len({c['case_id'] for c in cases}) == len(cases) and {c['unit_id'] for c in cases} == unit_ids,
            'Supply uniquely named, explicit expected-result fixture cases for every unit.')
    entries = {e['unit_id']:e for e in bundle['manifest']['document']['units']}
    rules_cubes = [entries[u['unit_id']]['source_cube'] for u in bundle['units'] if u['analysis']['language'] == 'tm1_rules']
    result = []
    for unit in bundle['units']:
        analysis = unit['analysis']; language = analysis['language']; unit_id = unit['unit_id']
        evidence = [{'file_id':unit['source_file']['file_id'],'locator':'file:/','rule':'bounded_tm1_capability_v1'}]
        if language == 'tm1_turbointegrator':
            evidence.extend({'file_id':s['source']['file_id'],'locator':s['source']['locator'],
                             'rule':'bounded_tm1_explicit_ti_section'}
                            for s in analysis['sections'] if s['source'] is not None)
        evidence.extend({'file_id':s['source']['file_id'],'locator':s['source']['locator'],
                         'rule':'bounded_tm1_explicit_catalog_context'} for s in bundle['metadata_inputs'])
        capability = {'unit_id':unit_id,'language':language,'decision':'UNSUPPORTED','rule':'dense_numeric_leaf_snapshot_v1',
                      'reasons':[],'program':None,'evidence':evidence}
        try:
            require(unit_id != 'all', 'Unit ID all is reserved by the preview widget; supply a distinct manifest unit ID.')
            require(not catalog.issues, 'Conflicting/incomplete source metadata prevents translation.')
            require(not any(d.get('severity') == 'ERROR' for d in analysis['diagnostics']), 'Source parser reported errors.')
            config = configs[unit_id]
            require(config['model_id'] in models, 'Missing target model: ' + config['model_id'])
            model = numeric_model(catalog, models[config['model_id']], mapping['max_rows'])
            context_cube = entries[unit_id]['source_cube']
            require(context_cube is None or context_cube == model['cube'], 'Source cube context disagrees with target mapping.')
            if language == 'tm1_rules':
                require(context_cube == model['cube'] and rules_cubes.count(model['cube']) == 1, 'Rule cube/context/file order is missing or ambiguous.')
                program = lower_rules(analysis,config,model)
            else:
                require(model['cube'] not in rules_cubes, 'Cross-unit rule evaluation/execution ordering requires review; no dependency was guessed.')
                program = lower_ti(analysis,config,model,catalog,mapping['max_rows']) if language == 'tm1_turbointegrator' else lower_mdx(analysis,config,model,mapping['max_rows'])
            program['max_rows'] = mapping['max_rows']; program['unit_id'] = unit_id
            capability.update(decision='DETERMINISTIC',program=program)
        except CapabilityError as exc:
            capability['reasons'].append(str(exc))
        result.append(capability)
    return result
