"""Arithmetic expression evaluator: tokenizer + recursive-descent parser.

Grammar (lowest to highest precedence):

    expr   := term (('+' | '-') term)*
    term   := unary (('*' | '/' | '//' | '%') unary)*
    unary  := ('+' | '-') unary | power
    power  := primary ('**' unary)?          # right-associative
    primary:= NUMBER | NAME '(' args ')' | '(' expr ')'

`**` binds tighter than unary minus (like Python): -2 ** 2 == -4.
"""

_FUNCTIONS = {"min": min, "max": max, "abs": abs}
_ADD_OPS = ("+", "-")
_MUL_OPS = ("*", "/", "//", "%")


def _tokenize(text):
    """Return a list of (kind, value) tokens; raise ValueError on bad input."""
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or ch == ".":
            start = i
            while i < n and text[i].isdigit():
                i += 1
            is_float = False
            if i < n and text[i] == ".":
                is_float = True
                i += 1
                while i < n and text[i].isdigit():
                    i += 1
            lexeme = text[start:i]
            if is_float:
                if lexeme == ".":
                    raise ValueError("malformed number at position %d" % start)
                if i < n and text[i] == ".":
                    raise ValueError("malformed number at position %d" % start)
                tokens.append(("NUM", float(lexeme)))
            else:
                tokens.append(("NUM", int(lexeme)))
            continue
        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            tokens.append(("NAME", text[start:i]))
            continue
        if text.startswith("**", i) or text.startswith("//", i):
            tokens.append(("OP", text[i:i + 2]))
            i += 2
            continue
        if ch in _ADD_OPS or ch in ("*", "/", "%"):
            tokens.append(("OP", ch))
            i += 1
            continue
        if ch == "(":
            tokens.append(("LPAREN", ch))
            i += 1
            continue
        if ch == ")":
            tokens.append(("RPAREN", ch))
            i += 1
            continue
        if ch == ",":
            tokens.append(("COMMA", ch))
            i += 1
            continue
        raise ValueError("unexpected character %r at position %d" % (ch, i))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def _peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def _error(self, message):
        raise ValueError(message)

    def _expect(self, kind, what):
        tok = self._peek()
        if tok is None or tok[0] != kind:
            self._error("expected %s" % what)
        self.pos += 1
        return tok

    def parse(self):
        value = self._expr()
        if self._peek() is not None:
            self._error("unexpected token after end of expression")
        return value

    def _expr(self):
        value = self._term()
        while True:
            tok = self._peek()
            if tok is not None and tok[0] == "OP" and tok[1] in _ADD_OPS:
                self.pos += 1
                rhs = self._term()
                value = value + rhs if tok[1] == "+" else value - rhs
            else:
                return value

    def _term(self):
        value = self._unary()
        while True:
            tok = self._peek()
            if tok is not None and tok[0] == "OP" and tok[1] in _MUL_OPS:
                op = tok[1]
                self.pos += 1
                rhs = self._unary()
                if op == "*":
                    value = value * rhs
                elif op == "/":
                    value = value / rhs
                elif op == "//":
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

    def _unary(self):
        tok = self._peek()
        if tok is not None and tok[0] == "OP" and tok[1] in _ADD_OPS:
            self.pos += 1
            operand = self._unary()
            return +operand if tok[1] == "+" else -operand
        return self._power()

    def _power(self):
        base = self._primary()
        tok = self._peek()
        if tok is not None and tok[0] == "OP" and tok[1] == "**":
            self.pos += 1
            exponent = self._unary()
            return base ** exponent
        return base

    def _primary(self):
        tok = self._peek()
        if tok is None:
            self._error("unexpected end of expression")
        kind, value = tok
        if kind == "NUM":
            self.pos += 1
            return value
        if kind == "NAME":
            self.pos += 1
            if value not in _FUNCTIONS:
                raise ValueError("unknown function or name %r" % value)
            return self._call(value)
        if kind == "LPAREN":
            self.pos += 1
            inner = self._expr()
            self._expect("RPAREN", "')'")
            return inner
        self._error("unexpected token %r" % (value,))

    def _call(self, name):
        self._expect("LPAREN", "'(' after function name")
        args = [self._expr()]
        while True:
            tok = self._peek()
            if tok is not None and tok[0] == "COMMA":
                self.pos += 1
                args.append(self._expr())
            else:
                break
        self._expect("RPAREN", "')' to close function call")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs() takes exactly one argument")
            return abs(args[0])
        if not args:
            raise ValueError("%s() requires at least one argument" % name)
        return _FUNCTIONS[name](args)


def evaluate(text):
    """Parse and evaluate an arithmetic expression string to an int or float."""
    if not isinstance(text, str):
        raise ValueError("evaluate() expects a string")
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    return _Parser(tokens).parse()
