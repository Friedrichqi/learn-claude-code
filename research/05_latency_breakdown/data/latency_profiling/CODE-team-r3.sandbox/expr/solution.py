"""Arithmetic expression evaluator: tokenizer + recursive-descent parser.

Grammar (Python-like precedence, lowest to highest):
    additive       := multiplicative (('+' | '-') multiplicative)*
    multiplicative := unary (('*' | '/' | '//' | '%') unary)*
    unary          := ('+' | '-') unary | power
    power          := atom ('**' unary)?          # right-associative
    atom           := NUMBER | FUNC '(' args ')' | '(' additive ')'
"""

import re

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+\.\d*|\.\d+|\d+)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\*\*|//|[+\-*/%(),])
    """,
    re.VERBOSE,
)

_FUNCTIONS = ("min", "max", "abs")


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind, value):
        self.kind = kind
        self.value = value

    def __repr__(self):  # pragma: no cover - debugging aid only
        return "%s(%r)" % (self.kind, self.value)


def _tokenize(text):
    tokens = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise ValueError(
                "unexpected character %r at position %d" % (text[pos], pos)
            )
        pos = match.end()
        kind = match.lastgroup
        if kind == "ws":
            continue
        if kind == "number":
            lexeme = match.group("number")
            value = float(lexeme) if "." in lexeme else int(lexeme)
            tokens.append(_Token("number", value))
        elif kind == "name":
            tokens.append(_Token("name", match.group("name")))
        else:
            tokens.append(_Token("op", match.group("op")))
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
        if token is not None:
            self._pos += 1
        return token

    def _expect_op(self, op):
        token = self._peek()
        if token is None or token.kind != "op" or token.value != op:
            raise ValueError("expected %r, found %r" % (op, token))
        self._pos += 1

    def parse(self):
        if not self._tokens:
            raise ValueError("empty expression")
        result = self._additive()
        leftover = self._peek()
        if leftover is not None:
            raise ValueError("unexpected trailing token %r" % (leftover,))
        return result

    def _additive(self):
        value = self._multiplicative()
        while True:
            token = self._peek()
            if token is None or token.kind != "op" or token.value not in "+-":
                return value
            self._pos += 1
            right = self._multiplicative()
            value = value + right if token.value == "+" else value - right

    def _multiplicative(self):
        value = self._unary()
        while True:
            token = self._peek()
            if token is None or token.kind != "op":
                return value
            op = token.value
            if op == "*":
                self._pos += 1
                value = value * self._unary()
            elif op == "/":
                self._pos += 1
                value = value / self._unary()  # raises ZeroDivisionError
            elif op == "//":
                self._pos += 1
                value = value // self._unary()  # raises ZeroDivisionError
            elif op == "%":
                self._pos += 1
                value = value % self._unary()  # raises ZeroDivisionError
            else:
                return value

    def _unary(self):
        token = self._peek()
        if token is not None and token.kind == "op" and token.value in "+-":
            self._pos += 1
            value = self._unary()
            return -value if token.value == "-" else +value
        return self._power()

    def _power(self):
        base = self._atom()
        token = self._peek()
        if token is not None and token.kind == "op" and token.value == "**":
            self._pos += 1
            exponent = self._unary()  # right-associative, allows unary exponent
            return base ** exponent
        return base

    def _atom(self):
        token = self._next()
        if token is None:
            raise ValueError("unexpected end of expression")
        if token.kind == "number":
            return token.value
        if token.kind == "name":
            name = token.value
            if name not in _FUNCTIONS:
                raise ValueError("unknown function name %r" % (name,))
            self._expect_op("(")
            args = [self._additive()]
            while True:
                nxt = self._peek()
                if nxt is not None and nxt.kind == "op" and nxt.value == ",":
                    self._pos += 1
                    args.append(self._additive())
                else:
                    break
            self._expect_op(")")
            if name == "abs":
                if len(args) != 1:
                    raise ValueError("abs() takes exactly one argument")
                return abs(args[0])
            if not args:
                raise ValueError("%s() requires at least one argument" % (name,))
            return min(args) if name == "min" else max(args)
        if token.kind == "op" and token.value == "(":
            value = self._additive()
            self._expect_op(")")
            return value
        raise ValueError("unexpected token %r" % (token,))


def evaluate(text):
    """Parse and evaluate an arithmetic expression string to int or float."""
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    return _Parser(_tokenize(text)).parse()
