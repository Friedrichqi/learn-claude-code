"""Arithmetic expression evaluator.

Implements ``evaluate(text)`` per the README spec using a hand-written
tokenizer and a recursive-descent parser (no eval/exec/compile/ast).

Grammar (mirrors Python precedence):

    expr   := term (('+' | '-') term)*
    term   := unary (('*' | '/' | '//' | '%') unary)*
    unary  := ('+' | '-') unary | power
    power  := atom ('**' unary)?          # right-associative
    atom   := NUMBER | '(' expr ')' | CALL
    CALL   := ('min' | 'max') '(' expr (',' expr)* ')'
            | 'abs' '(' expr ')'
"""

import re

_NUMBER_RE = re.compile(r"\d+(\.\d+)?|\.\d+")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TWO_CHAR_OPS = ("**", "//")
_SINGLE_CHARS = "+-*/%(),"


def evaluate(text: str):
    """Parse and evaluate *text*; return an int or float."""
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    tokens = _tokenize(text)
    return _Parser(tokens).parse()


def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        m = _NUMBER_RE.match(text, i)
        if m is not None:
            word = m.group(0)
            tokens.append(("num", float(word) if "." in word else int(word)))
            i = m.end()
            continue
        m = _IDENT_RE.match(text, i)
        if m is not None:
            tokens.append(("name", m.group(0)))
            i = m.end()
            continue
        if text[i:i + 2] in _TWO_CHAR_OPS:
            tokens.append(("op", text[i:i + 2]))
            i += 2
            continue
        if ch in _SINGLE_CHARS:
            tokens.append(("op", ch))
            i += 1
            continue
        raise ValueError("unexpected character %r" % ch)
    tokens.append(("eof", None))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    # -- token helpers -------------------------------------------------
    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect_op(self, op):
        kind, val = self.advance()
        if kind != "op" or val != op:
            raise ValueError("expected %r" % op)

    # -- grammar -------------------------------------------------------
    def parse(self):
        if self.peek()[0] == "eof":
            raise ValueError("empty expression")
        value = self.expr()
        if self.peek()[0] != "eof":
            raise ValueError("unexpected trailing token %r" % (self.peek()[1],))
        return value

    def expr(self):
        value = self.term()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("+", "-"):
                self.advance()
                rhs = self.term()
                value = value + rhs if val == "+" else value - rhs
            else:
                return value

    def term(self):
        value = self.unary()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("*", "/", "//", "%"):
                self.advance()
                rhs = self.unary()
                if val == "*":
                    value = value * rhs
                elif val == "/":
                    value = value / rhs
                elif val == "//":
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

    def unary(self):
        kind, val = self.peek()
        if kind == "op" and val in ("+", "-"):
            self.advance()
            value = self.unary()
            return value if val == "+" else -value
        return self.power()

    def power(self):
        base = self.atom()
        kind, val = self.peek()
        if kind == "op" and val == "**":
            self.advance()
            exponent = self.unary()  # right-associative, unary in exponent
            return base ** exponent
        return base

    def atom(self):
        kind, val = self.advance()
        if kind == "num":
            return val
        if kind == "op" and val == "(":
            value = self.expr()
            self.expect_op(")")
            return value
        if kind == "name":
            if self.peek() == ("op", "("):
                return self.call(val)
            raise ValueError("unknown name %r" % val)
        if kind == "eof":
            raise ValueError("missing operand")
        raise ValueError("unexpected token %r" % val)

    def call(self, name):
        self.advance()  # consume '('
        args = []
        kind, val = self.peek()
        if not (kind == "op" and val == ")"):
            args.append(self.expr())
            while self.peek() == ("op", ","):
                self.advance()
                args.append(self.expr())
        self.expect_op(")")
        if name in ("min", "max"):
            if not args:
                raise ValueError("%s requires at least one argument" % name)
            return min(args) if name == "min" else max(args)
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs takes exactly one argument")
            return abs(args[0])
        raise ValueError("unknown function %r" % name)
