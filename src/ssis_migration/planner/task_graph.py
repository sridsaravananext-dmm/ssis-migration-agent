"""Name-independent task graph with explicit sequence entry/completion barriers."""
from __future__ import annotations
from collections import defaultdict
import hashlib
from ssis_migration.planner.models import MigrationPlan, MigrationStep
from ssis_migration.mapping import ConversionGap, translate_sql
from ssis_migration.drafts import pending_operation, procedure_references, translate_parameterless_procedure
from ssis_migration.sql_text import normalize_name


def topological_order(dependencies):
    remaining = {key:set(values) for key,values in dependencies.items()}
    ordered = []
    while remaining:
        ready = sorted(key for key,values in remaining.items() if not values)
        if not ready: return ordered, sorted(remaining)
        for key in ready:
            ordered.append(key)
            remaining.pop(key)
        for values in remaining.values(): values.difference_update(ready)
    return ordered, []


def step_id(ref):
    return 'task_' + hashlib.sha256(ref.encode()).hexdigest()[:16]


def build_graph_plan(context, config):
    from ssis_migration.assessment import finding, dependency_findings
    from ssis_migration.generator.graph_native import dataflow_operations, truth
    package = context.package_ir
    plan = MigrationPlan(package_name=package.package_name, metadata={
        'planner':'source_graph', 'model_version':'2.1', 'uses_legacy_controls':False,
        'operations_by_step':{}, 'source_inventory':[], 'dataflow_graphs':[], **config.to_dict()})
    by_ref, parent, children = {}, {}, defaultdict(list)
    for index, exe in enumerate(package.executables):
        ref = exe.ref_id or f'missing:{index}'
        if not exe.ref_id or ref in by_ref:
            plan.unresolved.append(finding('SOURCE_ID_INVALID', ref, 'Executable requires a unique source refId.'))
            ref += f':duplicate:{index}'
        by_ref[ref] = exe
    for ref, exe in by_ref.items():
        explicit = exe.properties.get('_parent_ref_id')
        inferred = ref.rsplit('\\',1)[0] if '\\' in ref else None
        p = explicit if explicit in by_ref else inferred if inferred in by_ref else None
        if explicit not in (None,'Package') and explicit not in by_ref:
            plan.unresolved.append(finding('PARENT_SCOPE_UNRESOLVED', ref, 'Parent executable is absent from the source graph.', evidence=explicit))
        parent[ref] = p
        children[p].append(ref)
    def sequence(exe):
        return (exe.executable_type or exe.creation_name or '').upper() in {'STOCK:SEQUENCE','MICROSOFT.SEQUENCE'}
    def event_scope(ref):
        seen = set()
        while ref in by_ref and ref not in seen:
            seen.add(ref)
            if by_ref[ref].properties.get('_event_handler') or '.EventHandlers[' in ref:
                return ref.split(']')[0] + ']' if '.EventHandlers[' in ref else 'event:' + ref
            ref = parent.get(ref)
        return None
    def finish(ref): return ref + '::complete' if sequence(by_ref[ref]) else ref
    deps = {ref:set() for ref in by_ref}
    barriers = {}
    for ref, exe in by_ref.items():
        if sequence(exe):
            end = finish(ref)
            deps[end] = set()
            barriers[end] = ref
            for child in children[ref]:
                deps[child].add(ref)
                deps[end].add(finish(child))
            if not children[ref]: deps[end].add(ref)
        elif children[ref]:
            plan.unresolved.append(finding('CONTAINER_SEMANTICS_UNSUPPORTED', ref, 'Non-sequence container/loop requires explicit execution semantics.'))
            for child in children[ref]: deps[child].add(ref)
    edge_records = []
    for index, edge in enumerate(package.precedence_constraints):
        a,b = edge.from_executable,edge.to_executable
        edge_records.append({'source_id':edge.ref_id or f'edge:{index}', 'from':a,'to':b,
            'logical_and':edge.logical_and,'evaluation_operation':edge.evaluation_operation,
            'value':edge.value,'expression':edge.expression})
        if a not in by_ref or b not in by_ref:
            plan.unresolved.append(finding('CONTROL_EDGE_UNRESOLVED', edge.ref_id or str(index), 'Precedence endpoint is absent.'))
            continue
        if parent[a] != parent[b] or event_scope(a) != event_scope(b):
            plan.unresolved.append(finding('CONTROL_SCOPE_UNSUPPORTED', edge.ref_id or str(index), 'Precedence connects different container scopes.'))
        deps[b].add(finish(a))
        if edge.expression or edge.value not in {None,'Success','0'} or edge.logical_and is False or edge.evaluation_operation not in {None,'Constraint','0'}:
            plan.unresolved.append(finding('CONTROL_FLOW_NOT_PRESERVED', edge.ref_id or str(index),
                'Conditional, failure, completion, or OR execution is retained but requires implementation.'))
    order, cycles = topological_order(deps)
    if cycles:
        plan.unresolved.append(finding('CONTROL_FLOW_CYCLE', package.package_name or 'package', 'Task graph contains a cycle.', evidence=cycles))
        order.extend(cycles)  # Diagnostics only: the package is fail-closed.
    # A single notebook serializes independent branches and changes failure isolation.
    # Preserve the DAG but block executable certification until pipeline semantics are chosen.
    flows = {f.ref_id:f for f in package.data_flows}
    if len(flows) != len(package.data_flows):
        raise ValueError('Duplicate data-flow task refIds cannot be associated safely')
    represented_flows = set()
    def disabled(ref):
        if str(package.metadata.get('package_attributes', {}).get('Disabled','')).casefold() in {'true','1','-1'}: return True
        seen = set()
        while ref:
            if ref in seen: return False
            seen.add(ref)
            attrs = by_ref[ref].properties.get('_attributes', {})
            if truth(attrs.get('Disabled',False)) or str(attrs.get('Disabled',''))=='-1': return True
            ref = parent.get(ref)
        return False
    waves = {key:set(values) for key,values in deps.items()}
    while waves:
        ready = {key for key,values in waves.items() if not values}
        if not ready: break
        concurrent = sorted(key for key in ready if key in by_ref and not disabled(key) and not event_scope(key))
        if len(concurrent) > 1:
            plan.unresolved.append(finding('PARALLEL_CONTROL_REVIEW_REQUIRED',package.package_name or 'package',
                'Independent task branches require an explicit concurrency/failure-isolation strategy.', evidence=concurrent))
        for key in ready: waves.pop(key)
        for values in waves.values(): values.difference_update(ready)
    root_attrs = package.metadata.get('package_attributes', {})
    if package.metadata.get('package_expressions'):
        plan.unresolved.append(finding('DYNAMIC_PACKAGE_PROPERTY','package','Package property expressions require typed evaluation.'))
    if str(root_attrs.get('TransactionOption','')) in {'2','Required'}:
        plan.unresolved.append(finding('TRANSACTION_SEMANTICS_UNSUPPORTED','package','Required package transactions need an explicit target design.'))
    for key in ('CheckpointUsage','SaveCheckpoints'):
        if str(root_attrs.get(key,'')) not in {'','0','Never','False','false'}:
            plan.unresolved.append(finding('CHECKPOINT_SEMANTICS_UNSUPPORTED','package','SSIS checkpoint/restart behavior needs an explicit target design.', evidence=key))
    for ref in order:
        barrier = ref in barriers
        origin = barriers.get(ref,ref)
        exe = by_ref[origin]
        sid = step_id(ref)
        kind = (exe.executable_type or exe.creation_name or '').casefold()
        task_type, ops, findings = 'GRAPH_TASK', [], []
        is_disabled = disabled(origin)
        mapping_uses = []
        props = exe.properties
        connection = props.get('connection_manager_ref_id') or props.get('Connection')
        connection = context.metadata.get('connection_aliases', {}).get(connection, connection)
        sql_source = props.get('SqlStatementSource')
        procedures, bodies = procedure_references(sql_source, connection, context) if sql_source and not is_disabled else ([], {})
        if not is_disabled and exe.properties.get('_property_expressions'):
            findings.append(finding('DYNAMIC_EXECUTABLE_PROPERTY',origin,'Executable property expressions require typed evaluation.'))
        if not is_disabled and event_scope(origin):
            findings.append(finding('EVENT_HANDLER_UNSUPPORTED',origin,'Event handler execution semantics require review.'))
        if is_disabled or barrier or sequence(exe):
            task_type = 'DISABLED' if is_disabled else 'SEQUENCE_COMPLETE' if barrier else 'SEQUENCE_START'
            ops = [{'operation_id':ref,'operation_type':'STRUCTURAL','order':0,'code':f'_completed_{sid} = True'}]
        elif event_scope(origin):
            findings.append(finding('EVENT_HANDLER_UNSUPPORTED',origin,'Event handlers cannot run as ordinary top-level tasks.'))
        elif exe.properties.get('_property_expressions'):
            findings.append(finding('DYNAMIC_EXECUTABLE_PROPERTY',origin,'Executable property expressions require typed evaluation.'))
        elif origin in flows:
            represented_flows.add(origin)
            ops, findings, graph = dataflow_operations(flows[origin],config,context.metadata.get("connection_aliases",{}))
            graph['task_id'] = sid
            plan.metadata['dataflow_graphs'].append(graph)
            mapping_uses = graph.get('mapping_uses',[])
            task_type = 'NATIVE_DATA_FLOW'
        elif 'executesqltask' in kind:
            task_type = 'NATIVE_SQL_TASK'
            try:
                if str(props.get('SqlStatementSourceType','DirectInput')) not in {'0','DirectInput'} or not props.get('SqlStatementSource'):
                    raise ConversionGap('SQL_EXTRACTION_INCOMPLETE','Static direct SQL is required.')
                if procedures:
                    if len(procedures) != 1 or procedures[0]['name'] not in bodies:
                        raise ConversionGap('PROCEDURE_IMPLEMENTATION_PENDING', 'Retain this procedure call until its definition and call semantics are available.')
                    sql, mapping_uses = translate_parameterless_procedure(sql_source, bodies[procedures[0]['name']], connection, config, props)
                elif props.get('parameter_bindings') or props.get('result_bindings') or str(props.get('ResultType','ResultSetType_None')) not in {'0','ResultSetType_None','None',''}:
                    raise ConversionGap('SQL_BINDINGS_UNSUPPORTED','SQL parameter/result bindings must be translated explicitly.')
                else:
                    sql, mapping_uses = translate_sql(sql_source, connection, config)
                if any(u['resolution'] == 'PLACEHOLDER' for u in mapping_uses):
                    raise ConversionGap('TARGET_LOCATION_UNRESOLVED', 'Target schema is unknown; retain a draft section until a schema or override is supplied.')
                ops = [{'operation_id':origin, 'operation_type':'NATIVE_SQL','order':0,'code':f'_result_{sid} = spark.sql({sql!r})'}]
            except ConversionGap as exc: findings.append(finding(exc.code,origin,str(exc),evidence=exc.evidence))
        else:
            findings.append(finding('TASK_TRANSLATION_UNSUPPORTED',origin,f'No native implementation for task type {exe.executable_type or exe.creation_name}.'))
        attrs = exe.properties.get('_attributes',{})
        for key in ('ForceExecutionResult','ForceExecutionValue'):
            if not is_disabled and str(attrs.get(key,'')) not in {'','-1','None','False','false'}:
                findings.append(finding('EXECUTION_OVERRIDE_UNSUPPORTED',origin,'Forced execution outcomes/values require explicit translation.',evidence=key))
        if not is_disabled and str(attrs.get('TransactionOption','')) in {'2','Required'}:
            findings.append(finding('TRANSACTION_SEMANTICS_UNSUPPORTED',origin,'Required transaction semantics are not implemented.'))
        if findings or not ops:
            if not ops: task_type = 'REVIEW_ONLY'
            if not ops:
                reasons = [f['code'] for f in findings] + [p['reason_code'] for p in procedures if p.get('reason_code')]
                ops = [pending_operation(origin, reasons, connection=connection, sql=sql_source,
                    properties={**props, '_target_proposals':mapping_uses}, procedures=procedures,
                    kind='STORED_PROCEDURE' if procedures else 'IMPLEMENTATION')]
        plan.unresolved.extend(findings)
        metadata = {'source_ref_id':origin,'source_scope':parent[origin], 'mapping_uses':mapping_uses,
                    'migration_strategy':{'strategy':'DETERMINISTIC' if not findings else 'REVIEW_REQUIRED'},
                    'graph_node_id':ref, 'event_handler_scope':event_scope(origin)}
        plan.steps.append(MigrationStep(step_id=sid,name=exe.name or origin,step_type=task_type,
            dependencies=[step_id(d) for d in sorted(deps[ref])],metadata=metadata))
        plan.metadata['operations_by_step'][sid] = ops
        if not barrier:
            plan.metadata['source_inventory'].append({'source_id':origin,'name':exe.name,'kind':'CONTAINER' if sequence(exe) or children[origin] else 'TASK',
                'step_id':sid,'disposition':'DISABLED' if is_disabled else 'REVIEW_ONLY' if findings else 'PLANNED_CANDIDATE'})
        if origin in flows and not barrier:
            flow=flows[origin]
            for component in flow.components:
                failed = any(f['source_id']==component.ref_id for f in findings) or not any(op['operation_id'] == component.ref_id for op in ops)
                plan.metadata['source_inventory'].append({'source_id':component.ref_id,'name':component.name,'kind':'COMPONENT','step_id':sid,
                    'disposition':'DISABLED' if is_disabled else 'REVIEW_ONLY' if failed or origin not in represented_flows else 'PLANNED_CANDIDATE'})
    for flow in package.data_flows:
        if flow.ref_id not in by_ref:
            plan.unresolved.append(finding('DATAFLOW_OWNER_UNRESOLVED',flow.ref_id or flow.name or 'flow','Flow has no executable owner.'))
            plan.metadata['source_inventory'].extend({'source_id':c.ref_id,'name':c.name,'kind':'COMPONENT','step_id':None,'disposition':'REVIEW_ONLY'} for c in flow.components)
    plan.unresolved.extend(dependency_findings(context))
    if not plan.metadata['source_inventory']:
        plan.unresolved.append(finding('NO_SOURCE_WORK',package.package_name or 'package','No source work was extracted.'))
    uses = [{**use, 'source_id':use.get('source_id', step.metadata['source_ref_id'])}
        for step in plan.steps for use in step.metadata.get('mapping_uses', [])]
    targets = [u for u in uses if u.get('role') in {'target','destination'}]
    for use in uses:
        if use['resolution'] == 'PLACEHOLDER':
            plan.unresolved.append(finding('TARGET_LOCATION_UNRESOLVED', use['source_id'],
                'Supply a target schema or an optional object override; the draft retains this target placeholder.', evidence=use['source']))
        elif use['resolution'] == 'CONVENTION':
            plan.unresolved.append(finding('CONVENTION_TARGET_REVIEW', use['source_id'],
                'Proposed catalog.schema.lowercase_table naming; confirm the target exists and is intended.', severity='WARNING', evidence=use['target']))
    grouped = defaultdict(list)
    for use in uses: grouped[use['target'].casefold()].append(use)
    for target, items in grouped.items():
        identities = {(u.get('connection_reference'), normalize_name(u['source'], config.default_schema)) for u in items}
        if len(identities) > 1 and any(u['resolution'] != 'EXPLICIT' for u in items):
            plan.unresolved.append(finding('TARGET_NAME_COLLISION', target,
                'Different legacy identities map to one proposed target. Supply overrides or confirm shared physical identity.',
                evidence=sorted(f'{connection}|{source}' for connection, source in identities)))
    if config.layer.casefold() in {'multi-layer','multilayer'}:
        if not targets or any(not u.get('layer') for u in targets):
            plan.unresolved.append(finding('LAYER_MAPPING_REQUIRED','target_layer','Multi-layer outputs require a layer on every target mapping.'))
    if config.target_schema and config.layer.casefold() not in {'multi-layer','multilayer'}:
        from ssis_migration.mapping import target_table
        if any(target_table(u['target']).db.casefold() != config.target_schema.casefold() for u in targets if u['resolution'] == 'EXPLICIT'):
            plan.unresolved.append(finding('TARGET_SCHEMA_CONTEXT_CONFLICT','target_schema','Explicit target mappings conflict with the configured target schema.'))
    for use in targets:
        if use.get('layer') and config.layer.casefold() not in {'unspecified','multi-layer','multilayer',use['layer'].casefold()}:
            plan.unresolved.append(finding('LAYER_CONTEXT_CONFLICT',use['mapping_key'],'Mapped output layer conflicts with package layer context.'))
    plan.metadata['execution_model'] = 'serialized_topological'
    plan.metadata['mapping_uses'] = uses
    plan.metadata['mapping_review_complete'] = not any(f['code'] in {'CONVENTION_TARGET_REVIEW','TARGET_LOCATION_UNRESOLVED','TARGET_NAME_COLLISION','LAYER_MAPPING_REQUIRED','TARGET_SCHEMA_CONTEXT_CONFLICT','LAYER_CONTEXT_CONFLICT'} for f in plan.unresolved)
    plan.metadata['unused_object_mappings'] = sorted(set(config.object_mappings) - {u['mapping_key'] for u in uses})
    plan.metadata['task_graph']={'nodes':[{'id':ref,'source_ref_id':barriers.get(ref,ref),'step_id':step_id(ref),'barrier':ref in barriers,
        'event_handler_scope':event_scope(barriers.get(ref,ref))} for ref in deps],
        'edges':[{'from':a,'to':b} for b,values in deps.items() for a in sorted(values)], 'source_precedence':edge_records,'order':order,'cycles':cycles}
    return plan
