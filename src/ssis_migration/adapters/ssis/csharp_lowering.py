"""Fail-closed lowering of a deliberately small SSIS ScriptTask C# subset.

This is not symbol resolution or a .NET compiler. A caller must explicitly
supply evidenced runtime bindings, types and access permissions. Unsupported
source anywhere in the compilation unit prevents a candidate program.
"""
from __future__ import annotations

import re

from ssis_migration.languages.csharp.parser import parse_csharp, parse_tree

BASE_TYPE = 'Microsoft.SqlServer.Dts.Tasks.ScriptTask.VSTARTScriptObjectModelBase'
_TYPES = {'int': 'int32', 'bool': 'bool', 'string': 'string'}


class UnsupportedCSharp(ValueError):
    def __init__(self, message, node=None):
        super().__init__(message)
        self.start_byte = node.start_byte if node else 0
        self.end_byte = node.end_byte if node else 0


def _children(node):
    return [child for child in node.named_children if child.type != 'comment'] if node else []


def _text(node):
    return node.text.decode('utf-8') if node else ''


def _field(node, name):
    return node.child_by_field_name(name) if node else None


def _span(node):
    return {'start_byte': node.start_byte, 'end_byte': node.end_byte}


def _require(condition, message, node=None):
    if not condition:
        raise UnsupportedCSharp(message, node)


class _Lowerer:
    def __init__(self, spec):
        self.spec = spec
        self.locals = {}
        self.local_counter = 0
        self.used_variables = set()

    def node(self, op, source, **values):
        return {'op': op, 'source_span': _span(source), **values}

    def literal(self, node):
        text = _text(node)
        if node.type == 'integer_literal':
            _require(bool(re.fullmatch(r'(0|[1-9][0-9]*)', text)), 'Only decimal int32 literals are supported.', node)
            _require(len(text) <= 10, 'Integer literal exceeds the supported int32 range.', node)
            value = int(text)
            _require(value <= 2147483647, 'Literal exceeds int32; unsigned/long promotion is unsupported.', node)
            return self.node('literal', node, type='int32', value=value)
        if node.type == 'boolean_literal':
            return self.node('literal', node, type='bool', value=text == 'true')
        if node.type == 'string_literal':
            _require(text.startswith('"') and text.endswith('"') and '\\' not in text and '\n' not in text and '\r' not in text,
                     'Escaped/non-ordinary string literals require a later translator.', node)
            return self.node('literal', node, type='string', value=text[1:-1])
        raise UnsupportedCSharp('Unsupported literal: ' + node.type, node)

    def variable(self, node, access):
        _require(node.type == 'member_access_expression' and _text(_field(node, 'name')) == 'Value',
                 'Only explicitly bound Dts.Variables["name"].Value access is supported.', node)
        element = _field(node, 'expression')
        _require(element is not None and element.type == 'element_access_expression', 'Unsupported variable receiver.', node)
        receiver = _field(element, 'expression')
        _require(receiver.type == 'member_access_expression' and _text(_field(receiver, 'name')) == 'Variables'
                 and _text(_field(receiver, 'expression')) == 'Dts', 'Unsupported variable receiver.', node)
        args = _children(_field(element, 'subscript'))
        _require(len(args) == 1 and args[0].type == 'argument', 'Variable key must be one positional string literal.', node)
        parts = _children(args[0])
        _require(len(parts) == 1 and parts[0].type == 'string_literal', 'Variable key must be a string literal.', node)
        key = self.literal(parts[0])['value']
        binding = self.spec['variables'].get(key)
        _require(binding is not None, 'Missing supplied variable binding: ' + key, node)
        required = {'READ': {'READ', 'READ_WRITE'}, 'WRITE': {'WRITE', 'READ_WRITE'}}
        _require(binding['access'] in required[access], 'Variable access conflicts with supplied permissions: ' + key, node)
        self.used_variables.add(key)
        return key, binding['type']

    def expression(self, node):
        kind = node.type
        if kind in {'integer_literal', 'boolean_literal', 'string_literal'}:
            return self.literal(node)
        if kind == 'identifier':
            binding = self.locals.get(_text(node))
            _require(binding is not None, 'Unknown/uninitialized local: ' + _text(node), node)
            return self.node('local', node, name=binding[0], type=binding[1])
        if kind == 'parenthesized_expression':
            children = _children(node)
            _require(len(children) == 1, 'Unsupported parenthesized expression.', node)
            return self.expression(children[0])
        if kind == 'cast_expression':
            value = _field(node, 'value')
            key, value_type = self.variable(value, 'READ')
            _require(_TYPES.get(_text(_field(node, 'type'))) == value_type,
                     'Cast must match the supplied variable type exactly.', node)
            return self.node('variable', node, name=key, type=value_type)
        if kind == 'prefix_unary_expression':
            children = _children(node)
            _require(len(children) == 1, 'Unsupported unary expression.', node)
            operator = node.children[0].type
            # C# has a special decimal -2147483648 literal rule.
            if operator == '-' and children[0].type == 'integer_literal' and _text(children[0]) == '2147483648':
                return self.node('literal', node, type='int32', value=-2147483648)
            value = self.expression(children[0])
            _require((operator == '!' and value['type'] == 'bool') or
                     (operator in {'+', '-'} and value['type'] == 'int32'), 'Unsupported unary operator/type.', node)
            if value['op'] == 'literal':
                result = not value['value'] if operator == '!' else (+value['value'] if operator == '+' else -value['value'])
                _require(value['type'] != 'int32' or -2147483648 <= result <= 2147483647,
                         'Constant int32 overflow would fail C# compilation.', node)
                return self.node('literal', node, type=value['type'], value=result)
            return self.node('unary', node, operator=operator, value=value, type=value['type'])
        if kind == 'binary_expression':
            left, right = self.expression(_field(node, 'left')), self.expression(_field(node, 'right'))
            operator = _text(_field(node, 'operator'))
            _require(left['type'] == right['type'], 'Implicit conversions and mixed operand types are unsupported.', node)
            value_type = left['type']
            if operator in {'+', '-', '*'} and value_type == 'int32':
                output_type = 'int32'
            elif operator in {'<', '<=', '>', '>='} and value_type == 'int32':
                output_type = 'bool'
            elif operator in {'==', '!='}:
                output_type = 'bool'
            elif operator in {'&&', '||'} and value_type == 'bool':
                output_type = 'bool'
            else:
                raise UnsupportedCSharp('Unsupported binary operator/type: ' + operator, node)
            if output_type == 'int32' and left['op'] == right['op'] == 'literal':
                a, b = left['value'], right['value']
                value = {'+': lambda: a + b, '-': lambda: a - b, '*': lambda: a * b}[operator]()
                _require(-2147483648 <= value <= 2147483647, 'Constant int32 overflow would fail C# compilation.', node)
                return self.node('literal', node, type='int32', value=value)
            return self.node('binary', node, operator=operator, left=left, right=right, type=output_type)
        raise UnsupportedCSharp('Unsupported expression: ' + kind, node)

    def statement(self, node):
        kind = node.type
        if kind == 'block':
            before = dict(self.locals)
            statements = [self.statement(child) for child in _children(node)]
            self.locals = before
            return self.node('block', node, statements=statements)
        if kind == 'local_declaration_statement':
            children = _children(node)
            _require(len(children) == 1 and children[0].type == 'variable_declaration', 'Local modifiers are unsupported.', node)
            declaration = children[0]
            dtype = _TYPES.get(_text(_field(declaration, 'type')))
            parts = [p for p in _children(declaration) if p.type == 'variable_declarator']
            _require(dtype is not None and len(parts) == 1, 'One explicitly typed local per declaration is required.', node)
            part = parts[0]
            name = _text(_field(part, 'name'))
            values = [p for p in _children(part) if p != _field(part, 'name')]
            _require(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name) and name != 'Dts' and name not in self.locals,
                     'Local shadowing/reserved identifiers are unsupported.', node)
            _require(len(values) == 1, 'Local initialization is required.', node)
            value = self.expression(values[0])
            _require(value['type'] == dtype, 'Local initializer type mismatch.', node)
            target = '_local_' + str(self.local_counter)
            self.local_counter += 1
            self.locals[name] = (target, dtype)
            return self.node('assign_local', node, name=target, value=value)
        if kind == 'expression_statement':
            children = _children(node)
            _require(len(children) == 1 and children[0].type == 'assignment_expression', 'Only simple assignments are supported.', node)
            assignment = children[0]
            _require(_text(_field(assignment, 'operator')) == '=', 'Compound assignments are unsupported.', node)
            left, right = _field(assignment, 'left'), _field(assignment, 'right')
            value = self.expression(right)
            if left.type == 'identifier':
                binding = self.locals.get(_text(left))
                _require(binding is not None and binding[1] == value['type'], 'Assignment local/type is unresolved.', node)
                return self.node('assign_local', node, name=binding[0], value=value)
            if left.type == 'member_access_expression' and _text(_field(left, 'expression')) == 'Dts' and _text(_field(left, 'name')) == 'TaskResult':
                _require(value['op'] == 'literal' and value['type'] == 'int32' and value['value'] == self.spec['runtime']['success_value'],
                         'Only the explicitly bound literal success result is supported.', node)
                return self.node('task_result', node, value=value)
            key, dtype = self.variable(left, 'WRITE')
            _require(dtype == value['type'], 'Variable assignment type mismatch.', node)
            return self.node('assign_variable', node, name=key, value=value)
        if kind == 'if_statement':
            condition = self.expression(_field(node, 'condition'))
            _require(condition['type'] == 'bool', 'Condition must be boolean.', node)
            consequence = _field(node, 'consequence')
            alternative = _field(node, 'alternative')
            _require(consequence.type == 'block' and (alternative is None or alternative.type == 'block'),
                     'If branches must be explicit blocks.', node)
            return self.node('if', node, condition=condition, then=self.statement(consequence),
                             otherwise=self.statement(alternative) if alternative else None)
        raise UnsupportedCSharp('Unsupported statement: ' + kind, node)


def lower_main(source: str, spec: dict, *, file_id: str, locator: str) -> dict:
    """Lower one complete compilation unit. Bindings are validated by the caller."""
    syntax = parse_csharp(source, file_id=file_id, locator=locator)
    _require(syntax['status'] == 'PARSED', 'Complete-file grammar parsing did not succeed.')
    _require(not any(k.startswith('preproc_') for k in syntax['construct_counts']), 'Preprocessor behavior requires compilation-context review.')
    root = parse_tree(source).root_node
    items = _children(root)
    for node in items:
        if node.type == 'using_directive':
            _require(re.fullmatch(r'using\s+(?:System|Microsoft\.SqlServer\.Dts\.Tasks\.ScriptTask)\s*;', _text(node)) is not None,
                     'Unresolved using/alias/static import.', node)
    items = [n for n in items if n.type != 'using_directive']
    namespace = ''
    _require(len(items) == 1, 'Exactly one class/namespace compilation unit is required.', root)
    if items[0].type == 'namespace_declaration':
        namespace = _text(_field(items[0], 'name')) + '.'
        items = _children(_field(items[0], 'body'))
    _require(len(items) == 1 and items[0].type == 'class_declaration', 'Only one complete class is supported.', root)
    cls = items[0]
    for child in _children(cls):
        _require(child.type in {'modifier', 'identifier', 'base_list', 'declaration_list'} and
                 (child.type != 'modifier' or _text(child) == 'public'), 'Class features/partial definitions require review.', child)
    base = [n for n in _children(cls) if n.type == 'base_list']
    _require(len(base) == 1 and re.sub(r'\s+', '', _text(base[0])) == ':' + BASE_TYPE,
             'A fully qualified, explicitly bound SSIS ScriptTask base type is required.', cls)
    members = _children(_field(cls, 'body'))
    _require(len(members) == 1 and members[0].type == 'method_declaration',
             'Extra methods, fields, constructors, enums or nested types require review.', cls)
    method = members[0]
    for child in _children(method):
        _require(child.type in {'modifier', 'predefined_type', 'identifier', 'parameter_list', 'block'} and
                 (child.type != 'modifier' or _text(child) == 'public'), 'Method modifiers/features require review.', child)
    entry = namespace + _text(_field(cls, 'name')) + '.' + _text(_field(method, 'name'))
    _require(entry == spec['entry_point'] and _text(_field(method, 'name')) == 'Main'
             and _text(_field(method, 'returns')) == 'void' and not _children(_field(method, 'parameters')),
             'Entry point must match the supplied public void Main() binding.', method)
    _require(any(n.type == 'modifier' and _text(n) == 'public' for n in _children(method)), 'Main must be public.', method)
    lowerer = _Lowerer(spec)
    program = lowerer.statement(_field(method, 'body'))
    statements = program['statements']
    _require(statements and statements[-1]['op'] == 'task_result', 'Main must end with the supplied literal success result.', method)
    def count_results(value):
        if isinstance(value, dict):
            return int(value.get('op') == 'task_result') + sum(count_results(v) for v in value.values())
        if isinstance(value, list):
            return sum(count_results(v) for v in value)
        return 0
    _require(count_results(program) == 1, 'Early/conditional task results require control-flow review.', method)
    return {'program_version': '1.0', 'capability': 'SSIS_PURE_MAIN_V1', 'source_sha256': syntax['source_sha256'],
            'semantic_status': 'NOT_VERIFIED', 'entry_point': entry, 'program': program,
            'variables': {key: spec['variables'][key] for key in sorted(lowerer.used_variables)},
            'runtime': spec['runtime'], 'evidence': syntax['evidence']}
