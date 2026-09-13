"""Arithmetic expression evaluator: hand-written tokenizer + recursive-descent parser."""

import re


class _Tok:
    def __init__(self, kind, value):
        self.kind = kind
        self.value = value

    def __repr__(self):  # pragma: no cover - debugging aid
        return "%s(%r)" % (self.kind, self.value)


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+\.\d+|\d+|\.\d+)
  | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
  | (?P<op>\*\*|//|[+\-*/%(),])
    """,
    re.VERBOSE,
)


def _tokenize(text):
    tokens = []
    pos = 0
    n = len(text)
    while pos < n:
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise ValueError("unexpected character %r at position %d" % (text[pos], pos))
        pos = match.end()
        if match.lastgroup == "ws":
            continue
        if match.lastgroup == "num":
            lexeme = match.group("num")
            if "." in lexeme:
                tokens.append(_Tok("num", float(lexeme)))
            else:
                tokens.append(_Tok("num", int(lexeme)))
        elif match.lastgroup == "name":
            tokens.append(_Tok("name", match.group("name")))
        else:
            lexeme = match.group("op")
            if lexeme == "(":
                tokens.append(_Tok("lparen", lexeme))
            elif lexeme == ")":
                tokens.append(_Tok("rparen", lexeme))
            else:
                tokens.append(_Tok("op", lexeme))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0

    def peek(self):
        if self.index < len(self.tokens):
            return self.tokens[self.index]
        return None

    def take(self):
        token = self.peek()
        if token is not None:
            self.index += 1
        return token

    def expect(self, kind):
        token = self.take()
        if token is None or token.kind != kind:
            raise ValueError("expected %s" % kind)
        return token

    def parse_expression(self):
        value = self.parse_term()
        while True:
            token = self.peek()
            if token is None or token.kind != "op" or token.value not in ("+", "-"):
                return value
            self.take()
            rhs = self.parse_term()
            value = value + rhs if token.value == "+" else value - rhs

    def parse_term(self):
        value = self.parse_unary()
        while True:
            token = self.peek()
            if token is None or token.kind != "op":
                return value
            op = token.value
            if op == "*":
                self.take()
                value = value * self.parse_unary()
            elif op == "/":
                self.take()
                value = value / self.parse_unary()
            elif op == "//":
                self.take()
                value = value // self.parse_unary()
            elif op == "%":
                self.take()
                value = value % self.parse_unary()
            else:
                return value

    def parse_unary(self):
        token = self.peek()
        if token is not None and token.kind == "op" and token.value in ("+", "-"):
            self.take()
            operand = self.parse_unary()
            return operand if token.value == "+" else -operand
        return self.parse_power()

    def parse_power(self):
        base = self.parse_primary()
        token = self.peek()
        if token is not None and token.kind == "op" and token.value == "**":
            self.take()
            exponent = self.parse_unary()  # right-associative, binds looser than unary
            return base ** exponent
        return base

    def parse_primary(self):
        token = self.take()
        if token is None:
            raise ValueError("missing operand")
        if token.kind == "num":
            return token.value
        if token.kind == "lparen":
            value = self.parse_expression()
            self.expect("rparen")
            return value
        if token.kind == "name":
            name = token.value
            if name not in ("min", "max", "abs"):
                raise ValueError("unknown name %r" % name)
            self.expect("lparen")
            args = [self.parse_expression()]
            while True:
                nxt = self.peek()
                if nxt is not None and nxt.kind == "op" and nxt.value == ",":
                    self.take()
                    args.append(self.parse_expression())
                    continue
                break
            self.expect("rparen")
            if name == "abs":
                if len(args) != 1:
                    raise ValueError("abs() takes exactly one argument")
                return abs(args[0])
            if len(args) < 1:
                raise ValueError("%s() takes at least one argument" % name)
            return min(args) if name == "min" else max(args)
        raise ValueError("unexpected token %r" % (token.value,))


def evaluate(text):
    """Parse and evaluate an arithmetic expression, returning an int or float."""
    if not isinstance(text, str):
        raise ValueError("expected a string")
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    parser = _Parser(tokens)
    result = parser.parse_expression()
    if parser.peek() is not None:
        raise ValueError("unexpected trailing token %r" % (parser.peek().value,))
    return result
