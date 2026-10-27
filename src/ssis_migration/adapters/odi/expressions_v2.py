"""Native date/window syntax without selecting engine defaults or date rules."""
from __future__ import annotations

from collections import Counter
import copy
import re

from ssis_migration.schemas import validate_document
from . import expressions as v1

PROFILE = 'odi_native_expression_syntax_v2'
MAX_WINDOW_KEYS = 16
MAX_FRAME_BOUND = 1_000_000
MAX_FORMAT_TOKEN = 256


def _tokens(text, *, references):
    pattern = re.compile((r'@\{R[0-9]+\}|' if references else '')
                         + r"\s+|'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?|[0-9]+|[(),]")
    result = []; position = 0
    while position < len(text):
        match = pattern.match(text, position)
        if match is None: raise v1.ExpressionReviewRequired('Unsupported extended native syntax at offset '+str(position)+'.')
        if not match[0].isspace(): result.append(match[0])
        position = match.end()
        if len(result) > v1.MAX_TOKENS: raise v1.ExpressionReviewRequired('Extended native expression token limit exceeded.')
    return result


class ExtendedParser:
    def __init__(self, tokens, bindings):
        self.tokens = tokens; self.bindings = bindings; self.position = 0; self.used = set()

    def take(self, token):
        if self.position < len(self.tokens) and self.tokens[self.position].upper() == token:
            self.position += 1; return True
        return False

    def require(self, token):
        if not self.take(token): raise v1.ExpressionReviewRequired('Expected native token '+token+'.')

    def reference(self):
        if self.position >= len(self.tokens): raise v1.ExpressionReviewRequired('Missing native attribute reference.')
        match = v1._REFERENCE.fullmatch(self.tokens[self.position]); self.position += 1
        if not match or match[1] not in self.bindings:
            raise v1.ExpressionReviewRequired('Extended syntax requires an exact declared native reference.')
        self.used.add(match[1])
        return {'kind': 'ATTRIBUTE_REFERENCE', 'reference_key': match[1],
                'attribute_node_id': self.bindings[match[1]]['attribute_node_id']}

    def integer(self):
        if self.position >= len(self.tokens) or re.fullmatch(r'[0-9]+', self.tokens[self.position]) is None:
            raise v1.ExpressionReviewRequired('Only explicit nonnegative integer tokens are supported here.')
        token = self.tokens[self.position]; self.position += 1
        if len(token) > 7 or int(token) > MAX_FRAME_BOUND:
            raise v1.ExpressionReviewRequired('Native integer token exceeds the bounded literal profile.')
        return {'kind': 'INTEGER_LITERAL', 'token': token}

    def reference_list(self):
        result = [self.reference()]
        while self.take(','):
            result.append(self.reference())
            if len(result) > MAX_WINDOW_KEYS: raise v1.ExpressionReviewRequired('Native window key count limit exceeded.')
        return result

    def parse(self):
        if self.take('TO_DATE'):
            self.require('('); self.require('SUBSTR'); self.require('(')
            reference = self.reference(); self.require(','); start = self.integer(); self.require(','); length = self.integer()
            self.require(')'); self.require(',')
            if self.position >= len(self.tokens): raise v1.ExpressionReviewRequired('Missing native date-format token.')
            token = self.tokens[self.position]; self.position += 1
            if len(token) > MAX_FORMAT_TOKEN or re.fullmatch(r"'(?:[^']|'')*'", token) is None:
                raise v1.ExpressionReviewRequired('Native date format must remain a bounded original string-literal token.')
            self.require(')')
            result = {'kind': 'DATE_CONVERSION', 'function': 'TO_DATE',
                'substring': {'function': 'SUBSTR', 'argument': reference, 'start': start, 'length': length},
                'format': {'kind': 'STRING_LITERAL', 'token': token}}
        else:
            if self.position >= len(self.tokens) or self.tokens[self.position].upper() not in {'LAG', 'MAX', 'SUM'}:
                raise v1.ExpressionReviewRequired('Native form is outside the bounded date/window profile.')
            function = self.tokens[self.position].upper(); self.position += 1
            self.require('('); argument = self.reference(); self.require(')'); self.require('OVER'); self.require('(')
            self.require('PARTITION'); self.require('BY'); partitions = self.reference_list()
            order = []
            if self.take('ORDER'):
                self.require('BY')
                order = [{'expression': ref, 'direction': None, 'null_order': None} for ref in self.reference_list()]
            frame = None
            if self.take('ROWS'):
                if not order: raise v1.ExpressionReviewRequired('This bounded ROWS profile requires declared ordering.')
                offset = self.integer(); self.require('PRECEDING')
                frame = {'unit': 'ROWS', 'syntax_form': 'SINGLE_BOUND', 'start': offset, 'start_direction': 'PRECEDING', 'end': None}
            self.require(')')
            if function == 'LAG' and (not order or frame is not None):
                raise v1.ExpressionReviewRequired('This LAG profile requires declared ordering and no explicit frame.')
            result = {'kind': 'WINDOW', 'function': function, 'argument': argument, 'partition_by': partitions,
                      'order_by': order, 'frame': frame, 'offset_literal': None, 'default_expression': None}
        if self.position != len(self.tokens): raise v1.ExpressionReviewRequired('Unparsed trailing date/window syntax was retained for review.')
        if self.used != set(self.bindings): raise v1.ExpressionReviewRequired('Extended expression references are missing or unused; none are discarded.')
        return result


def analyze_expression(index, expression):
    # Frozen v1 supplies exact evidence, bindings and all original declarations.
    result = copy.deepcopy(v1.analyze_expression(index, expression)); result['profile'] = PROFILE
    if result['status'] == 'REVIEW_REQUIRED':
        try:
            fields = result['source_fields']; text = fields['Txt']; parsed = fields['ParsedTxt']
            if (fields['Text'] != 'null' or fields['ParsedText'] != 'null' or fields['IsParsed'] != '1' or fields['TextOnly'] != '0'
                    or not isinstance(text, str) or not isinstance(parsed, str) or not text or not parsed
                    or max(len(text), len(parsed)) > v1.MAX_SYNTAX_CHARS):
                raise v1.ExpressionReviewRequired('Native declaration flags/texts are outside the extended profile; no precedence was selected.')
            bindings = result['bindings']; keys = Counter(b['reference_key'] for b in bindings)
            if (any(b['resolution'] != 'BOUND' for b in bindings)
                    or any(count != 1 or not isinstance(key, str) or re.fullmatch(r'R[0-9]+', key) is None for key, count in keys.items())):
                raise v1.ExpressionReviewRequired('Extended syntax requires unique, exactly scoped native reference bindings.')
            by_key = {b['reference_key']: b for b in bindings}; tokens = _tokens(parsed, references=True)
            ast = ExtendedParser(tokens, by_key).parse()
            expanded = [by_key[v1._REFERENCE.fullmatch(token)[1]]['reference_text'] if v1._REFERENCE.fullmatch(token) else token for token in tokens]
            if expanded != _tokens(text, references=False):
                raise v1.ExpressionReviewRequired('Date/window Txt and ParsedTxt declarations disagree; no winner was chosen.')
            result.update(status='PARSED', ast=ast)
            result['unresolved_items'] = [
                'Syntax only: source validity flags, types, null/coercion and runtime behavior remain unverified.',
                'Omitted window direction, null ordering, LAG arguments and frame endpoints remain undeclared; ordering uniqueness is not established.'
                if ast['kind'] == 'WINDOW' else
                'Source date dialect/calendar, malformed-input policy, substring indexing and target format rules are unconfirmed; original integer/format tokens are not rewritten.',
            ]
        except v1.ExpressionReviewRequired as exc:
            result['unresolved_items'].append(str(exc))
    validate_document(result, 'odi_expression_analysis_v2')
    return result
