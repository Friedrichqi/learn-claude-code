import re


class _Parser:
    def __init__(self, text):
        self.tokens = self._tokenize(text)
        self.pos = 0

    @staticmethod
    def _tokenize(text):
        toks = []
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if c.isspace():
                i += 1
            elif c.isdigit() or c == '.':
                j = i
                while j < n and (text[j].isdigit() or text[j] == '.'):
                    j += 1
                num = text[i:j]
                if num.count('.') > 1:
                    raise ValueError(f"malformed number: {num}")
                toks.append(('num', num))
                i = j
            elif c.isalpha() or c == '_':
                j = i
                while j < n and (text[j].isalnum() or text[j] == '_'):
                    j += 1
                toks.append(('name', text[i:j]))
                i = j
            elif text.startswith('**', i) or text.startswith('//', i):
                toks.append(('op', text[i:i+2]))
                i += 2
            elif c in '+-*/%(),':
                toks.append(('op', c))
                i += 1
            else:
                raise ValueError(f"unknown character: {c!r}")
        return toks

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def next(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def expect(self, kind, value=None):
        k, v = self.next()
        if k != kind or (value is not None and v != value):
            raise ValueError(f"expected {value or kind}, got {v!r}")
        return v

    def parse(self):
        if not self.tokens:
            raise ValueError("empty input")
        result = self.expr()
        if self.pos != len(self.tokens):
            raise ValueError(f"unexpected token: {self.tokens[self.pos][1]!r}")
        return result

    def expr(self):
        return self.additive()

    def additive(self):
        node = self.multiplicative()
        while True:
            k, v = self.peek()
            if k == 'op' and v in ('+', '-'):
                self.next()
                right = self.multiplicative()
                node = node + right if v == '+' else node - right
            else:
                return node

    def multiplicative(self):
        node = self.unary()
        while True:
            k, v = self.peek()
            if k == 'op' and v in ('*', '/', '//', '%'):
                self.next()
                right = self.unary()
                if v == '*':
                    node = node * right
                elif v == '/':
                    if right == 0:
                        raise ZeroDivisionError("division by zero")
                    node = node / right
                elif v == '//':
                    if right == 0:
                        raise ZeroDivisionError("integer division by zero")
                    node = node // right
                else:
                    if right == 0:
                        raise ZeroDivisionError("modulo by zero")
                    node = node % right
            else:
                return node

    def unary(self):
        k, v = self.peek()
        if k == 'op' and v in ('+', '-'):
            self.next()
            operand = self.unary()
            return operand if v == '+' else -operand
        return self.power()

    def power(self):
        base = self.primary()
        k, v = self.peek()
        if k == 'op' and v == '**':
            self.next()
            # right-associative: exponent is unary level so
            # 2 ** -3 works, but -2 ** 2 is handled by unary above
            exp = self.unary()
            return base ** exp
        return base

    def primary(self):
        k, v = self.next()
        if k == 'num':
            if '.' in v:
                return float(v)
            return int(v)
        if k == 'name':
            if v not in ('min', 'max', 'abs'):
                raise ValueError(f"unknown name: {v!r}")
            self.expect('op', '(')
            args = [self.expr()]
            while True:
                k2, v2 = self.peek()
                if k2 == 'op' and v2 == ',':
                    self.next()
                    args.append(self.expr())
                else:
                    break
            self.expect('op', ')')
            if len(args) < 1:
                raise ValueError("expected at least one argument")
            if v == 'abs' and len(args) != 1:
                raise ValueError("abs takes exactly one argument")
            if len(args) == 1 and v != 'abs':
                return args[0]
            return {'min': min, 'max': max, 'abs': abs}[v](*args)
        if k == 'op' and v == '(':
            node = self.expr()
            self.expect('op', ')')
            return node
        raise ValueError(f"unexpected token: {v!r}")


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("expected a string")
    return _Parser(text).parse()
