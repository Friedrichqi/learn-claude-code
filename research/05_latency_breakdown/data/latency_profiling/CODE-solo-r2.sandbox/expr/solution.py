"""Recursive-descent evaluator for arithmetic expressions."""

import re

_NUMBER_RE = re.compile(r"\d+\.\d*|\.\d+|\d+")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_FUNCTIONS = ("min", "max", "abs")


def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or ch == ".":
            match = _NUMBER_RE.match(text, i)
            if match is None:
                raise ValueError(f"malformed number at position {i}")
            lexeme = match.group(0)
            tokens.append(("num", float(lexeme) if "." in lexeme else int(lexeme)))
            i = match.end()
            continue
        if ch.isalpha() or ch == "_":
            match = _NAME_RE.match(text, i)
            tokens.append(("name", match.group(0)))
            i = match.end()
            continue
        if text.startswith("**", i) or text.startswith("//", i):
            tokens.append(("op", text[i:i + 2]))
            i += 2
            continue
        if ch in "+-*/%(),":
            tokens.append(("op", ch))
            i += 1
            continue
        raise ValueError(f"unknown character {ch!r} at position {i}")
    return tokens


class _Parser:
    def __init__(self, tokens):
        self._tokens = tokens
        self._pos = 0

    def _peek(self):
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _next(self):
        token = self._peek()
        if token is None:
            raise ValueError("unexpected end of input")
        self._pos += 1
        return token

    def _expect(self, op):
        if self._peek() != ("op", op):
            raise ValueError(f"expected {op!r}")
        self._pos += 1

    def parse(self):
        value = self._expr()
        if self._pos != len(self._tokens):
            raise ValueError("unexpected token after expression")
        return value

    def _expr(self):
        value = self._term()
        while self._peek() in (("op", "+"), ("op", "-")):
            op = self._next()[1]
            rhs = self._term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def _term(self):
        value = self._factor()
        while self._peek() in (("op", "*"), ("op", "/"), ("op", "//"), ("op", "%")):
            op = self._next()[1]
            rhs = self._factor()
            if op == "*":
                value = value * rhs
            elif op == "/":
                value = value / rhs
            elif op == "//":
                value = value // rhs
            else:
                value = value % rhs
        return value

    def _factor(self):
        token = self._peek()
        if token in (("op", "-"), ("op", "+")):
            op = self._next()[1]
            value = self._factor()
            return -value if op == "-" else value
        return self._power()

    def _power(self):
        base = self._atom()
        if self._peek() == ("op", "**"):
            self._next()
            return base ** self._factor()  # right-associative, unary allowed
        return base

    def _atom(self):
        token = self._next()
        kind, payload = token
        if kind == "num":
            return payload
        if kind == "name":
            if payload not in _FUNCTIONS:
                raise ValueError(f"unknown name {payload!r}")
            self._expect("(")
            args = [self._expr()]
            while self._peek() == ("op", ","):
                self._next()
                args.append(self._expr())
            self._expect(")")
            if payload == "abs":
                if len(args) != 1:
                    raise ValueError("abs() takes exactly one argument")
                return abs(args[0])
            if payload == "min":
                return min(args)
            return max(args)
        if token == ("op", "("):
            value = self._expr()
            self._expect(")")
            return value
        raise ValueError(f"unexpected token {payload!r}")


def evaluate(text):
    """Parse and evaluate an arithmetic expression string."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty input")
    return _Parser(_tokenize(text)).parse()
