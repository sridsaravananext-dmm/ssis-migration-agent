"""Evidence-backed PowerCenter declarations and unresolved migration requirements.

This pass consumes replayed native IR, never a fabricated combined XML export.
Settings retain their original layers and values; no effective configuration,
runtime results, business keys or migration equivalence are invented.
"""
from __future__ import annotations

import copy
from collections import defaultdict

from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ..xml_export import validate_export_graph
from .expressions import analyze_expression

PROFILE = 'powercenter_declared_behavior_v1'


class BehaviorAnalysis:
    def __init__(self, graph):
        self.graph = graph
        self.nodes = {n['node_id']: n for n in graph['nodes']}
        self.children, self.parents, self.outgoing = defaultdict(list), {}, defaultdict(list)
        for edge in graph['edges']:
            self.outgoing[edge['source_id']].append(edge)
            if edge['kind'] == 'CONTAINS':
                self.children[edge['source_id']].append(self.nodes[edge['target_id']])
                self.parents[edge['target_id']] = edge['source_id']
        self.requirements = []

    @staticmethod
    def attrs(node):
        return node['attributes']['export_attributes']

    def child(self, node, *operations):
        return [n for n in self.children[node['node_id']] if n['operation'] in operations]

    def ancestor(self, node, operation):
        while node is not None:
            if node['operation'] == operation:return node
            node = self.nodes.get(self.parents.get(node['node_id']))
        return None

    def settings(self, node):
        return [{'node_id': n['node_id'], 'name': self.attrs(n).get('NAME'),
                 'value': self.attrs(n).get('VALUE'), 'attributes': copy.deepcopy(self.attrs(n))}
                for n in self.child(node, 'ATTRIBUTE', 'TABLEATTRIBUTE')]

    @staticmethod
    def setting(settings, name):
        matches = [s['value'] for s in settings if s['name'] == name]
        return matches[0] if len(matches) == 1 else None

    def emit(self, node, kind, declarations, preservation, unresolved, related=()):
        # Named settings remain a list: duplicates must not overwrite evidence.
        settings = declarations.get('settings', [])
        names = [s['name'] for s in settings]
        for name in dict.fromkeys(names):
            if not name or names.count(name) > 1:
                unresolved.append('Missing or repeated setting name '+repr(name)+'; precedence is unresolved.')
        evidence_nodes = [node, *self.children[node['node_id']], *related]
        evidence = []
        for subject in evidence_nodes:
            for ref in subject['evidence']:
                if ref not in evidence:evidence.append(copy.deepcopy(ref))
        item = {'subject_node_id': node['node_id'], 'kind': kind,
                'declarations': copy.deepcopy(declarations), 'preservation_requirements': preservation,
                'unresolved_items': list(dict.fromkeys(unresolved)), 'evidence': evidence,
                'semantic_status': 'NOT_VERIFIED'}
        item['requirement_id'] = 'requirement-' + content_digest([PROFILE, item])[:24]
        validate_document(item, 'source_requirement')
        self.requirements.append(item)

    def mapping(self, session):
        if session is None:return None
        edges = [e for e in self.outgoing[session['node_id']] if e['attributes'].get('role') == 'SESSION_MAPPING']
        return self.nodes.get(edges[0]['target_id']) if len(edges) == 1 and edges[0]['resolution'] == 'RESOLVED' else None

    def instance(self, node):
        session = self.ancestor(node, 'SESSION'); mapping = self.mapping(session)
        name = self.attrs(node).get('SINSTANCENAME')
        candidates = [n for n in self.child(mapping, 'INSTANCE') if self.attrs(n).get('NAME') == name] if mapping is not None and name else []
        instance = candidates[0] if len(candidates) == 1 else None
        links = [e for e in self.outgoing[instance['node_id']] if e['kind'] == 'REQUIRES' and e['resolution'] == 'RESOLVED'] if instance else []
        definition = self.nodes.get(links[0]['target_id']) if len(links) == 1 else None
        return mapping, instance, definition

    def transformation(self, node):
        attrs = self.attrs(node); kind = attrs.get('TYPE')
        settings = self.settings(node)
        fields = self.child(node, 'PORT'); ports = defaultdict(list)
        for field in fields:ports[self.attrs(field).get('NAME')].append(field['node_id'])
        expressions = []
        for field in fields:
            if 'EXPRESSION' in self.attrs(field):
                expressions.append({'field_node_id': field['node_id'], **analyze_expression(self.attrs(field)['EXPRESSION'], ports)})
        details = {'name': attrs.get('NAME'), 'transformation_type': kind, 'settings': settings,
                   'ports': [{'node_id': f['node_id'], 'declaration': copy.deepcopy(self.attrs(f))} for f in fields],
                   'expressions': expressions}
        preservation = ['Preserve declared port types, precision, defaults, nulls and expression/error behavior.']
        unresolved = ['Native expression/type/null semantics and source observations remain unverified.']
        if kind == 'Lookup Procedure':
            details['condition'] = analyze_expression(self.setting(settings, 'Lookup condition'), ports)
            details['multiple_match_policy'] = self.setting(settings, 'Lookup policy on multiple match')
            preservation.append('Preserve the declared lookup condition, duplicate-match policy and cache behavior.')
            unresolved.append('Lookup match cardinality and duplicate winner behavior need source evidence; no unique key or tie-break is inferred.')
        elif kind == 'Sequence':
            details['sequence'] = {name: self.setting(settings, name) for name in (
                'Start Value', 'Increment By', 'Current Value', 'End Value', 'Cycle', 'Reset', 'Number of Cached Values', 'Is Current Value Shared')}
            preservation.append('Preserve sequence state, range, reset, cache, restart, rollback and concurrency behavior.')
            unresolved.append('Sequence lifecycle/state equivalence is unimplemented; per-run row numbering is not established as equivalent.')
        elif kind == 'Update Strategy':
            details['row_operation_expression'] = analyze_expression(self.setting(settings, 'Update Strategy Expression'), ports, row_flags=True)
            preservation.append('Preserve row-operation flags together with the selected session mode and target writer policies.')
            unresolved.append('A row-operation flag alone does not establish the effective target write, matching keys or error handling.')
        elif kind == 'Router':
            preservation.append('Preserve every declared group, predicate, field reference, output connector and default route.')
        elif kind == 'Source Qualifier':
            details['sql_override'] = self.setting(settings, 'Sql Query')
            preservation.append('Preserve declared source filtering, joins, overrides and database execution behavior without inventing a source query.')
            unresolved.append('Empty or absent SQL overrides do not supply the source-runtime-generated query or connection schema.')
        elif kind != 'Expression':
            unresolved.append('Transformation type has no behavior profile: '+str(kind))
        for expression in [*expressions, *[details[k] for k in ('condition', 'row_operation_expression') if k in details]]:
            unresolved.extend(expression['unresolved_items'])
        self.emit(node, 'TRANSFORMATION', details, preservation, unresolved)

    def group(self, node):
        parent = self.nodes.get(self.parents.get(node['node_id'])); attrs = self.attrs(node)
        fields = self.child(parent, 'PORT') if parent else []
        ports = defaultdict(list)
        for field in fields:
            if self.attrs(field).get('PORTTYPE', '').startswith('INPUT'):
                ports[self.attrs(field).get('NAME')].append(field['node_id'])
        members = [f['node_id'] for f in fields if self.attrs(f).get('GROUP') == attrs.get('NAME')]
        links = [e['edge_id'] for e in self.graph['edges'] if e['kind'] == 'COLUMN_LINEAGE' and e['source_id'] in members]
        expression = analyze_expression(attrs['EXPRESSION'], ports) if 'EXPRESSION' in attrs else None
        issues = ['Router predicate, null/comparison, multi-group and default-route semantics require verification.']
        field_bindings = []
        related = [f for f in fields if f['node_id'] in members]
        for field in related[:]:
            if self.attrs(field).get('PORTTYPE') == 'OUTPUT':
                name = self.attrs(field).get('REF_FIELD')
                matches = [f for f in fields if self.attrs(f).get('NAME') == name and self.attrs(f).get('PORTTYPE', '').startswith('INPUT')]
                field_bindings.append({'output_node_id': field['node_id'], 'input_node_id': matches[0]['node_id'] if len(matches) == 1 else None,
                                       'reference': name, 'resolution': 'DECLARATION_FOUND' if len(matches) == 1 else 'UNRESOLVED'})
                related.extend(matches)
                if len(matches) != 1:issues.append('Missing or ambiguous router input field reference: '+str(name))
        if parent is None or self.attrs(parent).get('TYPE') != 'Router':issues.append('Group is outside the implemented Router declaration profile.')
        if attrs.get('TYPE') == 'OUTPUT' and 'EXPRESSION' not in attrs:
            issues.append('Output group predicate is missing; no predicate or destination was invented.')
        if attrs.get('TYPE') not in {'INPUT', 'OUTPUT', 'OUTPUT/DEFAULT'}:
            issues.append('Group type is outside the implemented native Router profile.')
        if expression:issues.extend(expression['unresolved_items'])
        if attrs.get('TYPE', '').startswith('OUTPUT') and not links:
            issues.append('This output group has no declared outgoing column links; confirm intended discard handling without inventing a destination.')
        self.emit(node, 'ROUTER_GROUP', {'group': copy.deepcopy(attrs), 'expression': expression,
                  'port_node_ids': members, 'field_bindings': field_bindings, 'outgoing_column_link_ids': links},
                  ['Preserve group membership, predicate, default-route behavior and its actual declared consumers.'], issues,
                  related=related)

    def session(self, node):
        mapping = self.mapping(node); folder = self.ancestor(node, 'FOLDER')
        settings = self.settings(node)
        references, related, issues = [], [], ['Configuration precedence, transaction boundaries, error/recovery behavior and parameter binding remain unverified.']
        for ref in self.child(node, 'CONFIGREFERENCE'):
            name = self.attrs(ref).get('REFOBJECTNAME')
            candidates = [n for n in self.nodes.values() if n['operation'] == 'CONFIG' and name
                          and self.attrs(n).get('NAME') == name and self.ancestor(n, 'FOLDER') == folder]
            references.append({'reference_node_id': ref['node_id'], 'name': name,
                               'candidate_node_ids': [n['node_id'] for n in candidates],
                               'resolution': 'DECLARATION_FOUND' if len(candidates) == 1 else 'UNRESOLVED'})
            related.extend(candidates)
            if len(candidates) != 1:issues.append('Missing or ambiguous local session configuration: '+str(name))
        if mapping is None:issues.append('Session mapping is missing or ambiguous; no mapping behavior was assumed.')
        self.emit(node, 'SESSION', {'session': copy.deepcopy(self.attrs(node)), 'settings': settings,
                  'source_row_mode': self.setting(settings, 'Treat source rows as'),
                  'transaction_policy': {name: self.setting(settings, name) for name in (
                      'Commit Type', 'Commit Interval', 'Commit On End Of File', 'Rollback Transactions on Errors', 'Recovery Strategy')},
                  'mapping_node_id': mapping['node_id'] if mapping else None, 'configuration_references': references,
                  'effective_configuration': None},
                  ['Preserve session row mode, commit/rollback boundaries, recovery, precision, parameters and configuration layers.'],
                  issues, related=[*related, *([mapping] if mapping else [])])

    def writer_or_override(self, node):
        mapping, instance, definition = self.instance(node)
        related = [n for n in (mapping, instance, definition) if n is not None]
        issues = ['Session override precedence, physical bindings and execution/partition behavior require verification.']
        if instance is None or definition is None:issues.append('Mapping instance or its definition is missing or ambiguous for '+str(self.attrs(node).get('SINSTANCENAME')))
        writer = node['operation'] == 'SESSIONEXTENSION'
        if writer and (definition is None or definition['operation'] != 'TARGET_DEFINITION'):
            issues.append('Writer target definition is unresolved; no output table was invented.')
        details = {'declaration': copy.deepcopy(self.attrs(node)), 'settings': self.settings(node),
                   'mapping_node_id': mapping['node_id'] if mapping else None,
                   'instance_node_id': instance['node_id'] if instance else None,
                   'definition_node_id': definition['node_id'] if definition else None,
                   'partitions': [{'node_id': n['node_id'], 'declaration': copy.deepcopy(self.attrs(n))} for n in self.child(node, 'PARTITION')]}
        preservation = ['Preserve instance-specific overrides and partition/execution declarations independently of base mapping settings.']
        if writer:
            details['declared_operation_options'] = {name: self.setting(details['settings'], name) for name in (
                'Insert', 'Update as Update', 'Update as Insert', 'Update else Insert', 'Delete', 'Truncate target table option')}
            details['effective_row_operations'] = None
            issues.append('Enabled writer operations are permissions, not proof that corresponding row flags are emitted. Shared-target coordination, matching keys and failure behavior are unresolved.')
            preservation.append('Preserve writer insert/update/delete/truncate/reject policies together with data-driven row flags and transaction handling.')
        self.emit(node, 'TARGET_WRITER' if writer else 'SESSION_TRANSFORMATION', details, preservation, issues, related)

    def run(self):
        for node in self.graph['nodes']:
            operation = node['operation']
            if operation == 'TRANSFORMATION':self.transformation(node)
            elif operation == 'GROUP':self.group(node)
            elif operation == 'SESSION':self.session(node)
            elif operation == 'SESSTRANSFORMATIONINST' or (operation == 'SESSIONEXTENSION' and self.attrs(node).get('TYPE') == 'WRITER'):
                self.writer_or_override(node)
            elif operation == 'CONFIG':
                self.emit(node, 'SESSION_CONFIGURATION', {'declaration': copy.deepcopy(self.attrs(node)), 'settings': self.settings(node)},
                          ['Preserve the selected configuration layer and its error, precision, date/time and partition settings.'],
                          ['Effective defaults, override precedence and error thresholds require version-specific verification.'])
            elif operation == 'CONNECTION_REFERENCE':
                self.emit(node, 'CONNECTION_BINDING', {'reference': copy.deepcopy(self.attrs(node))},
                          ['Bind the declared connection alias to supplied physical schema/database metadata before generation.'],
                          ['Connection aliases alone do not establish physical identities, schemas or credentials; no connection binding was invented.'])
            elif operation == 'TARGETLOADORDER':
                parent = self.nodes[self.parents[node['node_id']]]
                peers = [n for n in self.child(parent, 'TARGETLOADORDER') if self.attrs(n).get('ORDER') == self.attrs(node).get('ORDER')]
                self.emit(node, 'TARGET_LOAD_ORDER', {'declaration': copy.deepcopy(self.attrs(node)), 'same_declared_rank_node_ids': [n['node_id'] for n in peers]},
                          ['Preserve declared target load ranks and verify dependencies and shared-target coordination.'],
                          ['Equal ranks do not establish a serial tie-break; XML listing order is not an execution strategy.'] if len(peers) > 1 else ['Target load ordering semantics require verification.'], peers)
            elif operation in {'SOURCE_DEFINITION', 'TARGET_DEFINITION'}:
                fields = self.child(node, 'COLUMN')
                self.emit(node, operation, {'declaration': copy.deepcopy(self.attrs(node)),
                          'columns': [{'node_id': n['node_id'], 'declaration': copy.deepcopy(self.attrs(n))} for n in fields]},
                          ['Preserve declared column types, precision, nullability and keys; verify physical metadata and actual data constraints.'],
                          ['Exported definitions do not prove observed uniqueness, target matching policy or data equivalence.'])
        document = {'analysis_version': '1.0', 'profile': PROFILE, 'ir_id': self.graph['ir_id'],
                    'source_analysis_sha256': self.graph['provenance']['source_analysis_sha256'],
                    'evidence_files': copy.deepcopy(self.graph['evidence_files']), 'requirements': self.requirements,
                    'semantic_status': 'NOT_VERIFIED', 'scope': 'DECLARATIONS_AND_REVIEW_REQUIREMENTS'}
        validate_document(document, 'source_behavior_analysis'); validate_evidence_references(document)
        return document


def analyze_behavior(graph):
    validate_export_graph(graph)
    if graph['source']['platform'] != 'informatica':
        raise SchemaValidationError('PowerCenter behavior analysis requires a replayable PowerCenter graph.')
    return BehaviorAnalysis(graph).run()


def validate_behavior_analysis(document, graph):
    validate_document(document, 'source_behavior_analysis'); validate_evidence_references(document)
    if document != analyze_behavior(graph):
        raise SchemaValidationError('PowerCenter behavior requirements do not replay from the original export declarations.')
