"""ODI object/field extraction with class-scoped, declared-key references.

Knowledge modules, repository IDs and object names do not imply Oracle SQL or
execution order. Unknown object classes/field encodings remain explicit gaps.
"""
from ..xml_export import XMLExportAdapter, local


class ODIAdapter(XMLExportAdapter):
    platform = 'odi'
    export_format = 'odi_xml'
    roots = frozenset({'SUNOPSISEXPORT'})
    supported_export_formats = frozenset({'odi_xml'})

    # Each entry describes a structural declaration, not a migration capability.
    classes = {
        'SnpProject': ('REQUIREMENT','PROJECT','IProject'),
        'SnpFolder': ('REQUIREMENT','FOLDER','IFolder'),
        'SnpPop': ('DATA_FLOW','INTERFACE','IPop'),
        'SnpPackage': ('TASK','PACKAGE','IPackage'),
        'SnpStep': ('TASK','PACKAGE_STEP','IStep'),
        'SnpTrt': ('TASK','PROCEDURE','ITrt'),
        'SnpTrtStep': ('TASK','PROCEDURE_STEP','ITrtStep'),
        'SnpVar': ('PARAMETER','VARIABLE','IVar'),
        'SnpConnect': ('CONNECTION','CONNECTION','IConnect'),
        'SnpModel': ('REQUIREMENT','MODEL','IMod'),
        'SnpTable': ('REFERENCE','TABLE','ITable'),
        'SnpCol': ('EXPRESSION','COLUMN','ICol'),
        'SnpSourceTab': ('SOURCE','SOURCE_TABLE','ISourceTab'),
        'SnpPopCol': ('EXPRESSION','INTERFACE_COLUMN','IPopCol'),
        'SnpPopJoin': ('TRANSFORMATION','JOIN','IPopJoin'),
        'SnpPopFilter': ('TRANSFORMATION','FILTER','IPopFilter'),
        'SnpTxt': ('EXPRESSION','SOURCE_TEXT','ITxt'),
        'SnpTechno': ('REQUIREMENT','TECHNOLOGY','ITechno'),
    }
    references = {'IProject': 'SnpProject', 'IFolder': 'SnpFolder', 'IPop': 'SnpPop',
                  'IPackage': 'SnpPackage', 'ITrt': 'SnpTrt', 'IMod': 'SnpModel',
                  'ITable': 'SnpTable', 'ICol': 'SnpCol', 'IConnect': 'SnpConnect',
                  'ITechno': 'SnpTechno', 'ITxt': 'SnpTxt'}

    def export_version(self, root):
        direct = root.get('version') or root.get('VERSION')
        admins = [e for e in root if local(e.tag).upper() == 'ADMIN']
        return direct or (admins[0].get('RepositoryVersion') if len(admins) == 1 else None)

    @staticmethod
    def object_class(element):
        value = element.get('class','')
        return value.rsplit('.',1)[-1] if value.startswith('com.sunopsis.dwg.dbobj.') else value

    def classify(self, element):
        tag = local(element.tag).upper()
        if tag == 'OBJECT':
            source_class = self.object_class(element)
            kind, operation, key = self.classes.get(source_class, ('REQUIREMENT','UNSUPPORTED_OBJECT',None))
            fields = [{'name': e.get('name'), 'value': e.text or '', 'type': e.get('type')}
                      for e in element if local(e.tag).upper() == 'FIELD']
            return kind, operation, {'object_class':element.get('class'), 'declared_key_field':key, 'fields':fields}, source_class in self.classes
        if tag == 'FIELD':
            return 'EXPRESSION', 'DECLARED_FIELD', {'field_name':element.get('name'), 'value':element.text or '',
                                                   'declared_type':element.get('type')}, True
        return 'REQUIREMENT', tag, {}, tag in {'SUNOPSISEXPORT','ADMIN','EXPORTSUMMARY','NULL'}

    def relationships(self, graph):
        objects = [e for e in graph.elements if local(e.tag).upper() == 'OBJECT']
        index = {}
        for obj in objects:
            cls = self.object_class(obj); profile = self.classes.get(cls)
            fields = {}
            for field in obj:
                if local(field.tag).upper() != 'FIELD': continue
                name = field.get('name')
                if name in fields:
                    graph.diagnostic('DUPLICATE_OBJECT_FIELD', 'ODI object field is ambiguous: '+str(name), graph.paths[field], 'ERROR')
                fields.setdefault(name, []).append(field)
            if profile:
                keys = fields.get(profile[2], [])
                if len(keys) != 1 or not (keys[0].text or '').strip():
                    graph.diagnostic('MISSING_OBJECT_KEY', 'ODI object lacks its unique declared identity field: '+profile[2], graph.paths[obj], 'ERROR')
                else: index.setdefault((cls, keys[0].text.strip()), []).append(obj)
        for (cls, value), matches in index.items():
            if len(matches) > 1:
                graph.diagnostic('DUPLICATE_OBJECT_KEY', f'ODI identity {cls}.{value} is ambiguous.', graph.paths[matches[0]], 'ERROR')
        for obj in objects:
            own = self.classes.get(self.object_class(obj), (None,None,None))[2]
            for field in obj:
                name = field.get('name'); text = (field.text or '').strip()
                if local(field.tag).upper() != 'FIELD' or name not in self.references or name == own or not text: continue
                matches = index.get((self.references[name],text), [])
                target = graph.ids[matches[0]] if len(matches) == 1 else None
                graph.edge('REQUIRES', graph.ids[obj], target, graph.paths[field],
                           {'field':name, 'declared_identity':text, 'target_class':self.references[name]})
                # Only a foreign-key relationship is established. Source/target
                # execution, transaction scope and KM order remain unverified.
        if not objects:
            graph.diagnostic('MISSING_OBJECT_DEFINITIONS', 'Sunopsis export contains no object definitions.', graph.paths[graph.elements[0]], 'ERROR')
        graph.diagnostic('ODI_KNOWLEDGE_MODULE_SEMANTICS_UNVERIFIED',
                         'Knowledge modules, technology bindings, generated commands and package execution semantics require explicit source evidence and capability rules.',
                         graph.paths[graph.elements[0]])
