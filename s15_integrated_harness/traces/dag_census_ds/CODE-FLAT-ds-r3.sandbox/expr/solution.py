"""Arithmetic expression evaluator.

Implements ``evaluate(text)`` using a hand-written tokenizer and a
recursive-descent (precedence-climbing) parser.  Numeric literals without a
decimal point produce ``int`` values, literals with a decimal point produce
``float`` values.  No use of ``eval``/``exec``/``compile``/``ast``.
"""

import re

__all__ = ["evaluate"]


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d+\.\d+|\.\d+|\d+")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WS_RE = re.compile(r"[ \t\r\n\f\v]+")

# Multi-character operators must be tried before single-character ones.
_OPERATORS = ("**", "//", "+", "-", "*", "/", "%", "(", ")", ",")

_FUNCTIONS = {"min", "max", "abs"}


class _Token:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind, value, pos):
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self):  # pragma: no cover - debugging helper
        return "_Token(%r, %r, %r)" % (self.kind, self.value, self.pos)


def _tokenize(text):
    """Return a list of tokens (without an EOF marker) for *text*."""
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n\f\v":
            i += 1
            continue
        m = _NUMBER_RE.match(text, i)
        if m is not None:
            lexeme = m.group(0)
            if "." in lexeme:
                value = float(lexeme)
            else:
                value = int(lexeme)
            tokens.append(_Token("number", value, i))
            i = m.end()
            continue
        m = _NAME_RE.match(text, i)
        if m is not None:
            tokens.append(_Token("name", m.group(0), i))
            i = m.end()
            continue
        for op in _OPERATORS:
            if text.startswith(op, i):
                tokens.append(_Token("op", op, i))
                i += len(op)
                break
        else:
            raise ValueError(
                "unexpected character %r at position %d" % (ch, i)
            )
    return tokens


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, tokens, text):
        self.tokens = tokens
        self.text = text
        self.index = 0

    # -- token helpers ------------------------------------------------------
    def _peek(self):
        if self.index < len(self.tokens):
            return self.tokens[self.index]
        return None

    def _advance(self):
        tok = self._peek()
        if tok is None:
            raise ValueError("unexpected end of expression: %r" % (self.text,))
        self.index += 1
        return tok

    def _accept_op(self, *ops):
        tok = self._peek()
        if tok is not None and tok.kind == "op" and tok.value in ops:
            self.index += 1
            return tok.value
        return None

    # -- grammar ------------------------------------------------------------
    def parse(self):
        if not self.tokens:
            raise ValueError("empty expression: %r" % (self.text,))
        value = self._expression()
        tok = self._peek()
        if tok is not None:
            raise ValueError(
                "unexpected token %r at position %d" % (tok.value, tok.pos)
            )
        return value

    def _expression(self):
        value = self._term()
        while True:
            op = self._accept_op("+", "-")
            if op is None:
                return value
            rhs = self._term()
            if op == "+":
                value = value + rhs
            else:
                value = value - rhs

    def _term(self):
        value = self._factor()
        while True:
            op = self._accept_op("*", "/", "//", "%")
            if op is None:
                return value
            rhs = self._factor()
            if op == "*":
                value = value * rhs
            elif op == "/":
                value = value / rhs
            elif op == "//":
                value = value // rhs
            else:
                value = value % rhs

    def _factor(self):
        """Unary +/- bind looser than ``**`` but tighter than ``* / // %``."""
        op = self._accept_op("+", "-")
        if op is not None:
            value = self._factor()
            if op == "-":
                return -value
            return +value
        return self._power()

    def _power(self):
        base = self._atom()
        if self._accept_op("**") is not None:
            # Right-associative; the exponent may itself carry a unary sign
            # (``2 ** -1``) so recurse through ``_factor``.
            exponent = self._factor()
            return base ** exponent
        return base

    def _atom(self):
        tok = self._peek()
        if tok is None:
            raise ValueError("unexpected end of expression: %r" % (self.text,))
        if tok.kind == "number":
            self.index += 1
            return tok.value
        if tok.kind == "name":
            return self._call(tok)
        if tok.kind == "op" and tok.value == "(":
            self.index += 1
            value = self._expression()
            if self._accept_op(")") is None:
                raise ValueError("missing closing parenthesis in %r" % (self.text,))
            return value
        raise ValueError(
            "unexpected token %r at position %d" % (tok.value, tok.pos)
        )

    def _call(self, tok):
        name = tok.value
        if name not in _FUNCTIONS:
            raise ValueError(
                "unknown name %r at position %d" % (name, tok.pos)
            )
        self.index += 1  # consume the name
        if self._accept_op("(") is None:
            raise ValueError(
                "expected '(' after %r at position %d" % (name, tok.pos)
            )
        args = []
        # A closing ')' immediately means zero arguments.
        if self._accept_op(")") is None:
            while True:
                args.append(self._expression())
                if self._accept_op(",") is not None:
                    continue
                if self._accept_op(")") is not None:
                    break
                raise ValueError(
                    "missing closing parenthesis in %r" % (self.text,)
                )
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs() takes exactly one argument")
            return abs(args[0])
        if not args:
            raise ValueError("%s() requires at least one argument" % name)
        if name == "min":
            return min(args)
        return max(args)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate(text):
    """Evaluate the arithmetic expression *text* and return int or float."""
    if not isinstance(text, str):
        raise ValueError("expression must be a string, got %r" % (type(text),))
    tokens = _tokenize(text)
    return _Parser(tokens, text).parse()
