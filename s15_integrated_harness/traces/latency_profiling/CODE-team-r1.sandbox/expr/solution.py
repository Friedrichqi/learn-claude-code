"""Arithmetic expression evaluator.

Parses and evaluates an arithmetic expression string using a hand-written
tokenizer plus a recursive-descent parser (no eval/exec/compile/ast).
"""

_FUNCTIONS = ("min", "max", "abs")


def evaluate(text):
    """Parse and evaluate *text*, returning an int or float."""
    tokens = _tokenize(text)
    return _Parser(tokens).parse()


def _tokenize(text):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c.isdigit() or c == ".":
            j = i
            seen_dot = False
            while j < n and (text[j].isdigit() or (text[j] == "." and not seen_dot)):
                if text[j] == ".":
                    seen_dot = True
                j += 1
            literal = text[i:j]
            if literal == ".":
                raise ValueError("malformed number: %r" % literal)
            value = float(literal) if seen_dot else int(literal)
            tokens.append(("num", value))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("name", text[i:j]))
            i = j
            continue
        if text.startswith("**", i):
            tokens.append(("op", "**"))
            i += 2
            continue
        if text.startswith("//", i):
            tokens.append(("op", "//"))
            i += 2
            continue
        if c in "+-*/%(),":
            tokens.append(("op", c))
            i += 1
            continue
        raise ValueError("unknown character: %r" % c)
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    # -- token helpers ----------------------------------------------------
    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def at_op(self, *ops):
        tok = self.peek()
        return tok is not None and tok[0] == "op" and tok[1] in ops

    def expect_op(self, op):
        if not self.at_op(op):
            raise ValueError("expected %r, found %s" % (op, _describe(self.peek())))
        self.pos += 1

    # -- grammar ----------------------------------------------------------
    def parse(self):
        if not self.tokens:
            raise ValueError("empty input")
        value = self.expr()
        if self.pos != len(self.tokens):
            raise ValueError("unexpected token: %s" % _describe(self.peek()))
        return value

    def expr(self):
        value = self.term()
        while self.at_op("+", "-"):
            op = self.tokens[self.pos][1]
            self.pos += 1
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self):
        value = self.unary()
        while self.at_op("*", "/", "//", "%"):
            op = self.tokens[self.pos][1]
            self.pos += 1
            rhs = self.unary()
            if op == "*":
                value = value * rhs
            elif op == "/":
                value = value / rhs  # always float; ZeroDivisionError propagates
            elif op == "//":
                value = value // rhs
            else:
                value = value % rhs
        return value

    def unary(self):
        if self.at_op("+", "-"):
            op = self.tokens[self.pos][1]
            self.pos += 1
            operand = self.unary()
            return -operand if op == "-" else +operand
        return self.power()

    def power(self):
        base = self.primary()
        if self.at_op("**"):
            self.pos += 1
            exponent = self.unary()  # right-associative; allows 2 ** -3
            return base ** exponent
        return base

    def primary(self):
        tok = self.peek()
        if tok is None:
            raise ValueError("missing operand")
        kind, val = tok
        if kind == "num":
            self.pos += 1
            return val
        if kind == "op" and val == "(":
            self.pos += 1
            value = self.expr()
            self.expect_op(")")
            return value
        if kind == "name":
            self.pos += 1
            return self._call(val)
        raise ValueError("unexpected token: %s" % _describe(tok))

    def _call(self, name):
        if name not in _FUNCTIONS:
            raise ValueError("unknown name: %r" % name)
        self.expect_op("(")
        args = [self.expr()]
        while self.at_op(","):
            self.pos += 1
            args.append(self.expr())
        self.expect_op(")")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs() takes exactly one argument (%d given)" % len(args))
            return abs(args[0])
        if not args:
            raise ValueError("%s() expects at least one argument" % name)
        return min(args) if name == "min" else max(args)


def _describe(tok):
    if tok is None:
        return "end of input"
    return "%r" % (tok[1],)
