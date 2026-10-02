"""Arithmetic expression evaluator (tokenizer + precedence-climbing parser)."""


def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or c == ".":
            j = i
            dot = False
            while j < n and (text[j].isdigit() or text[j] == "."):
                if text[j] == ".":
                    if dot:
                        raise ValueError("invalid number literal")
                    dot = True
                j += 1
            num = text[i:j]
            if num == ".":
                raise ValueError("invalid number literal")
            tokens.append(("num", float(num) if dot else int(num)))
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("name", text[i:j]))
            i = j
        elif text.startswith("**", i):
            tokens.append(("op", "**"))
            i += 2
        elif text.startswith("//", i):
            tokens.append(("op", "//"))
            i += 2
        elif c in "+-*/%(),":
            tokens.append(("op", c))
            i += 1
        else:
            raise ValueError(f"unexpected character {c!r}")
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
        tok = self.peek()
        self.pos += 1
        return tok

    def expect_op(self, op):
        kind, val = self.peek()
        if kind != "op" or val != op:
            raise ValueError(f"expected {op!r}")
        self.advance()

    def parse(self):
        if not self.tokens:
            raise ValueError("empty expression")
        value = self.parse_add()
        if self.pos != len(self.tokens):
            raise ValueError(f"unexpected token {self.tokens[self.pos][1]!r}")
        return value

    def parse_add(self):
        left = self.parse_mul()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("+", "-"):
                self.advance()
                right = self.parse_mul()
                left = left + right if val == "+" else left - right
            else:
                return left

    def parse_mul(self):
        left = self.parse_unary()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("*", "/", "//", "%"):
                self.advance()
                right = self.parse_unary()
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

    def parse_unary(self):
        kind, val = self.peek()
        if kind == "op" and val in ("+", "-"):
            self.advance()
            operand = self.parse_unary()
            return -operand if val == "-" else +operand
        return self.parse_power()

    def parse_power(self):
        base = self.parse_atom()
        kind, val = self.peek()
        if kind == "op" and val == "**":
            self.advance()
            exponent = self.parse_unary()  # right-associative, signed exponent
            return base ** exponent
        return base

    def parse_atom(self):
        kind, val = self.peek()
        if kind == "num":
            self.advance()
            return val
        if kind == "op" and val == "(":
            self.advance()
            value = self.parse_add()
            self.expect_op(")")
            return value
        if kind == "name":
            return self.parse_call()
        if kind is None:
            raise ValueError("unexpected end of input")
        raise ValueError(f"unexpected token {val!r}")

    def parse_call(self):
        _, name = self.advance()
        if name not in ("min", "max", "abs"):
            raise ValueError(f"unknown function {name!r}")
        self.expect_op("(")
        args = [self.parse_add()]
        while True:
            kind, val = self.peek()
            if kind == "op" and val == ",":
                self.advance()
                args.append(self.parse_add())
            else:
                break
        self.expect_op(")")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs() takes exactly one argument")
            return abs(args[0])
        return (min if name == "min" else max)(args)


def evaluate(text):
    return _Parser(_tokenize(text)).parse()
