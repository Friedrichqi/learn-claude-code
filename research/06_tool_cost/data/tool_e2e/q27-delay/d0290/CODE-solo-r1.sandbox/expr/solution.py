"""Arithmetic expression evaluator: tokenizer + recursive-descent parser.

Grammar (lowest to highest precedence):
    expression := term (('+' | '-') term)*
    term       := factor (('*' | '/' | '//' | '%') factor)*
    factor     := ('+' | '-') factor | power
    power      := atom ('**' factor)?
    atom       := NUMBER | '(' expression ')' | NAME '(' args ')'
"""


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or c == '.':
            j = i
            seen_dot = False
            while j < n and (text[j].isdigit() or text[j] == '.'):
                if text[j] == '.':
                    if seen_dot:
                        break
                    seen_dot = True
                j += 1
            chunk = text[i:j]
            if chunk == '.':
                raise ValueError("malformed number literal")
            value = float(chunk) if seen_dot else int(chunk)
            tokens.append(('num', value))
            i = j
        elif c.isalpha() or c == '_':
            j = i
            while j < n and (text[j].isalpha() or text[j] == '_'):
                j += 1
            tokens.append(('name', text[i:j]))
            i = j
        elif text.startswith('**', i):
            tokens.append(('op', '**'))
            i += 2
        elif text.startswith('//', i):
            tokens.append(('op', '//'))
            i += 2
        elif c in '+-*/%(),':
            tokens.append(('op', c))
            i += 1
        else:
            raise ValueError("unexpected character %r" % c)
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

    def expect_op(self, op):
        kind, value = self.advance()
        if not (kind == 'op' and value == op):
            raise ValueError("expected %r" % op)

    def parse(self):
        if not self.tokens:
            raise ValueError("empty expression")
        value = self.expression()
        if self.pos != len(self.tokens):
            raise ValueError("unexpected token after end of expression")
        return value

    def expression(self):
        value = self.term()
        while True:
            kind, op = self.peek()
            if kind == 'op' and op in ('+', '-'):
                self.advance()
                rhs = self.term()
                value = value + rhs if op == '+' else value - rhs
            else:
                return value

    def term(self):
        value = self.factor()
        while True:
            kind, op = self.peek()
            if kind == 'op' and op in ('*', '/', '//', '%'):
                self.advance()
                rhs = self.factor()
                if op == '*':
                    value = value * rhs
                elif op == '/':
                    value = value / rhs
                elif op == '//':
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

    def factor(self):
        kind, op = self.peek()
        if kind == 'op' and op in ('+', '-'):
            self.advance()
            operand = self.factor()
            return +operand if op == '+' else -operand
        return self.power()

    def power(self):
        base = self.atom()
        kind, op = self.peek()
        if kind == 'op' and op == '**':
            self.advance()
            # Right-associative; exponent is a factor so unary applies
            # before ** binds the base (-2 ** 2 == -4, 2 ** 3 ** 2 == 512).
            return base ** self.factor()
        return base

    def atom(self):
        kind, value = self.advance()
        if kind == 'num':
            return value
        if kind == 'op' and value == '(':
            inner = self.expression()
            self.expect_op(')')
            return inner
        if kind == 'name':
            return self.call(value)
        raise ValueError("unexpected token %r" % (value,))

    def call(self, name):
        k, v = self.peek()
        if not (k == 'op' and v == '('):
            raise ValueError("name %r not followed by '('" % name)
        self.advance()
        if name == 'abs':
            arg = self.expression()
            self.expect_op(')')
            return abs(arg)
        if name in ('min', 'max'):
            args = [self.expression()]
            while True:
                k, v = self.peek()
                if k == 'op' and v == ',':
                    self.advance()
                    args.append(self.expression())
                else:
                    break
            self.expect_op(')')
            return min(args) if name == 'min' else max(args)
        raise ValueError("unknown name %r" % name)


def evaluate(text):
    return _Parser(_tokenize(text)).parse()
