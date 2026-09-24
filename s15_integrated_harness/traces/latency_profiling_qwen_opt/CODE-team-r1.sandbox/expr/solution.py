"""Arithmetic expression evaluator (tokenizer + recursive-descent parser)."""

import operator as _op


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

    def expect(self, kind, value=None):
        k, v = self.advance()
        if k != kind or (value is not None and v != value):
            raise ValueError("expected %s, got %s" % (value or kind, v))
        return k, v

    def at_end(self):
        return self.pos >= len(self.tokens)

    # expression := addsub
    def parse(self):
        val = self.addsub()
        if not self.at_end():
            raise ValueError("unexpected token: %s" % (self.peek()[1],))
        return val

    def addsub(self):
        left = self.muldiv()
        while True:
            k, v = self.peek()
            if v in ("+", "-") and k == "op":
                self.advance()
                right = self.muldiv()
                left = left + right if v == "+" else left - right
            else:
                return left

    def muldiv(self):
        left = self.unary()
        while True:
            k, v = self.peek()
            if k == "op" and v in ("*", "/", "//", "%"):
                self.advance()
                right = self.unary()
                if v == "*":
                    left = left * right
                elif v == "/":
                    left = left / right
                elif v == "//":
                    left = left // right
                else:
                    left = left % right
            else:
                return left

    def unary(self):
        k, v = self.peek()
        if k == "op" and v in ("+", "-"):
            self.advance()
            operand = self.unary()
            return -operand if v == "-" else +operand
        return self.power()

    def power(self):
        base = self.primary()
        k, v = self.peek()
        if k == "op" and v == "**":
            self.advance()
            exponent = self.unary()
            return base ** exponent
        return base

    def primary(self):
        k, v = self.peek()
        if k == "num":
            self.advance()
            return v
        if k == "lparen":
            self.advance()
            val = self.addsub()
            self.expect("rparen", ")")
            return val
        if k == "name":
            self.advance()
            if v not in ("min", "max", "abs"):
                raise ValueError("unknown function: %s" % v)
            self.expect("lparen", "(")
            args = []
            if self.peek() == ("rparen", ")"):
                raise ValueError("wrong number of arguments for %s" % v)
            args.append(self.addsub())
            while self.peek() == ("op", ","):
                self.advance()
                args.append(self.addsub())
            self.expect("rparen", ")")
            if v == "abs":
                if len(args) != 1:
                    raise ValueError("wrong number of arguments for abs")
                return abs(args[0])
            if v == "min":
                return min(args)
            return max(args)
        raise ValueError("unexpected token: %s" % (v,))


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            j = i
            seen_dot = False
            while j < n and (text[j].isdigit() or (text[j] == "." and not seen_dot)):
                if text[j] == ".":
                    seen_dot = True
                j += 1
            lit = text[i:j]
            if seen_dot:
                tokens.append(("num", float(lit)))
            else:
                tokens.append(("num", int(lit)))
            i = j
            continue
        if c.isalpha():
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("name", text[i:j]))
            i = j
            continue
        if c == "*" and i + 1 < n and text[i + 1] == "*":
            tokens.append(("op", "**"))
            i += 2
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            tokens.append(("op", "//"))
            i += 2
            continue
        if c in "+-*/%":
            tokens.append(("op", c))
            i += 1
            continue
        if c == "(":
            tokens.append(("lparen", c))
            i += 1
            continue
        if c == ")":
            tokens.append(("rparen", c))
            i += 1
            continue
        if c == ",":
            tokens.append(("op", ","))
            i += 1
            continue
        raise ValueError("unknown character: %s" % c)
    return tokens


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    return _Parser(tokens).parse()
