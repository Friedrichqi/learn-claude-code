"""Recursive-descent arithmetic expression evaluator."""


class _Lexer:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.n = len(text)

    def skip_ws(self):
        while self.pos < self.n and self.text[self.pos].isspace():
            self.pos += 1

    def peek(self):
        self.skip_ws()
        if self.pos >= self.n:
            return None
        c = self.text[self.pos]
        if c in "+-*/%(),":
            return c
        if c.isalpha() or c == "_":
            i = self.pos
            while i < self.n and (self.text[i].isalnum() or self.text[i] == "_"):
                i += 1
            return self.text[self.pos:i]
        if c.isdigit() or c == ".":
            i = self.pos
            seen_dot = False
            while i < self.n and (self.text[i].isdigit() or (self.text[i] == "." and not seen_dot)):
                if self.text[i] == ".":
                    seen_dot = True
                i += 1
            tok = self.text[self.pos:i]
            if seen_dot and tok == ".":
                raise ValueError("malformed number")
            return tok
        raise ValueError("unknown character: %r" % c)

    def take(self):
        tok = self.peek()
        if tok is None:
            return None
        self.pos = self.text.index  # placeholder, replaced below
        return tok


def _tokenize(text):
    toks = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c in "+-*/%(),":
            if c == "/" and i + 1 < n and text[i + 1] == "/":
                toks.append("//")
                i += 2
            elif c == "*" and i + 1 < n and text[i + 1] == "*":
                toks.append("**")
                i += 2
            else:
                toks.append(c)
                i += 1
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            toks.append(text[i:j])
            i = j
            continue
        if c.isdigit() or c == ".":
            j = i
            seen_dot = False
            while j < n and (text[j].isdigit() or (text[j] == "." and not seen_dot)):
                if text[j] == ".":
                    seen_dot = True
                j += 1
            tok = text[i:j]
            if tok == ".":
                raise ValueError("malformed number")
            toks.append(tok)
            i = j
            continue
        raise ValueError("unknown character: %r" % c)
    return toks


_FUNCTIONS = {"min", "max", "abs"}


class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def next(self):
        tok = self.peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        self.pos += 1
        return tok

    def expect(self, tok):
        got = self.next()
        if got != tok:
            raise ValueError("expected %r, got %r" % (tok, got))

    def parse(self):
        val = self.expression()
        if self.pos != len(self.toks):
            raise ValueError("unexpected token: %r" % self.toks[self.pos])
        return val

    def _is_number(self, tok):
        return tok is not None and (
            tok[0].isdigit() or (tok == "." and False) or (tok[0] == "." and len(tok) > 1 and tok[1:].isdigit())
        )

    @staticmethod
    def _number(tok):
        if "." in tok:
            return float(tok)
        return int(tok)

    def expression(self):
        # + -
        left = self.term()
        while self.peek() in ("+", "-"):
            op = self.next()
            right = self.term()
            left = left + right if op == "+" else left - right
        return left

    def term(self):
        # * / // %
        left = self.unary()
        while self.peek() in ("*", "/", "//", "%"):
            op = self.next()
            right = self.unary()
            if op == "*":
                left = left * right
            elif op == "/":
                left = left / right
            elif op == "//":
                left = left // right
            else:
                left = left % right
        return left

    def unary(self):
        tok = self.peek()
        if tok in ("+", "-"):
            self.next()
            val = self.unary()
            return -val if tok == "-" else val
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek() == "**":
            self.next()
            exp = self.unary()  # right-assoc, unary allowed on RHS
            return base ** exp
        return base

    def atom(self):
        tok = self.peek()
        if tok is None:
            raise ValueError("missing operand")
        if self._is_number(tok):
            self.next()
            return self._number(tok)
        if tok == "(":
            self.next()
            val = self.expression()
            self.expect(")")
            return val
        if tok in _FUNCTIONS:
            self.next()
            self.expect("(")
            args = []
            if self.peek() != ")":
                while True:
                    args.append(self.expression())
                    if self.peek() == ",":
                        self.next()
                    elif self.peek() == ")":
                        break
                    else:
                        raise ValueError("expected ',' or ')'")
            self.expect(")")
            if tok == "abs":
                if len(args) != 1:
                    raise ValueError("abs takes exactly 1 argument")
                return abs(args[0])
            if not args:
                raise ValueError("%s takes at least 1 argument" % tok)
            return min(args) if tok == "min" else max(args)
        raise ValueError("unexpected token: %r" % tok)


def evaluate(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty input")
    return _Parser(_tokenize(text)).parse()
