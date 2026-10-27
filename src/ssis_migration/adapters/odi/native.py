"""Versioned ODI 12.2.1 Smart Export declarations and explicit mapping links.

Each leaf Object/Field is a typed evidence record on its object. Other elements
remain nodes. Physical/KM behavior, null text and source expression semantics are
not interpreted or converted into executable code.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ssis_migration.intake import describe_file
from ssis_migration.intake.inventory import has_unsafe_xml_declarations
from ssis_migration.schemas import SchemaValidationError, validate_document, validate_evidence_references
from ..base import SourceAdapterResult, SourceEvidenceError, UnsupportedSourceError
from ..xml_export import ExportGraph, local
from .preflight import _decode_inventory_xml, _read_source

PROFILE = 'odi_12c_mapping_declarations_v1'
MAX_ELEMENTS = 100_000
MAX_NODES = 6000
MAX_EDGES = 20_000
MAX_FIELDS_PER_OBJECT = 256
MAX_MAPPINGS = 256
MAX_DEPTH = 128
PRODUCT_VERSION = '12.2.1'
REPOSITORY_VERSION = '05.02.02.07'

# Structural declarations only; no entry is a target-generation capability.
CLASSES = {
    'SnpMapping': ('DATA_FLOW', 'MAPPING', 'IMapping', 'Name'),
    'SnpMapComp': ('TRANSFORMATION', 'MAPPING_COMPONENT', 'IMapComp', 'Name'),
    'SnpMapCp': ('REFERENCE', 'CONNECTION_POINT', 'IMapCp', 'Name'),
    'SnpMapConn': ('REFERENCE', 'MAPPING_CONNECTION', 'IMapConn', 'Name'),
    'SnpMapAttr': ('EXPRESSION', 'MAPPING_ATTRIBUTE', 'IMapAttr', 'Name'),
    'SnpMapExpr': ('EXPRESSION', 'MAPPING_EXPRESSION', 'IMapExpr', None),
    'SnpMapExprRef': ('REFERENCE', 'EXPRESSION_REFERENCE', 'IMapExprRef', 'RefText'),
    'SnpMapRef': ('REFERENCE', 'EXTERNAL_OBJECT_REFERENCE', 'IMapRef', 'QualifiedName'),
    'SnpMapProp': ('REQUIREMENT', 'MAPPING_PROPERTY', 'IMapProp', 'Name'),
    'SnpProject': ('REQUIREMENT', 'PROJECT', 'IProject', 'ProjectName'),
    'SnpFolder': ('REQUIREMENT', 'FOLDER', 'IFolder', 'FolderName'),
    'SnpTable': ('REFERENCE', 'DATASTORE_DEFINITION', 'ITable', 'TableName'),
    'SnpCol': ('EXPRESSION', 'COLUMN_DEFINITION', 'ICol', 'ColName'),
    'SnpModel': ('REQUIREMENT', 'MODEL', 'IMod', 'ModName'),
    'SnpConnect': ('CONNECTION', 'CONNECTION_DECLARATION', 'IConnect', 'ConName'),
    'SnpPackage': ('TASK', 'PACKAGE_DECLARATION', 'IPackage', 'PackName'),
    'SnpStep': ('TASK', 'PACKAGE_STEP_DECLARATION', 'IStep', 'StepName'),
    'SnpTrt': ('REQUIREMENT', 'PROCEDURE_OR_KM_DECLARATION', 'ITrt', 'TrtName'),
    'SnpDeploySpec': ('REQUIREMENT', 'PHYSICAL_DESIGN', 'IDeploySpec', 'Name'),
    'SnpPhyNode': ('REQUIREMENT', 'PHYSICAL_NODE', 'IPhyNode', 'Name'),
    'SnpExecUnit': ('REQUIREMENT', 'EXECUTION_UNIT', 'IExecUnit', 'Name'),
    'SnpExecUnitGrp': ('REQUIREMENT', 'EXECUTION_GROUP', 'IExecUnitGrp', 'Name'),
    'SnpLschema': ('CONNECTION', 'LOGICAL_SCHEMA', 'ILschema', 'LschemaName'),
    'SnpContext': ('REQUIREMENT', 'CONTEXT', 'IContext', 'ContextName'),
    'SnpKey': ('REQUIREMENT', 'KEY_DECLARATION', 'IKey', 'KeyName'),
}

# Each tuple is (field, target class, required numeric reference). Optional
# nonnumeric declarations remain unbound field evidence; "null" is not a value
# we invent or substitute for SQL NULL or an object definition.
REFERENCES = {
    'SnpMapping': [('IFolder', 'SnpFolder', True)],
    'SnpMapComp': [('IOwnerMapping', 'SnpMapping', True), ('IMapRef', 'SnpMapRef', False)],
    'SnpMapCp': [('IOwnerMapComp', 'SnpMapComp', True)],
    'SnpMapConn': [('IOwnerMapping', 'SnpMapping', True), ('IStartMapCp', 'SnpMapCp', True), ('IEndMapCp', 'SnpMapCp', True)],
    'SnpMapAttr': [('IOwnerMapCp', 'SnpMapCp', True), ('IMapRef', 'SnpMapRef', False), ('IDataMapRef', 'SnpMapRef', False)],
    'SnpMapExpr': [('IOwnerMapAttr', 'SnpMapAttr', False), ('IOwnerMapProp', 'SnpMapProp', False), ('IMapCp', 'SnpMapCp', False)],
    'SnpMapExprRef': [('IOwnerMapExpr', 'SnpMapExpr', True), ('IRefMapAttr', 'SnpMapAttr', False), ('IRefMapComp', 'SnpMapComp', False), ('IRefMapRef', 'SnpMapRef', False), ('IScopingMapCp', 'SnpMapCp', False)],
    'SnpMapRef': [('IOwnerMapping', 'SnpMapping', True)],
    'SnpMapProp': [('IOwnerMapping', 'SnpMapping', True), ('IMapPropParent', 'SnpMapProp', False), ('IMapComp', 'SnpMapComp', False), ('IMapCp', 'SnpMapCp', False), ('IMapAttr', 'SnpMapAttr', False), ('IDeploySpec', 'SnpDeploySpec', False), ('IExecUnit', 'SnpExecUnit', False), ('IPhyNode', 'SnpPhyNode', False)],
    'SnpDeploySpec': [('IOwnerMapping', 'SnpMapping', True)],
    'SnpPhyNode': [('IOwnerDs', 'SnpDeploySpec', True), ('IMapComp', 'SnpMapComp', False), ('IMapCp', 'SnpMapCp', False), ('IExecUnit', 'SnpExecUnit', False), ('IParPhyNode', 'SnpPhyNode', False), ('ICheckKm', 'SnpMapRef', False), ('ISrcCompKm', 'SnpMapRef', False), ('ITgtCompKm', 'SnpMapRef', False)],
    'SnpExecUnit': [('IOwnerDs', 'SnpDeploySpec', True), ('IEuKmRef', 'SnpMapRef', False), ('ILschemaRef', 'SnpMapRef', False)],
    'SnpExecUnitGrp': [('IOwnerDs', 'SnpDeploySpec', False)],
    'SnpCol': [('ITable', 'SnpTable', True)],
    'SnpTable': [('IMod', 'SnpModel', True)],
}
EXTERNAL_CLASSES = {'IDataStore': 'SnpTable', 'IColumn': 'SnpCol', 'IKnowledgeModule': 'SnpTrt',
                    'ILogicalSchema': 'SnpLschema', 'IContext': 'SnpContext', 'IKey': 'SnpKey'}


def matches_preflight(report):
    admins = report['version_declarations']['admin']
    if len(admins) != 1: return False
    attrs = admins[0]['declarations']
    return (attrs.get('OdiVersion') == PRODUCT_VERSION and attrs.get('RepositoryVersion') == REPOSITORY_VERSION
            and attrs.get('IsSmartExportFile') == 'true'
            and all(v == REPOSITORY_VERSION for v in report['version_declarations']['root'].values()))


def field(node, name):
    """Exactly one plain field; duplicates/nested declarations never pick a winner."""
    found = [f for f in node['attributes'].get('declared_fields', []) if f['name'] == name]
    return found[0] if len(found) == 1 and not found[0]['nested_child_count'] else None


def value(node, name):
    found = field(node, name)
    return found['value'] if found else None


def numeric(text):
    return isinstance(text, str) and re.fullmatch(r'[0-9]+', text) is not None


class MappingGraph(ExportGraph):
    def edge(self, *args, **kwargs):
        if len(self.edges) >= MAX_EDGES:
            raise UnsupportedSourceError('ODI normalized edge limit exceeded.')
        return super().edge(*args, **kwargs)


def build_graph(data, record):
    if len(data) > 16 * 1024 * 1024 or has_unsafe_xml_declarations(data):
        raise UnsupportedSourceError('ODI export byte limit or unsafe XML declarations.')
    text, encoding = _decode_inventory_xml(data)
    try: root = ET.fromstring(data)
    except ET.ParseError as exc: raise UnsupportedSourceError('ODI normalization requires well-formed XML.') from exc
    admins = [e for e in root if local(e.tag).upper() == 'ADMIN']
    declarations = {'version_declarations': {'root': {k: root.attrib[k] for k in ('version', 'VERSION') if k in root.attrib},
                                           'admin': [{'declarations': dict(e.attrib)} for e in admins]}}
    if local(root.tag).upper() != 'SUNOPSISEXPORT' or not matches_preflight(declarations):
        raise UnsupportedSourceError('ODI mapping profile requires one Smart Export Admin declaring ODI 12.2.1 and repository 05.02.02.07 without conflicting versions.')
    graph = MappingGraph('odi', 'odi_xml', record, text, REPOSITORY_VERSION, encoding)
    objects = []; unsupported = defaultdict(list); element_count = 0; folded = 0

    def visit(element, path, parent_id=None, depth=1):
        nonlocal element_count, folded
        element_count += 1
        if element_count > MAX_ELEMENTS or depth > MAX_DEPTH:
            raise UnsupportedSourceError('ODI normalized element/depth limit exceeded.')
        tag = local(element.tag).upper()
        cls = element.get('class', '').removeprefix('com.sunopsis.dwg.dbobj.') if tag == 'OBJECT' else None
        profile = CLASSES.get(cls)
        supported = bool(profile) if tag == 'OBJECT' else tag in {'SUNOPSISEXPORT', 'ADMIN', 'ENCRYPTION', 'SMARTEXPORTLIST', 'INCLUDE'}
        attrs = {'export_tag': local(element.tag), 'export_attributes': dict(element.attrib),
                 'export_text': element.text or '', 'export_tail': element.tail or '', 'structure_supported': supported}
        if tag == 'OBJECT':
            attrs.update(object_class=cls, declared_key_field=profile[2] if profile else None, declared_fields=[])
            for child in element:
                if local(child.tag).upper() == 'FIELD':
                    attrs['declared_fields'].append({'name': child.get('name'), 'declared_type': child.get('type'),
                        'value': child.text or '', 'attributes': dict(child.attrib), 'tail': child.tail or '',
                        'nested_child_count': len(child), 'evidence': []})
            if len(attrs['declared_fields']) > MAX_FIELDS_PER_OBJECT:
                raise UnsupportedSourceError('ODI normalized per-object field limit exceeded.')
        if len(graph.nodes) >= MAX_NODES:
            raise UnsupportedSourceError('ODI normalized node limit exceeded.')
        kind, operation = profile[:2] if profile else ('REQUIREMENT', 'UNSUPPORTED_OBJECT' if tag == 'OBJECT' else tag)
        if parent_id is None:
            kind = 'TASK'; attrs.update(analysis_unit=True, analysis_status='STRUCTURED')
        identity = graph.node(path, kind, operation, attributes=attrs)
        node = graph.nodes[-1]
        if profile and profile[3]: node['name'] = value(node, profile[3])
        if parent_id: graph.edge('CONTAINS', parent_id, identity, path)
        if tag == 'OBJECT': objects.append(node)
        if not supported: unsupported[(tag, cls)].append(node)
        counts = Counter(); field_index = 0
        for child in element:
            child_tag = local(child.tag); counts[child_tag] += 1
            child_path = path + '/' + child_tag + '[' + str(counts[child_tag]) + ']'
            if tag == 'OBJECT' and child_tag.upper() == 'FIELD':
                node['attributes']['declared_fields'][field_index]['evidence'] = graph.evidence(child_path, 'odi_declared_field_exact_text')
                field_index += 1
                if not len(child):
                    element_count += 1; folded += 1
                    if element_count > MAX_ELEMENTS or depth + 1 > MAX_DEPTH:
                        raise UnsupportedSourceError('ODI normalized element/depth limit exceeded.')
                    continue
            visit(child, child_path, identity, depth+1)

    visit(root, '/'+local(root.tag)+'[1]')
    if sum(n['operation'] == 'MAPPING' for n in graph.nodes) > MAX_MAPPINGS:
        raise UnsupportedSourceError('ODI normalized mapping limit exceeded.')
    for (tag, cls), nodes in unsupported.items():
        graph.diagnostic('UNSUPPORTED_ODI_OBJECT_CLASS' if cls is not None else 'UNSUPPORTED_ODI_ELEMENT',
                         f'{cls or tag!r}: {len(nodes)} declarations are retained without implemented behavior semantics.', nodes[0]['source_ref'], 'ERROR')
    index = defaultdict(list)
    for node in objects:
        fields = node['attributes']['declared_fields']; names = Counter(f['name'] for f in fields)
        for name, count in names.items():
            if count > 1: graph.diagnostic('DUPLICATE_OBJECT_FIELD', 'Ambiguous ODI field: '+str(name), node['source_ref'], 'ERROR')
        key = node['attributes']['declared_key_field']
        if key:
            declared = value(node, key)
            if numeric(declared): index[(node['attributes']['object_class'], declared)].append(node)
            else: graph.diagnostic('MISSING_OBJECT_KEY', 'ODI object lacks a unique declared numeric key: '+key, node['source_ref'], 'ERROR')
    for (cls, key), nodes in index.items():
        if len(nodes) > 1: graph.diagnostic('DUPLICATE_OBJECT_KEY', 'Ambiguous ODI class/key: '+cls+'.'+key, nodes[0]['source_ref'], 'ERROR')

    def resolve(node, name, target_class, required=False):
        declaration = field(node, name); identity = value(node, name)
        if not numeric(identity) and not required: return None
        matches = index.get((target_class, identity), []) if numeric(identity) else []
        target = matches[0] if len(matches) == 1 else None
        path = declaration['evidence'][0]['locator'][4:] if declaration else node['source_ref']
        graph.edge('REQUIRES', node['node_id'], target['node_id'] if target else None, path,
                   {'field': name, 'declared_identity': identity, 'target_class': target_class})
        return target

    links = {}
    for node in objects:
        cls = node['attributes']['object_class']
        for name, target_class, required in REFERENCES.get(cls, []):
            links[(node['node_id'], name)] = resolve(node, name, target_class, required)
        if cls == 'SnpMapRef':
            interface = value(node, 'AdapterIntfType'); target_class = EXTERNAL_CLASSES.get(interface)
            matches = index.get((target_class, value(node, 'IRefId')), []) if target_class and value(node, 'AdapterName') == 'ODI' else []
            target = matches[0] if len(matches) == 1 and value(matches[0], 'GlobalId') == value(node, 'RefGuid') and value(node, 'RefGuid') not in (None, '', 'null') else None
            links[(node['node_id'], 'IRefId')] = target
            graph.edge('REQUIRES', node['node_id'], target['node_id'] if target else None, node['source_ref'],
                       {'field': 'IRefId', 'declared_identity': value(node, 'IRefId'), 'target_class': target_class,
                        'interface': interface, 'declared_guid': value(node, 'RefGuid'), 'rule': 'class_key_and_exact_guid'})

    def owner(node, seen=None):
        if node is None: return None
        seen = set() if seen is None else seen
        if node['node_id'] in seen: return None
        seen.add(node['node_id']); cls = node['attributes']['object_class']
        if cls == 'SnpMapping': return node['node_id']
        parent_fields = {'SnpMapComp': ['IOwnerMapping'], 'SnpMapCp': ['IOwnerMapComp'],
                         'SnpMapAttr': ['IOwnerMapCp'], 'SnpMapExpr': ['IOwnerMapAttr', 'IOwnerMapProp'],
                         'SnpMapExprRef': ['IOwnerMapExpr'], 'SnpMapProp': ['IOwnerMapping'],
                         'SnpMapConn': ['IOwnerMapping'], 'SnpMapRef': ['IOwnerMapping']}
        parents = [links.get((node['node_id'], name)) for name in parent_fields.get(cls, [])]
        parents = [p for p in parents if p is not None]
        return owner(parents[0], seen) if len(parents) == 1 else None

    by_id = {n['node_id']: n for n in objects}
    for edge in graph.edges:
        if edge['kind'] != 'REQUIRES' or edge['resolution'] != 'RESOLVED' or edge['attributes']['field'] == 'IRefId':
            continue
        start_owner = owner(by_id.get(edge['source_id'])); end_owner = owner(by_id.get(edge['target_id']))
        if start_owner is not None and end_owner is not None and start_owner != end_owner:
            graph.diagnostic('CROSS_MAPPING_REFERENCE', 'Declared ODI reference crosses mapping scopes; identity resolution does not establish valid mapping behavior.', edge['evidence'][0]['locator'][4:], 'ERROR')

    for node in objects:
        cls = node['attributes']['object_class']
        if cls == 'SnpMapConn':
            start = links.get((node['node_id'], 'IStartMapCp')); end = links.get((node['node_id'], 'IEndMapCp'))
            valid = (owner(node) is not None and owner(start) == owner(end) == owner(node)
                     and start is not None and end is not None and value(start, 'Direction') == 'O' and value(end, 'Direction') == 'I')
            graph.edge('DATA_FLOW', start['node_id'] if valid else None, end['node_id'] if valid else None,
                       node['source_ref'], {'connection_node_id': node['node_id'], 'mapping_node_id': owner(node), 'rule': 'explicit_scoped_connection_points'})
        elif cls == 'SnpMapExpr':
            parents = [links.get((node['node_id'], k)) for k in ('IOwnerMapAttr', 'IOwnerMapProp')]
            parents = [p for p in parents if p is not None]
            graph.edge('HAS_EXPRESSION', parents[0]['node_id'] if len(parents) == 1 else None, node['node_id'], node['source_ref'])
        elif cls == 'SnpMapExprRef':
            source = links.get((node['node_id'], 'IRefMapAttr')); expression = links.get((node['node_id'], 'IOwnerMapExpr'))
            target = links.get((expression['node_id'], 'IOwnerMapAttr')) if expression else None
            if numeric(value(node, 'IRefMapAttr')) and target is not None:
                valid = owner(source) is not None and owner(source) == owner(target) == owner(node)
                graph.edge('COLUMN_LINEAGE', source['node_id'] if valid else None, target['node_id'] if valid else None,
                           node['source_ref'], {'expression_reference_node_id': node['node_id'],
                           'reference_key': value(node, 'RefKey'), 'validity_declaration': value(node, 'IsValid'),
                           'rule': 'declared_attribute_reference_only'})
    component_flows = defaultdict(list)
    for edge in graph.edges:
        if edge['kind'] == 'DATA_FLOW' and edge['resolution'] == 'RESOLVED':
            for direction, endpoint in [('OUT', edge['source_id']), ('IN', edge['target_id'])]:
                cp = by_id[endpoint]
                component = links.get((cp['node_id'], 'IOwnerMapComp'))
                if component: component_flows[component['node_id']].append((direction, edge))
    for node in objects:
        if node['attributes']['object_class'] != 'SnpMapComp': continue
        flows = component_flows[node['node_id']]; directions = {direction for direction, _ in flows}
        role = 'SOURCE' if directions == {'OUT'} else 'TARGET' if directions == {'IN'} else 'INTERMEDIATE' if directions else 'UNCONNECTED'
        node['attributes']['declared_data_flow_role'] = role
        if value(node, 'TypeName') in ('DATASTORE', 'FILE') and role in ('SOURCE', 'TARGET'):
            node['kind'] = role
        for _, edge in flows:
            for evidence in edge['evidence']:
                if evidence not in node['evidence']: node['evidence'].append(copy.deepcopy(evidence))
    graph.diagnostic('ODI_KNOWLEDGE_MODULE_SEMANTICS_UNVERIFIED',
                     'Physical designs, Knowledge Module commands/options, transactions, null/expression and write/error behavior remain unverified.', graph.nodes[0]['source_ref'])
    return graph.finish(PROFILE, analysis_metadata={
        'product_version': PRODUCT_VERSION, 'repository_version': REPOSITORY_VERSION,
        'element_count': element_count, 'folded_field_count': folded, 'node_count': len(graph.nodes),
        'mapping_count': sum(n['operation'] == 'MAPPING' for n in graph.nodes),
        'limits': {'max_elements': MAX_ELEMENTS, 'max_nodes': MAX_NODES, 'max_edges': MAX_EDGES,
                   'max_fields_per_object': MAX_FIELDS_PER_OBJECT, 'max_mappings': MAX_MAPPINGS, 'max_depth': MAX_DEPTH},
        'execution_order': 'NOT_ESTABLISHED', 'field_values': 'EXACT_DECLARED_TEXT_NOT_RUNTIME_VALUES'})


def analyze_export(source_path, *, file_record=None):
    path = Path(source_path).expanduser().absolute(); data = _read_source(path)
    record = describe_file(path)
    if record['sha256'] != hashlib.sha256(data).hexdigest():
        raise SourceEvidenceError('ODI source changed before normalization.')
    if file_record is not None:
        keys = ('path', 'sha256', 'size_bytes', 'platform', 'format', 'export_version', 'status')
        if not isinstance(file_record, dict) or any(file_record.get(k) != record[k] for k in keys):
            raise SourceEvidenceError('ODI XML inventory does not match its source.')
        record = copy.deepcopy(file_record)
    graph = build_graph(data, record)
    envelope = {'envelope_version': '2.0', 'source': copy.deepcopy(graph['source']),
                'evidence_files': copy.deepcopy(graph['evidence_files']), 'diagnostics': copy.deepcopy(graph['diagnostics']),
                'status': graph['status'], 'native_analysis': copy.deepcopy(graph['extensions']['source_analysis'])}
    validate_document(envelope, 'source_envelope_v2'); validate_evidence_references(envelope)
    if _read_source(path) != data: raise SourceEvidenceError('ODI source changed during normalization.')
    return SourceAdapterResult(parsed={'name': path.stem}, package_ir=None,
        analysis={'platform': 'odi', 'status': graph['status'], 'semantic_status': 'NOT_VERIFIED'},
        source_envelope=envelope, normalized_ir=graph)


def validate_graph(graph):
    if len(graph['evidence_files']) != 1:
        raise SchemaValidationError('ODI native mapping graphs require one original export.')
    record = graph['evidence_files'][0]; snapshot = graph['extensions']['source_analysis']['source_snapshot']
    if not isinstance(snapshot, dict):
        raise SchemaValidationError('ODI native source snapshot must be an object.')
    encoding = snapshot.get('encoding', 'utf-8')
    if encoding not in ('utf-8', 'windows-1252', 'iso-8859-1'):
        raise SchemaValidationError('ODI native snapshot encoding is unsupported.')
    try: data = snapshot['text'].encode(encoding)
    except (UnicodeError, TypeError, AttributeError, KeyError) as exc:
        raise SchemaValidationError('ODI native snapshot cannot reproduce original bytes.') from exc
    if (hashlib.sha256(data).hexdigest() != record['sha256'] or len(data) != record['size_bytes']
            or snapshot.get('sha256') != record['sha256']):
        raise SchemaValidationError('ODI native source snapshot identity is stale.')
    if graph != build_graph(data, record):
        raise SchemaValidationError('ODI mapping normalization does not replay from original source evidence.')
