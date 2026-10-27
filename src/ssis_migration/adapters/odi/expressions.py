"""Bounded native ODI expression syntax with original reference bindings.

ParsedTxt is not passed to a SQL parser. Literal null text, unsupported functions,
windows and metadata remain original declarations, never inferred runtime values.
"""
from __future__ import annotations

from collections import Counter
import copy
import re

from ssis_migration.schemas import validate_document
from ..base import UnsupportedSourceError
from .native import field, value

PROFILE = 'odi_native_expression_syntax_v1'
MAX_EXPRESSIONS = 512
MAX_REFERENCES = 256
MAX_TEXT_CHARS = 65536
MAX_SYNTAX_CHARS = 4096
MAX_TOKENS = 256
MAX_DEPTH = 32
_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_REFERENCE = re.compile(r'@\{(R[0-9]+)\}')


class ExpressionReviewRequired(ValueError):
    pass


def _evidence(nodes, names=()):
    result = []
    for node in nodes:
        if node is None: continue
        refs = list(node['evidence'])
        for name in names:
            declaration = field(node, name)
            if declaration: refs.extend(declaration['evidence'])
        for ref in refs:
            if ref not in result: result.append(copy.deepcopy(ref))
    return result


def analyze_expression(index, expression):
    fields = {name: value(expression, name) for name in ('Txt', 'ParsedTxt', 'Text', 'ParsedText', 'IsParsed', 'TextOnly')}
    if any(isinstance(text, str) and len(text) > MAX_TEXT_CHARS for text in fields.values()):
        raise UnsupportedSourceError('ODI expression declaration exceeds its text limit.')
    owner = index.owner(expression); prop = index.target(expression, 'IOwnerMapProp')
    references = [r for r in index.classes['SnpMapExprRef'] if index.target(r, 'IOwnerMapExpr') is expression]
    if len(references) > MAX_REFERENCES:
        raise UnsupportedSourceError('ODI expression reference limit exceeded.')
    bindings = []
    for reference in references:
        attribute = index.target(reference, 'IRefMapAttr'); cp = index.target(attribute, 'IOwnerMapCp')
        component = index.target(cp, 'IOwnerMapComp')
        alias = value(component, 'Name') if component else None
        column = value(attribute, 'Name') if attribute else None
        peers = [n for n in index.classes['SnpMapComp'] if index.owner(n) == owner and value(n, 'Name') == alias]
        text = value(reference, 'RefText')
        bound = (owner is not None and attribute is not None and component is not None
                 and index.owner(attribute) == index.owner(reference) == owner and len(peers) == 1
                 and isinstance(alias, str) and isinstance(column, str)
                 and _IDENTIFIER.fullmatch(alias) is not None and _IDENTIFIER.fullmatch(column) is not None
                 and text == alias + '.' + column)
        bindings.append({'reference_node_id': reference['node_id'], 'reference_key': value(reference, 'RefKey'),
            'reference_text': text, 'validity_declaration': value(reference, 'IsValid'),
            'attribute_node_id': attribute['node_id'] if attribute else None,
            'component_node_id': component['node_id'] if component else None, 'alias': alias, 'column': column,
            'resolution': 'BOUND' if bound else 'UNRESOLVED',
            'evidence': _evidence([reference, attribute, cp, component], ('RefKey', 'RefText', 'IRefMapAttr', 'Name', 'IOwnerMapCp', 'IOwnerMapComp'))})
    result = {'analysis_version': '1.0', 'profile': PROFILE, 'expression_node_id': expression['node_id'],
        'owner_property_name': value(prop, 'Name') if prop else None, 'source_fields': fields,
        'status': 'REVIEW_REQUIRED', 'ast': None, 'bindings': bindings,
        'semantic_status': 'NOT_VERIFIED', 'unresolved_items': [],
        'evidence': _evidence([expression, prop], ('Txt', 'ParsedTxt', 'Text', 'ParsedText', 'IsParsed', 'TextOnly', 'Name'))}
    try:
        text, parsed = fields['Txt'], fields['ParsedTxt']
        if fields['Text'] != 'null' or fields['ParsedText'] != 'null':
            raise ExpressionReviewRequired('Alternate Text/ParsedText declarations are outside this native syntax profile; no precedence was chosen.')
        if text == '' and parsed == 'null' and fields['IsParsed'] == '0' and fields['TextOnly'] == '0':
            result['status'] = 'EMPTY_DECLARATION'
            raise ExpressionReviewRequired('Empty Txt and literal null ParsedTxt are retained; inherited/default behavior is not established.')
        if (result['owner_property_name'] == 'XMLDiagramSettings' and fields['TextOnly'] == '1'
                and fields['IsParsed'] == '0' and parsed == ''):
            result['status'] = 'TEXT_METADATA_DECLARATION'
            raise ExpressionReviewRequired('Declared layout text is retained for review and is not translated as an executable expression.')
        if fields['IsParsed'] != '1' or fields['TextOnly'] != '0':
            raise ExpressionReviewRequired('Native expression flags do not match the bounded parsed-expression profile.')
        if (not isinstance(text, str) or not isinstance(parsed, str) or not text or not parsed
                or max(len(text), len(parsed)) > MAX_SYNTAX_CHARS):
            raise ExpressionReviewRequired('Native expression text is absent or exceeds the syntax limit.')
        if any(b['resolution'] != 'BOUND' for b in bindings):
            raise ExpressionReviewRequired('An expression reference lacks an exact scoped attribute/name binding.')
        keys = Counter(b['reference_key'] for b in bindings)
        if any(count != 1 or not isinstance(key, str) or re.fullmatch(r'R[0-9]+', key) is None for key, count in keys.items()):
            raise ExpressionReviewRequired('Native reference keys are missing, ambiguous or unsupported.')
        by_key = {b['reference_key']: b for b in bindings}
        tokens = []; offset = 0
        pattern = re.compile(r'\s+|@\{R[0-9]+\}|[A-Za-z_][A-Za-z0-9_]*|[=(),]')
        while offset < len(parsed):
            match = pattern.match(parsed, offset)
            if match is None: raise ExpressionReviewRequired('Unsupported native expression syntax at offset '+str(offset)+'.')
            if not match[0].isspace(): tokens.append(match[0])
            offset = match.end()
            if len(tokens) > MAX_TOKENS: raise ExpressionReviewRequired('Native expression token limit exceeded.')
        position = 0; used = set()

        def atom(depth=0):
            nonlocal position
            if depth > MAX_DEPTH or position >= len(tokens):
                raise ExpressionReviewRequired('Native expression is incomplete or exceeds its nesting limit.')
            token = tokens[position]; position += 1
            if token == '(':
                node = atom(depth+1)
                if position >= len(tokens) or tokens[position] != ')':
                    raise ExpressionReviewRequired('Incomplete native expression parentheses.')
                position += 1; return node
            ref = _REFERENCE.fullmatch(token)
            if ref:
                key = ref[1]
                if key not in by_key: raise ExpressionReviewRequired('Native placeholder has no unique declared binding: '+key)
                used.add(key)
                return {'kind': 'ATTRIBUTE_REFERENCE', 'reference_key': key, 'attribute_node_id': by_key[key]['attribute_node_id']}
            if token.upper() in {'SUM', 'YEAR'}:
                if position >= len(tokens) or tokens[position] != '(':
                    raise ExpressionReviewRequired('Native function requires one explicit attribute argument.')
                position += 1; argument = atom(depth+1)
                if argument['kind'] != 'ATTRIBUTE_REFERENCE' or position >= len(tokens) or tokens[position] != ')':
                    raise ExpressionReviewRequired('This profile supports SUM/YEAR on one declared attribute only.')
                position += 1
                return {'kind': 'CALL', 'name': token.upper(), 'argument': argument}
            raise ExpressionReviewRequired('Native function or expression form requires a separate capability rule: '+token)

        ast = atom()
        if position < len(tokens) and tokens[position] == '=':
            position += 1; right = atom()
            if ast['kind'] != right['kind'] or ast['kind'] != 'ATTRIBUTE_REFERENCE':
                raise ExpressionReviewRequired('Equality is supported between two explicit attributes only.')
            ast = {'kind': 'COMPARE', 'operator': '=', 'left': ast, 'right': right}
        if position != len(tokens): raise ExpressionReviewRequired('Trailing native syntax, including window clauses, remains unsupported.')
        if used != set(by_key): raise ExpressionReviewRequired('Unused declared references require review; none were discarded.')
        expanded = _REFERENCE.sub(lambda match: by_key[match[1]]['reference_text'], parsed)
        def source_tokens(source):
            result = []; offset = 0
            pattern = re.compile(r'\s+|[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?|[=(),]')
            while offset < len(source):
                match = pattern.match(source, offset)
                if match is None: raise ExpressionReviewRequired('Txt contains syntax outside the declared-reference profile.')
                if not match[0].isspace(): result.append(match[0])
                offset = match.end()
            return result
        if source_tokens(expanded) != source_tokens(text):
            raise ExpressionReviewRequired('Txt and ParsedTxt/reference declarations disagree; no expression was selected as authoritative.')
        result.update(status='PARSED', ast=ast)
        result['unresolved_items'].append('Syntax only: source validity flags, types, null/coercion, aggregate context and calendar/time-zone behavior remain unverified.')
    except ExpressionReviewRequired as exc:
        result['unresolved_items'].append(str(exc))
    validate_document(result, 'odi_expression_analysis')
    return result
