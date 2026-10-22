"""Structural SELECT grammar; complete consumption is required.

Function names/signatures below establish syntax categories only. They do not
resolve cubes, dimensionality, hierarchy levels, subsets or cell semantics.
"""
from __future__ import annotations

from .lexer import MDXError, tokenize

_AXES = {'COLUMNS': 0, 'ROWS': 1, 'PAGES': 2, 'CHAPTERS': 3, 'SECTIONS': 4}
_PROPERTIES = {'MEMBERS': 'SET', 'CHILDREN': 'SET', 'CURRENTMEMBER': 'MEMBER',
               'PARENT': 'MEMBER', 'DEFAULTMEMBER': 'MEMBER'}
# Category checks do not imply these functions can be translated faithfully.
_SIGNATURES = {
    'CROSSJOIN': [('SET', 'SET')], 'UNION': [('SET', 'SET')],
    'INTERSECT': [('SET', 'SET')], 'EXCEPT': [('SET', 'SET')],
    'HIERARCHIZE': [('SET',)], 'HEAD': [('SET',), ('SET', 'INTEGER')],
    'TAIL': [('SET',), ('SET', 'INTEGER')],
    'TM1FILTERBYLEVEL': [('SET', 'INTEGER')],
    'TM1SUBSETTOSET': [('MEMBER', 'STRING'), ('MEMBER', 'STRING', 'STRING')],
}


class Parser:
    def __init__(self, source):
        self.tokens, self.index, self.depth = tokenize(source), 0, 0
        self.findings = []
        self.heights = {}

    @property
    def token(self):
        return self.tokens[self.index]

    def take(self):
        token = self.token
        self.index += 1
        return token

    def is_name(self, word):
        return self.token.kind == 'NAME' and self.token.value.upper() == word

    def expect(self, kind, word=None):
        token = self.token
        if token.kind != kind or (word is not None and token.value.upper() != word):
            raise MDXError('EXPECTED_TOKEN', 'Expected ' + (word or kind) + ', found ' + (token.value or 'end of input') + '.', token.start, token.end)
        return self.take()

    def node(self, kind, start, end, **fields):
        def height(value):
            if isinstance(value, dict):
                return self.heights.get(id(value)) or max((height(v) for v in value.values()), default=0)
            if isinstance(value, list):
                return max((height(v) for v in value), default=0)
            return 0
        depth = 1 + height(fields)
        if depth > 24:
            raise MDXError('AST_DEPTH_LIMIT', 'MDX AST depth exceeded; no partial acceptance.', start, end, status='LIMIT_EXCEEDED')
        result = {'kind': kind, 'span': {'start': start, 'end': end}, **fields}
        self.heights[id(result)] = depth
        return result

    def note(self, code, message, node):
        self.findings.append({'code': code, 'message': message, 'span': node['span']})

    def query(self):
        if not self.is_name('SELECT'):
            token = self.token
            raise MDXError('UNSUPPORTED_QUERY', 'Only complete SELECT MDX queries are supported; WITH/formulas/commands require separate analysis.', token.start, token.end, status='UNSUPPORTED')
        start = self.take().start
        axes = []
        while True:
            non_empty = False
            if self.is_name('NON'):
                self.take(); self.expect('NAME', 'EMPTY'); non_empty = True
            expression = self.expression()
            if expression['category'] not in {'SET', 'UNKNOWN'}:
                self.note('AXIS_NOT_SET', 'Axis expression is not an explicit set; implicit coercion/dimensionality is unresolved.', expression)
            self.expect('NAME', 'ON')
            axis = self.take()
            if axis.kind == 'NUMBER' and axis.value.isdecimal() and len(axis.value) < 3:
                ordinal = int(axis.value)
            elif axis.kind == 'NAME' and axis.value.upper() in _AXES:
                ordinal = _AXES[axis.value.upper()]
            else:
                raise MDXError('UNSUPPORTED_AXIS', 'Axis must be a supported named or numeric ordinal.', axis.start, axis.end, status='UNSUPPORTED')
            if ordinal not in _AXES.values():
                raise MDXError('UNSUPPORTED_AXIS', 'Only axis ordinals 0 through 4 are supported.', axis.start, axis.end, status='UNSUPPORTED')
            axes.append({'ordinal': ordinal, 'source_label': axis.value, 'non_empty': non_empty, 'expression': expression})
            if self.token.kind != ',':
                break
            self.take()
        if len({axis['ordinal'] for axis in axes}) != len(axes):
            raise MDXError('DUPLICATE_AXIS', 'Duplicate axis ordinals cannot be resolved.', start, self.token.start)
        self.expect('NAME', 'FROM')
        if self.token.kind == '(':
            raise MDXError('UNSUPPORTED_SUBSELECT', 'MDX subselect context requires a later grammar increment.', self.token.start, self.token.end, status='UNSUPPORTED')
        cube = self.expect('IDENTIFIER')
        cube_node = self.node('cube', cube.start, cube.end, name=cube.value)
        slicer = None
        if self.is_name('WHERE'):
            self.take()
            slicer = self.expression()
            if slicer['category'] not in {'MEMBER', 'TUPLE', 'SET', 'UNKNOWN'}:
                self.note('UNSUPPORTED_SLICER', 'Slicer must be a member, tuple or set; context requires metadata.', slicer)
        if self.token.kind == ';':
            self.take()
        if self.token.kind != 'EOF':
            token = self.token
            raise MDXError('UNSUPPORTED_REMAINDER', 'The full query contains unsupported trailing clauses or additional statements; no parsed prefix is accepted.', token.start, token.end, status='UNSUPPORTED')
        return self.node('select', start, self.token.end, axes=axes, cube=cube_node, slicer=slicer)

    def expression(self, minimum=0):
        self.depth += 1
        if self.depth > 48:
            raise MDXError('DEPTH_LIMIT', 'MDX expression depth exceeded; no partial acceptance.', self.token.start, self.token.end, status='LIMIT_EXCEEDED')
        try:
            left = self.atom()
            precedence = {'+': 1, '-': 1, '*': 2, ':': 3}
            while self.token.kind in precedence and precedence[self.token.kind] >= minimum:
                operator = self.take()
                right = self.expression(precedence[operator.kind] + 1)
                category = 'SET'
                if operator.kind == ':':
                    valid = left['category'] == right['category'] == 'MEMBER'
                else:
                    valid = left['category'] == right['category'] == 'SET'
                left = self.node('set_operator', left['span']['start'], right['span']['end'],
                                 category=category, operator=operator.kind, left=left, right=right)
                if not valid:
                    self.note('UNRESOLVED_OPERATOR_TYPES', 'Set/range operands require explicit compatible categories and source dimensional metadata.', left)
            return left
        finally:
            self.depth -= 1

    def atom(self):
        token = self.token
        if token.kind == 'IDENTIFIER':
            first = self.take()
            segments = [{'kind': 'NAME', 'value': first.value}]
            category, prop, end = 'MEMBER', None, first.end
            while self.token.kind == '.':
                self.take()
                key = self.take() if self.token.kind == '&' else None
                part = self.token
                if part.kind == 'IDENTIFIER':
                    self.take(); end = part.end
                    segments.append({'kind': 'KEY' if key else 'NAME', 'value': part.value})
                elif not key and part.kind == 'NAME':
                    self.take(); end = part.end; prop = part.value
                    category = _PROPERTIES.get(prop.upper(), 'UNKNOWN')
                    if self.token.kind == '(':
                        raise MDXError('UNSUPPORTED_MEMBER_FUNCTION', 'Member-property function calls require later analysis.', part.start, self.token.end, status='UNSUPPORTED')
                    break
                else:
                    raise MDXError('EXPECTED_MEMBER', 'Expected an explicit bracketed member/key or supported member property.', part.start, part.end)
            node = self.node('member_path', first.start, end, category=category, segments=segments, property=prop)
            if category == 'UNKNOWN':
                self.note('UNKNOWN_MEMBER_PROPERTY', 'Member property requires unsupported dialect/metadata analysis: ' + str(prop), node)
            return node
        if token.kind in {'STRING', 'NUMBER'}:
            self.take()
            category = 'STRING' if token.kind == 'STRING' else 'INTEGER' if '.' not in token.value else 'NUMBER'
            return self.node('literal', token.start, token.end, category=category, value=token.value)
        if token.kind in {'{', '('}:
            start = self.take()
            closing = '}' if start.kind == '{' else ')'
            items = []
            if self.token.kind != closing:
                while True:
                    items.append(self.expression())
                    if self.token.kind != ',':
                        break
                    self.take()
            finish = self.expect(closing)
            if start.kind == '(' and len(items) == 1:
                return self.node('group', start.start, finish.end, category=items[0]['category'], expression=items[0])
            category = 'SET' if start.kind == '{' else 'TUPLE'
            node = self.node('set' if category == 'SET' else 'tuple', start.start, finish.end, category=category, items=items)
            allowed = {'MEMBER', 'TUPLE', 'SET'} if category == 'SET' else {'MEMBER'}
            if (category == 'TUPLE' and not items) or any(item['category'] not in allowed for item in items):
                self.note('UNRESOLVED_COLLECTION_TYPES', 'Set/tuple elements require compatible member coordinates; no implicit coercion was applied.', node)
            return node
        if token.kind == 'NAME':
            name = self.take()
            if self.token.kind != '(':
                raise MDXError('UNRESOLVED_NAME', 'Bare names/named sets require definitions; no member was invented.', name.start, name.end, status='UNSUPPORTED')
            self.take()
            arguments = []
            if self.token.kind != ')':
                while True:
                    arguments.append(self.expression())
                    if self.token.kind != ',':
                        break
                    self.take()
            finish = self.expect(')')
            signature = tuple(arg['category'] for arg in arguments)
            known = signature in _SIGNATURES.get(name.value.upper(), [])
            node = self.node('function', name.start, finish.end, category='SET' if known else 'UNKNOWN', name=name.value, arguments=arguments)
            if not known:
                self.note('UNSUPPORTED_FUNCTION_SIGNATURE', 'Function/signature requires further MDX analysis: ' + name.value, node)
            return node
        if token.kind in {'+', '-'}:
            raise MDXError('UNSUPPORTED_SCALAR_EXPRESSION', 'Scalar arithmetic/unary expressions require later MDX analysis.', token.start, token.end, status='UNSUPPORTED')
        raise MDXError('EXPECTED_EXPRESSION', 'Expected an MDX set, tuple, member or supported function.', token.start, token.end)
