"""Arithmetic expression evaluator (tokenizer + recursive-descent parser)."""

import re

_TOKEN_RE = re.compile(
    r"\s+"
    r"|\d+\.\d+|\.\d+|\d+"
    r"|[A-Za-z_][A-Za-z0-9_]*"
    r"|//|\*\*|[+\-*/%(),]"
)

_FUNCTIONS = {"min", "max", "abs"}


def _tokenize(text):
    tokens = []
    pos = 0
    length = len(text)
    while pos < length:
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise ValueError(f"invalid character {text[pos]!r}")
        pos = match.end()
        token = match.group(0)
        if token.isspace():
            continue
        if token[0].isdigit() or token.startswith("."):
            value = int(token) if re.fullmatch(r"\d+", token) else float(token)
            tokens.append(("num", value))
        elif token[0].isalpha() or token[0] == "_":
            tokens.append(("name", token))
        else:
            tokens.append(("op", token))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self._tokens = tokens
        self._pos = 0

    def _peek(self):
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return (None, None)

    def _expect_op(self, op):
        kind, value = self._peek()
        if kind != "op" or value != op:
            raise ValueError(f"expected {op!r}")
        self._pos += 1

    def parse(self):
        value = self._expr()
        if self._pos != len(self._tokens):
            raise ValueError("unexpected token after end of expression")
        return value

    def _expr(self):
        value = self._term()
        while True:
            kind, op = self._peek()
            if kind == "op" and op in ("+", "-"):
                self._pos += 1
                rhs = self._term()
                value = value + rhs if op == "+" else value - rhs
            else:
                return value

    def _term(self):
        value = self._unary()
        while True:
            kind, op = self._peek()
            if kind == "op" and op in ("*", "/", "//", "%"):
                self._pos += 1
                rhs = self._unary()
                if op == "*":
                    value = value * rhs
                elif op == "/":
                    value = value / rhs
                elif op == "//":
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

    def _unary(self):
        kind, op = self._peek()
        if kind == "op" and op in ("+", "-"):
            self._pos += 1
            operand = self._unary()
            return -operand if op == "-" else +operand
        return self._power()

    def _power(self):
        base = self._atom()
        kind, op = self._peek()
        if kind == "op" and op == "**":
            self._pos += 1
            exponent = self._unary()  # right-associative; unary allowed on RHS
            return base ** exponent
        return base

    def _atom(self):
        kind, value = self._peek()
        if kind == "num":
            self._pos += 1
            return value
        if kind == "op" and value == "(":
            self._pos += 1
            inner = self._expr()
            self._expect_op(")")
            return inner
        if kind == "name":
            if value not in _FUNCTIONS:
                raise ValueError(f"unknown name {value!r}")
            self._pos += 1
            self._expect_op("(")
            args = [self._expr()]
            while True:
                kind, op = self._peek()
                if kind == "op" and op == ",":
                    self._pos += 1
                    args.append(self._expr())
                else:
                    break
            self._expect_op(")")
            if value == "abs":
                if len(args) != 1:
                    raise ValueError("abs takes exactly one argument")
                return abs(args[0])
            if value == "min":
                if len(args) == 1:
                    return args[0]
                return min(*args)
            if len(args) == 1:
                return args[0]
            return max(*args)
        raise ValueError("unexpected token")


def evaluate(text: str):
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    return _Parser(tokens).parse()
