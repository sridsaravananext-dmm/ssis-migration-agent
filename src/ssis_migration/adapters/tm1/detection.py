"""Conservative TM1 signature hints; not a rules or TurboIntegrator parser."""
from __future__ import annotations


def code_for_signature_hints(text: str) -> str:
    """Mask comments and quoted text so their contents cannot confirm TM1.

    This is detection only. Unclosed comments/quotes mask the rest of the input;
    no successful source parse or missing export version is inferred.
    """
    chars = list(text)
    i = 0
    while i < len(text):
        start = i
        if text.startswith(('--', '//'), i) or text[i] == '#':
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = len(text) if end < 0 else end + 2
        elif text[i] in "['\"":
            closing = ']' if text[i] == '[' else text[i]
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                elif text[i] == closing:
                    if i + 1 < len(text) and text[i + 1] == closing:
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
        else:
            i += 1
            continue
        for j in range(start, min(i, len(chars))):
            if chars[j] not in '\r\n':
                chars[j] = ' '
    return ''.join(chars)
