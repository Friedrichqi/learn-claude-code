"""Arithmetic expression evaluator: tokenizer + recursive-descent parser."""

import re

_TOKEN_RE = re.compile(
    r"\s*(?:(?P<number>\d+\.\d+|\d+|\.\d+)"
    r"|(?P<name>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<op>\*\*|//|[+\-*/%(),]))"
)

# name -> (min_args, max_args); max_args None means unbounded
_FUNCTIONS = {
    "min": (1, None),
    "max": (1, None),
    "abs": (1, 1),
}


def _tokenize(text):
    tokens = []
    pos = 0
    length = len(text)
    while pos < length:
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            if text[pos:].strip() == "":
                break
            raise ValueError(
                "unexpected character at position %d: %r" % (pos, text[pos])
            )
        pos = match.end()
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def advance(self):
        token = self.peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        self.pos += 1
        return token

    def expect(self, kind, value):
        token = self.advance()
        if token[0] != kind or token[1] != value:
            raise ValueError("expected %r but got %r" % (value, token[1]))
        return token

    def parse(self):
        if not self.tokens:
            raise ValueError("empty expression")
        value = self.expression()
        if self.pos != len(self.tokens):
            raise ValueError("unexpected token %r" % (self.peek()[1],))
        return value

    def expression(self):
        value = self.term()
        while True:
            token = self.peek()
            if token is not None and token[0] == "op" and token[1] in ("+", "-"):
                self.pos += 1
                rhs = self.term()
                value = value + rhs if token[1] == "+" else value - rhs
            else:
                return value

    def term(self):
        value = self.unary()
        while True:
            token = self.peek()
            if token is not None and token[0] == "op" and token[1] in ("*", "/", "//", "%"):
                self.pos += 1
                rhs = self.unary()
                op = token[1]
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

    def unary(self):
        token = self.peek()
        if token is not None and token[0] == "op" and token[1] in ("+", "-"):
            self.pos += 1
            value = self.unary()
            return -value if token[1] == "-" else value
        return self.power()

    def power(self):
        base = self.primary()
        token = self.peek()
        if token is not None and token[0] == "op" and token[1] == "**":
            self.pos += 1
            exponent = self.unary()  # right-associative; exponent may be unary
            return base ** exponent
        return base

    def primary(self):
        token = self.peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        kind, value = token
        if kind == "number":
            self.pos += 1
            return float(value) if "." in value else int(value)
        if kind == "name":
            self.pos += 1
            if value not in _FUNCTIONS:
                raise ValueError("unknown name: %r" % value)
            return self.call(value)
        if kind == "op" and value == "(":
            self.pos += 1
            inner = self.expression()
            self.expect("op", ")")
            return inner
        raise ValueError("unexpected token %r" % value)

    def call(self, name):
        self.expect("op", "(")
        args = []
        token = self.peek()
        if token is None or token != ("op", ")"):
            args.append(self.expression())
            while True:
                token = self.peek()
                if token is not None and token[0] == "op" and token[1] == ",":
                    self.pos += 1
                    args.append(self.expression())
                else:
                    break
        self.expect("op", ")")
        min_args, max_args = _FUNCTIONS[name]
        if len(args) < min_args or (max_args is not None and len(args) > max_args):
            raise ValueError(
                "%s expects %s argument(s), got %d"
                % (name, min_args if max_args == min_args else "1+", len(args))
            )
        if name == "abs":
            return abs(args[0])
        if name == "min":
            return min(args)
        return max(args)


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    return _Parser(_tokenize(text)).parse()
