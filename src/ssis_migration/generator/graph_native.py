"""Native, deliberately bounded graph translation; unsupported work never vanishes."""
from __future__ import annotations
import hashlib
import re
from dataclasses import asdict
import sqlglot
from sqlglot import exp, ErrorLevel
from ssis_migration.mapping import ConversionGap, resolve_mapping, translate_sql


def variable(ref):
    return 'df_' + hashlib.sha256(ref.encode()).hexdigest()[:12]


def truth(value):
    return str(value).casefold() in {'1', '-1', 'true'}


def connection_of(component):
    refs = {r.connection_manager_ref_id for r in component.connection_manager_refs if r.connection_manager_ref_id}
    if len(refs) > 1: raise ConversionGap('CONNECTION_AMBIGUOUS', 'Component has multiple connection references.')
    return next(iter(refs), None)


def expression_sql(text, lineage, columns, *, expect_boolean=False):
    """Only column/literal/comparison expressions with explicit SSIS operators."""
    if not isinstance(text, str) or not text.strip():
        raise ConversionGap('EXPRESSION_UNSUPPORTED', 'An explicit expression is required.')
    if '@' in text or '?' in text or re.search(r'\bDT_\w+|\(\s*DT_', text, re.I):
        raise ConversionGap('EXPRESSION_UNSUPPORTED', 'Variables, ternaries, and SSIS casts need typed translation.')
    def identifier(match):
        key = match.group(0)
        name = lineage.get(key) if key.startswith('#') else match.group(1)
        if not name or name not in columns:
            raise ConversionGap('COLUMN_LINEAGE_UNRESOLVED', f'Cannot resolve expression column {key}.')
        return '`' + name.replace('`', '``') + '`'
    # Preserve literal boundaries; never rewrite operators inside strings.
    parts = re.split(r'("(?:\\.|[^"\\])*")', text)
    emitted = []
    for index, part in enumerate(parts):
        if index % 2:
            # SSIS escapes beyond quotes/backslashes are not silently approximated.
            inner = part[1:-1]
            if re.search(r'\\(?!["\\])', inner): raise ConversionGap('EXPRESSION_UNSUPPORTED', 'Unsupported string escape.')
            emitted.append("'" + inner.replace('\\"', '"').replace('\\\\', '\\').replace("'", "''") + "'")
        else:
            if '--' in part or '/*' in part: raise ConversionGap('EXPRESSION_UNSUPPORTED', 'SQL comments are not SSIS expressions.')
            if "'" in part: raise ConversionGap('EXPRESSION_UNSUPPORTED', 'Use SSIS double-quoted string literals.')
            part = re.sub(r'\[([^\]]+)\]|#\d+', identifier, part)
            part = part.replace('&&', ' AND ').replace('||', ' OR ').replace('==', '=').replace('!=', '<>')
            part = re.sub(r'!(?!=)', ' NOT ', part)
            part = re.sub(r'\bISNULL\s*\((`(?:``|[^`])+`)\)', r'(\1 IS NULL)', part, flags=re.I)
            emitted.append(part)
    try:
        statements = [s for s in sqlglot.parse(''.join(emitted), read='databricks', error_level=ErrorLevel.RAISE) if s]
        if len(statements) != 1: raise ValueError('Expected one complete SSIS expression')
        tree = statements[0]
    except Exception as exc: raise ConversionGap('EXPRESSION_UNSUPPORTED', str(exc)) from exc
    allowed = (exp.Column, exp.Identifier, exp.Literal, exp.Boolean, exp.Null, exp.Paren,
               exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Is, exp.Neg)
    if any(not isinstance(node, allowed) for node in tree.walk()):
        raise ConversionGap('EXPRESSION_UNSUPPORTED', 'Only literal, column, null, and comparison expressions are supported.')
    for column in tree.find_all(exp.Column):
        if column.db or column.catalog or column.table or column.name not in columns:
            raise ConversionGap('COLUMN_LINEAGE_UNRESOLVED', f'Unknown column: {column.sql()}')
    if expect_boolean:
        def boolean(node):
            if isinstance(node, exp.Paren): return boolean(node.this)
            if isinstance(node, (exp.And, exp.Or)): return boolean(node.this) and boolean(node.expression)
            if isinstance(node, exp.Not): return boolean(node.this)
            return isinstance(node, (exp.Boolean, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Is))
        if not boolean(tree): raise ConversionGap('EXPRESSION_TYPE_UNSUPPORTED', 'Split conditions require explicit Boolean expressions.')
    return tree.sql(dialect='databricks')


def ports_graph(flow):
    """Resolve only exact input/output IDs, retaining all component paths."""
    components = {}
    inputs, outputs = {}, {}
    for comp in flow.components:
        if not comp.ref_id or comp.ref_id in components:
            raise ConversionGap('DUPLICATE_SOURCE_ID', 'Each component requires a unique refId.')
        components[comp.ref_id] = comp
        for key, ports, registry in [('input', comp.inputs, inputs), ('output', comp.outputs, outputs)]:
            for port in ports:
                ref = port.get('ref_id')
                if not ref or ref in inputs or ref in outputs: raise ConversionGap('PORT_ID_INVALID', f'Invalid {key} identity on {comp.ref_id}.')
                registry[ref] = comp.ref_id
    edges, inbound, outbound = [], {}, {}
    for path in flow.paths:
        start, end = path.get('start_id'), path.get('end_id')
        if start not in outputs or end not in inputs:
            raise ConversionGap('DATAFLOW_PATH_UNRESOLVED', 'A path endpoint is absent from the parsed ports.', path)
        if end in inbound:
            raise ConversionGap('DATAFLOW_INPUT_AMBIGUOUS', 'Multiple paths feed the same input port.', end)
        inbound[end] = start
        outbound.setdefault(start, []).append(end)
        edges.append({'from_component': outputs[start], 'from_output': start,
                      'to_component': inputs[end], 'to_input': end})
    return components, edges, inbound, outbound


def dataflow_operations(flow, config, connection_aliases=None):
    from ssis_migration.assessment import finding
    findings, operations, mapping_uses = [], [], []
    aliases = connection_aliases or {}
    def canonical(component):
        ref = connection_of(component)
        return aliases.get(ref, aliases.get(str(ref).strip('{}').casefold(), ref))
    try: components, edges, inbound, outbound = ports_graph(flow)
    except ConversionGap as exc:
        return [], [finding(exc.code, flow.ref_id or flow.name or 'flow', str(exc), evidence=exc.evidence)], {'components': [], 'edges': []}
    from ssis_migration.planner.task_graph import topological_order
    deps = {key: set() for key in components}
    for edge in edges: deps[edge['to_component']].add(edge['from_component'])
    ordered, cycles = topological_order(deps)
    if cycles: findings.append(finding('DATAFLOW_CYCLE', flow.ref_id or 'flow', 'Data-flow cycle detected.', evidence=cycles))
    state = {}  # output ref -> (DataFrame variable, column names, lineage)
    for ref in ordered:
        comp = components[ref]
        code = []
        try:
            if comp.properties.get('_type_evidence') == 'description':
                raise ConversionGap('COMPONENT_TYPE_UNRESOLVED', 'A description-based type hint is not sufficient for native generation.')
            if comp.properties.get('_property_expressions'):
                raise ConversionGap('DYNAMIC_COMPONENT_PROPERTY', 'Dynamic component properties require evaluation.')
            owned_attributes = [comp.properties.get('_attributes', {})]
            for port in comp.inputs + comp.outputs:
                owned_attributes.append(port.get('attributes', {}))
                owned_attributes.extend(c.get('attributes', {}) for c in port.get('columns', []))
            for attrs in owned_attributes:
                for key, value in attrs.items():
                    if key.casefold() in {'errorrowdisposition','truncationrowdisposition'} and value not in {'RD_FailComponent','RD_NotUsed','0'}:
                        raise ConversionGap('ERROR_DISPOSITION_UNSUPPORTED', 'Ignore/redirect/unknown row-error behavior needs explicit translation.')
            ports = [p for p in comp.outputs if not truth(p.get('attributes', {}).get('isErrorOut', False))]
            if comp.detected_type == 'OLE_DB_DESTINATION':
                table = comp.properties.get('OpenRowset')
                if not table or str(comp.properties.get('AccessMode','')) not in {'','0','3'}:
                    raise ConversionGap('TARGET_MAPPING_REVIEW_REQUIRED', 'Destination table must be a static OpenRowset.')
                mapped, key = resolve_mapping(table, canonical(comp), config.object_mappings, config.default_schema)
                mapping_uses.append({'source': table, 'mapping_key': key, 'target': mapped.sql(dialect='databricks'), 'role':'destination'})
                findings.append(finding('LOAD_STRATEGY_REQUIRED', ref,
                    'Destination is mapped. Define target write/load semantics before generating a write.', evidence=mapped.sql(dialect='databricks')))
                operations.append({'operation_id': ref + ':write', 'operation_type':'WRITE_PENDING', 'order':len(operations), 'code':'# REVIEW_REQUIRED: destination write semantics pending'})

            linked = [state[inbound[p['ref_id']]] for p in comp.inputs if p.get('ref_id') in inbound and inbound[p['ref_id']] in state]
            actual_inputs = [p for p in comp.inputs if p.get('ref_id') in inbound]
            if comp.detected_type != 'OLE_DB_SOURCE' and (len(actual_inputs) != 1 or len(linked) != 1 or len(comp.inputs) != 1):
                raise ConversionGap('DATAFLOW_INPUT_UNSUPPORTED', 'This component requires a single resolved input; multi-input semantics are not inferred.')
            if comp.detected_type == 'OLE_DB_SOURCE':
                if comp.inputs or len(ports) != 1: raise ConversionGap('DATAFLOW_SOURCE_UNSUPPORTED', 'Source must have one normal output and no inputs.')
                port = ports[0]
                query = comp.properties.get('SqlCommand')
                access = str(comp.properties.get('AccessMode', ''))
                if comp.properties.get('SqlCommandVariable') or access not in {'','0','2'}:
                    raise ConversionGap('SQL_EXTRACTION_INCOMPLETE', 'Variable-backed source SQL/table cannot be inferred.')
                connection = canonical(comp)
                if not query:
                    table = comp.properties.get('OpenRowset')
                    if not table: raise ConversionGap('SQL_EXTRACTION_INCOMPLETE', 'Source has no static query or table.')
                    query = 'SELECT * FROM ' + table
                sql, uses = translate_sql(query, connection, config, query_only=True)
                mapping_uses.extend(uses)
                name = variable(port['ref_id'])
                cols = [c['name'] for c in port.get('columns', []) if c.get('name')]
                if not cols or len(set(cols)) != len(cols): raise ConversionGap('COLUMN_LINEAGE_UNRESOLVED', 'Source requires unique captured output columns.')
                lineage = {str(c.get('lineage_id')): c['name'] for c in port['columns'] if c.get('lineage_id')}
                lineage.update({'#' + k: v for k, v in list(lineage.items()) if k.isdigit()})
                code.append(f'{name} = spark.sql({sql!r})')
                code.append(f'{name} = {name}.select(*[F.col("`" + c.replace("`", "``") + "`") for c in {cols!r}])')
                state[port['ref_id']] = (name, cols, lineage)
            elif comp.detected_type == 'MULTICAST':
                source, cols, lineage = linked[0]
                if any(c.get('name') not in cols for p in ports for c in p.get('columns',[])):
                    raise ConversionGap('COLUMN_LINEAGE_UNRESOLVED','Multicast declares unknown output columns.')
                if not ports: raise ConversionGap('DATAFLOW_OUTPUT_UNRESOLVED', 'Multicast has no normal outputs.')
                for port in ports:
                    name = variable(port['ref_id'])
                    code.append(f'{name} = {source}')
                    state[port['ref_id']] = (name, list(cols), dict(lineage))
            elif comp.detected_type == 'DERIVED_COLUMN':
                if len(ports) != 1: raise ConversionGap('DATAFLOW_COMPONENT_UNSUPPORTED', 'Derived Column needs one synchronous output.')
                source, cols, lineage = linked[0]
                cols, lineage = list(cols), dict(lineage)
                name = variable(ports[0]['ref_id'])
                code.append(f'{name} = {source}')
                expressions = list(ports[0].get('columns', []))
                expressions += [c for p in comp.inputs for c in p.get('columns', []) if c.get('properties')]
                applied = False
                for col in expressions:
                    props = col.get('properties', {})
                    expression = props.get('FriendlyExpression') or props.get('Expression') or props.get('DerivedColumnExpression')
                    if not expression: continue
                    column = col.get('name') or lineage.get(str(col.get('lineage_id')))
                    if not column: raise ConversionGap('COLUMN_LINEAGE_UNRESOLVED', 'Derived column name is missing.')
                    sql = expression_sql(expression, lineage, cols)
                    code.append(f'{name} = {name}.withColumn({column!r}, F.expr({sql!r}))')
                    if column not in cols: cols.append(column)
                    if col.get('lineage_id'):
                        key = str(col['lineage_id'])
                        lineage[key] = column
                        if key.isdigit(): lineage['#' + key] = column
                    applied = True
                if applied:
                    findings.append(finding('DERIVED_TYPE_REVIEW_REQUIRED', ref, 'Verify declared SSIS type, length, precision, null, and overflow behavior for derived outputs.', severity='WARNING'))
                if not applied: raise ConversionGap('EXPRESSION_UNSUPPORTED', 'No captured derived expression was found.')
                state[ports[0]['ref_id']] = (name, cols, lineage)
            elif comp.detected_type == 'CONDITIONAL_SPLIT':
                source, cols, lineage = linked[0]
                if any(c.get('name') not in cols for p in ports for c in p.get('columns',[])):
                    raise ConversionGap('COLUMN_LINEAGE_UNRESOLVED','Split declares unknown output columns.')
                rules, default = [], []
                for port in ports:
                    props = port.get('properties', {})
                    if truth(props.get('IsDefaultOut', port.get('attributes', {}).get('isDefaultOut', False))): default.append(port)
                    else:
                        expression = props.get('FriendlyExpression') or props.get('Expression')
                        order = props.get('EvaluationOrder')
                        if len(ports) > 2 and (order is None or not str(order).isdigit()):
                            raise ConversionGap('SPLIT_ORDER_UNRESOLVED', 'Multiple split rules require explicit EvaluationOrder.')
                        rules.append((int(order or 0), port, expression))
                if len(default) != 1 or not rules or len({r[0] for r in rules}) != len(rules):
                    raise ConversionGap('SPLIT_ORDER_UNRESOLVED', 'Expected ordered rules and one default output.')
                remaining = 'remaining_' + variable(ref)
                code.append(f'{remaining} = F.lit(True)')
                for _, port, expression in sorted(rules, key=lambda x: x[0]):
                    sql = expression_sql(expression, lineage, cols, expect_boolean=True)
                    condition = 'condition_' + variable(port['ref_id'])
                    code.append(f'{condition} = F.expr({sql!r})')
                    # SSIS null/invalid Boolean results fail; they must not silently enter default output.
                    code.append(f'if {source}.filter({remaining} & {condition}.isNull()).limit(1).count():')
                    code.append("    raise RuntimeError('Conditional Split expression evaluated NULL; inspect source error semantics.')")
                    name = variable(port['ref_id'])
                    code.append(f'{name} = {source}.filter({remaining} & {condition})')
                    code.append(f'{remaining} = {remaining} & ~{condition}')
                    state[port['ref_id']] = (name, list(cols), dict(lineage))
                name = variable(default[0]['ref_id'])
                code.append(f'{name} = {source}.filter({remaining})')
                state[default[0]['ref_id']] = (name, list(cols), dict(lineage))
            elif comp.detected_type == 'OLE_DB_DESTINATION':
                source, cols, lineage = linked[0]
                code.append(f'{variable(ref)} = {source}')
            else:
                raise ConversionGap('TRANSFORMATION_UNSUPPORTED', f'No native translator for {comp.detected_type}.')
            # Error redirection is never dropped. Any wired error output remains a blocker.
            for port in comp.outputs:
                if truth(port.get('attributes', {}).get('isErrorOut', False)) and port.get('ref_id') in outbound:
                    raise ConversionGap('ERROR_ROUTING_UNSUPPORTED', 'Connected error output needs explicit implementation.')
            operations.append({'operation_id': ref, 'operation_type':'NATIVE_DATAFRAME', 'order':len(operations),
                               'code':'from pyspark.sql import functions as F\n' + '\n'.join(code)})
        except (ConversionGap, KeyError, ValueError) as exc:
            gap = exc if isinstance(exc, ConversionGap) else ConversionGap('DATAFLOW_METADATA_INCOMPLETE', str(exc))
            findings.append(finding(gap.code, ref, str(gap), evidence=gap.evidence))
            # Failed outputs cannot be reused by downstream components.
            for port in comp.outputs: state.pop(port.get('ref_id'), None)
            operations.append({'operation_id': ref, 'operation_type':'REVIEW_ONLY','order':len(operations), 'code':'# REVIEW_REQUIRED: ' + gap.code})
    graph = {'components':[{'source_id':ref,'type':components[ref].detected_type} for ref in components],
             'edges':edges,'order':ordered,'cycles':cycles,'mapping_uses':mapping_uses}
    return operations, findings, graph
