"""Arithmetic expression evaluator (tokenizer + recursive descent parser)."""


class _Parser:
    def __init__(self, text):
        self.toks = self._tokenize(text)
        self.pos = 0

    # ------------------------------------------------------------------ #
    # Tokenizer
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tokenize(text):
        tokens = []
        i, n = 0, len(text)
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
                            raise ValueError("malformed number: %r" % text[i:j + 1])
                        seen_dot = True
                    j += 1
                literal = text[i:j]
                tokens.append(('float', float(literal)) if seen_dot
                              else ('int', int(literal)))
                i = j
            elif c.isalpha() or c == '_':
                j = i
                while j < n and (text[j].isalnum() or text[j] == '_'):
                    j += 1
                tokens.append(('name', text[i:j]))
                i = j
            elif text[i:i + 2] in ('**', '//'):
                tokens.append((text[i:i + 2], text[i:i + 2]))
                i += 2
            elif c in '+-*/%(),':
                tokens.append((c, c))
                i += 1
            else:
                raise ValueError("unknown character: %r" % c)
        return tokens

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else (None, None)

    def _advance(self):
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def _accept(self, kind):
        if self._peek()[0] == kind:
            return self._advance()
        return None

    # ------------------------------------------------------------------ #
    # Grammar
    # ------------------------------------------------------------------ #
    def parse(self):
        if not self.toks:
            raise ValueError("empty input")
        value = self.expr()
        if self.pos != len(self.toks):
            raise ValueError("unexpected token: %r" % (self.toks[self.pos][1],))
        return value

    def expr(self):
        return self.additive()

    def additive(self):
        left = self.multiplicative()
        while True:
            kind, _ = self._peek()
            if kind in ('+', '-'):
                self._advance()
                right = self.multiplicative()
                left = left + right if kind == '+' else left - right
            else:
                return left

    def multiplicative(self):
        left = self.unary()
        while True:
            kind, _ = self._peek()
            if kind in ('*', '/', '//', '%'):
                self._advance()
                right = self.unary()
                if kind == '*':
                    left = left * right
                elif kind == '/':
                    if right == 0:
                        raise ZeroDivisionError("division by zero")
                    left = left / right
                elif kind == '//':
                    if right == 0:
                        raise ZeroDivisionError("integer division by zero")
                    left = left // right
                else:
                    if right == 0:
                        raise ZeroDivisionError("modulo by zero")
                    left = left % right
            else:
                return left

    def unary(self):
        kind, _ = self._peek()
        if kind in ('+', '-'):
            self._advance()
            operand = self.unary()
            return operand if kind == '+' else -operand
        return self.power()

    def power(self):
        base = self.atom()
        if self._peek()[0] == '**':
            self._advance()
            exponent = self.unary()  # right-associative, allows 2 ** -1
            return base ** exponent
        return base

    def atom(self):
        kind, value = self._peek()
        if kind == 'int' or kind == 'float':
            self._advance()
            return value
        if kind == '(':
            self._advance()
            inner = self.expr()
            if not self._accept(')'):
                raise ValueError("unbalanced parentheses")
            return inner
        if kind == 'name':
            self._advance()
            name = value
            if name not in ('min', 'max', 'abs'):
                raise ValueError("unknown name: %r" % name)
            if not self._accept('('):
                raise ValueError("expected '(' after %r" % name)
            args = []
            if self._peek()[0] != ')':
                args.append(self.expr())
                while self._accept(','):
                    args.append(self.expr())
            if not self._accept(')'):
                raise ValueError("unbalanced parentheses")
            if name == 'abs':
                if len(args) != 1:
                    raise ValueError("abs takes exactly one argument")
                return abs(args[0])
            if not args:
                raise ValueError("%s takes at least one argument" % name)
            return min(args) if name == 'min' else max(args)
        raise ValueError("unexpected token: %r" % (value,))


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    return _Parser(text).parse()
