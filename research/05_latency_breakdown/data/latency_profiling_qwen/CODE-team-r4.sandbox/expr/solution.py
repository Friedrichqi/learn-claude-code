"""Arithmetic expression evaluator.

Implements `evaluate(text)` per the README specification: a hand-written
tokenizer plus a recursive-descent parser with Python operator semantics
(precedence, associativity, int/float type rules).
"""

import re

_NUMBER_RE = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_KNOWN_FUNCTIONS = ("min", "max", "abs")


def _tokenize(text):
    """Convert source text into a list of (kind, value) tokens."""
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or c == ".":
            match = _NUMBER_RE.match(text, i)
            if match is None:
                raise ValueError("invalid number literal at position %d" % i)
            literal = match.group(0)
            if "." in literal:
                tokens.append(("num", float(literal)))
            else:
                tokens.append(("num", int(literal)))
            i = match.end()
        elif c.isalpha() or c == "_":
            match = _NAME_RE.match(text, i)
            tokens.append(("name", match.group(0)))
            i = match.end()
        elif text.startswith("**", i):
            tokens.append(("pow", "**"))
            i += 2
        elif text.startswith("//", i):
            tokens.append(("floordiv", "//"))
            i += 2
        elif c in "+-*/%():,":
            tokens.append((c, c))
            i += 1
        else:
            raise ValueError("unknown character %r" % c)
    return tokens


class _Parser:
    """Recursive-descent parser over the token list.

    Grammar (lowest to highest precedence):
        expression := term (('+' | '-') term)*
        term       := unary (('*' | '/' | '//' | '%') unary)*
        unary      := ('+' | '-') unary | power
        power      := atom ('**' unary)?          # right-associative
        atom       := NUMBER | NAME '(' args ')' | '(' expression ')'
    """

    def __init__(self, tokens):
        self._tokens = tokens
        self._pos = 0

    def parse(self):
        value = self._expression()
        if not self._at_end():
            raise ValueError(
                "unexpected token %r after expression" % self._peek()[1]
            )
        return value

    # -- token helpers -------------------------------------------------

    def _peek(self):
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return (None, None)

    def _advance(self):
        token = self._peek()
        self._pos += 1
        return token

    def _at_end(self):
        return self._pos >= len(self._tokens)

    def _expect(self, kind):
        token_kind, token_value = self._peek()
        if token_kind != kind:
            raise ValueError("expected %r, got %r" % (kind, token_value))
        self._advance()

    # -- grammar rules ---------------------------------------------------

    def _expression(self):
        value = self._term()
        while True:
            kind, _ = self._peek()
            if kind == "+":
                self._advance()
                value = value + self._term()
            elif kind == "-":
                self._advance()
                value = value - self._term()
            else:
                return value

    def _term(self):
        value = self._unary()
        while True:
            kind, _ = self._peek()
            if kind == "*":
                self._advance()
                value = value * self._unary()
            elif kind == "/":
                self._advance()
                value = value / self._unary()
            elif kind == "floordiv":
                self._advance()
                value = value // self._unary()
            elif kind == "%":
                self._advance()
                value = value % self._unary()
            else:
                return value

    def _unary(self):
        kind, _ = self._peek()
        if kind == "+":
            self._advance()
            return +self._unary()
        if kind == "-":
            self._advance()
            return -self._unary()
        return self._power()

    def _power(self):
        base = self._atom()
        kind, _ = self._peek()
        if kind == "pow":
            self._advance()
            exponent = self._unary()
            return base ** exponent
        return base

    def _atom(self):
        kind, value = self._peek()
        if kind == "num":
            self._advance()
            return value
        if kind == "name":
            self._advance()
            return self._call(value)
        if kind == "(":
            self._advance()
            inner = self._expression()
            self._expect(")")
            return inner
        raise ValueError(
            "unexpected token %r where an expression was expected" % (value,)
        )

    def _call(self, name):
        if name not in _KNOWN_FUNCTIONS:
            raise ValueError("unknown name %r" % name)
        self._expect("(")
        args = []
        if self._peek()[0] != ")":
            while True:
                args.append(self._expression())
                kind, _ = self._peek()
                if kind == ",":
                    self._advance()
                    if self._peek()[0] == ")":
                        raise ValueError(
                            "trailing comma in call to %r" % name
                        )
                elif kind == ")":
                    break
                else:
                    raise ValueError(
                        "expected ',' or ')' in call to %r" % name
                    )
        if not args:
            raise ValueError("%r requires at least one argument" % name)
        if name == "abs" and len(args) != 1:
            raise ValueError("abs() takes exactly one argument")
        self._expect(")")
        if name == "min":
            return min(args)
        if name == "max":
            return max(args)
        return abs(args[0])


def evaluate(text: str):
    """Parse and evaluate an arithmetic expression string.

    Returns an ``int`` or ``float``.  Raises ``ZeroDivisionError`` for
    division/floor-division/modulo by zero and ``ValueError`` for any
    malformed input.
    """
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    return _Parser(tokens).parse()
