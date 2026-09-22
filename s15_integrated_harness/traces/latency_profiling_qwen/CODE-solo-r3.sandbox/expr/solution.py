"""Recursive-descent arithmetic expression evaluator (no eval/ast)."""

# Token kinds
NUM, NAME, OP, LPAREN, RPAREN, COMMA = "NUM", "NAME", "OP", "LPAREN", "RPAREN", "COMMA"

_FUNCTIONS = ("min", "max", "abs")


def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            start = i
            seen_dot = False
            while i < n and (text[i].isdigit() or (text[i] == "." and not seen_dot)):
                if text[i] == ".":
                    seen_dot = True
                i += 1
            raw = text[start:i]
            if not any(c.isdigit() for c in raw):
                raise ValueError("malformed number: %r" % raw)
            tokens.append((NUM, float(raw) if seen_dot else int(raw)))
        elif ch.isalpha() or ch == "_":
            start = i
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            tokens.append((NAME, text[start:i]))
        elif ch == "(":
            tokens.append((LPAREN, ch))
            i += 1
        elif ch == ")":
            tokens.append((RPAREN, ch))
            i += 1
        elif ch == ",":
            tokens.append((COMMA, ch))
            i += 1
        elif text[i:i + 2] in ("**", "//"):
            tokens.append((OP, text[i:i + 2]))
            i += 2
        elif ch in "+-*/%":
            tokens.append((OP, ch))
            i += 1
        else:
            raise ValueError("unknown character: %r" % ch)
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
        token = self.peek()
        self.pos += 1
        return token

    def parse(self):
        if not self.tokens:
            raise ValueError("empty expression")
        value = self.additive()
        if self.pos != len(self.tokens):
            raise ValueError("unexpected token after expression: %r" % (self.peek()[1],))
        return value

    def additive(self):
        left = self.multiplicative()
        while self.peek() == (OP, "+") or self.peek() == (OP, "-"):
            op = self.advance()[1]
            left = left + self.multiplicative() if op == "+" else left - self.multiplicative()
        return left

    def multiplicative(self):
        left = self.unary()
        while self.peek()[0] == OP and self.peek()[1] in ("*", "/", "//", "%"):
            op = self.advance()[1]
            right = self.unary()
            if op == "*":
                left = left * right
            elif op == "/":
                left = left / right  # ZeroDivisionError propagates
            elif op == "//":
                left = left // right
            else:
                left = left % right
        return left

    def unary(self):
        kind, value = self.peek()
        if kind == OP and value in ("+", "-"):
            self.advance()
            operand = self.unary()
            return -operand if value == "-" else operand
        return self.power()

    def power(self):
        base = self.primary()
        if self.peek() == (OP, "**"):
            self.advance()
            # Right-associative; the exponent is a (possibly unary) expression.
            return base ** self.unary()
        return base

    def primary(self):
        kind, value = self.advance()
        if kind == NUM:
            return value
        if kind == LPAREN:
            inner = self.additive()
            if self.peek()[0] != RPAREN:
                raise ValueError("unbalanced parentheses")
            self.advance()
            return inner
        if kind == NAME:
            if value not in _FUNCTIONS:
                raise ValueError("unknown name: %r" % value)
            if self.peek()[0] != LPAREN:
                raise ValueError("expected '(' after function name %r" % value)
            self.advance()
            args = [self.additive()]
            while self.peek()[0] == COMMA:
                self.advance()
                args.append(self.additive())
            if self.peek()[0] != RPAREN:
                raise ValueError("unbalanced parentheses in call %r" % value)
            self.advance()
            if len(args) < 1:
                raise ValueError("%s takes at least 1 argument" % value)
            if value == "abs":
                if len(args) != 1:
                    raise ValueError("abs takes exactly 1 argument")
                return abs(args[0])
            return min(args) if value == "min" else max(args)
        raise ValueError("unexpected token: %r" % (value,))


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    return _Parser(_tokenize(text)).parse()
