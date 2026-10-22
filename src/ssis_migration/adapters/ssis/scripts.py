"""Extract explicitly named C# source exports from native SSIS script objects.

Never decompress binaries, follow paths or search external files by name.
Native reference ambiguity is retained; descendant tasks/components cannot
supply the containing object's source accidentally.
"""
from __future__ import annotations

from ssis_migration.languages.csharp import assess_csharp
from ssis_migration.parser.namespace import local_name

_TASK_TYPES = {'microsoft.scripttask', 'microsoft.sqlserver.dts.tasks.scripttask.scripttask'}
_COMPONENT_TYPES = {'microsoft.scriptcomponent', 'microsoft.sqlserver.txscript'}


def extract_script_assessments(root, legacy_ir, file_id):
    paths = {}
    def index(element, path):
        paths[id(element)] = path
        for i, child in enumerate(element):
            index(child, path.rstrip('/') + '/' + str(i))
    index(root, 'xml:/')

    def attrs(element):
        return {local_name(key).lower(): value for key, value in element.attrib.items()}

    def source_files(element):
        result = []
        def visit(current):
            if current is not element and local_name(current.tag).lower() in {'executable', 'component'}:
                return
            values = attrs(current)
            # Only explicit File/ProjectItem .cs entries with text-only content are
            # supported. Alternate encodings/layouts remain missing evidence.
            name = values.get('name', '')
            if (local_name(current.tag).lower() in {'file', 'projectitem'}
                    and name.lower().endswith('.cs') and not len(current)
                    and values.get('encoding', 'utf8').lower() in {'utf8', 'utf-8'}):
                result.append({'name': name, 'assessment': assess_csharp(
                    current.text or '', file_id=file_id, locator=paths[id(current)])})
            for child in current:
                visit(child)
        visit(element)
        return result

    elements = list(root.iter())
    result = {}
    def assess(record, pointer, tag, native_type):
        candidates = [e for e in elements if local_name(e.tag).lower() == tag
                      and attrs(e).get('refid') == record.get('ref_id')]
        # A missing refId must never match every unnamed XML element.
        if not record.get('ref_id'):
            candidates = []
        exports = []
        if len(candidates) == 1:
            exports = source_files(candidates[0])
        result[pointer] = {
            'native_type': native_type, 'source_status': 'AVAILABLE' if exports else 'MISSING_OR_UNSUPPORTED',
            'identity_status': 'RESOLVED' if len(candidates) == 1 else 'AMBIGUOUS_OR_MISSING',
            'classification': 'REVIEW_REQUIRED', 'semantic_status': 'NOT_VERIFIED',
            'files': exports,
            'unresolved_items': [
                'Full script project, assembly references and SSIS bindings require review.',
                'Binary-only, compressed and alternate source layouts are not decoded.',
                'No automatic translation, compilation or execution is authorized by this assessment.',
            ],
        }
        if not exports:
            result[pointer]['unresolved_items'].append('Supply the original C# source/project export; no source was invented.')
        # Aggregate only when every exported source has been assessed. Additional
        # missing project files/references remain unresolved even in this case.
        classes = {item['assessment']['classification'] for item in exports}
        if result[pointer]['identity_status'] == 'RESOLVED' and classes:
            if 'REQUIRES_REDESIGN' in classes:
                result[pointer]['classification'] = 'REQUIRES_REDESIGN'
            elif classes == {'PARTIALLY_TRANSLATABLE'}:
                result[pointer]['classification'] = 'PARTIALLY_TRANSLATABLE'

    for i, record in enumerate(legacy_ir.get('executables', [])):
        native = record.get('executable_type') or record.get('creation_name') or ''
        if native.split(',')[0].strip().lower() in _TASK_TYPES:
            assess(record, f'/executables/{i}', 'executable', native)
    for i, flow in enumerate(legacy_ir.get('data_flows', [])):
        for j, record in enumerate(flow.get('components', [])):
            native = record.get('component_class_id') or ''
            if native.lower() in _COMPONENT_TYPES:
                assess(record, f'/data_flows/{i}/components/{j}', 'component', native)
    return result
