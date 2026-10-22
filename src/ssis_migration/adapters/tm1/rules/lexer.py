"""Plain rules lexer using the shared bounded TM1 token mechanics."""
from ..syntax import TM1SyntaxError as RuleSyntaxError, Token, tokenize_tm1


def tokenize(source):
    return tokenize_tm1(source, label='rules')
