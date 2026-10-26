"""Bounded PowerCenter expression syntax, separate from SQL and execution."""
from __future__ import annotations

import re


class ExpressionReviewRequired(ValueError):
    pass


def analyze_expression(text, ports, *, row_flags=False):
    """Recognize declared port refs, ISNULL, NOT, comparisons, AND/OR and DD_UPDATE.

    This produces syntax evidence only. No source or generated expression is
    evaluated, and no claim about vendor null/type/error semantics is made.
    """
    result = {'text': text, 'status': 'REVIEW_REQUIRED', 'ast': None,
              'semantic_status': 'NOT_VERIFIED', 'unresolved_items': []}
    try:
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ExpressionReviewRequired('Expression is absent or exceeds the 4096-character syntax limit.')
        tokens, offset = [], 0
        pattern = re.compile(r'\s+|!=|<>|<=|>=|[=<>(),]|[A-Za-z_][A-Za-z0-9_]*')
        while offset < len(text):
            match = pattern.match(text, offset)
            if match is None:
                raise ExpressionReviewRequired('Unsupported native expression syntax at offset '+str(offset)+'.')
            if not match[0].isspace():tokens.append(match[0])
            offset = match.end()
            if len(tokens) > 256:
                raise ExpressionReviewRequired('Expression exceeds the 256-token syntax limit.')
        index, depth = 0, 0

        def take(value):
            nonlocal index
            if index < len(tokens) and tokens[index].upper() == value:
                index += 1
                return True
            return False

        def atom():
            nonlocal index, depth
            depth += 1
            if depth > 32:raise ExpressionReviewRequired('Expression exceeds the syntax nesting limit.')
            try:
                if take('NOT'):
                    value = atom()
                    if value['kind'] not in {'ISNULL', 'COMPARE', 'AND', 'OR', 'NOT'}:
                        raise ExpressionReviewRequired('This syntax profile requires NOT on ISNULL or a parenthesized predicate.')
                    return {'kind': 'NOT', 'value': value}
                if take('('):
                    value = expression()
                    if not take(')'):raise ExpressionReviewRequired('Missing closing parenthesis.')
                    return value
                if index >= len(tokens):raise ExpressionReviewRequired('Missing expression operand.')
                name = tokens[index];index += 1
                if name.upper() == 'ISNULL' and take('('):
                    value = expression()
                    if not take(')'):raise ExpressionReviewRequired('ISNULL needs one complete argument.')
                    return {'kind': 'ISNULL', 'value': value}
                if row_flags and name == 'DD_UPDATE':
                    return {'kind': 'ROW_OPERATION_FLAG', 'name': name}
                matches = ports.get(name, [])
                if len(matches) != 1:
                    raise ExpressionReviewRequired('Port is absent or ambiguous in this transformation: '+name)
                return {'kind': 'PORT', 'name': name, 'field_node_id': matches[0]}
            finally:
                depth -= 1

        def comparison():
            nonlocal index
            value = atom()
            if index < len(tokens) and tokens[index] in {'=', '!=', '<>', '<', '>', '<=', '>='}:
                if value['kind'] == 'NOT':
                    raise ExpressionReviewRequired('Comparison after NOT requires a separately supported native expression profile.')
                operator = tokens[index];index += 1
                value = {'kind': 'COMPARE', 'operator': operator, 'left': value, 'right': atom()}
            return value

        def conjunction():
            value = comparison()
            while take('AND'):value = {'kind': 'AND', 'left': value, 'right': comparison()}
            return value

        def expression():
            value = conjunction()
            while take('OR'):value = {'kind': 'OR', 'left': value, 'right': conjunction()}
            return value

        ast = expression()
        if index != len(tokens):raise ExpressionReviewRequired('Unsupported or incomplete trailing expression syntax.')
        result.update(status='PARSED', ast=ast)
    except ExpressionReviewRequired as exc:
        result['unresolved_items'].append(str(exc))
    return result
