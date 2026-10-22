"""Bounded rules/feeder grammar preserving source order and coordinate syntax.

Recognition does not resolve dimensionality, cell type, aggregation, rule
precedence, feeder reprocessing or numeric behavior against a TM1 server.
"""
from __future__ import annotations

from .lexer import RuleSyntaxError, tokenize

_DIRECTIVES = {'SKIPCHECK', 'FEEDSTRINGS', 'UNDEFVALS', 'FEEDERS'}
_ARITY = {'IF': {3}, 'ABS': {1}, 'MIN': {2}, 'MAX': {2}, 'ROUND': {2},
          'NUMBR': {1}, 'STR': {3}, 'ATTRN': {3}, 'ATTRS': {3},
          'ELISANC': {3}, 'ELISCOMP': {3}, 'ELLEV': {2}, 'DIMIX': {2}}


class Parser:
    def __init__(self, source):
        self.tokens, self.index = tokenize(source), 0
        self.depth, self.heights, self.findings = 0, {}, []

    @property
    def token(self): return self.tokens[self.index]

    def take(self):
        token = self.token
        self.index += 1
        return token

    def expect(self, kind):
        token = self.token
        if token.kind != kind:
            raise RuleSyntaxError('EXPECTED_TOKEN', 'Expected ' + kind + ', found ' + (token.value or 'end of input') + '.', token.start, token.end)
        return self.take()

    def node(self, kind, start, end, **fields):
        def height(value):
            if isinstance(value, dict): return self.heights.get(id(value)) or max((height(v) for v in value.values()), default=0)
            if isinstance(value, list): return max((height(v) for v in value), default=0)
            return 0
        depth = 1 + height(fields)
        if depth > 24:
            raise RuleSyntaxError('AST_DEPTH_LIMIT', 'TM1 AST depth exceeded; no partial acceptance.', start, end, 'LIMIT_EXCEEDED')
        result = {'kind':kind, 'span':{'start':start,'end':end}, **fields}
        self.heights[id(result)] = depth
        return result

    def note(self, code, message, node):
        self.findings.append({'code':code,'message':message,'span':node['span']})

    def document(self):
        statements, section, directives = [], 'CALCULATIONS', set()
        while self.token.kind != 'EOF':
            token = self.token
            if token.kind == 'NAME' and token.value.upper() in _DIRECTIVES:
                directive = self.take(); finish = self.expect(';')
                node = self.node('directive', directive.start, finish.end, name=directive.value.upper())
                if node['name'] in directives:
                    self.note('REPEATED_DIRECTIVE', 'Repeated directives require source/runtime verification; no occurrence was removed.', node)
                if section == 'FEEDERS' and node['name'] != 'FEEDERS':
                    self.note('DIRECTIVE_AFTER_FEEDERS', 'Directive placement after FEEDERS requires format/semantic review.', node)
                directives.add(node['name'])
                if node['name'] == 'FEEDERS': section = 'FEEDERS'
            elif token.kind == '[':
                area = self.area()
                if self.token.kind == '=>':
                    self.take(); target = self.expression(); finish = self.expect(';')
                    node = self.node('feeder', area['span']['start'], finish.end, source_area=area, target=target)
                    if section != 'FEEDERS': self.note('FEEDER_WITHOUT_SECTION', 'Feeder declaration lacks an explicit preceding FEEDERS section.', node)
                    if target['kind'] != 'area' and not (target['kind'] == 'call' and target['name'].upper() == 'DB'):
                        self.note('UNSUPPORTED_FEEDER_TARGET', 'Feeder target must be an area or explicit DB call in this grammar.', target)
                else:
                    self.expect('=')
                    qualifier = None
                    if self.token.kind == 'NAME' and self.token.value.upper() in {'N','C','S'}:
                        qualifier = self.take().value.upper(); self.expect(':')
                    value = self.expression(); finish = self.expect(';')
                    node = self.node('calculation', area['span']['start'], finish.end, area=area, qualifier=qualifier, value=value)
                    if section == 'FEEDERS': self.note('CALCULATION_AFTER_FEEDERS', 'Calculation after FEEDERS requires section/format review.', node)
            else:
                raise RuleSyntaxError('UNSUPPORTED_STATEMENT', 'Expected a directive, explicit area calculation or feeder; no statement was skipped.', token.start, token.end, 'UNSUPPORTED')
            statements.append(node)
        return statements

    def area(self):
        opening = self.expect('['); selectors = []
        if self.token.kind != ']':
            while True:
                first = self.expect('STRING'); dimension = None; element = first
                if self.token.kind == ':':
                    self.take(); dimension = first.value; element = self.expect('STRING')
                    if self.token.kind == ':':
                        raise RuleSyntaxError('UNSUPPORTED_HIERARCHY_SELECTOR', 'Multi-part hierarchy selectors require a later format-specific grammar.', first.start, self.token.end, 'UNSUPPORTED')
                selectors.append({'dimension':dimension,'element':element.value,'span':{'start':first.start,'end':element.end}})
                if self.token.kind != ',': break
                self.take()
        finish = self.expect(']')
        return self.node('area', opening.start, finish.end, selectors=selectors)

    def expression(self, minimum=0):
        self.depth += 1
        if self.depth > 48:
            raise RuleSyntaxError('DEPTH_LIMIT','TM1 expression nesting exceeded.',self.token.start,self.token.end,'LIMIT_EXCEEDED')
        try:
            left = self.atom()
            precedence = {'%':1,'&':2,'=':3,'<>':3,'>':3,'<':3,'>=':3,'<=':3,
                          '@=':3,'@<>':3,'@>':3,'@<':3,'@>=':3,'@<=':3,
                          '|':4,'+':5,'-':5,'*':6,'/':6,'\\':6,'^':7}
            while self.token.kind in precedence and precedence[self.token.kind] >= minimum:
                operator = self.take()
                right = self.expression(precedence[operator.kind] + (0 if operator.kind == '^' else 1))
                left = self.node('binary',left['span']['start'],right['span']['end'],operator=operator.kind,left=left,right=right)
            return left
        finally: self.depth -= 1

    def atom(self):
        token = self.token
        if token.kind in {'NUMBER','STRING'}:
            self.take()
            return self.node('literal',token.start,token.end,literal_type=token.kind,value=token.value)
        if token.kind == '[': return self.area()
        if token.kind == '!':
            start = self.take()
            if self.token.kind != 'NAME':
                raise RuleSyntaxError('UNSUPPORTED_DIMENSION_CONTEXT','Only simple explicit dimension context identifiers are supported.',start.start,self.token.end,'UNSUPPORTED')
            name = self.take()
            if self.token.kind == 'NAME':
                raise RuleSyntaxError('UNSUPPORTED_DIMENSION_CONTEXT','Spaced dimension context identifiers require a later grammar increment.',start.start,self.token.end,'UNSUPPORTED')
            return self.node('dimension_context',start.start,name.end,name=name.value)
        if token.kind in {'+','-','~'}:
            operator = self.take(); value = self.expression(8)
            return self.node('unary',operator.start,value['span']['end'],operator=operator.kind,value=value)
        if token.kind == '(':
            start = self.take(); value = self.expression(); finish = self.expect(')')
            return self.node('group',start.start,finish.end,value=value)
        if token.kind == 'NAME':
            name = self.take()
            if name.value.upper() in {'STET','CONTINUE'} and self.token.kind != '(':
                return self.node('control',name.start,name.end,name=name.value.upper())
            if self.token.kind != '(':
                raise RuleSyntaxError('UNRESOLVED_BARE_NAME','Bare names require definitions; no value was invented.',name.start,name.end,'UNSUPPORTED')
            self.take(); arguments = []
            if self.token.kind != ')':
                while True:
                    arguments.append(self.expression())
                    if self.token.kind != ',': break
                    self.take()
            finish = self.expect(')')
            node = self.node('call',name.start,finish.end,name=name.value,arguments=arguments)
            valid = len(arguments) >= 2 if name.value.upper() == 'DB' else len(arguments) in _ARITY.get(name.value.upper(), set())
            if not valid: self.note('UNSUPPORTED_FUNCTION_SIGNATURE','Function or signature requires explicit further analysis: '+name.value,node)
            return node
        if token.kind in {'{',':'}:
            raise RuleSyntaxError('UNSUPPORTED_SELECTOR','Selector syntax requires a later grammar increment.',token.start,token.end,'UNSUPPORTED')
        raise RuleSyntaxError('EXPECTED_EXPRESSION','Expected a bounded rules expression.',token.start,token.end)
