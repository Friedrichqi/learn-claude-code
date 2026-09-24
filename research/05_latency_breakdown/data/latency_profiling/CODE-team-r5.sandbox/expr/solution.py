"""Arithmetic expression evaluator.

A hand-written tokenizer and recursive-descent parser (no eval/exec/compile/ast).

Grammar (lowest to highest precedence):
    expr    := term (('+' | '-') term)*
    term    := factor (('*' | '/' | '//' | '%') factor)*
    factor  := ('+' | '-') factor | power
    power   := primary ('**' factor)?          # right-associative
    primary := NUMBER | NAME '(' args ')' | '(' expr ')'
"""

_DIGITS = "0123456789"
_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind, value=None):
        self.kind = kind
        self.value = value

    def __repr__(self):
        return "Token(%r, %r)" % (self.kind, self.value)


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in _DIGITS or ch == ".":
            start = i
            seen_dot = False
            while i < n and (text[i] in _DIGITS or (text[i] == "." and not seen_dot)):
                if text[i] == ".":
                    seen_dot = True
                i += 1
            lexeme = text[start:i]
            if lexeme == ".":
                raise ValueError("malformed number %r" % lexeme)
            tokens.append(_Token("NUMBER", float(lexeme) if seen_dot else int(lexeme)))
            continue
        if ch in _LETTERS:
            start = i
            while i < n and (text[i] in _LETTERS or text[i] in _DIGITS):
                i += 1
            tokens.append(_Token("NAME", text[start:i]))
            continue
        if text.startswith("**", i) or text.startswith("//", i):
            tokens.append(_Token("OP", text[i:i + 2]))
            i += 2
            continue
        if ch in "+-*/%(),":
            tokens.append(_Token("COMMA" if ch == "," else "OP", ch))
            i += 1
            continue
        raise ValueError("unknown character %r at position %d" % (ch, i))
    tokens.append(_Token("EOF"))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self._tokens = tokens
        self._pos = 0

    def _peek(self):
        return self._tokens[self._pos]

    def _next(self):
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _expect(self, kind, value=None):
        token = self._next()
        if token.kind != kind or (value is not None and token.value != value):
            raise ValueError("expected %r, got %r" % (value if value else kind, token))
        return token

    def parse(self):
        value = self._expr()
        token = self._next()
        if token.kind != "EOF":
            raise ValueError("unexpected trailing token %r" % (token,))
        return value

    def _expr(self):
        value = self._term()
        while True:
            token = self._peek()
            if token.kind == "OP" and token.value in ("+", "-"):
                self._next()
                rhs = self._term()
                value = value + rhs if token.value == "+" else value - rhs
            else:
                return value

    def _term(self):
        value = self._factor()
        while True:
            token = self._peek()
            if token.kind == "OP" and token.value in ("*", "/", "//", "%"):
                self._next()
                rhs = self._factor()
                if token.value == "*":
                    value = value * rhs
                elif token.value == "/":
                    value = value / rhs
                elif token.value == "//":
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

    def _factor(self):
        token = self._peek()
        if token.kind == "OP" and token.value in ("+", "-"):
            self._next()
            value = self._factor()
            return value if token.value == "+" else -value
        return self._power()

    def _power(self):
        base = self._primary()
        token = self._peek()
        if token.kind == "OP" and token.value == "**":
            self._next()
            return base ** self._factor()
        return base

    def _primary(self):
        token = self._next()
        if token.kind == "NUMBER":
            return token.value
        if token.kind == "OP" and token.value == "(":
            value = self._expr()
            self._expect("OP", ")")
            return value
        if token.kind == "NAME":
            name = token.value
            self._expect("OP", "(")
            args = [self._expr()]
            while self._peek().kind == "COMMA":
                self._next()
                args.append(self._expr())
            self._expect("OP", ")")
            if name in ("min", "max"):
                if not args:
                    raise ValueError("%s() needs at least one argument" % name)
                return min(args) if name == "min" else max(args)
            if name == "abs":
                if len(args) != 1:
                    raise ValueError("abs() takes exactly one argument")
                return abs(args[0])
            raise ValueError("unknown name %r" % name)
        raise ValueError("unexpected token %r" % (token,))


def evaluate(text):
    """Parse and evaluate an arithmetic expression string."""
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    return _Parser(_tokenize(text)).parse()
