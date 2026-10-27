"""Scoped exported contributions and connection ownership, without precedence guesses."""
from __future__ import annotations

import copy
from collections import defaultdict

from ..base import UnsupportedSourceError

MAX_INSTANCE_SCOPES = 256
MAX_CONFIGURATION_CANDIDATES = 64
MAX_INSTANCE_LAYERS = 64
MAX_CONFIGURATION_REFERENCES = 64
MAX_SETTING_CONTRIBUTIONS = 16384


def _check_contributions(count):
    if count > MAX_SETTING_CONTRIBUTIONS:
        raise UnsupportedSourceError('PowerCenter context exceeds the 16,384-setting contribution limit.')


def _layer(index, node, origin):
    settings = index.settings(node)
    return {'origin': origin, 'node_id': node['node_id'], 'settings': settings}


def _properties(layers):
    properties = defaultdict(list)
    for layer in layers:
        for setting in layer['settings']:
            properties[setting['name']].append({'origin': layer['origin'], 'layer_node_id': layer['node_id'],
                                                'setting_node_id': setting['node_id'], 'value': setting['value']})
    result = []
    for name, contributions in properties.items():
        layer_ids = [c['layer_node_id'] for c in contributions]
        if len(set(layer_ids)) != len(layer_ids):status = 'DUPLICATE_WITHIN_LAYER'
        elif len({c['value'] for c in contributions}) > 1:status = 'DIFFERING_DECLARATIONS'
        elif len(contributions) > 1:status = 'REPEATED_DECLARATIONS'
        else:status = 'SINGLE_DECLARATION'
        result.append({'name': name, 'contributions': contributions, 'declaration_status': status,
                       'effective_value': None, 'precedence_status': 'NOT_VERIFIED'})
    return result


def _definition(index, instance):
    if instance is None:return None
    links = [e for e in index.outgoing[instance['node_id']] if e['kind'] == 'REQUIRES' and e['resolution'] == 'RESOLVED']
    return index.nodes.get(links[0]['target_id']) if len(links) == 1 else None


def instance_scope(index, session, instance):
    definition = _definition(index, instance)
    name = index.attrs(instance).get('NAME')
    overrides = [n for n in index.child(session, 'SESSTRANSFORMATIONINST') if name and index.attrs(n).get('SINSTANCENAME') == name]
    extensions = [n for n in index.child(session, 'SESSIONEXTENSION') if name and index.attrs(n).get('SINSTANCENAME') == name]
    nodes = [*([definition] if definition else []), *overrides, *extensions]
    if len(nodes) > MAX_INSTANCE_LAYERS:
        raise UnsupportedSourceError('PowerCenter instance exceeds the 64-layer contribution limit.')
    layers = [*([_layer(index, definition, 'MAPPING_DEFINITION')] if definition else []),
              *[_layer(index, n, 'SESSION_INSTANCE_OVERRIDE') for n in overrides],
              *[_layer(index, n, 'SESSION_EXTENSION') for n in extensions]]
    return {'instance_node_id': instance['node_id'], 'instance_name': name,
            'definition_node_id': definition['node_id'] if definition else None,
            'definition_declaration': copy.deepcopy(index.attrs(definition)) if definition else None,
            'layers': layers, 'properties': _properties(layers), 'effective_object_binding': None}, nodes


def configuration_context(index, session):
    folder = index.ancestor(session, 'FOLDER')
    references = index.child(session, 'CONFIGREFERENCE')
    if len(references) > MAX_CONFIGURATION_REFERENCES:
        raise UnsupportedSourceError('PowerCenter session exceeds the 64-configuration-reference limit.')
    bindings, configs, related = [], [], []
    for reference in references:
        attrs = index.attrs(reference); name = attrs.get('REFOBJECTNAME')
        candidates = [n for n in index.nodes.values() if n['operation'] == 'CONFIG' and name
                      and index.attrs(n).get('NAME') == name and index.ancestor(n, 'FOLDER') == folder]
        if len(candidates) > MAX_CONFIGURATION_CANDIDATES:
            raise UnsupportedSourceError('PowerCenter configuration exceeds the 64-candidate resolution limit.')
        supported = attrs.get('TYPE') == 'Session config'
        bound = supported and len(candidates) == 1
        bindings.append({'reference_node_id': reference['node_id'], 'reference': copy.deepcopy(attrs),
                         'candidate_node_ids': [n['node_id'] for n in candidates],
                         'resolution': 'DECLARATION_BOUND' if bound else 'UNRESOLVED'})
        if bound:
            for candidate in candidates:
                if candidate not in configs:configs.append(candidate)
        related.extend([reference, *candidates])
    layers = [_layer(index, n, 'CONFIGURATION_OBJECT') for n in configs] + [_layer(index, session, 'SESSION')]
    contribution_count = sum(len(layer['settings']) for layer in layers)
    _check_contributions(contribution_count)
    mapping = index.mapping(session)
    scopes = []
    instances = index.child(mapping, 'INSTANCE') if mapping else []
    if len(instances) > MAX_INSTANCE_SCOPES:
        raise UnsupportedSourceError('PowerCenter session exceeds the 256-instance configuration limit.')
    for instance in instances:
        scoped, nodes = instance_scope(index, session, instance)
        contribution_count += sum(len(layer['settings']) for layer in scoped['layers'])
        _check_contributions(contribution_count)
        scopes.append(scoped);related.extend([instance, *nodes])
    matched = {layer['node_id'] for scope in scopes for layer in scope['layers']}
    unmatched = [n['node_id'] for n in index.child(session, 'SESSTRANSFORMATIONINST', 'SESSIONEXTENSION') if n['node_id'] not in matched]
    for layer in [*layers, *[layer for scope in scopes for layer in scope['layers']]]:
        related.extend([index.nodes[layer['node_id']], *[index.nodes[s['node_id']] for s in layer['settings']]])
    parameters = [s for s in index.settings(session) if s['name'] == 'Parameter Filename']
    parameter_state = 'ABSENT' if not parameters else 'AMBIGUOUS' if len(parameters) != 1 else 'EMPTY' if parameters[0]['value'] == '' else 'DECLARED'
    return {'configuration_references': bindings,
            'configuration_selection': 'DECLARATION_BOUND' if len(bindings) == 1 and bindings[0]['resolution'] == 'DECLARATION_BOUND' else 'UNRESOLVED',
            'layers': layers, 'properties': _properties(layers), 'instance_scopes': scopes,
            'unmatched_instance_declaration_ids': unmatched,
            'parameter_file': {'declaration_state': parameter_state, 'declarations': parameters, 'resolved_values': None},
            'product_version': None, 'repository_export_versions': [f['export_version'] for f in index.graph['evidence_files']],
            'precedence_status': 'NOT_VERIFIED', 'effective_configuration': None}, related


def connection_context(index, reference):
    owner = index.nodes.get(index.parents.get(reference['node_id']))
    session = index.ancestor(reference, 'SESSION')
    mapping = index.mapping(session)
    alias = index.attrs(reference).get('CONNECTIONNAME')
    related = [n for n in (owner, session, mapping) if n is not None]
    name = index.attrs(owner).get('SINSTANCENAME') if owner else None
    candidates = [n for n in index.child(mapping, 'INSTANCE') if name and index.attrs(n).get('NAME') == name] if mapping else []
    instance = candidates[0] if len(candidates) == 1 else None
    definition = _definition(index, instance)
    role = index.attrs(owner).get('TYPE') if owner else None
    valid_kind = (definition is not None and (
        (role == 'WRITER' and definition['operation'] == 'TARGET_DEFINITION')
        or (role == 'READER' and (definition['operation'] == 'SOURCE_DEFINITION' or index.attrs(definition).get('TYPE') == 'Source Qualifier'))
        or (role == 'LOOKUPEXTENSION' and index.attrs(definition).get('TYPE') == 'Lookup Procedure')))
    scoped = None
    contribution_count = 0
    if instance is not None:
        scoped, nodes = instance_scope(index, session, instance)
        contribution_count = sum(len(layer['settings']) for layer in scoped['layers'])
        _check_contributions(contribution_count)
        related.extend([instance, *nodes])
        for layer in scoped['layers']:
            related.extend(index.nodes[s['node_id']] for s in layer['settings'])
    associated = []
    for source_ref in index.child(instance, 'ASSOCIATED_SOURCE_INSTANCE') if instance else []:
        source_name = index.attrs(source_ref).get('NAME')
        sources = [n for n in index.child(mapping, 'INSTANCE') if source_name and index.attrs(n).get('NAME') == source_name and index.attrs(n).get('TYPE') == 'SOURCE']
        source_instance = sources[0] if len(sources) == 1 else None
        source_definition = _definition(index, source_instance)
        related.extend([source_ref, *sources, *([source_definition] if source_definition else [])])
        source_scope = None
        if source_instance:
            source_scope, nodes = instance_scope(index, session, source_instance);related.extend(nodes)
            contribution_count += sum(len(layer['settings']) for layer in source_scope['layers'])
            _check_contributions(contribution_count)
            for layer in source_scope['layers']:
                related.extend(index.nodes[s['node_id']] for s in layer['settings'])
        associated.append({'reference_node_id': source_ref['node_id'], 'instance_name': source_name,
                           'candidate_node_ids': [n['node_id'] for n in sources], 'instance_scope': source_scope,
                           'resolution': 'DECLARATION_BOUND' if source_definition and source_definition['operation'] == 'SOURCE_DEFINITION' else 'UNRESOLVED'})
    supplied_connections = [n for n in index.nodes.values() if n['operation'] == 'CONNECTION']
    related.extend(supplied_connections)
    return {'connection_alias': alias, 'owner_extension_node_id': owner['node_id'] if owner else None,
            'owner_role': role, 'owner_instance_name': name, 'candidate_instance_ids': [n['node_id'] for n in candidates],
            'owner_resolution': 'DECLARATION_BOUND' if valid_kind and owner['operation'] == 'SESSIONEXTENSION' else 'UNRESOLVED',
            'instance_scope': scoped, 'associated_source_declarations': associated,
            'physical_binding': {'status': 'UNSUPPORTED_DEFINITION_FORMAT' if supplied_connections else 'MISSING_METADATA',
                                 'supplied_connection_node_ids': [n['node_id'] for n in supplied_connections],
                                 'database': None, 'schema': None, 'connection_definition_node_id': None, 'parameter_values': None},
            'required_exports': [
                'Readable connection properties for alias '+repr(alias)+' with physical database/service, technology and schema mapping, without credentials.',
                'Actual source/target DDL and applicable table/lookup overrides for this connection context.',
                'Exact PowerCenter product version, selected configuration and parameter/environment values used for the run.'],
            'semantic_status': 'NOT_VERIFIED'}, related
