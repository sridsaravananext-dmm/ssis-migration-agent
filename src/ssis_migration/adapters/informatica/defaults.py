"""PowerCenter port-default declarations; syntax recognition never executes them."""
from __future__ import annotations

import re

from .expressions import analyze_expression


def analyze_port_default(attributes, ports):
    present = 'DEFAULTVALUE' in attributes
    text = attributes.get('DEFAULTVALUE')
    result = {'declaration_state': 'ABSENT' if not present else 'EMPTY' if text == '' else 'EXPRESSION',
              'raw_text': text, 'expression': None, 'effective_default_value': None,
              'runtime_outcome': None, 'semantic_status': 'NOT_VERIFIED', 'unresolved_items': []}
    if not present or text == '':
        result['unresolved_items'].append('Absent or empty port-default metadata does not establish a NULL, empty string, zero or effective runtime fallback.')
        return result
    # Bounded native syntax observed in the original sequence ports. Do not
    # guess escaping conventions, dynamic messages or the resulting failure scope.
    literal = re.fullmatch(r"\s*ERROR\s*\(\s*'([^'\\\x00-\x1f]*)'\s*\)\s*", text, re.I) if isinstance(text, str) and len(text) <= 4096 else None
    if literal:
        result['expression'] = {
            'text': text, 'status': 'PARSED',
            'ast': {'kind': 'ERROR_EXPRESSION', 'message': {'kind': 'STRING_LITERAL', 'value': literal[1]}},
            'semantic_status': 'NOT_VERIFIED', 'unresolved_items': []}
        result['unresolved_items'].append('A declared ERROR expression does not prove an observed error, a rejected row or a whole-job abort; invocation and error/recovery policy remain unverified.')
    else:
        result['expression'] = analyze_expression(text, ports)
        result['unresolved_items'].extend(result['expression']['unresolved_items'])
        result['unresolved_items'].append('Port-default invocation, type coercion and error/null behavior require source-runtime verification.')
    return result
