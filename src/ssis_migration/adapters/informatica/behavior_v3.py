"""Version 3 binds exported configuration/connection contexts without merging them."""
from __future__ import annotations

from .behavior_v2 import DefaultBehaviorAnalysis
from .configuration import configuration_context, connection_context

PROFILE = 'powercenter_declared_behavior_v3'


class ConfigurationBehaviorAnalysis(DefaultBehaviorAnalysis):
    profile = PROFILE

    def emit(self, node, kind, declarations, preservation, unresolved, related=()):
        if kind == 'SESSION':
            context, nodes = configuration_context(self, node)
            declarations['configuration_contributions'] = context
            related = [*related, *nodes]
            preservation = [*preservation, 'Preserve configuration/session/instance/extension contributions in their original scopes; verify product-version-specific precedence before applying any winner.']
            unresolved = [*unresolved, 'Exact PowerCenter product version and verified precedence policy are missing; effective configuration remains unset.']
            if context['configuration_selection'] != 'DECLARATION_BOUND':
                unresolved.append('Selected session configuration is missing, ambiguous or outside the supported same-folder declaration scope.')
            if context['unmatched_instance_declaration_ids']:
                unresolved.append('Session instance declarations have no unique supplied mapping owner.')
            for scope in context['instance_scopes']:
                for prop in scope['properties']:
                    if prop['declaration_status'] in {'DIFFERING_DECLARATIONS', 'DUPLICATE_WITHIN_LAYER'}:
                        unresolved.append('Unresolved scoped property '+repr(prop['name'])+' for instance '+repr(scope['instance_name'])+': '+prop['declaration_status']+'.')
        elif kind == 'CONNECTION_BINDING':
            context, nodes = connection_context(self, node)
            declarations['binding_context'] = context
            related = [*related, *nodes]
            preservation = [*preservation, 'Keep connection references distinct by owner instance and role; bind real physical metadata before generation.']
            unresolved = [*unresolved, 'Physical database/schema binding unresolved for connection '+repr(context['connection_alias'])+' used by '+repr(context['owner_instance_name'])+'.', *context['required_exports']]
            if context['owner_resolution'] != 'DECLARATION_BOUND':
                unresolved.append('Connection reference has no unique supported owner/definition declaration.')
        super().emit(node, kind, declarations, preservation, unresolved, related)
