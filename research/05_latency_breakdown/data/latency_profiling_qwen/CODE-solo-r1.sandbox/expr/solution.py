"""Arithmetic expression evaluator (tokenizer + recursive-descent parser)."""

_FUNCS = ("min", "max", "abs")
_OPS = ("+", "-", "*", "/", "//", "%", "**", "(", ")", ",")


def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
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
            num = text[i:j]
            tokens.append(("num", float(num) if seen_dot else int(num)))
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            name = text[i:j]
            if name not in _FUNCS:
                raise ValueError("unknown name: %r" % name)
            tokens.append(("name", name))
            i = j
        else:
            two = text[i:i + 2]
            if two in ("//", "**"):
                tokens.append(("op", two))
                i += 2
            elif c in "+-*/%(),":
                tokens.append(("op", c))
                i += 1
            else:
                raise ValueError("unknown character: %r" % c)
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def advance(self):
        tok = self.peek()
        if tok is None:
            raise ValueError("unexpected end of input")
        self.pos += 1
        return tok

    def parse_expr(self):
        left = self.parse_term()
        while True:
            tok = self.peek()
            if tok in (("op", "+"), ("op", "-")):
                self.advance()
                right = self.parse_term()
                left = left + right if tok == ("op", "+") else left - right
            else:
                return left

    def parse_term(self):
        left = self.parse_unary()
        while True:
            tok = self.peek()
            if tok in (("op", "*"), ("op", "/"), ("op", "//"), ("op", "%")):
                self.advance()
                right = self.parse_unary()
                if tok == ("op", "*"):
                    left = left * right
                elif tok == ("op", "/"):
                    left = left / right
                elif tok == ("op", "//"):
                    left = left // right
                else:
                    left = left % right
            else:
                return left

    def parse_unary(self):
        tok = self.peek()
        if tok in (("op", "-"), ("op", "+")):
            self.advance()
            value = self.parse_unary()
            return -value if tok == ("op", "-") else +value
        return self.parse_power()

    def parse_power(self):
        base = self.parse_primary()
        if self.peek() == ("op", "**"):
            self.advance()
            exponent = self.parse_unary()  # right-associative
            return base ** exponent
        return base

    def parse_primary(self):
        tok = self.peek()
        if tok is None:
            raise ValueError("unexpected end of input")
        kind, value = tok
        if kind == "num":
            self.advance()
            return value
        if kind == "name":
            return self.parse_call(value)
        if tok == ("op", "("):
            self.advance()
            inner = self.parse_expr()
            close = self.advance()
            if close != ("op", ")"):
                raise ValueError("unbalanced parentheses")
            return inner
        raise ValueError("unexpected token: %r" % (value,))

    def parse_call(self, name):
        self.advance()
        open_paren = self.advance()
        if open_paren != ("op", "("):
            raise ValueError("expected '(' after %r" % name)
        args = []
        if self.peek() != ("op", ")"):
            while True:
                args.append(self.parse_expr())
                tok = self.peek()
                if tok == ("op", ","):
                    self.advance()
                elif tok == ("op", ")"):
                    self.advance()
                    break
                else:
                    raise ValueError("expected ',' or ')' in arguments")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs takes exactly one argument")
            return abs(args[0])
        if not args:
            raise ValueError("%s takes at least one argument" % name)
        return min(args) if name == "min" else max(args)


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    parser = _Parser(tokens)
    value = parser.parse_expr()
    if parser.peek() is not None:
        raise ValueError("unexpected token: %r" % (parser.peek()[1],))
    return value
