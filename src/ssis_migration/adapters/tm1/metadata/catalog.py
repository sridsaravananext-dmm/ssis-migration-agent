"""Explicit, offline TM1 metadata declarations; never a relational schema loader."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ssis_migration.intake.inventory import describe_file
from ssis_migration.schemas import SchemaValidationError, validate_document
from ssis_migration.metadata.json_io import read_json  # Compatibility re-export.

MAX_METADATA_BYTES = 8 * 1_048_576


def metadata_snapshot(raw: bytes, *, file_id: str, locator: str = 'json:/', path: str | None = None) -> dict:
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError('TM1 metadata exceeds the 8 MiB per-file limit.')
    if not isinstance(file_id, str) or not file_id or not isinstance(locator, str) or not locator:
        raise ValueError('TM1 metadata requires original file identity and locator.')
    document = read_json(raw)
    validate_document(document, 'tm1_metadata')
    return {'document': document, 'source': {
        'file_id': file_id, 'locator': locator, 'path': path,
        # Preserve a possible BOM in text, so UTF-8 encoding reconstructs raw bytes.
        'text': raw.decode('utf-8'), 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw),
    }}


def validate_metadata_snapshot(snapshot: dict) -> None:
    if not isinstance(snapshot, dict) or set(snapshot) != {'document', 'source'}:
        raise SchemaValidationError('Expected TM1 metadata document and original source snapshot.')
    source = snapshot['source']
    if (not isinstance(source, dict) or set(source) != {'file_id', 'locator', 'path', 'text', 'sha256', 'size_bytes'}
        or not isinstance(source['text'], str) or not isinstance(source['file_id'], str) or not source['file_id']
        or not isinstance(source['locator'], str) or not source['locator']
        or (source['path'] is not None and not isinstance(source['path'], str))):
        raise SchemaValidationError('Malformed TM1 metadata source identity.')
    raw = source['text'].encode('utf-8')
    if source['sha256'] != hashlib.sha256(raw).hexdigest() or source['size_bytes'] != len(raw):
        raise SchemaValidationError('TM1 metadata source hash/size is stale.')
    parsed = metadata_snapshot(raw, file_id=source['file_id'], locator=source['locator'], path=source['path'])
    if parsed != snapshot:
        raise SchemaValidationError('TM1 metadata declarations differ from their original JSON evidence.')


def load_tm1_metadata(path: str | Path) -> dict:
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError('Supply a regular TM1 metadata JSON file, not a symlink.')
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError('TM1 metadata exceeds the 8 MiB per-file limit.')
    raw = path.read_bytes()
    inventory = describe_file(path)
    if inventory['sha256'] != hashlib.sha256(raw).hexdigest():
        raise ValueError('TM1 metadata changed during inventory.')
    result = metadata_snapshot(raw, file_id=inventory['file_id'], path=str(path))
    if path.is_symlink() or path.read_bytes() != raw:
        raise ValueError('TM1 metadata changed during loading.')
    return result


class Catalog:
    """Retain every candidate and exact JSON-pointer evidence; no first-match wins."""
    def __init__(self, snapshots):
        self.entries = {kind: [] for kind in ('dimension', 'hierarchy', 'member', 'cube', 'subset',
                                             'process', 'parameter', 'variable', 'datasource', 'column')}
        self.issues = []
        roots = [{'evidence': {'file_id': s['source']['file_id'], 'locator': s['source']['locator'],
                              'rule': 'supplied_tm1_catalog_identity'}} for s in snapshots]
        systems, versions = set(), set()
        for snapshot in snapshots:
            doc = snapshot['document']; source = snapshot['source']
            systems.add(doc['source_system'])
            if doc['source_export_version'] is not None:
                versions.add(doc['source_export_version'])

            def add(kind, value, pointer, **scope):
                entry = {'value': value, 'evidence': {'file_id': source['file_id'],
                         'locator': source['locator'] + pointer, 'rule': 'supplied_tm1_metadata_declaration'}, **scope}
                self.entries[kind].append(entry)
                return entry

            for d, dimension in enumerate(doc['dimensions']):
                dp = 'dimensions/' + str(d)
                add('dimension', dimension, dp)
                for h, hierarchy in enumerate(dimension['hierarchies']):
                    hp = dp + '/hierarchies/' + str(h)
                    scope = {'dimension': dimension['name'], 'hierarchy': hierarchy['name']}
                    add('hierarchy', hierarchy, hp, dimension=dimension['name'])
                    for m, member in enumerate(hierarchy['members']):
                        add('member', member, hp + '/members/' + str(m), **scope)
            for kind, field in (('cube', 'cubes'), ('subset', 'subsets'), ('datasource', 'datasources'), ('process', 'processes')):
                for i, value in enumerate(doc[field]):
                    pointer = field + '/' + str(i)
                    add(kind, value, pointer)
                    if kind == 'process':
                        for child, plural in (('parameter', 'parameters'), ('variable', 'variables')):
                            for j, item in enumerate(value[plural]):
                                add(child, item, pointer + '/' + plural + '/' + str(j), process=value['name'])
                    elif kind == 'datasource':
                        for j, item in enumerate(value['columns']):
                            add('column', item, pointer + '/columns/' + str(j), datasource=value['name'])
        self.indices = {}
        for kind, entries in self.entries.items():
            fields = self.indices[kind] = {}
            for entry in entries:
                for key in ('name', 'dimension', 'hierarchy', 'process', 'datasource', 'scope', 'owner'):
                    value = entry.get(key, entry['value'].get(key))
                    fields.setdefault(key, {}).setdefault(value, []).append(entry)
        self.versions = versions
        if len(systems) > 1:
            self.issue('CONFLICTING_SOURCE_SYSTEMS', 'Metadata files declare different source systems; no cross-system binding is safe.', roots)
        if len(versions) > 1:
            self.issue('CONFLICTING_SOURCE_VERSIONS', 'Metadata files declare conflicting export/server versions.', roots)
        self._check_links()

    def find(self, kind, name=None, **scope):
        filters = {**scope, **({'name': name} if name is not None else {})}
        pools = [self.indices[kind].get(key, {}).get(value, []) for key, value in filters.items()]
        pool = min(pools, key=len) if pools else self.entries[kind]
        return [e for e in pool
                if (name is None or e['value']['name'] == name)
                and all(e.get(k, e['value'].get(k)) == v for k, v in scope.items())]

    def issue(self, code, message, entries):
        self.issues.append({'code': code, 'severity': 'ERROR', 'message': message,
                            'evidence': [e['evidence'] for e in entries]})

    def hierarchy(self, dimension, hierarchy):
        return self.find('hierarchy', hierarchy, dimension=dimension)

    def _check_links(self):
        scopes = {'hierarchy': ('dimension',), 'member': ('dimension', 'hierarchy'),
                  'parameter': ('process',), 'variable': ('process',), 'column': ('datasource',),
                  'subset': ('dimension', 'hierarchy', 'scope', 'owner')}
        for kind, entries in self.entries.items():
            grouped = {}
            for entry in entries:
                key = (entry['value']['name'], *(entry.get(s, entry['value'].get(s)) for s in scopes.get(kind, ())))
                grouped.setdefault(key, []).append(entry)
            for key, candidates in grouped.items():
                if len(candidates) > 1:
                    self.issue('DUPLICATE_METADATA_IDENTITY', 'Duplicate ' + kind + ' declaration: ' + repr(key), candidates)
        for cube in self.entries['cube']:
            seen = set()
            for axis in cube['value']['dimensions']:
                if axis['dimension'] in seen or len(self.find('dimension', axis['dimension'])) != 1 or len(self.hierarchy(**axis)) != 1:
                    self.issue('INVALID_CUBE_DIMENSION', 'Cube axes require unique dimensions and an unambiguous declared coordinate hierarchy.', [cube])
                seen.add(axis['dimension'])
        for hierarchy in self.entries['hierarchy']:
            value = hierarchy['value']; scope = {'dimension': hierarchy['dimension'], 'hierarchy': value['name']}
            members = self.find('member', **scope)
            by_name = {}
            for entry in members:
                by_name.setdefault(entry['value']['name'], []).append(entry)
            if value['default_member'] is not None and len(by_name.get(value['default_member'], [])) != 1:
                self.issue('INVALID_DEFAULT_MEMBER', 'Default member lacks a unique member declaration.', [hierarchy])
            graph = {}
            for member in members:
                node = member['value']; graph[node['name']] = []
                seen = set()
                if node['type'] != 'C' and node['children']:
                    self.issue('NON_CONSOLIDATED_CHILDREN', 'Only explicitly consolidated members can declare weighted children.', [member])
                for child in node['children']:
                    if child['member'] in seen or len(by_name.get(child['member'], [])) != 1:
                        self.issue('INVALID_CONSOLIDATION_CHILD', 'Consolidation children require unique declared member references.', [member])
                    seen.add(child['member']); graph[node['name']].append(child['member'])
            # Iterative DFS keeps large user hierarchies off the Python call stack.
            colours = {}
            cycle = False
            for start in graph:
                if colours.get(start):
                    continue
                stack = [(start, False)]
                while stack:
                    name, exiting = stack.pop()
                    if exiting:
                        colours[name] = 2
                    elif colours.get(name) == 1:
                        cycle = True
                    elif not colours.get(name):
                        colours[name] = 1; stack.append((name, True))
                        stack.extend((child, False) for child in reversed(graph.get(name, [])))
            if cycle:
                self.issue('CONSOLIDATION_CYCLE', 'Consolidation links contain a cycle; no aggregation is evaluated.', [hierarchy])
        for subset in self.entries['subset']:
            value = subset['value']; scope = {'dimension': value['dimension'], 'hierarchy': value['hierarchy']}
            if len(self.hierarchy(**scope)) != 1:
                self.issue('INVALID_SUBSET_HIERARCHY', 'Subset hierarchy is missing or ambiguous.', [subset])
            if value['members'] is not None:
                for name in value['members']:
                    if len(self.find('member', name, **scope)) != 1:
                        self.issue('INVALID_SUBSET_MEMBER', 'Static subset references a missing/ambiguous member: ' + name, [subset])
        for process in self.entries['process']:
            value = process['value']; name = value['name']
            datasource = self.find('datasource', value['datasource']) if value['datasource'] is not None else []
            if value['datasource'] is not None and len(datasource) != 1:
                self.issue('INVALID_PROCESS_DATASOURCE', 'Process datasource mapping is missing or ambiguous.', [process])
            params = self.find('parameter', process=name)
            for variable in self.find('variable', process=name):
                if any(p['value']['name'] == variable['value']['name'] for p in params):
                    self.issue('PARAMETER_VARIABLE_COLLISION', 'A process symbol has both parameter and variable declarations.', [process, variable])
                v = variable['value']
                if v['role'] == 'DATASOURCE_COLUMN':
                    columns = self.find('column', v['column'], datasource=value['datasource']) if v['column'] is not None else []
                    if len(datasource) != 1 or len(columns) != 1 or columns[0]['value']['data_type'] != v['data_type']:
                        self.issue('INVALID_DATASOURCE_COLUMN_BINDING', 'Datasource variable requires a unique column with the declared type.', [variable, *columns])
                elif v['column'] is not None:
                    self.issue('INVALID_VARIABLE_COLUMN_ROLE', 'A non-datasource variable cannot claim a datasource column binding.', [variable])
        for datasource in self.entries['datasource']:
            value = datasource['value']
            if value['kind'] == 'NONE' and (value['columns'] or any(value.get(k) is not None for k in ('location_reference','client_location_reference','connection_reference','sql_dialect','cube','view'))):
                self.issue('CONFLICTING_NONE_DATASOURCE', 'NONE datasource cannot also declare source columns or external mappings.', [datasource])
            if value['kind'] == 'CUBE_VIEW' and (value['cube'] is None or value['view'] is None or len(self.find('cube', value['cube'])) != 1):
                self.issue('INVALID_CUBE_VIEW_DATASOURCE', 'Cube/view datasource requires a declared cube and view reference.', [datasource])
