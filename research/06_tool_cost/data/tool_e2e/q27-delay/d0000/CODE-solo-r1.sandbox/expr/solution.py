"""Arithmetic expression evaluator (tokenizer + recursive-descent parser)."""

import re

_TOKEN_RE = re.compile(
    r"(?:"
    r"(?P<number>\d+(?:\.\d*)?|\.\d+)"
    r"|(?P<op>\*\*|//|[+\-*/%(),])"
    r"|(?P<name>[A-Za-z_][A-Za-z_0-9]*)"
    r")"
)

_FUNCTIONS = {"min", "max", "abs"}


def _tokenize(text):
    tokens = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = _TOKEN_RE.match(text, pos)
        if not match or match.end() == pos:
            raise ValueError(f"unexpected character at position {pos}")
        pos = match.end()
        if match.group("number") is not None:
            raw = match.group("number")
            tokens.append(("num", float(raw) if "." in raw else int(raw)))
        elif match.group("op") is not None:
            tokens.append(("op", match.group("op")))
        else:
            tokens.append(("name", match.group("name")))
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
        if kind == "op" and value == op:
            self._pos += 1
            return
        raise ValueError(f"expected {op!r}")

    def parse(self):
        value = self._expr()
        if self._pos != len(self._tokens):
            raise ValueError(f"unexpected token {self._tokens[self._pos][1]!r}")
        return value

    # expr := term (('+' | '-') term)*
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

    # term := unary (('*' | '/' | '//' | '%') unary)*
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

    # unary := ('+' | '-') unary | power
    def _unary(self):
        kind, op = self._peek()
        if kind == "op" and op in ("+", "-"):
            self._pos += 1
            value = self._unary()
            return -value if op == "-" else value
        return self._power()

    # power := atom ('**' unary)?      (right-associative)
    def _power(self):
        base = self._atom()
        kind, op = self._peek()
        if kind == "op" and op == "**":
            self._pos += 1
            exponent = self._unary()
            return base ** exponent
        return base

    # atom := number | '(' expr ')' | funcall
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
            return self._call(value)
        if kind is None:
            raise ValueError("unexpected end of input")
        raise ValueError(f"unexpected token {value!r}")

    def _call(self, name):
        self._pos += 1
        self._expect_op("(")
        args = []
        if self._peek() != ("op", ")"):
            args.append(self._expr())
            while self._peek() == ("op", ","):
                self._pos += 1
                args.append(self._expr())
        self._expect_op(")")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs takes exactly one argument")
            return abs(args[0])
        if name in ("min", "max"):
            if not args:
                raise ValueError(f"{name} takes at least one argument")
            return (min if name == "min" else max)(args)
        raise ValueError(f"unknown function {name!r}")


def evaluate(text: str):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    tokens = _tokenize(text)
    return _Parser(tokens).parse()
