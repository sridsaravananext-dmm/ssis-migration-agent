"""Shared bounded lexical mechanics for explicitly selected TM1 text grammars."""
from __future__ import annotations

from dataclasses import dataclass
import re

_NUMBER = re.compile(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_NAME = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')


class TM1SyntaxError(ValueError):
    def __init__(self, code, message, start, end, status='INVALID'):
        super().__init__(message)
        self.code, self.start, self.end, self.status = code, start, end, status


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


def tokenize_tm1(source, *, label):
    if len(source.encode('utf-8')) > 1_048_576:
        raise TM1SyntaxError('SOURCE_LIMIT', label + ' source exceeds the 1 MiB parsing limit; no prefix was accepted.', 0, len(source), 'LIMIT_EXCEEDED')
    result, i = [], 0
    while i < len(source):
        start, char = i, source[i]
        if char.isspace():
            i += 1
            continue
        if char == '#':
            end = source.find('\n', i)
            end = len(source) if end < 0 else end
            if re.match(r'#\s*(?:include|import)\b', source[i:end], re.I):
                raise TM1SyntaxError('UNSUPPORTED_INCLUDE_DIRECTIVE', 'Include/import-like directives require supplied files and format verification; they were not treated as comments.', i, end, 'UNSUPPORTED')
            i = end
            continue
        if char == "'":
            value = []
            i += 1
            while i < len(source):
                if source[i] == "'":
                    if i + 1 < len(source) and source[i+1] == "'":
                        value.append("'"); i += 2
                    else:
                        i += 1
                        break
                elif source[i] in '\r\n':
                    raise TM1SyntaxError('UNSUPPORTED_MULTILINE_STRING', 'Multiline strings require a later grammar increment.', start, i, 'UNSUPPORTED')
                else:
                    value.append(source[i]); i += 1
            else:
                raise TM1SyntaxError('UNTERMINATED_STRING', 'Unterminated TM1 string/selector.', start, len(source))
            token = Token('STRING', ''.join(value), start, i)
        elif char.isascii() and (char.isalpha() or char == '_'):
            match = _NAME.match(source, i); i = match.end()
            token = Token('NAME', match[0], start, i)
        elif (char.isascii() and char.isdigit()) or (char == '.' and i + 1 < len(source) and source[i+1].isascii() and source[i+1].isdigit()):
            match = _NUMBER.match(source, i); i = match.end()
            token = Token('NUMBER', match[0], start, i)
        else:
            operator = next((op for op in ('@<>','@>=','@<=','=>','>=','<=','<>','@=','@>','@<') if source.startswith(op, i)), None)
            if operator:
                i += len(operator); token = Token(operator, operator, start, i)
            elif char in '[](),;:=!+-*/\\^&%~|<>':
                i += 1; token = Token(char, char, start, i)
            else:
                raise TM1SyntaxError('UNSUPPORTED_TOKEN', 'Token is outside the bounded ' + label + ' grammar: ' + repr(char), start, start+1, 'UNSUPPORTED')
        result.append(token)
        if len(result) > 50_000:
            raise TM1SyntaxError('TOKEN_LIMIT', label + ' token limit exceeded; no prefix was accepted.', 0, len(source), 'LIMIT_EXCEEDED')
    result.append(Token('EOF', '', len(source), len(source)))
    return result
