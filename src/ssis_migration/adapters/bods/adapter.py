"""Bounded Data Services DI XML structure; ATL is explicitly unsupported.

Only explicit sourceId/targetId relationships are resolved by this profile.
Undocumented link encodings are retained as blockers, never guessed by name.
"""
from ..xml_export import XMLExportAdapter, local


class BODSAdapter(XMLExportAdapter):
    platform = 'bods'
    export_format = 'bods_xml'
    roots = frozenset({'DATASERVICES', 'DIEXPORT', 'DIJOB'})
    supported_export_formats = frozenset({'bods_xml'})

    def classify(self, element):
        tag = local(element.tag).upper()
        known = {'DIJOB': ('TASK','JOB'), 'DIWORKFLOW': ('TASK','WORKFLOW'),
                 'DIDATAFLOW': ('DATA_FLOW','DATA_FLOW'), 'DITRANSFORM': ('TRANSFORMATION','TRANSFORMATION'),
                 'DIQUERY': ('TRANSFORMATION','QUERY'), 'DISCRIPT': ('EXPRESSION','SCRIPT'),
                 'DIPARAMETER': ('PARAMETER','PARAMETER'), 'DIVARIABLE': ('PARAMETER','VARIABLE'),
                 'DIDATASTORE': ('CONNECTION','DATASTORE'), 'DICONNECTION': ('CONNECTION','CONNECTION'),
                 'DITABLE': ('REFERENCE','TABLE'), 'DISOURCE': ('SOURCE','SOURCE'), 'DITARGET': ('TARGET','TARGET'),
                 'DICOLUMN': ('EXPRESSION','COLUMN'), 'DILINK': ('REFERENCE','LINK'),
                 'DIEXPRESSION': ('EXPRESSION','EXPRESSION')}
        containers = {'DATASERVICES','DIEXPORT','DIATTRIBUTE','DIINPUT','DIOUTPUT','DIPORT','DIPROPERTY'}
        kind, operation = known.get(tag, ('REQUIREMENT',tag))
        attributes = {}
        if tag in {'DIQUERY','DIEXPRESSION','DISCRIPT'}:
            attributes.update(language=element.get('language'), expression=element.text or '',
                              semantic_interpretation='NOT_VERIFIED')
        if tag == 'DICOLUMN': attributes.update(column=element.get('name'), data_type=element.get('datatype'))
        if tag == 'DITRANSFORM': attributes['transformation_type'] = element.get('type')
        return kind, operation, attributes, tag in known or tag in containers

    def relationships(self, graph):
        identities = {}
        for element in graph.elements:
            identity = element.get('id')
            if identity: identities.setdefault(identity, []).append(element)
        for identity, records in identities.items():
            if len(records) > 1:
                graph.diagnostic('DUPLICATE_OBJECT_ID', 'BODS object ID is ambiguous: '+identity, graph.paths[records[0]], 'ERROR')

        def endpoint(identity):
            matches = identities.get(identity, [])
            return graph.ids[matches[0]] if len(matches) == 1 else None

        for element in graph.elements:
            path = graph.paths[element]; tag = local(element.tag).upper()
            if tag == 'DILINK':
                if not element.get('sourceId') or not element.get('targetId'):
                    graph.diagnostic('UNSUPPORTED_LINK_ENCODING', 'This structural profile requires explicit sourceId and targetId; other link formats remain unsupported.', path, 'ERROR')
                # A link is not presumed to be execution control or column lineage.
                graph.edge('DECLARED_RELATION', endpoint(element.get('sourceId')), endpoint(element.get('targetId')), path,
                           {'source_port': element.get('sourcePort'), 'target_port': element.get('targetPort'),
                            'declared_type': element.get('type'), 'execution_semantics': 'NOT_VERIFIED'})
            for attribute, kind in (('datastoreId','USES_CONNECTION'),('refId','REQUIRES')):
                if attribute in element.attrib:
                    graph.edge(kind, graph.ids[element], endpoint(element.get(attribute)), path, {'attribute': attribute, 'reference': element.get(attribute)})
        if not any(local(e.tag).upper() in {'DIJOB','DIWORKFLOW','DIDATAFLOW'} for e in graph.elements):
            graph.diagnostic('MISSING_EXECUTABLE_DEFINITION', 'No DIJob, DIWorkflow or DIDataflow definition was supplied.', graph.paths[graph.elements[0]], 'ERROR')
