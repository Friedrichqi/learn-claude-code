"""Recursive-descent arithmetic expression evaluator."""

_NUM_START = "0123456789."


def _tokenize(text):
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
            tokens.append(("num", text[i:j]))
            i = j
            continue
        if c.isalpha():
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("name", text[i:j]))
            i = j
            continue
        if text[i:i + 2] in ("**", "//"):
            tokens.append(("op", text[i:i + 2]))
            i += 2
            continue
        if c in ",":
            tokens.append(("comma", c))
            i += 1
            continue
        if c in "+-*/%()":
            tokens.append(("op" if c not in "()" else "paren", c))
            i += 1
            continue
        raise ValueError("unknown character: %r" % c)
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

    def expect_op(self, value):
        kind, val = self.peek()
        if val != value:
            raise ValueError("expected %r" % value)
        return self.advance()

    # expr := term (('+'|'-') term)*
    def parse_expr(self):
        value = self.parse_term()
        while True:
            kind, val = self.peek()
            if val in ("+", "-"):
                self.advance()
                rhs = self.parse_term()
                value = value + rhs if val == "+" else value - rhs
            else:
                return value

    # term := factor (('*'|'/'|'//'|'%') factor)*
    def parse_term(self):
        value = self.parse_factor()
        while True:
            kind, val = self.peek()
            if val in ("*", "/", "//", "%"):
                self.advance()
                rhs = self.parse_factor()
                if val == "*":
                    value = value * rhs
                elif val == "/":
                    value = value / rhs
                elif val == "//":
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

    # factor := unary | unary '**' factor
    def parse_factor(self):
        base = self.parse_unary()
        kind, val = self.peek()
        if val == "**":
            self.advance()
            exponent = self.parse_factor()  # right-associative
            return base ** exponent
        return base

    def parse_unary(self):
        kind, val = self.peek()
        if val == "+":
            self.advance()
            return +self.parse_unary()
        if val == "-":
            self.advance()
            # Python semantics: unary minus binds looser than '**',
            # and '-a ** b' parses as -(a ** b).
            operand = self.parse_factor()
            return -operand
        return self.parse_primary()

    def parse_primary(self):
        kind, val = self.peek()
        if kind is None:
            raise ValueError("unexpected end of expression")
        if kind == "num":
            self.advance()
            return _to_number(val)
        if val == "(":
            self.advance()
            value = self.parse_expr()
            self.expect_op(")")
            return value
        if kind == "name":
            self.advance()
            if val not in ("min", "max", "abs"):
                raise ValueError("unknown name: %r" % val)
            self.expect_op("(")
            args = []
            if self.peek()[1] != ")":
                args.append(self.parse_expr())
                while self.peek()[0] == "comma":
                    self.advance()
                    args.append(self.parse_expr())
            self.expect_op(")")
            if val == "abs":
                if len(args) != 1:
                    raise ValueError("abs() takes exactly one argument")
                return abs(args[0])
            if not args:
                raise ValueError("%s() takes at least one argument" % val)
            return max(args) if val == "max" else min(args)
        raise ValueError("unexpected token: %r" % val)


def _to_number(word):
    if "." in word:
        return float(word)
    return int(word)


def evaluate(text: str):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    parser = _Parser(tokens)
    value = parser.parse_expr()
    if parser.pos != len(parser.tokens):
        raise ValueError("unexpected token: %r" % parser.peek()[1])
    return value
