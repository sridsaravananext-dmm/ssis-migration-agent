"""Read actual executable containment from the already-loaded DTSX tree."""
from __future__ import annotations

from typing import Any

from ssis_migration.parser.namespace import local_name


def executable_hierarchy(root: Any) -> list[dict[str, Any]]:
    records = []

    def reference(element):
        return next((v for k, v in element.attrib.items() if local_name(k) == 'refId'), None)

    def visit(element, parent):
        current = parent
        if local_name(element.tag) == 'Executable':
            if parent is not None:
                records.append({'parent_ref': reference(parent), 'child_ref': reference(element),
                                'parent_is_package': parent is root})
            current = element
        for child in element:
            visit(child, current)

    visit(root, None)
    return records
