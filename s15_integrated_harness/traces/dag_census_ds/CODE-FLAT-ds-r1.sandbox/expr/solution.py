"""Arithmetic expression evaluator.

Implements a tokenizer plus a recursive-descent (precedence-climbing) parser
for a small arithmetic language. No eval/exec/compile/ast is used.
"""

import re

__all__ = ["evaluate"]

_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<number>\d+\.\d+|\.\d+|\d+)
  | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
  | (?P<op>\*\*|//|[+\-*/%(),])
    """,
    re.VERBOSE,
)

_FUNCTIONS = {"min", "max", "abs"}


def _tokenize(text):
    tokens = []
    pos = 0
    length = len(text)
    while pos < length:
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise ValueError("unexpected character %r at position %d" % (text[pos], pos))
        kind = match.lastgroup
        value = match.group()
        pos = match.end()
        if kind == "space":
            continue
        tokens.append((kind, value))
    tokens.append(("end", ""))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0

    def peek(self):
        return self.tokens[self.index]

    def advance(self):
        token = self.tokens[self.index]
        self.index += 1
        return token

    def expect(self, kind, value=None):
        token = self.peek()
        if token[0] != kind or (value is not None and token[1] != value):
            raise ValueError("expected %r, found %r" % (value or kind, token[1]))
        return self.advance()

    # expression := term (('+' | '-') term)*
    def parse_expression(self):
        value = self.parse_term()
        while True:
            token = self.peek()
            if token[0] == "op" and token[1] in ("+", "-"):
                self.advance()
                right = self.parse_term()
                value = value + right if token[1] == "+" else value - right
            else:
                return value

    # term := factor (('*' | '/' | '//' | '%') factor)*
    def parse_term(self):
        value = self.parse_factor()
        while True:
            token = self.peek()
            if token[0] == "op" and token[1] in ("*", "/", "//", "%"):
                self.advance()
                right = self.parse_factor()
                if token[1] == "*":
                    value = value * right
                elif token[1] == "/":
                    value = value / right
                elif token[1] == "//":
                    value = value // right
                else:
                    value = value % right
            else:
                return value

    # factor := ('+' | '-') factor | power
    def parse_factor(self):
        token = self.peek()
        if token[0] == "op" and token[1] in ("+", "-"):
            self.advance()
            operand = self.parse_factor()
            return operand if token[1] == "+" else -operand
        return self.parse_power()

    # power := atom ('**' factor)?   (right-associative, looser than unary)
    def parse_power(self):
        base = self.parse_atom()
        token = self.peek()
        if token[0] == "op" and token[1] == "**":
            self.advance()
            exponent = self.parse_factor()
            return base ** exponent
        return base

    # atom := number | '(' expression ')' | name '(' args ')'
    def parse_atom(self):
        token = self.peek()
        if token[0] == "number":
            self.advance()
            text = token[1]
            if "." in text:
                return float(text)
            return int(text)
        if token[0] == "name":
            return self.parse_call()
        if token[0] == "op" and token[1] == "(":
            self.advance()
            value = self.parse_expression()
            self.expect("op", ")")
            return value
        raise ValueError("unexpected token %r" % (token[1],))

    def parse_call(self):
        token = self.advance()
        name = token[1]
        if name not in _FUNCTIONS:
            raise ValueError("unknown name %r" % (name,))
        self.expect("op", "(")
        args = [self.parse_expression()]
        while self.peek()[0] == "op" and self.peek()[1] == ",":
            self.advance()
            args.append(self.parse_expression())
        self.expect("op", ")")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs() takes exactly one argument")
            return abs(args[0])
        if name == "min":
            return min(args)
        return max(args)


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    tokens = _tokenize(text)
    parser = _Parser(tokens)
    value = parser.parse_expression()
    token = parser.peek()
    if token[0] != "end":
        raise ValueError("unexpected trailing token %r" % (token[1],))
    return value
