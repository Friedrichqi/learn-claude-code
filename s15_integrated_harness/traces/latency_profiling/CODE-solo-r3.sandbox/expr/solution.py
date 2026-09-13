"""Arithmetic expression evaluator: tokenizer + recursive-descent parser.

Grammar (lowest to highest precedence):
    expr   := term (('+' | '-') term)*
    term   := unary (('*' | '/' | '//' | '%') unary)*
    unary  := ('+' | '-') unary | power        # ** binds tighter than unary
    power  := atom ('**' unary)?               # right-associative
    atom   := NUMBER | NAME '(' args ')' | '(' expr ')'
"""

_TWO_CHAR_OPS = ("**", "//")
_ONE_CHAR_OPS = "+-*/%(),"


def _tokenize(text):
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            j = i
            seen_dot = False
            while j < n and (text[j].isdigit() or (text[j] == "." and not seen_dot)):
                if text[j] == ".":
                    seen_dot = True
                j += 1
            literal = text[i:j]
            if literal == ".":
                raise ValueError(f"malformed number at position {i}")
            value = float(literal) if seen_dot else int(literal)
            tokens.append(("NUM", value))
            i = j
            continue
        two = text[i:i + 2]
        if two in _TWO_CHAR_OPS:
            tokens.append(("OP", two))
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            tokens.append(("OP", ch))
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("NAME", text[i:j]))
            i = j
            continue
        raise ValueError(f"unknown character {ch!r} at position {i}")
    return tokens


_FUNCTIONS = {"min": None, "max": None, "abs": 1}


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def take(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def expect_op(self, symbol):
        kind, value = self.take()
        if kind != "OP" or value != symbol:
            raise ValueError(f"expected {symbol!r}")

    def parse(self):
        if not self.tokens:
            raise ValueError("empty expression")
        value = self.expr()
        if self.pos != len(self.tokens):
            kind, tok = self.peek()
            raise ValueError(f"unexpected {tok!r} after expression")
        return value

    def expr(self):
        value = self.term()
        while self.peek() == ("OP", "+") or self.peek() == ("OP", "-"):
            _, op = self.take()
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self):
        value = self.unary()
        while self.peek() in (("OP", "*"), ("OP", "/"), ("OP", "//"), ("OP", "%")):
            _, op = self.take()
            rhs = self.unary()
            if op == "*":
                value = value * rhs
            elif op == "/":
                value = value / rhs
            elif op == "//":
                value = value // rhs
            else:
                value = value % rhs
        return value

    def unary(self):
        kind, value = self.peek()
        if kind == "OP" and value in "+-":
            self.take()
            operand = self.unary()
            return -operand if value == "-" else +operand
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek() == ("OP", "**"):
            self.take()
            exponent = self.unary()  # right-associative; allows 2 ** -3
            return base ** exponent
        return base

    def atom(self):
        kind, value = self.take()
        if kind == "NUM":
            return value
        if kind == "OP" and value == "(":
            inner = self.expr()
            self.expect_op(")")
            return inner
        if kind == "NAME":
            if self.peek() != ("OP", "("):
                raise ValueError(f"unknown name {value!r}")
            self.take()
            args = [self.expr()]
            while self.peek() == ("OP", ","):
                self.take()
                args.append(self.expr())
            self.expect_op(")")
            return self._call(value, args)
        if kind is None:
            raise ValueError("missing operand")
        raise ValueError(f"unexpected token {value!r}")

    @staticmethod
    def _call(name, args):
        if name not in _FUNCTIONS:
            raise ValueError(f"unknown function {name!r}")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs() takes exactly one argument")
            return abs(args[0])
        if not args:
            raise ValueError(f"{name}() expects at least one argument")
        return min(args) if name == "min" else max(args)


def evaluate(text):
    """Parse and evaluate an arithmetic expression, returning an int or float."""
    return _Parser(_tokenize(text)).parse()
