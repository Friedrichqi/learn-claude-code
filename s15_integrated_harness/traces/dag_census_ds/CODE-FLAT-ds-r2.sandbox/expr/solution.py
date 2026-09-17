"""Arithmetic expression evaluator.

Implements ``evaluate(text)`` using a hand written tokenizer and a
recursive-descent parser.  No use of ``eval``/``exec``/``compile``/``ast``.
"""

import re

__all__ = ["evaluate"]

_NUMBER_RE = re.compile(r"\d+\.\d*|\.\d+|\d+")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

# Multi-character operators must be tried before single-character ones.
_OPERATORS = ("**", "//", "+", "-", "*", "/", "%")

_PUNCT = {"(": "LPAREN", ")": "RPAREN", ",": "COMMA"}

_TWO_ARG_FUNCS = {"min": lambda a: min(a), "max": lambda a: max(a)}
_ONE_ARG_FUNCS = {"abs": abs}


class _Token:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind, value, pos):
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self):  # pragma: no cover - debugging helper
        return "_Token(%r, %r, %r)" % (self.kind, self.value, self.pos)


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in _PUNCT:
            tokens.append(_Token(_PUNCT[ch], ch, i))
            i += 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            match = _NUMBER_RE.match(text, i)
            # The regex always matches when the guard above is true.
            raw = match.group(0)
            if "." in raw:
                value = float(raw)
            else:
                value = int(raw)
            tokens.append(_Token("NUM", value, i))
            i = match.end()
            continue
        if ch.isalpha() or ch == "_":
            match = _IDENT_RE.match(text, i)
            tokens.append(_Token("IDENT", match.group(0), i))
            i = match.end()
            continue
        for op in _OPERATORS:
            if text.startswith(op, i):
                tokens.append(_Token("OP", op, i))
                i += len(op)
                break
        else:
            raise ValueError("unexpected character %r at position %d" % (ch, i))
    tokens.append(_Token("EOF", None, n))
    return tokens


class _Parser:
    def __init__(self, text):
        self.tokens = _tokenize(text)
        self.index = 0

    # -- token helpers -------------------------------------------------
    @property
    def current(self):
        return self.tokens[self.index]

    def advance(self):
        token = self.tokens[self.index]
        self.index += 1
        return token

    def at_op(self, *values):
        tok = self.current
        return tok.kind == "OP" and tok.value in values

    # -- grammar -------------------------------------------------------
    def parse(self):
        if self.current.kind == "EOF":
            raise ValueError("empty expression")
        value = self.expression()
        if self.current.kind != "EOF":
            raise ValueError(
                "unexpected token %r at position %d" % (self.current.value, self.current.pos)
            )
        return value

    def expression(self):
        value = self.term()
        while self.at_op("+", "-"):
            op = self.advance().value
            right = self.term()
            value = value + right if op == "+" else value - right
        return value

    def term(self):
        value = self.unary()
        while self.at_op("*", "/", "//", "%"):
            op = self.advance().value
            right = self.unary()
            if op == "*":
                value = value * right
            elif op == "/":
                value = value / right
            elif op == "//":
                value = value // right
            else:
                value = value % right
        return value

    def unary(self):
        if self.at_op("+", "-"):
            op = self.advance().value
            operand = self.unary()
            return operand if op == "+" else -operand
        return self.power()

    def power(self):
        base = self.primary()
        if self.at_op("**"):
            self.advance()
            # Right-associative, and the exponent may carry a unary sign.
            exponent = self.unary()
            return base ** exponent
        return base

    def primary(self):
        tok = self.current
        if tok.kind == "NUM":
            self.advance()
            return tok.value
        if tok.kind == "LPAREN":
            self.advance()
            value = self.expression()
            if self.current.kind != "RPAREN":
                raise ValueError("expected ')' at position %d" % self.current.pos)
            self.advance()
            return value
        if tok.kind == "IDENT":
            return self.function_call()
        raise ValueError("unexpected token %r at position %d" % (tok.value, tok.pos))

    def function_call(self):
        name_tok = self.advance()
        name = name_tok.value
        if name not in _ONE_ARG_FUNCS and name not in _TWO_ARG_FUNCS:
            raise ValueError("unknown name %r at position %d" % (name, name_tok.pos))
        if self.current.kind != "LPAREN":
            raise ValueError("expected '(' after %r at position %d" % (name, self.current.pos))
        self.advance()
        args = []
        if self.current.kind == "RPAREN":
            raise ValueError("%s() requires at least one argument" % name)
        while True:
            args.append(self.expression())
            if self.current.kind == "COMMA":
                self.advance()
                continue
            break
        if self.current.kind != "RPAREN":
            raise ValueError("expected ')' at position %d" % self.current.pos)
        self.advance()
        if name in _ONE_ARG_FUNCS:
            if len(args) != 1:
                raise ValueError("%s() takes exactly one argument" % name)
            return _ONE_ARG_FUNCS[name](args[0])
        return _TWO_ARG_FUNCS[name](args)


def evaluate(text):
    """Parse and evaluate ``text``, returning an ``int`` or ``float``."""
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    return _Parser(text).parse()
