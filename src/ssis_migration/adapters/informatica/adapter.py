"""PowerCenter XML declarations and scoped instance/column/workflow links.

No execution semantics are inferred from names, transformation type labels or
repository version numbers. Shortcuts and unknown constructs remain blockers.
"""
from ..xml_export import XMLExportAdapter, local


class PowerCenterAdapter(XMLExportAdapter):
    platform = 'informatica'
    export_format = 'powercenter_xml'
    roots = frozenset({'POWERMART'})
    supported_export_formats = frozenset({'powercenter_xml'})

    def export_version(self, root):
        return root.get('REPOSITORY_VERSION')

    def classify(self, element):
        tag = local(element.tag).upper()
        kinds = {'MAPPING': ('DATA_FLOW', 'MAPPING'), 'WORKFLOW': ('TASK', 'WORKFLOW'),
                 'WORKLET': ('TASK', 'WORKLET'), 'SESSION': ('TASK', 'SESSION'),
                 'TASK': ('TASK', 'TASK'), 'TASKINSTANCE': ('TASK', 'TASK_INSTANCE'),
                 'SOURCE': ('SOURCE', 'SOURCE_DEFINITION'), 'TARGET': ('TARGET', 'TARGET_DEFINITION'),
                 'TRANSFORMATION': ('TRANSFORMATION', 'TRANSFORMATION'),
                 'INSTANCE': ('REFERENCE', 'INSTANCE'), 'CONNECTOR': ('REFERENCE', 'COLUMN_CONNECTOR'),
                 'WORKFLOWLINK': ('REFERENCE', 'WORKFLOW_LINK'),
                 'MAPPINGVARIABLE': ('PARAMETER', 'MAPPING_VARIABLE'), 'WORKFLOWVARIABLE': ('PARAMETER', 'WORKFLOW_VARIABLE'),
                 'CONNECTIONREFERENCE': ('CONNECTION', 'CONNECTION_REFERENCE'),
                 'TRANSFORMFIELD': ('EXPRESSION', 'PORT'), 'SOURCEFIELD': ('EXPRESSION', 'COLUMN'),
                 'TARGETFIELD': ('EXPRESSION', 'COLUMN')}
        containers = {'POWERMART', 'REPOSITORY', 'FOLDER', 'TABLEATTRIBUTE', 'ATTRIBUTE', 'ASSOCIATED_SOURCE_INSTANCE',
                      'SESSIONEXTENSION', 'CONNECTION', 'CONFIG', 'CONFIGREFERENCE', 'METADATAEXTENSION', 'FIELDATTRIBUTE'}
        kind, operation = kinds.get(tag, ('REQUIREMENT', tag))
        attrs = {}
        if tag == 'TRANSFORMATION': attrs['transformation_type'] = element.get('TYPE')
        if tag.endswith('FIELD'):
            attrs.update(column=element.get('NAME'), data_type=element.get('DATATYPE'), expression=element.get('EXPRESSION'))
        if tag == 'WORKFLOWLINK': attrs['condition'] = element.get('CONDITION')
        return kind, operation, attrs, tag in kinds or tag in containers

    def relationships(self, graph):
        def control_scope(element):
            scope = graph.scope(element, 'WORKLET')
            return scope if scope is not None else graph.scope(element, 'WORKFLOW')

        def unique(candidates, name):
            if not isinstance(name, str) or not name: return None
            matches = [e for e in candidates if e.get('NAME') == name]
            return graph.ids[matches[0]] if len(matches) == 1 else None

        for element in graph.elements:
            tag = local(element.tag).upper(); path = graph.paths[element]
            if tag == 'CONNECTOR':
                scope = graph.scope(element, 'MAPPING')
                instances = [e for e in graph.elements if local(e.tag).upper() == 'INSTANCE' and graph.scope(e, 'MAPPING') is scope] if scope is not None else []
                graph.edge('DATA_FLOW', unique(instances, element.get('FROMINSTANCE')), unique(instances, element.get('TOINSTANCE')), path,
                           {'source_column': element.get('FROMFIELD'), 'target_column': element.get('TOFIELD'), 'resolution_scope': 'INSTANCE_ONLY'})
                # Field declarations are distinct obligations; instance links
                # alone do not prove that a referenced port exists.
                port_ids = []
                for side in ('FROM', 'TO'):
                    refs = [e for e in instances if e.get('NAME') == element.get(side+'INSTANCE')]
                    definitions = []
                    if len(refs) == 1:
                        ref = refs[0]; folder = graph.scope(scope, 'FOLDER')
                        definitions = [e for e in graph.elements if e.get('NAME') == ref.get('TRANSFORMATION_NAME')
                                       and graph.scope(e, 'FOLDER') is folder
                                       and local(e.tag).upper() in {'SOURCE','TARGET','TRANSFORMATION'}
                                       and (local(e.tag).upper() != 'TRANSFORMATION' or graph.scope(e, 'MAPPING') is scope)]
                    fields = [c for d in definitions for c in d if local(c.tag).upper().endswith('FIELD') and c.get('NAME') == element.get(side+'FIELD')]
                    if len(definitions) != 1 or len(fields) != 1:
                        graph.diagnostic('UNRESOLVED_PORT', side+' connector port is missing or ambiguous: '+str(element.get(side+'FIELD')), path, 'ERROR')
                        port_ids.append(None)
                    else: port_ids.append(graph.ids[fields[0]])
                graph.edge('COLUMN_LINEAGE', *port_ids, path, {'scope': 'DECLARED_CONNECTOR_ONLY'})
            elif tag == 'INSTANCE':
                scope = graph.scope(element, 'MAPPING'); folder = graph.scope(element, 'FOLDER')
                definitions = [e for e in graph.elements if e.get('NAME') == element.get('TRANSFORMATION_NAME')
                               and graph.scope(e, 'FOLDER') is folder
                               and (local(e.tag).upper() in {'SOURCE','TARGET'}
                                    or (local(e.tag).upper() == 'TRANSFORMATION' and graph.scope(e, 'MAPPING') is scope))]
                target = graph.ids[definitions[0]] if len(definitions) == 1 else None
                graph.edge('REQUIRES', graph.ids[element], target, path, {'definition': element.get('TRANSFORMATION_NAME')})
            elif tag == 'WORKFLOWLINK':
                scope = control_scope(element)
                tasks = [e for e in graph.elements if local(e.tag).upper() == 'TASKINSTANCE' and
                         control_scope(e) is scope] if scope is not None else []
                graph.edge('CONTROL_FLOW', unique(tasks, element.get('FROMTASK')), unique(tasks, element.get('TOTASK')), path,
                           {'condition': element.get('CONDITION'), 'condition_semantics': 'NOT_VERIFIED'})
            elif tag == 'SESSION':
                folder = graph.scope(element, 'FOLDER')
                definitions = [e for e in graph.elements if local(e.tag).upper() == 'MAPPING' and graph.scope(e, 'FOLDER') is folder]
                graph.edge('REQUIRES', graph.ids[element], unique(definitions, element.get('MAPPINGNAME')), path,
                           {'definition': element.get('MAPPINGNAME'), 'role': 'SESSION_MAPPING'})
            elif tag == 'TASKINSTANCE' and element.get('TASKTYPE', '').upper() not in {'START', 'END'}:
                folder = graph.scope(element, 'FOLDER')
                definitions = [e for e in graph.elements if local(e.tag).upper() in {'SESSION','TASK','WORKLET'} and graph.scope(e, 'FOLDER') is folder]
                graph.edge('REQUIRES', graph.ids[element], unique(definitions, element.get('TASKNAME')), path,
                           {'definition': element.get('TASKNAME'), 'role': 'WORKFLOW_TASK'})
        if not any(local(e.tag).upper() in {'MAPPING','WORKFLOW','WORKLET'} for e in graph.elements):
            graph.diagnostic('MISSING_EXECUTABLE_DEFINITION', 'No mapping, workflow or worklet definition was supplied.', graph.paths[graph.elements[0]], 'ERROR')
