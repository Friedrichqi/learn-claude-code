"""Recursive-descent evaluator for a small arithmetic expression language."""

import re

_TOKEN_RE = re.compile(
    r"\s*(?:(\d+\.\d*|\.\d+|\d+)|([A-Za-z_][A-Za-z_0-9]*)|(//|\*\*|[+\-*/%(),]))"
)

_FUNCTIONS = ("min", "max", "abs")


def _tokenize(text):
    tokens = []
    pos = 0
    size = len(text)
    while pos < size:
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            if text[pos:].strip() == "":
                break  # trailing whitespace only
            raise ValueError("invalid character at position %d" % pos)
        pos = match.end()
        number, name, op = match.groups()
        if number is not None:
            tokens.append(("num", float(number) if "." in number else int(number)))
        elif name is not None:
            tokens.append(("name", name))
        else:
            tokens.append(("op", op))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return (None, None)

    def advance(self):
        token = self.peek()
        self.pos += 1
        return token

    def parse(self):
        value = self.additive()
        kind, _ = self.peek()
        if kind is not None:
            raise ValueError("unexpected token after expression")
        return value

    def additive(self):
        left = self.multiplicative()
        while True:
            kind, op = self.peek()
            if kind == "op" and op in ("+", "-"):
                self.advance()
                right = self.multiplicative()
                left = left + right if op == "+" else left - right
            else:
                return left

    def multiplicative(self):
        left = self.unary()
        while True:
            kind, op = self.peek()
            if kind == "op" and op in ("*", "/", "//", "%"):
                self.advance()
                right = self.unary()
                if op == "*":
                    left = left * right
                elif op == "/":
                    left = left / right
                elif op == "//":
                    left = left // right
                else:
                    left = left % right
            else:
                return left

    def unary(self):
        kind, op = self.peek()
        if kind == "op" and op in ("+", "-"):
            self.advance()
            operand = self.unary()
            return -operand if op == "-" else +operand
        return self.power()

    def power(self):
        base = self.primary()
        kind, op = self.peek()
        if kind == "op" and op == "**":
            self.advance()
            # Right-associative; the exponent may carry its own unary sign.
            return base ** self.unary()
        return base

    def primary(self):
        kind, value = self.peek()
        if kind == "num":
            self.advance()
            return value
        if kind == "op" and value == "(":
            self.advance()
            inner = self.additive()
            kind2, op2 = self.peek()
            if kind2 != "op" or op2 != ")":
                raise ValueError("missing closing parenthesis")
            self.advance()
            return inner
        if kind == "name":
            self.advance()
            return self.call(value)
        raise ValueError("unexpected token")

    def call(self, name):
        kind, op = self.peek()
        if kind != "op" or op != "(":
            raise ValueError("expected '(' after %s" % name)
        self.advance()
        args = [self.additive()]
        while True:
            kind2, op2 = self.peek()
            if kind2 == "op" and op2 == ",":
                self.advance()
                args.append(self.additive())
            elif kind2 == "op" and op2 == ")":
                self.advance()
                break
            else:
                raise ValueError("malformed function call")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs takes exactly one argument")
            return abs(args[0])
        if name in _FUNCTIONS:
            return min(args) if name == "min" else max(args)
        raise ValueError("unknown name %r" % name)


def evaluate(text):
    tokens = _tokenize(text)
    return _Parser(tokens).parse()
