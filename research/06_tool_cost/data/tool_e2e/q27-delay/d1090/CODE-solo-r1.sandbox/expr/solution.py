"""Arithmetic expression evaluator (tokenizer + recursive-descent parser).

Precedence, lowest to highest: + -  <  * / // %  <  unary + -  <  **.
+ - * / // % are left-associative; ** is right-associative and binds
tighter than unary minus (-2 ** 2 == -4). / always yields a float.
"""

_FUNCTIONS = ("min", "max", "abs")


def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isdigit():
            start = i
            while i < n and text[i].isdigit():
                i += 1
            if i < n and text[i] == ".":
                i += 1
                while i < n and text[i].isdigit():
                    i += 1
            lexeme = text[start:i]
            tokens.append(float(lexeme) if "." in lexeme else int(lexeme))
        elif c == ".":
            if i + 1 < n and text[i + 1].isdigit():
                start = i
                i += 1
                while i < n and text[i].isdigit():
                    i += 1
                tokens.append(float(text[start:i]))
            else:
                raise ValueError("malformed number at position %d" % i)
        elif c.isalpha() or c == "_":
            start = i
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            tokens.append(text[start:i])
        elif c == "*" and i + 1 < n and text[i + 1] == "*":
            tokens.append("**")
            i += 2
        elif c == "*":
            tokens.append("*")
            i += 1
        elif c == "/":
            if i + 1 < n and text[i + 1] == "/":
                tokens.append("//")
                i += 2
            else:
                tokens.append("/")
                i += 1
        elif c in "+-()%," :
            tokens.append(c)  # binary/paren/comma operators
            i += 1
        else:
            raise ValueError("unknown character %r at position %d" % (c, i))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self._tokens = tokens
        self._pos = 0

    def _peek(self):
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _next(self):
        tok = self._peek()
        if tok is None:
            raise ValueError("unexpected end of input")
        self._pos += 1
        return tok

    def parse(self):
        value = self._additive()
        if self._peek() is not None:
            raise ValueError("unexpected token %r after expression" % (self._peek(),))
        return value

    def _additive(self):
        value = self._multiplicative()
        while True:
            tok = self._peek()
            if tok == "+":
                self._next()
                value = value + self._multiplicative()
            elif tok == "-":
                self._next()
                value = value - self._multiplicative()
            else:
                return value

    def _multiplicative(self):
        value = self._unary()
        while True:
            tok = self._peek()
            if tok in ("*", "/", "//", "%"):
                self._next()
                rhs = self._unary()
                if tok == "*":
                    value = value * rhs
                elif tok == "/":
                    value = value / rhs
                elif tok == "//":
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

    def _unary(self):
        tok = self._peek()
        if tok == "+":
            self._next()
            return +self._unary()
        if tok == "-":
            self._next()
            return -self._unary()
        return self._power()

    def _power(self):
        base = self._primary()
        if self._peek() == "**":
            self._next()
            # Right-associative: the exponent is a full unary expression,
            # so 2 ** 3 ** 2 == 2 ** (3 ** 2).
            exponent = self._unary()
            return base ** exponent
        return base

    def _primary(self):
        tok = self._next()
        if isinstance(tok, (int, float)):
            return tok
        if tok == "(":
            value = self._additive()
            if self._next() != ")":
                raise ValueError("missing closing parenthesis")
            return value
        if isinstance(tok, str) and tok.isidentifier():
            return self._call(tok)
        raise ValueError("unexpected token %r" % (tok,))

    def _call(self, name):
        if name not in _FUNCTIONS:
            raise ValueError("unknown name %r" % name)
        if self._next() != "(":
            raise ValueError("expected '(' after name %r" % name)
        args = []
        if self._peek() != ")":
            args.append(self._additive())
            while self._peek() == ",":
                self._next()
                args.append(self._additive())
        if self._next() != ")":
            raise ValueError("missing closing parenthesis in %s call" % name)
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs() takes exactly one argument, got %d" % len(args))
            return abs(args[0])
        if not args:
            raise ValueError("%s() requires at least one argument" % name)
        return max(args) if name == "max" else min(args)


def evaluate(text):
    """Parse and evaluate an arithmetic expression string."""
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    return _Parser(_tokenize(text)).parse()
