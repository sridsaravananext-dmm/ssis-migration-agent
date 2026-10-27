"""Version 2 adds port-default evidence while retaining the version 1 analyzer."""
from __future__ import annotations

from collections import defaultdict

from .behavior import BehaviorAnalysis
from .defaults import analyze_port_default

PROFILE = 'powercenter_declared_behavior_v2'


class DefaultBehaviorAnalysis(BehaviorAnalysis):
    profile = PROFILE

    def emit(self, node, kind, declarations, preservation, unresolved, related=()):
        defaults = []
        if kind == 'TRANSFORMATION':
            fields = self.child(node, 'PORT'); ports = defaultdict(list)
            for field in fields:ports[self.attrs(field).get('NAME')].append(field['node_id'])
            for field in fields:
                defaults.append({'field_node_id': field['node_id'], 'port_name': self.attrs(field).get('NAME'),
                                 'port_type': self.attrs(field).get('PORTTYPE'),
                                 'default': analyze_port_default(self.attrs(field), ports)})
            declarations['port_defaults'] = defaults
            preservation = [*preservation, 'Keep absent, empty and explicit port defaults distinct; verify default invocation, type coercion and error handling.']
            unresolved = [*unresolved, *[item for value in defaults for item in value['default']['unresolved_items']]]
        super().emit(node, kind, declarations, preservation, unresolved, related)
        for value in defaults:
            if value['default']['declaration_state'] == 'EXPRESSION':
                field = self.nodes[value['field_node_id']]
                links = [e['edge_id'] for e in self.outgoing[field['node_id']] if e['kind'] == 'COLUMN_LINEAGE']
                super().emit(field, 'PORT_DEFAULT', {'transformation_node_id': node['node_id'],
                             'outgoing_column_link_ids': links, **value},
                             ['Preserve this declared port-default expression and error message in its invocation context; verify row rejection, stop, transaction and recovery policies.'],
                             value['default']['unresolved_items'], related=[node])
