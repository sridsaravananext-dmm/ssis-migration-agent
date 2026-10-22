"""Bounded MDX lexical analysis, separate from SQL and TM1 rules/TI."""
from __future__ import annotations

from dataclasses import dataclass
import re

MAX_BYTES = 1_048_576
MAX_TOKENS = 50_000
_NAME = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
_NUMBER = re.compile(r'[0-9]+(?:\.[0-9]+)?')


class MDXError(ValueError):
    def __init__(self, code, message, start, end, *, status='INVALID'):
        super().__init__(message)
        self.code, self.start, self.end, self.status = code, start, end, status


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


def tokenize(source: str) -> list[Token]:
    if len(source.encode('utf-8')) > MAX_BYTES:
        raise MDXError('SOURCE_LIMIT', 'MDX exceeds the 1 MiB limit; no prefix was accepted.', 0, len(source), status='LIMIT_EXCEEDED')
    tokens = []
    i = 0
    while i < len(source):
        start, char = i, source[i]
        if char.isspace():
            i += 1
            continue
        if source.startswith(('--', '//'), i):
            end = source.find('\n', i)
            i = len(source) if end < 0 else end
            continue
        if source.startswith('/*', i):
            end = source.find('*/', i + 2)
            if end < 0:
                raise MDXError('UNTERMINATED_COMMENT', 'Unterminated MDX block comment.', start, len(source))
            if '/*' in source[i + 2:end]:
                raise MDXError('NESTED_COMMENT', 'Nested comment syntax requires separate dialect review.', start, end + 2, status='UNSUPPORTED')
            i = end + 2
            continue
        if char in "['\"":
            closing = ']' if char == '[' else char
            kind = 'IDENTIFIER' if char == '[' else 'STRING'
            value = []
            i += 1
            while i < len(source):
                if source[i] == closing:
                    if i + 1 < len(source) and source[i + 1] == closing:
                        value.append(closing)
                        i += 2
                        continue
                    i += 1
                    break
                if source[i] == '\\' and kind == 'STRING':
                    raise MDXError('STRING_ESCAPE', 'Backslash string escapes are outside the supported MDX subset.', start, i + 1, status='UNSUPPORTED')
                value.append(source[i])
                i += 1
            else:
                raise MDXError('UNTERMINATED_LITERAL', 'Unterminated MDX identifier/string.', start, len(source))
            if kind == 'IDENTIFIER' and not value:
                raise MDXError('EMPTY_IDENTIFIER', 'MDX identifier cannot be empty.', start, i)
            token = Token(kind, ''.join(value), start, i)
        elif (char.isascii() and char.isalpha()) or char == '_':
            match = _NAME.match(source, i)
            i = match.end()
            token = Token('NAME', match[0], start, i)
        elif char.isascii() and char.isdigit():
            match = _NUMBER.match(source, i)
            i = match.end()
            token = Token('NUMBER', match[0], start, i)
        elif char in '{}(),.:;*&+-':
            i += 1
            token = Token(char, char, start, i)
        else:
            raise MDXError('UNSUPPORTED_TOKEN', 'Token is outside the bounded MDX grammar: ' + repr(char), start, start + 1, status='UNSUPPORTED')
        tokens.append(token)
        if len(tokens) > MAX_TOKENS:
            raise MDXError('TOKEN_LIMIT', 'MDX token limit exceeded; no prefix was accepted.', 0, len(source), status='LIMIT_EXCEEDED')
    tokens.append(Token('EOF', '', len(source), len(source)))
    return tokens
