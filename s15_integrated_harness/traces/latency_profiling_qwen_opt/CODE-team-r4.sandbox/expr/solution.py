"""Arithmetic expression evaluator (hand-written tokenizer + recursive descent)."""


class _Tokenizer:
    def __init__(self, text):
        self.text = text
        self.pos = 0

    def error(self, msg):
        raise ValueError(msg)

    def peek(self):
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1
        if self.pos >= len(self.text):
            return None
        return self.text[self.pos]

    def next_token(self):
        ch = self.peek()
        if ch is None:
            return ("EOF", None)
        if ch.isdigit() or ch == ".":
            return self._number()
        if ch.isalpha() or ch == "_":
            return self._name()
        self.pos += 1
        two = self.text[self.pos - 1: self.pos + 1]
        if two in ("**", "//"):
            self.pos += 1
            return ("OP", two)
        if ch in "+-*/%()":
            return ("OP", ch)
        if ch == ",":
            return ("COMMA", ",")
        self.error("unexpected character %r" % ch)

    def _number(self):
        start = self.pos
        self.pos += 1
        is_float = False
        if self.text[self.pos - 1] == ".":
            is_float = True
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        if self.pos < len(self.text) and self.text[self.pos] == ".":
            if is_float:
                self.error("invalid number")
            is_float = True
            self.pos += 1
            while self.pos < len(self.text) and self.text[self.pos].isdigit():
                self.pos += 1
        literal = self.text[start:self.pos]
        value = float(literal) if is_float else int(literal)
        return ("NUM", value)

    def _name(self):
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self.pos += 1
        return ("NAME", self.text[start:self.pos])


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.idx = 0

    def peek(self):
        return self.tokens[self.idx]

    def advance(self):
        tok = self.tokens[self.idx]
        self.idx += 1
        return tok

    def expect_op(self, value):
        kind, val = self.peek()
        if kind != "OP" or val != value:
            raise ValueError("expected %r" % value)
        self.idx += 1

    def parse(self):
        value = self.expr()
        kind, val = self.peek()
        if kind != "EOF":
            raise ValueError("unexpected token %r" % val)
        return value

    def expr(self):
        return self.additive()

    def additive(self):
        left = self.multiplicative()
        while True:
            kind, val = self.peek()
            if kind == "OP" and val in ("+", "-"):
                self.idx += 1
                right = self.multiplicative()
                left = left + right if val == "+" else left - right
            else:
                return left

    def multiplicative(self):
        left = self.unary()
        while True:
            kind, val = self.peek()
            if kind == "OP" and val in ("*", "/", "//", "%"):
                self.idx += 1
                right = self.unary()
                if val == "*":
                    left = left * right
                elif val == "/":
                    left = left / right
                elif val == "//":
                    left = left // right
                else:
                    left = left % right
            else:
                return left

    def unary(self):
        kind, val = self.peek()
        if kind == "OP" and val in ("+", "-"):
            self.idx += 1
            operand = self.unary()
            return operand if val == "+" else -operand
        return self.power()

    def power(self):
        base = self.primary()
        kind, val = self.peek()
        if kind == "OP" and val == "**":
            self.idx += 1
            exponent = self.unary()  # right-associative
            return base ** exponent
        return base

    def primary(self):
        kind, val = self.peek()
        if kind == "NUM":
            self.idx += 1
            return val
        if kind == "OP" and val == "(":
            self.idx += 1
            value = self.expr()
            self.expect_op(")")
            return value
        if kind == "NAME":
            return self.call()
        if kind == "EOF":
            raise ValueError("unexpected end of expression")
        raise ValueError("unexpected token %r" % val)

    def call(self):
        _, name = self.advance()
        if name not in ("min", "max", "abs"):
            raise ValueError("unknown name %r" % name)
        self.expect_op("(")
        if self.peek() == ("OP", ")"):
            self.idx += 1
            args = []
        else:
            args = [self.expr()]
            while self.peek() == ("COMMA", ","):
                self.idx += 1
                if self.peek() == ("OP", ")"):
                    raise ValueError("trailing comma in call")
                args.append(self.expr())
            self.expect_op(")")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs takes exactly one argument")
            return abs(args[0])
        if not args:
            raise ValueError("%s takes at least one argument" % name)
        return min(args) if name == "min" else max(args)


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    tokens = []
    tokenizer = _Tokenizer(text)
    while True:
        tok = tokenizer.next_token()
        tokens.append(tok)
        if tok[0] == "EOF":
            break
    return _Parser(tokens).parse()
