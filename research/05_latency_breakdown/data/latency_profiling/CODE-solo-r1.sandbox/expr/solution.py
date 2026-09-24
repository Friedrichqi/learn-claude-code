"""Arithmetic expression evaluator: tokenizer + recursive-descent parser.

Grammar (lowest to highest precedence):
    expr    := term (('+' | '-') term)*
    term    := unary (('*' | '/' | '//' | '%') unary)*
    unary   := ('+' | '-') unary | power
    power   := atom ('**' unary)?          # right-associative, binds tighter than unary
    atom    := NUMBER | NAME '(' args ')' | '(' expr ')'
"""

FUNCS = {
    "min": (1, None),
    "max": (1, None),
    "abs": (1, 1),
}


def tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch.isdigit() or ch == ".":
            start = i
            seen_dot = False
            while i < n and (text[i].isdigit() or text[i] == "."):
                if text[i] == ".":
                    if seen_dot:
                        raise ValueError(f"malformed number at {start}: {text[start:i + 1]!r}")
                    seen_dot = True
                i += 1
            literal = text[start:i]
            if literal == ".":
                raise ValueError(f"malformed number: {literal!r}")
            value = float(literal) if seen_dot else int(literal)
            tokens.append(("num", value))
        elif ch.isalpha() or ch == "_":
            start = i
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            tokens.append(("name", text[start:i]))
        elif ch == "*":
            if text.startswith("**", i):
                tokens.append(("op", "**"))
                i += 2
            else:
                tokens.append(("op", "*"))
                i += 1
        elif ch == "/":
            if text.startswith("//", i):
                tokens.append(("op", "//"))
                i += 2
            else:
                tokens.append(("op", "/"))
                i += 1
        elif ch in "+-%()":
            tokens.append(("op", ch))
            i += 1
        elif ch == ",":
            tokens.append(("comma", ","))
            i += 1
        else:
            raise ValueError(f"unknown character {ch!r} at position {i}")
    tokens.append(("end", None))
    return tokens


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos]

    def next(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect_op(self, op):
        kind, value = self.next()
        if kind != "op" or value != op:
            raise ValueError(f"expected {op!r}, got {value!r}")

    def parse(self):
        value = self.expr()
        kind, tok = self.peek()
        if kind != "end":
            raise ValueError(f"unexpected trailing token {tok!r}")
        return value

    def expr(self):
        value = self.term()
        while True:
            kind, op = self.peek()
            if kind == "op" and op in "+-":
                self.next()
                rhs = self.term()
                value = value + rhs if op == "+" else value - rhs
            else:
                return value

    def term(self):
        value = self.unary()
        while True:
            kind, op = self.peek()
            if kind == "op" and op in ("*", "/", "//", "%"):
                self.next()
                rhs = self.unary()
                if op == "*":
                    value = value * rhs
                elif op == "/":
                    if rhs == 0:
                        raise ZeroDivisionError("division by zero")
                    value = value / rhs
                elif op == "//":
                    if rhs == 0:
                        raise ZeroDivisionError("integer division or modulo by zero")
                    value = value // rhs
                else:
                    if rhs == 0:
                        raise ZeroDivisionError("integer division or modulo by zero")
                    value = value % rhs
            else:
                return value

    def unary(self):
        kind, op = self.peek()
        if kind == "op" and op in "+-":
            self.next()
            operand = self.unary()
            return operand if op == "+" else -operand
        return self.power()

    def power(self):
        base = self.atom()
        kind, op = self.peek()
        if kind == "op" and op == "**":
            self.next()
            return base ** self.unary()  # right-associative; -2 ** 2 handled in unary()
        return base

    def atom(self):
        kind, value = self.next()
        if kind == "num":
            return value
        if kind == "name":
            if value not in FUNCS:
                raise ValueError(f"unknown name {value!r}")
            lo, hi = FUNCS[value]
            self.expect_op("(")
            args = [self.expr()]
            while True:
                tok = self.next()
                if tok == ("comma", ","):
                    args.append(self.expr())
                elif tok == ("op", ")"):
                    break
                else:
                    raise ValueError(f"expected ',' or ')' in {value}(…), got {tok[1]!r}")
            if len(args) < lo or (hi is not None and len(args) != hi):
                raise ValueError(f"wrong number of arguments for {value}: {len(args)}")
            if value == "abs":
                return abs(args[0])
            return min(args) if value == "min" else max(args)
        if kind == "op" and value == "(":
            inner = self.expr()
            self.expect_op(")")
            return inner
        raise ValueError(f"unexpected token {value!r}")


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    return Parser(tokenize(text)).parse()
