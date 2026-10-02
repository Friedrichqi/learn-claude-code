"""Arithmetic expression evaluator: hand-written tokenizer + recursive-descent parser."""

_NUM = "NUM"
_NAME = "NAME"
_OP = "OP"
_EOF = "EOF"


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch.isdigit():
            start = i
            while i < n and text[i].isdigit():
                i += 1
            if i + 1 < n and text[i] == "." and text[i + 1].isdigit():
                i += 1
                while i < n and text[i].isdigit():
                    i += 1
                tokens.append((_NUM, float(text[start:i]), start))
            else:
                tokens.append((_NUM, int(text[start:i]), start))
        elif ch == ".":
            if i + 1 < n and text[i + 1].isdigit():
                j = i + 1
                while j < n and text[j].isdigit():
                    j += 1
                tokens.append((_NUM, float(text[i:j]), i))
                i = j
            else:
                raise ValueError("malformed number at position %d" % i)
        elif ch.isalpha() or ch == "_":
            start = i
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            tokens.append((_NAME, text[start:i], start))
        elif ch == "*" and i + 1 < n and text[i + 1] == "*":
            tokens.append((_OP, "**", i))
            i += 2
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            tokens.append((_OP, "//", i))
            i += 2
        elif ch in "+-*/%(),":
            tokens.append((_OP, ch, i))
            i += 1
        else:
            raise ValueError("unknown character %r at position %d" % (ch, i))
    tokens.append((_EOF, None, n))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def _peek(self):
        return self.tokens[self.pos]

    def _advance(self):
        tok = self.tokens[self.pos]
        if tok[0] != _EOF:
            self.pos += 1
        return tok

    def _at_op(self, op):
        tok = self._peek()
        return tok[0] == _OP and tok[1] == op

    def _expect_op(self, op):
        if not self._at_op(op):
            tok = self._peek()
            raise ValueError("expected %r but found %r" % (op, tok[1]))
        return self._advance()

    def parse(self):
        value = self._expr()
        tok = self._peek()
        if tok[0] != _EOF:
            raise ValueError("unexpected token %r" % (tok[1],))
        return value

    def _expr(self):
        value = self._term()
        while True:
            if self._at_op("+"):
                self._advance()
                value = value + self._term()
            elif self._at_op("-"):
                self._advance()
                value = value - self._term()
            else:
                return value

    def _term(self):
        value = self._unary()
        while True:
            if self._at_op("*"):
                self._advance()
                value = value * self._unary()
            elif self._at_op("/"):
                self._advance()
                value = value / self._unary()
            elif self._at_op("//"):
                self._advance()
                value = value // self._unary()
            elif self._at_op("%"):
                self._advance()
                value = value % self._unary()
            else:
                return value

    def _unary(self):
        if self._at_op("-"):
            self._advance()
            return -self._unary()
        if self._at_op("+"):
            self._advance()
            return +self._unary()
        return self._power()

    def _power(self):
        base = self._primary()
        if self._at_op("**"):
            self._advance()
            # right-associative; exponent may itself start with unary
            return base ** self._unary()
        return base

    def _primary(self):
        tok = self._peek()
        kind = tok[0]
        if kind == _NUM:
            self._advance()
            return tok[1]
        if kind == _OP and tok[1] == "(":
            self._advance()
            value = self._expr()
            self._expect_op(")")
            return value
        if kind == _NAME:
            return self._call()
        if kind == _EOF:
            raise ValueError("unexpected end of input")
        raise ValueError("unexpected token %r" % (tok[1],))

    def _call(self):
        name = self._advance()[1]
        if name not in ("min", "max", "abs"):
            raise ValueError("unknown name %r" % name)
        self._expect_op("(")
        args = []
        if self._at_op(")"):
            self._advance()
        else:
            args.append(self._expr())
            while self._at_op(","):
                self._advance()
                args.append(self._expr())
            self._expect_op(")")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs expects exactly one argument")
            return abs(args[0])
        if not args:
            raise ValueError("%s expects at least one argument" % name)
        return min(args) if name == "min" else max(args)


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    tokens = _tokenize(text)
    if len(tokens) == 1:  # only the EOF token: empty input
        raise ValueError("empty expression")
    return _Parser(tokens).parse()
