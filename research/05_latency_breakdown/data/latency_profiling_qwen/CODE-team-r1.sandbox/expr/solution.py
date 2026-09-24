"""Arithmetic expression evaluator.

Grammar (lowest to highest precedence):

    expr    := term (('+' | '-') term)*
    term    := unary (('*' | '/' | '//' | '%') unary)*
    unary   := ('+' | '-') unary | power
    power   := atom ('**' unary)?          # right-associative
    atom    := NUMBER | '(' expr ')' | funcall
    funcall := ('min' | 'max' | 'abs') '(' [expr (',' expr)*] ')'

Number literals are ints or floats; all operators follow Python semantics.
Anything malformed raises ValueError; division/modulo by zero raises
ZeroDivisionError (naturally, via Python's operators).
"""


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n\f\v":
            i += 1
            continue
        if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            j = i
            while j < n and text[j].isdigit():
                j += 1
            is_float = False
            if j < n and text[j] == ".":
                is_float = True
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
            literal = text[i:j]
            tokens.append(("num", float(literal) if is_float else int(literal)))
            i = j
            continue
        if c.isalpha():
            j = i
            while j < n and (text[j].isalnum()):
                j += 1
            tokens.append(("name", text[i:j]))
            i = j
            continue
        two = text[i:i + 2]
        if two in ("**", "//"):
            tokens.append(("op", two))
            i += 2
            continue
        if c in "+-*/%(),":
            tokens.append(("op", c))
            i += 1
            continue
        raise ValueError("unknown character %r" % c)
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
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect_op(self, op):
        kind, val = self.peek()
        if kind != "op" or val != op:
            raise ValueError("expected %r" % op)
        return self.advance()

    def parse_expr(self):
        left = self.parse_term()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("+", "-"):
                self.advance()
                right = self.parse_term()
                left = left + right if val == "+" else left - right
            else:
                return left

    def parse_term(self):
        left = self.parse_unary()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("*", "/", "//", "%"):
                self.advance()
                right = self.parse_unary()
                if val == "*":
                    left = left * right
                elif val == "/":
                    left = left / right
                elif val == "//":
                    left = left // right
                else:
                    left = left % right
            else:
                return left

    def parse_unary(self):
        kind, val = self.peek()
        if kind == "op" and val in ("+", "-"):
            self.advance()
            operand = self.parse_unary()
            return -operand if val == "-" else +operand
        return self.parse_power()

    def parse_power(self):
        base = self.parse_atom()
        kind, val = self.peek()
        if kind == "op" and val == "**":
            self.advance()
            exponent = self.parse_unary()  # right-associative
            return base ** exponent
        return base

    def parse_atom(self):
        kind, val = self.peek()
        if kind == "num":
            self.advance()
            return val
        if kind == "op" and val == "(":
            self.advance()
            value = self.parse_expr()
            self.expect_op(")")
            return value
        if kind == "name":
            self.advance()
            fname = val
            if fname not in ("min", "max", "abs"):
                raise ValueError("unknown name %r" % fname)
            self.expect_op("(")
            args = []
            k, v = self.peek()
            if not (k == "op" and v == ")"):
                args.append(self.parse_expr())
                while True:
                    k, v = self.peek()
                    if k == "op" and v == ",":
                        self.advance()
                        args.append(self.parse_expr())
                    else:
                        break
            self.expect_op(")")
            if fname == "abs":
                if len(args) != 1:
                    raise ValueError("abs takes exactly one argument")
                return abs(args[0])
            if not args:
                raise ValueError("%s requires at least one argument" % fname)
            if len(args) == 1:
                return args[0]
            return min(*args) if fname == "min" else max(*args)
        raise ValueError("unexpected %s" % ("end of input" if kind is None else repr(val)))


def evaluate(text):
    if not isinstance(text, str):
        raise ValueError("expected a string")
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    parser = _Parser(tokens)
    result = parser.parse_expr()
    if parser.pos != len(parser.tokens):
        kind, val = parser.tokens[parser.pos]
        raise ValueError("unexpected %r" % val)
    return result
