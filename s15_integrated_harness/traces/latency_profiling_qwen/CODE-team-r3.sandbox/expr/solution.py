"""Arithmetic expression evaluator: tokenizer + recursive-descent parser.

Grammar (lowest to highest precedence):
    expr   := term (('+' | '-') term)*
    term   := unary (('*' | '/' | '//' | '%') unary)*
    unary  := ('+' | '-') unary | power
    power  := atom ('**' unary)?          # right-associative; unary binds looser
    atom   := NUMBER | '(' expr ')' | call
    call   := 'min' '(' args ')' | 'max' '(' args ')' | 'abs' '(' expr ')'
"""

_DIGITS = set("0123456789")
_SINGLE_OPS = set("+-*/%(),()")


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c in _DIGITS or c == ".":
            j = i
            seen_dot = False
            while j < n:
                ch = text[j]
                if ch in _DIGITS:
                    j += 1
                elif ch == "." and not seen_dot:
                    seen_dot = True
                    j += 1
                else:
                    break
            literal = text[i:j]
            tokens.append(("num", float(literal) if seen_dot else int(literal)))
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("name", text[i:j]))
            i = j
        elif text[i:i + 2] == "**":
            tokens.append(("**", "**"))
            i += 2
        elif text[i:i + 2] == "//":
            tokens.append(("//", "//"))
            i += 2
        elif c in _SINGLE_OPS:
            tokens.append((c, c))
            i += 1
        else:
            raise ValueError("unknown character %r" % c)
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

    def expect(self, kind):
        tok = self.advance()
        if tok[0] != kind:
            raise ValueError("expected %r, got %r" % (kind, tok[0]))
        return tok

    def parse(self):
        value = self.parse_expr()
        if self.pos != len(self.tokens):
            raise ValueError("unexpected token %r" % (self.tokens[self.pos][0],))
        return value

    def parse_expr(self):
        value = self.parse_term()
        while True:
            kind, _ = self.peek()
            if kind == "+":
                self.advance()
                value = value + self.parse_term()
            elif kind == "-":
                self.advance()
                value = value - self.parse_term()
            else:
                return value

    def parse_term(self):
        value = self.parse_unary()
        while True:
            kind, _ = self.peek()
            if kind not in ("*", "/", "//", "%"):
                return value
            self.advance()
            rhs = self.parse_unary()
            if kind == "*":
                value = value * rhs
            elif kind == "/":
                value = value / rhs
            elif kind == "//":
                value = value // rhs
            else:
                value = value % rhs

    def parse_unary(self):
        kind, _ = self.peek()
        if kind == "+":
            self.advance()
            return +self.parse_unary()
        if kind == "-":
            self.advance()
            return -self.parse_unary()
        return self.parse_power()

    def parse_power(self):
        base = self.parse_atom()
        if self.peek()[0] == "**":
            self.advance()
            return base ** self.parse_unary()
        return base

    def parse_args(self):
        args = []
        if self.peek()[0] != ")":
            args.append(self.parse_expr())
            while self.peek()[0] == ",":
                self.advance()
                args.append(self.parse_expr())
        self.expect(")")
        return args

    def parse_atom(self):
        kind, value = self.peek()
        if kind == "num":
            self.advance()
            return value
        if kind == "(":
            self.advance()
            inner = self.parse_expr()
            self.expect(")")
            return inner
        if kind == "name":
            self.advance()
            if value not in ("min", "max", "abs"):
                raise ValueError("unknown name %r" % value)
            self.expect("(")
            args = self.parse_args()
            if value in ("min", "max"):
                if not args:
                    raise ValueError("%s() needs at least one argument" % value)
                return min(args) if value == "min" else max(args)
            if len(args) != 1:
                raise ValueError("abs() needs exactly one argument")
            return abs(args[0])
        if kind is None:
            raise ValueError("unexpected end of expression")
        raise ValueError("unexpected token %r" % kind)


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    return _Parser(_tokenize(text)).parse()
