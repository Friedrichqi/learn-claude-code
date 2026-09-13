import re

TOKEN = re.compile(r"\s*(?:(\d+\.\d*|\.\d+|\d+)|(\*\*|//|[-+*/%(),])|([A-Za-z_]\w*)|(\S))")
FUNCS = {"min": (1, None), "max": (1, None), "abs": (1, 1)}

def tokenize(text):
    pos = 0; tokens = []
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        num, op, name, bad = m.groups()
        if num is not None:
            tokens.append(("num", float(num) if "." in num else int(num)))
        elif op is not None:
            tokens.append(("op", op))
        elif name is not None:
            tokens.append(("name", name))
        elif bad is not None:
            raise ValueError(f"unexpected character {bad!r}")
    if text[pos:].strip():
        raise ValueError("trailing garbage")
    return tokens

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens; self.i = 0
    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)
    def take(self):
        tok = self.peek(); self.i += 1; return tok
    def expect(self, value):
        tok = self.take()
        if tok != ("op", value):
            raise ValueError(f"expected {value}")
    def expr(self):
        left = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]; right = self.term()
            left = left + right if op == "+" else left - right
        return left
    def term(self):
        left = self.unary()
        while self.peek() in (("op", "*"), ("op", "/"), ("op", "//"), ("op", "%")):
            op = self.take()[1]; right = self.unary()
            if op == "*": left = left * right
            elif op == "/": left = left / right
            elif op == "//": left = left // right
            else: left = left % right
        return left
    def unary(self):
        if self.peek() == ("op", "-"):
            self.take(); return -self.unary()
        if self.peek() == ("op", "+"):
            self.take(); return +self.unary()
        return self.power()
    def power(self):
        base = self.atom()
        if self.peek() == ("op", "**"):
            self.take()
            return base ** self.unary()  # right-assoc, allows 2 ** -1
        return base
    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return val
        if kind == "op" and val == "(":
            v = self.expr(); self.expect(")"); return v
        if kind == "name":
            if val not in FUNCS:
                raise ValueError(f"unknown function {val}")
            self.expect("(")
            args = [self.expr()]
            while self.peek() == ("op", ","):
                self.take(); args.append(self.expr())
            self.expect(")")
            lo, hi = FUNCS[val]
            if len(args) < lo or (hi is not None and len(args) > hi):
                raise ValueError("wrong argument count")
            if len(args) == 1 and val in ("min", "max"):
                return args[0]
            return {"min": min, "max": max, "abs": abs}[val](*args)
        raise ValueError("expected operand")

def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    tokens = tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    parser = Parser(tokens)
    result = parser.expr()
    if parser.i != len(tokens):
        raise ValueError("unexpected token")
    return result
