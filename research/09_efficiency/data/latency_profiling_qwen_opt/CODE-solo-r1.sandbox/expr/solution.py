"""Arithmetic expression evaluator (no eval/exec/ast)."""

_TOKENS = {"+", "-", "*", "/", "//", "%", "**", "(", ")", ","}
_FUNCS = {"min", "max", "abs"}


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or c == ".":
            j = i
            seen_dot = False
            while j < n and (text[j].isdigit() or (text[j] == "." and not seen_dot)):
                if text[j] == ".":
                    seen_dot = True
                j += 1
            word = text[i:j]
            if "." not in word:
                tokens.append(("num", int(word)))
            else:
                tokens.append(("num", float(word)))
            i = j
        elif c.isalpha():
            j = i
            while j < n and text[j].isalpha():
                j += 1
            word = text[i:j]
            if word not in _FUNCS:
                raise ValueError(f"unknown name: {word!r}")
            tokens.append(("name", word))
            i = j
        elif text[i:i + 2] in ("**", "//"):
            tokens.append(("op", text[i:i + 2]))
            i += 2
        elif c in "+-*/%(),":
            tokens.append(("op", c))
            i += 1
        else:
            raise ValueError(f"unknown character: {c!r}")
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self):
        tok = self.peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        self.pos += 1
        return tok

    def expect_op(self, *ops):
        tok = self.next()
        if tok[0] != "op" or tok[1] not in ops:
            raise ValueError(f"expected operator, got {tok[1]!r}")
        return tok[1]

    def parse(self):
        if not self.tokens:
            raise ValueError("empty expression")
        value = self.expr()
        if self.pos != len(self.tokens):
            raise ValueError(f"unexpected token: {self.tokens[self.pos][1]!r}")
        return value

    def expr(self):
        return self.additive()

    def additive(self):
        value = self.multiplicative()
        while True:
            tok = self.peek()
            if tok and tok[0] == "op" and tok[1] in ("+", "-"):
                self.next()
                rhs = self.multiplicative()
                value = value + rhs if tok[1] == "+" else value - rhs
            else:
                return value

    def multiplicative(self):
        value = self.unary()
        while True:
            tok = self.peek()
            if tok and tok[0] == "op" and tok[1] in ("*", "/", "//", "%"):
                self.next()
                rhs = self.unary()
                if tok[1] == "*":
                    value = value * rhs
                elif tok[1] == "/":
                    value = value / rhs
                elif tok[1] == "//":
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

    def unary(self):
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in ("+", "-"):
            self.next()
            operand = self.unary()
            return -operand if tok[1] == "-" else +operand
        return self.power()

    def power(self):
        base = self.primary()
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] == "**":
            self.next()
            # right-associative, binds tighter than unary
            exponent = self.unary()
            return base ** exponent
        return base

    def primary(self):
        tok = self.next()
        if tok[0] == "num":
            return tok[1]
        if tok[0] == "name":
            self.expect_op("(")
            args = [self.expr()]
            while True:
                t = self.peek()
                if t and t[0] == "op" and t[1] == ",":
                    self.next()
                    args.append(self.expr())
                else:
                    break
            self.expect_op(")")
            if tok[1] == "abs":
                if len(args) != 1:
                    raise ValueError("abs takes exactly 1 argument")
                return abs(args[0])
            if len(args) < 1:
                raise ValueError(f"{tok[1]} takes at least 1 argument")
            return min(args) if tok[1] == "min" else max(args)
        if tok[0] == "op" and tok[1] == "(":
            value = self.expr()
            self.expect_op(")")
            return value
        raise ValueError(f"unexpected token: {tok[1]!r}")


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("expected a string")
    return _Parser(_tokenize(text)).parse()
