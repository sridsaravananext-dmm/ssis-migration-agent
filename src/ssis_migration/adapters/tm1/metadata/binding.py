"""Declaration binding over validated TM1 reports; no expression evaluation."""
from __future__ import annotations

import copy

from ssis_migration.ir.normalized import content_digest
from ssis_migration.schemas import SchemaValidationError, validate_document
from ssis_migration.languages.mdx import validate_mdx_analysis
from ..rules import validate_rules_analysis
from ..ti import validate_ti_analysis
from ..ti.analysis import _walk
from .catalog import Catalog, validate_metadata_snapshot

MATCHED = 'MATCHED_DECLARATION'


def binding_report_id(report):
    return 'tm1-binding-' + content_digest({k: v for k, v in report.items() if k != 'binding_report_id'})


def _validate_inputs(analysis, metadata, context):
    validators = {'mdx': validate_mdx_analysis, 'tm1_rules': validate_rules_analysis,
                  'tm1_turbointegrator': validate_ti_analysis}
    if not isinstance(analysis, dict) or analysis.get('language') not in validators:
        raise ValueError('Supply a validated TM1 MDX, rules or TurboIntegrator analysis report.')
    validators[analysis['language']](analysis)
    if not isinstance(metadata, list) or len(metadata) > 32:
        raise ValueError('Supply at most 32 explicit TM1 metadata snapshots.')
    if analysis['language'] == 'tm1_turbointegrator':
        ids = {s['source']['file_id'] for s in analysis['sections'] if s['source'] is not None}
        if analysis['manifest_file'] is not None:
            ids.add(analysis['manifest_file']['file_id'])
    else:
        ids = {analysis['source']['file_id']}
    for snapshot in metadata:
        validate_metadata_snapshot(snapshot)
        identity = snapshot['source']['file_id']
        if identity in ids:
            raise SchemaValidationError('Duplicate/colliding TM1 metadata and source input file identity.')
        ids.add(identity)
    for name, value in context.items():
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(name + ' must be an explicit nonempty declaration or None.')
    if analysis['language'] != 'tm1_rules' and context['source_cube'] is not None:
        raise ValueError('source_cube context is only valid for rules; MDX/calls retain their source targets.')
    if analysis['language'] != 'tm1_turbointegrator' and context['process_name'] is not None:
        raise ValueError('process_name context is only valid for TurboIntegrator.')


def _literal(node):
    return node['value'] if node and node['kind'] == 'literal' and node.get('literal_type', node.get('category')) == 'STRING' else None


def _one(entries):
    return entries[0] if len(entries) == 1 else None


class Binder:
    def __init__(self, analysis, metadata, context):
        self.analysis, self.metadata, self.context = analysis, metadata, context
        self.catalog = Catalog(metadata)
        self.bindings = []
        self.process_name = None
        self.process = None

    def add(self, kind, requested, candidates=(), *, span=None, section=None, status=None, details=None, reason=None):
        candidates = list(candidates)
        if status is None:
            status = 'MISSING' if not candidates else 'AMBIGUOUS' if len(candidates) > 1 else MATCHED
        if status == MATCHED and self.catalog.issues:
            status = 'CONFLICT'
            reason = 'Catalog inconsistencies prevent safe declaration binding; inspect metadata_issues.'
        source_evidence = None
        if span is not None:
            source = self.analysis.get('source')
            suffix = 'mdx' if self.analysis['language'] == 'mdx' else 'rux'
            if self.analysis['language'] == 'tm1_turbointegrator':
                source = next(s['source'] for s in self.analysis['sections'] if s['name'] == section)
                suffix = 'ti'
            source_evidence = {'file_id': source['file_id'], 'locator': source['locator'] + '#' + suffix + '-chars=' +
                               str(span['start']) + ':' + str(span['end']), 'rule': 'tm1_metadata_binding_source'}
        item = {'binding_id': 'binding-' + content_digest([len(self.bindings), kind, requested, section, span])[:24],
                'kind': kind, 'requested': requested, 'status': status, 'section': section, 'span': span,
                'source_evidence': source_evidence, 'metadata_evidence': [e['evidence'] for e in candidates],
                'details': details or {}, 'reason': reason or {
                    MATCHED: 'Unique exact supplied declaration; runtime meaning remains unverified.',
                    'MISSING': 'Required declaration/context was not supplied.',
                    'AMBIGUOUS': 'Multiple declarations or interpretations exist; none was selected.',
                    'CONFLICT': 'Source and supplied declarations conflict.',
                    'DYNAMIC': 'Runtime expression/context remains unevaluated.',
                    'UNSUPPORTED': 'This binding needs additional source-specific semantics.',
                }[status]}
        self.bindings.append(item)
        return item

    def cube(self, name, *, span=None, section=None, kind='CUBE'):
        candidates = self.catalog.find('cube', name) if name else []
        self.add(kind, {'name': name}, candidates, span=span, section=section)
        return _one(candidates)

    def member(self, dimension, hierarchy, name, **source):
        candidates = self.catalog.find('member', name, dimension=dimension, hierarchy=hierarchy)
        return self.add('MEMBER', {'dimension': dimension, 'hierarchy': hierarchy, 'member': name}, candidates, **source)

    def coordinate_call(self, node, target_index, first_coordinate, section=None):
        args = node['arguments']; span = node['span']
        target = _literal(args[target_index]) if len(args) > target_index else None
        if target is None:
            self.add('CALL_CUBE', {'call': node['name'], 'target_expression': args[target_index] if len(args) > target_index else None},
                     span=span, section=section, status='DYNAMIC')
            return
        cube = self.cube(target, span=span, section=section, kind='CALL_CUBE')
        if cube is None:
            return
        axes = cube['value']['dimensions']; coordinates = args[first_coordinate:]
        exact = len(axes) == len(coordinates)
        self.add('COORDINATE_COUNT', {'cube': target, 'coordinate_count': len(coordinates)}, [cube],
                 span=span, section=section, status=MATCHED if exact else 'CONFLICT',
                 details={'declared_dimension_count': len(axes), 'ordered_dimensions': axes})
        if not exact:
            return  # A mismatched tuple cannot establish a coordinate-to-axis mapping.
        for axis, coordinate in zip(axes, coordinates):
            literal = _literal(coordinate)
            if literal is None:
                self.add('COORDINATE', {**axis, 'expression': coordinate}, [cube], span=coordinate['span'],
                         section=section, status='DYNAMIC')
            else:
                self.member(**axis, name=literal, span=coordinate['span'], section=section)

    def dimension_call(self, node, section=None):
        target = _literal(node['arguments'][0]) if node['arguments'] else None
        candidates = self.catalog.find('dimension', target) if target else []
        self.add('CALL_DIMENSION', {'call': node['name'], 'dimension': target}, candidates,
                 span=node['span'], section=section, status='DYNAMIC' if target is None else None)

    def version(self):
        declared = self.analysis.get('source_export_version')
        entries = []
        for snapshot in self.metadata:
            if snapshot['document']['source_export_version'] is not None:
                source = snapshot['source']
                entries.append({'evidence': {'file_id': source['file_id'], 'locator': source['locator'] + 'source_export_version',
                                             'rule': 'supplied_source_version'}})
        versions = self.catalog.versions
        status = 'MISSING' if not versions else 'CONFLICT' if len(versions) > 1 or (declared is not None and declared not in versions) else MATCHED
        self.add('SOURCE_VERSION', {'analysis_declared_version': declared}, entries, status=status,
                 details={'metadata_declared_versions': sorted(versions)},
                 reason='Version declarations are compared only; server compatibility is not verified.')

    def rules(self):
        cube = self.cube(self.context['source_cube'], kind='RULE_SOURCE_CUBE')
        ast = [s['ast'] for s in self.analysis['statements']]
        for node in _walk(ast):
            if node['kind'] == 'call' and node['name'].upper() == 'DB':
                self.coordinate_call(node, 0, 1)
            elif node['kind'] == 'call' and node['name'].upper() in {'ATTRN','ATTRS','ELISANC','ELISCOMP','ELLEV','DIMIX'}:
                self.dimension_call(node)
            elif node['kind'] == 'dimension_context':
                axes = [a for a in cube['value']['dimensions'] if a['dimension'] == node['name']] if cube else []
                candidates = self.catalog.find('dimension', node['name']) if len(axes) == 1 else []
                self.add('DIMENSION_CONTEXT', {'dimension': node['name']}, candidates, span=node['span'],
                         details={'member_value': 'DYNAMIC_NOT_EVALUATED'})
            elif node['kind'] == 'area':
                used = set()
                for selector in node['selectors']:
                    candidates = []
                    if cube:
                        for axis in cube['value']['dimensions']:
                            if selector['dimension'] is None or selector['dimension'] == axis['dimension']:
                                candidates.extend(self.catalog.find('member', selector['element'], **axis))
                    binding = self.add('AREA_MEMBER', selector, candidates, span=selector['span'])
                    if len(candidates) == 1:
                        dim = candidates[0]['dimension']
                        if dim in used:
                            binding['status'] = 'CONFLICT'; binding['reason'] = 'An area binds multiple selectors to the same dimension.'
                        used.add(dim)

    def mdx_path(self, node, cube):
        segments = node['segments']; values = [s['value'] for s in segments]
        if any(s['kind'] != 'NAME' for s in segments):
            self.add('MDX_PATH', {'segments': segments}, span=node['span'], status='UNSUPPORTED',
                     reason='MDX key/alias mapping is not supplied by canonical member-name metadata.')
            return
        candidates = []
        allowed = {a['dimension'] for a in cube['value']['dimensions']} if cube else set()
        if values[0] in allowed:
            if len(values) == 1:
                candidates = self.catalog.find('dimension', values[0])
            elif len(values) == 2:
                candidates = self.catalog.hierarchy(values[0], values[1])
                candidates += self.catalog.find('member', values[1], dimension=values[0])
            elif len(values) == 3:
                candidates = self.catalog.find('member', values[2], dimension=values[0], hierarchy=values[1])
        self.add('MDX_PATH', {'segments': segments}, candidates, span=node['span'])
        prop = (node['property'] or '').upper()
        if prop:
            hierarchy = _one(candidates)
            is_hierarchy = hierarchy is not None and 'members' in hierarchy['value']
            if prop == 'DEFAULTMEMBER' and is_hierarchy:
                name = hierarchy['value']['default_member']
                members = self.catalog.find('member', name, dimension=values[0], hierarchy=values[1]) if name is not None else []
                self.add('MDX_DEFAULT_MEMBER', {'path': values, 'declared_member': name}, [hierarchy, *members],
                         span=node['span'], status=MATCHED if len(members) == 1 else 'AMBIGUOUS' if members else 'MISSING')
            else:
                self.add('MDX_PROPERTY_CONTEXT', {'path': values, 'property': prop}, candidates, span=node['span'],
                         status='DYNAMIC' if prop in {'CURRENTMEMBER', 'DEFAULTMEMBER'} else 'UNSUPPORTED',
                         reason='Property evaluation, member traversal and query context are outside declaration binding.')

    def mdx(self):
        query = self.analysis['query']
        if query is None:
            return
        cube = self.cube(query['cube']['name'], span=query['cube']['span'])
        for node in _walk([query]):
            if node['kind'] == 'member_path':
                self.mdx_path(node, cube)
            elif node['kind'] == 'function' and node['name'].upper() == 'TM1SUBSETTOSET':
                args = node['arguments']; status = None; candidates = []
                name = _literal(args[1]) if len(args) > 1 else None
                scope = _literal(args[2]) if len(args) > 2 else None
                path = args[0] if args else None
                segments = path['segments'] if path and path['kind'] == 'member_path' else []
                values = [s['value'] for s in segments]
                if len(values) != 2 or any(s['kind'] != 'NAME' for s in segments) or not name or scope is None:
                    status = 'DYNAMIC'
                elif scope.lower() != 'public':
                    status = 'DYNAMIC'  # Never guess the private subset owner/security principal.
                else:
                    candidates = self.catalog.find('subset', name, dimension=values[0], hierarchy=values[1], scope='public', owner=None)
                    if len(candidates) == 1 and candidates[0]['value']['members'] is None:
                        status = 'DYNAMIC'
                self.add('MDX_SUBSET', {'name': name, 'scope': scope, 'hierarchy_path': values}, candidates,
                         span=node['span'], status=status)

    def symbol_candidates(self, name):
        if self.process is None:
            return []
        return self.catalog.find('parameter', name, process=self.process_name) + self.catalog.find('variable', name, process=self.process_name)

    def expression_type(self, node):
        if node['kind'] == 'literal':
            return 'STRING' if node['literal_type'] == 'STRING' else 'NUMERIC'
        if node['kind'] == 'variable':
            symbol = _one(self.symbol_candidates(node['name']))
            return symbol['value']['data_type'] if symbol else None
        return None

    def child_process(self, node, section):
        args = node['arguments']; target = _literal(args[0]) if args else None
        if not target:
            self.add('CHILD_PROCESS', {'call': node['name'], 'target': target}, span=node['span'], section=section, status='DYNAMIC')
            return
        candidates = self.catalog.find('process', target)
        self.add('CHILD_PROCESS', {'call': node['name'], 'target': target}, candidates, span=node['span'], section=section)
        child = _one(candidates)
        if child is None:
            return
        if len(args) % 2 != 1:
            self.add('CHILD_PARAMETER_PAIRS', {'process': target}, [child], span=node['span'], section=section, status='CONFLICT')
            return
        supplied = set(); dynamic_names = False
        for i in range(1, len(args), 2):
            name = _literal(args[i]); value = args[i+1]
            params = self.catalog.find('parameter', name, process=target) if name else []
            status = None
            value_type = self.expression_type(value)
            if name is None:
                dynamic_names = True; status = 'DYNAMIC'
            elif name in supplied:
                status = 'CONFLICT'
            elif len(params) == 1:
                status = 'DYNAMIC' if value_type is None else MATCHED if value_type == params[0]['value']['data_type'] else 'CONFLICT'
            self.add('CHILD_PARAMETER', {'process': target, 'name': name, 'value_expression': value}, params,
                     span=args[i]['span'], section=section, status=status, details={'declared_value_type': value_type})
            supplied.add(name)
        for param in self.catalog.find('parameter', process=target):
            declaration = param['value']
            if declaration['name'] not in supplied:
                self.add('OMITTED_CHILD_PARAMETER', {'process': target, 'name': declaration['name']}, [param],
                         span=node['span'], section=section,
                         status='DYNAMIC' if dynamic_names else 'MISSING' if declaration['required'] else MATCHED,
                         details={'required': declaration['required'], 'default_not_evaluated': True})

    def ti(self):
        declared = self.analysis['process_name']; selected = self.context['process_name']
        conflict = declared is not None and selected is not None and declared != selected
        self.process_name = declared if declared is not None else selected
        candidates = self.catalog.find('process', self.process_name) if self.process_name else []
        self.add('PROCESS', {'analysis_name': declared, 'context_name': selected}, candidates, status='CONFLICT' if conflict else None)
        self.process = None if conflict else _one(candidates)
        ds_name = self.process['value']['datasource'] if self.process else None
        datasource = self.catalog.find('datasource', ds_name) if ds_name is not None else []
        self.add('PROCESS_DATASOURCE', {'process': self.process_name, 'datasource': ds_name}, datasource)
        ds = _one(datasource)
        if ds is not None:
            value = ds['value']; kind = value['kind']
            fields = {'ASCII': ['location_reference'], 'ODBC': ['connection_reference','sql_dialect'],
                      'CUBE_VIEW': ['cube','view'], 'NONE': [], 'OTHER': []}[kind]
            missing = [key for key in fields if value[key] is None]
            if kind not in {'NONE','OTHER'} and not value['columns']:
                missing.append('columns')
            mapping_status = 'UNSUPPORTED' if kind == 'OTHER' or value['sql_dialect'] == 'other' else (
                'MISSING' if missing else 'DYNAMIC' if kind == 'CUBE_VIEW' else MATCHED)
            self.add('DATASOURCE_MAPPING', {'name': ds_name, 'kind': kind}, [ds], status=mapping_status,
                     details={'missing_fields': missing, 'view_definition_not_supplied': kind == 'CUBE_VIEW',
                              'mapping_does_not_prove_runtime_source': True})
        for ref in self.analysis['references']:
            symbols = self.symbol_candidates(ref['value'])
            self.add('SYMBOL_DECLARATION', {'name': ref['value'], 'access': ref['kind']}, symbols,
                     span=ref['span'], section=ref['section'], details={'declared_roles': [
                         s['value'].get('role', 'PARAMETER') for s in symbols]})
            symbol = _one(symbols)
            if symbol and symbol['value'].get('role') == 'DATASOURCE_COLUMN':
                name = symbol['value']['column']
                columns = self.catalog.find('column', name, datasource=ds_name) if name is not None and ds is not None else []
                self.add('DATASOURCE_COLUMN', {'variable': ref['value'], 'column': name}, [symbol, *columns],
                         span=ref['span'], section=ref['section'],
                         status=MATCHED if len(columns) == 1 and columns[0]['value']['data_type'] == symbol['value']['data_type'] else 'CONFLICT')
        for assignment in self.analysis['configuration_assignments']:
            key = assignment['name'].upper(); literal = assignment['literal_string']; expected = None
            if ds:
                if key == 'DATASOURCETYPE':
                    expected = {'ASCII': 'ASCII', 'ODBC': 'ODBC', 'CUBE_VIEW': 'VIEW', 'NONE': 'NONE'}.get(ds['value']['kind'])
                elif key == 'DATASOURCENAMEFORSERVER':
                    expected = ds['value'][{'ODBC':'connection_reference','CUBE_VIEW':'cube'}.get(ds['value']['kind'],'location_reference')]
                elif key == 'DATASOURCENAMEFORCLIENT':
                    # A server path/DSN never supplies an unexported client location.
                    expected = ds['value'].get('client_location_reference')
                elif key == 'DATASOURCECUBEVIEW':
                    expected = ds['value']['view']
            status = 'DYNAMIC' if literal is None else 'MISSING' if expected is None else MATCHED if literal == expected else 'CONFLICT'
            self.add('DATASOURCE_CONFIGURATION', {'name': assignment['name'], 'value_expression': assignment['value_expression']},
                     datasource, span=assignment['span'], section=assignment['section'], status=status,
                     details={'mapped_literal': expected, 'assignments_not_executed': True})
        for section in self.analysis['sections']:
            for node in _walk(section['ast']):
                if node['kind'] != 'call':
                    continue
                upper = node['name'].upper()
                if upper in {'CELLGETN', 'CELLGETS'}:
                    self.coordinate_call(node, 0, 1, section['name'])
                elif upper in {'CELLPUTN', 'CELLPUTS', 'CELLINCREMENTN'}:
                    self.coordinate_call(node, 1, 2, section['name'])
                elif upper in {'EXECUTEPROCESS', 'RUNPROCESS'}:
                    self.child_process(node, section['name'])
                elif upper in {'DIMENSIONEXISTS','DIMENSIONELEMENTINSERT','DIMENSIONDESTROY','SUBSETCREATE','SUBSETDESTROY'}:
                    self.dimension_call(node, section['name'])
                elif upper in {'CUBEEXISTS','VIEWZEROOUT','CUBECLEARDATA','CUBEDESTROY','VIEWCREATE'}:
                    target = _literal(node['arguments'][0]) if node['arguments'] else None
                    if target is None:
                        self.add('CALL_CUBE', {'call': node['name'], 'target_expression': node['arguments'][0] if node['arguments'] else None},
                                 span=node['span'], section=section['name'], status='DYNAMIC')
                    else:
                        self.cube(target, span=node['span'], section=section['name'], kind='CALL_CUBE')
                elif upper in {'ODBCOPEN', 'ODBCOUTPUT', 'ODBCCLOSE'}:
                    target = _literal(node['arguments'][0]) if node['arguments'] else None
                    mappings = [d for d in self.catalog.entries['datasource'] if target is not None and
                                d['value']['kind'] == 'ODBC' and d['value']['connection_reference'] == target]
                    self.add('CONNECTION_MAPPING', {'connection_reference': target}, mappings, span=node['span'],
                             section=section['name'], status='DYNAMIC' if target is None else None,
                             details={'sql_dialects': [d['value']['sql_dialect'] for d in mappings], 'sql_not_parsed_or_executed': True})

    def build(self):
        self.version()
        {'mdx': self.mdx, 'tm1_rules': self.rules, 'tm1_turbointegrator': self.ti}[self.analysis['language']]()
        status = 'BLOCKED' if self.catalog.issues or self.analysis['status'] != 'STRUCTURED' else (
            'PARTIAL' if any(b['status'] != MATCHED for b in self.bindings) else 'BOUND_REVIEW_REQUIRED')
        report = {'binding_version': '1.0', 'scope': 'SUPPLIED_TM1_METADATA_DECLARATIONS',
                  'identifier_matching': 'EXACT_CANONICAL_NAMES', 'status': status,
                  'source_analysis': self.analysis, 'metadata_inputs': self.metadata, 'context': self.context,
                  'bindings': self.bindings, 'metadata_issues': self.catalog.issues,
                  'remaining_dependencies': copy.deepcopy(self.analysis['dependencies']),
                  'semantic_status': 'NOT_VERIFIED', 'migration_accepted': False,
                  'runtime_requirements': [
                      'Declarations do not prove runtime parameter values, definite assignment, source field availability or process execution.',
                      'Verify datasource lifecycle, separate Metadata/Data passes, early controls and transaction/error behavior.',
                      'Verify dimensions, hierarchy/alias/key semantics, consolidation weights, rules, feeders, defaults and security.',
                      'Resolve dynamic coordinates, subsets, child-process completion, external effects and SQL behavior.',
                      'Require migration contracts, critical-rule gates, weighted verified coverage, controlled execution and reconciliation.']}
        report['binding_report_id'] = binding_report_id(report)
        return report


def bind_tm1_metadata(analysis, metadata, *, source_cube=None, process_name=None):
    """Return an overlay; source analysis/dependencies and metadata are never mutated."""
    context = {'source_cube': source_cube, 'process_name': process_name}
    _validate_inputs(analysis, metadata, context)
    report = Binder(copy.deepcopy(analysis), copy.deepcopy(metadata), context).build()
    validate_document(report, 'tm1_metadata_binding')
    return report


def validate_tm1_binding_report(report):
    """Re-derive all evidence, candidates, statuses and omissions, even after rehashing."""
    validate_document(report, 'tm1_metadata_binding')
    _validate_inputs(report['source_analysis'], report['metadata_inputs'], report['context'])
    if report['binding_report_id'] != binding_report_id(report):
        raise SchemaValidationError('TM1 metadata binding report identity is stale.')
    expected = Binder(report['source_analysis'], report['metadata_inputs'], report['context']).build()
    if report != expected:
        raise SchemaValidationError('TM1 bindings, evidence or unresolved requirements do not match their supplied inputs.')
