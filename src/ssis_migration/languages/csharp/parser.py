"""Complete-file C# grammar parsing; syntax success is not .NET compilation."""
from __future__ import annotations

import hashlib
from collections import Counter
from importlib.metadata import version

from tree_sitter import Language, Parser
import tree_sitter_c_sharp

MAX_SOURCE_BYTES = 1_048_576
MAX_NODES = 100_000


def parse_tree(source: str):
    data = source.encode('utf-8')
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError('C# source exceeds the 1 MiB parsing limit; split/review the export explicitly.')
    return Parser(Language(tree_sitter_c_sharp.language())).parse(data)


def parse_csharp(source: str, *, file_id: str, locator: str) -> dict:
    """Parse all bytes and report recovery/missing nodes, preserving evidence."""
    if not isinstance(source, str):
        raise TypeError('C# source must be decoded text.')
    if not file_id or not locator:
        raise ValueError('C# syntax analysis requires source evidence.')
    data = source.encode('utf-8')
    evidence = {'file_id': file_id, 'locator': locator, 'rule': 'csharp_grammar_v1'}
    result = {
        'syntax_version': '1.0', 'status': 'PARSED', 'semantic_status': 'NOT_VERIFIED',
        'parser': {'name': 'tree-sitter-c-sharp', 'version': version('tree-sitter-c-sharp'),
                   'runtime_version': version('tree-sitter')},
        'source_sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data),
        'offset_unit': 'UTF8_BYTES', 'evidence': evidence,
        'construct_counts': {}, 'diagnostics': [],
        'limitations': ['Grammar parsing does not resolve symbols, assemblies, overloads or compilation options.',
                        'PARSED does not establish compilation, translation or semantic equivalence.'],
    }
    if len(data) > MAX_SOURCE_BYTES:
        result['status'] = 'LIMIT_EXCEEDED'
        result['diagnostics'].append({'code': 'SOURCE_LIMIT', 'start_byte': 0, 'end_byte': len(data),
                                      'line': 1, 'message': 'Source exceeds the 1 MiB limit; no prefix was accepted.'})
    else:
        root = parse_tree(source).root_node
        stack, counts, total = [root], Counter(), 0
        while stack:
            node = stack.pop()
            total += 1
            if total > MAX_NODES:
                result['status'] = 'LIMIT_EXCEEDED'
                result['diagnostics'].append({'code': 'NODE_LIMIT', 'start_byte': 0, 'end_byte': len(data),
                                              'line': 1, 'message': 'Syntax tree limit exceeded; no partial acceptance.'})
                break
            if node.is_named:
                counts[node.type] += 1
            if node.is_error or node.is_missing:
                result['status'] = 'INVALID'
                result['diagnostics'].append({'code': 'MISSING_TOKEN' if node.is_missing else 'SYNTAX_ERROR',
                                              'start_byte': node.start_byte, 'end_byte': node.end_byte,
                                              'line': node.start_point.row + 1,
                                              'message': 'C# grammar recovery: ' + node.type})
            stack.extend(reversed(node.children))
        result['construct_counts'] = dict(sorted(counts.items()))
        if root.has_error and result['status'] == 'PARSED':
            result['status'] = 'INVALID'
            result['diagnostics'].append({'code': 'SYNTAX_ERROR', 'start_byte': 0, 'end_byte': len(data),
                                          'line': 1, 'message': 'Syntax tree contains a grammar recovery node.'})
    from ssis_migration.schemas import validate_document
    validate_document(result, 'csharp_syntax')
    return result
