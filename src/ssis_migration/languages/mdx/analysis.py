"""Evidence-bound MDX structure and missing dimensional dependencies.

A structural parse never establishes cube semantics or a faithful SQL rewrite.
There is deliberately no SQL parser, AI call, code generation or execution here.
"""
from __future__ import annotations

import hashlib
import json

from .lexer import MDXError
from .parser import Parser
from ssis_migration.schemas import SchemaValidationError, validate_document


def analyze_mdx(source: str, *, dialect: str, file_id: str, locator: str = 'file:/') -> dict:
    if not isinstance(source, str):
        raise TypeError('MDX source must be decoded text.')
    if dialect != 'tm1':
        raise ValueError('Explicit tm1 MDX dialect is required; no SQL or other-language fallback is selected.')
    if not isinstance(file_id, str) or not file_id or not isinstance(locator, str) or not locator:
        raise ValueError('MDX analysis requires a source file identity and locator.')
    result = {
        'analysis_version': '1.0', 'language': 'mdx', 'dialect': dialect,
        'status': 'STRUCTURED', 'scope': 'BOUNDED_MDX_SELECT_STRUCTURE',
        'semantic_status': 'NOT_VERIFIED', 'migration_strategy': 'REVIEW_REQUIRED',
        'source': {'file_id': file_id, 'locator': locator, 'text': source,
                   'sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                   'size_bytes': len(source.encode('utf-8')), 'digest_scope': 'DECODED_UTF8_TEXT',
                   'offset_unit': 'UNICODE_CODE_POINTS'}, 'source_file': None,
        'query': None, 'references': [], 'dependencies': [], 'features': [],
        'diagnostics': [], 'assumptions': [], 'migration_accepted': False,
        'validation_requirements': [
            'Supply cube, dimension, hierarchy, member/key and subset definitions with original evidence.',
            'Verify axis order, tuple dimensionality, default members, aggregation, null/empty and slicer context.',
            'Supply TM1 rules/feeders, calculated members, security and runtime context that affect cell results.',
            'Review unsupported constructs before generation; verify controlled execution and data reconciliation.',
        ],
    }
    def evidence(span, rule):
        return {'file_id': file_id, 'locator': locator + '#mdx-chars=' + str(span['start']) + ':' + str(span['end']), 'rule': rule}
    def identity(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    def diagnostic(code, message, span, severity='WARNING'):
        result['diagnostics'].append({'code': code, 'severity': severity, 'message': message,
                                      'span': span, 'evidence': evidence(span, code)})
    def dependency(kind, declared, span, details=None):
        result['dependencies'].append({'dependency_id': 'dep-' + identity([file_id, kind, span, declared]),
                                       'kind': kind, 'declared_value': declared, 'status': 'NOT_SUPPLIED',
                                       'details': details or {}, 'span': span, 'evidence': evidence(span, 'mdx_dependency_requirement')})
    try:
        if not source.strip():
            raise MDXError('EMPTY_MDX', 'No MDX source was supplied.', 0, len(source))
        parser = Parser(source)
        query = parser.query()
        result['query'] = query
        for finding in parser.findings:
            diagnostic(finding['code'], finding['message'], finding['span'])
        if parser.findings:
            result['status'] = 'UNSUPPORTED'
            result['migration_strategy'] = 'UNSUPPORTED'
        cube = query['cube']
        result['references'].append({'reference_id': 'ref-' + identity([file_id, cube['span'], 'CUBE']),
                                     'kind': 'CUBE', 'value': cube['name'], 'segments': [],
                                     'resolution': 'UNRESOLVED', 'span': cube['span'],
                                     'evidence': evidence(cube['span'], 'mdx_from_cube')})
        dependency('CUBE_METADATA', cube['name'], cube['span'])
        dependency('CELL_RULES_AND_AGGREGATION', cube['name'], cube['span'])
        dependency('SECURITY_AND_DEFAULT_CONTEXT', None, query['span'])
        features = set()
        if any(axis['non_empty'] for axis in query['axes']):
            features.add('NON_EMPTY')
        if query['slicer'] is not None:
            features.add('SLICER_CONTEXT')
        stack = [axis['expression'] for axis in reversed(query['axes'])]
        if query['slicer'] is not None:
            stack.append(query['slicer'])
        while stack:
            node = stack.pop()
            if node['kind'] == 'member_path':
                value = source[node['span']['start']:node['span']['end']]
                result['references'].append({'reference_id': 'ref-' + identity([file_id, node['span'], 'MEMBER_PATH']),
                                             'kind': 'MEMBER_PATH', 'value': value, 'segments': node['segments'],
                                             'resolution': 'UNRESOLVED', 'span': node['span'],
                                             'evidence': evidence(node['span'], 'mdx_declared_identifier_path')})
                dependency('MEMBER_AND_HIERARCHY_BINDING', value, node['span'], {'property': node['property']})
                if node['property']:
                    features.add('MEMBER_PROPERTY')
                if (node['property'] or '').upper() in {'CURRENTMEMBER', 'DEFAULTMEMBER'}:
                    features.add('DYNAMIC_MEMBER_CONTEXT')
            elif node['kind'] == 'function':
                features.add('FUNCTION:' + node['name'].upper())
                if node['name'].upper() == 'TM1SUBSETTOSET':
                    args = node['arguments']
                    declared = args[1]['value'] if len(args) > 1 and args[1]['kind'] == 'literal' and args[1]['category'] == 'STRING' else None
                    scope = args[2]['value'] if len(args) == 3 and args[2]['kind'] == 'literal' and args[2]['category'] == 'STRING' else None
                    dependency('SUBSET_DEFINITION', declared, node['span'], {'declared_scope': scope, 'scope_resolution': 'UNVERIFIED'})
            elif node['kind'] == 'set_operator':
                features.add('SET_OPERATOR:' + node['operator'])
            elif node['kind'] == 'tuple':
                features.add('TUPLE_COORDINATES')
            # Traverse the actual expression tree, never quoted text/comments.
            children = []
            for value in node.values():
                if isinstance(value, dict) and 'kind' in value and 'span' in value:
                    children.append(value)
                elif isinstance(value, list):
                    children.extend(item for item in value if isinstance(item, dict) and 'kind' in item and 'span' in item)
            stack.extend(reversed(children))
        result['features'] = sorted(features)
        diagnostic('UNRESOLVED_MULTIDIMENSIONAL_SEMANTICS',
                   'Cube metadata, rules, security, dimensionality and cell context remain unresolved; no relational rewrite or acceptance was inferred.', query['span'])
    except MDXError as exc:
        result['status'] = exc.status
        result['migration_strategy'] = 'UNSUPPORTED'
        result['query'] = None
        result['references'] = []
        result['dependencies'] = []
        result['features'] = []
        diagnostic(exc.code, str(exc), {'start': exc.start, 'end': exc.end},
                   'ERROR' if exc.status == 'INVALID' else 'WARNING')
    result['analysis_id'] = mdx_analysis_id(result)
    validate_mdx_analysis(result)
    return result


def mdx_analysis_id(report: dict) -> str:
    content = {key: value for key, value in report.items() if key != 'analysis_id'}
    return 'mdx-' + hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'),
                                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_mdx_analysis(report: dict) -> None:
    """Validate schema, source/content hashes, evidence identity and span bounds.

    Content hashes detect stale edits, not fabricated source-review attestations.
    """
    validate_document(report, 'mdx_analysis')
    source = report['source']
    raw = source['text'].encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != source['sha256'] or len(raw) != source['size_bytes']:
        raise SchemaValidationError('MDX source hash/size does not match the preserved text.')
    if report['analysis_id'] != mdx_analysis_id(report):
        raise SchemaValidationError('MDX analysis content identity is stale.')
    stack = [report]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if 'span' in item:
                span = item['span']
                if not 0 <= span['start'] <= span['end'] <= len(source['text']):
                    raise SchemaValidationError('MDX source span is outside the original text.')
                if 'evidence' in item:
                    expected = source['locator'] + '#mdx-chars=' + str(span['start']) + ':' + str(span['end'])
                    if item['evidence']['file_id'] != source['file_id'] or item['evidence']['locator'] != expected:
                        raise SchemaValidationError('MDX evidence is not bound to its source file and span.')
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    for collection, key in (('references', 'reference_id'), ('dependencies', 'dependency_id')):
        ids = [item[key] for item in report[collection]]
        if len(ids) != len(set(ids)):
            raise SchemaValidationError('Duplicate MDX evidence identities.')
    if report['status'] == 'STRUCTURED' and (report['query'] is None or report['migration_strategy'] != 'REVIEW_REQUIRED'):
        raise SchemaValidationError('Structural MDX success must retain review-required query semantics.')
    if report['status'] in {'INVALID', 'LIMIT_EXCEEDED'} and report['query'] is not None:
        raise SchemaValidationError('Incomplete MDX cannot retain an accepted query prefix.')
