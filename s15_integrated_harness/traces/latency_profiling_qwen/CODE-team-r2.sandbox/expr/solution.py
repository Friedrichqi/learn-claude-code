"""Arithmetic expression evaluator.

Implements ``evaluate(text)`` using a hand-written tokenizer and a
recursive-descent parser with the following grammar (highest precedence
last, matching Python semantics):

    expr   := term (('+' | '-') term)*
    term   := unary (('*' | '/' | '//' | '%') unary)*
    unary  := ('-' | '+') unary | power
    power  := atom ('**' unary)?          # right-associative
    atom   := NUMBER | '(' expr ')' | call
    call   := 'min' '(' expr (',' expr)* ')'
            | 'max' '(' expr (',' expr)* ')'
            | 'abs' '(' expr ')'
"""


def _tokenize(text):
    """Turn *text* into a list of (kind, value) tokens."""
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or ch == ".":
            start = i
            is_float = False
            if ch == ".":
                if i + 1 >= n or not text[i + 1].isdigit():
                    raise ValueError("malformed number at position %d" % i)
                is_float = True
                i += 1
                while i < n and text[i].isdigit():
                    i += 1
            else:
                while i < n and text[i].isdigit():
                    i += 1
                if i < n and text[i] == ".":
                    is_float = True
                    i += 1
                    while i < n and text[i].isdigit():
                        i += 1
            literal = text[start:i]
            tokens.append(("num", float(literal) if is_float else int(literal)))
            continue
        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (text[i].isalpha() or text[i] == "_"):
                i += 1
            tokens.append(("name", text[start:i]))
            continue
        two = text[i:i + 2]
        if two == "**":
            tokens.append(("op", "**"))
            i += 2
            continue
        if two == "//":
            tokens.append(("op", "//"))
            i += 2
            continue
        if ch in "+-*/%":
            tokens.append(("op", ch))
            i += 1
            continue
        if ch == "(":
            tokens.append(("lparen", None))
            i += 1
            continue
        if ch == ")":
            tokens.append(("rparen", None))
            i += 1
            continue
        if ch == ",":
            tokens.append(("comma", None))
            i += 1
            continue
        raise ValueError("unexpected character %r at position %d" % (ch, i))
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
        value = self.parse_expr()
        kind, val = self.peek()
        if kind is not None:
            raise ValueError("unexpected token %r" % (val,))
        return value

    def parse_expr(self):
        value = self.parse_term()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("+", "-"):
                self.advance()
                rhs = self.parse_term()
                value = value + rhs if val == "+" else value - rhs
            else:
                return value

    def parse_term(self):
        value = self.parse_unary()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("*", "/", "//", "%"):
                self.advance()
                rhs = self.parse_unary()
                if val == "*":
                    value = value * rhs
                elif val == "/":
                    value = value / rhs
                elif val == "//":
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

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
            # The exponent may itself be unary and power recurses through
            # unary, which yields right-associativity (2 ** 3 ** 2 == 512).
            exponent = self.parse_unary()
            return base ** exponent
        return base

    def parse_atom(self):
        kind, val = self.peek()
        if kind == "num":
            self.advance()
            return val
        if kind == "lparen":
            self.advance()
            value = self.parse_expr()
            k2, v2 = self.advance()
            if k2 != "rparen":
                raise ValueError("expected ')' but found %r" % (v2,))
            return value
        if kind == "name":
            self.advance()
            if val not in ("min", "max", "abs"):
                raise ValueError("unknown name %r" % (val,))
            k2, v2 = self.advance()
            if k2 != "lparen":
                raise ValueError("expected '(' after %r" % (val,))
            k3, v3 = self.peek()
            if k3 == "rparen":
                raise ValueError("%s requires at least one argument" % val)
            args = [self.parse_expr()]
            while True:
                k4, v4 = self.peek()
                if k4 == "comma":
                    self.advance()
                    args.append(self.parse_expr())
                elif k4 == "rparen":
                    self.advance()
                    break
                else:
                    raise ValueError("expected ',' or ')' but found %r" % (v4,))
            if val == "abs":
                if len(args) != 1:
                    raise ValueError("abs() takes exactly one argument")
                return abs(args[0])
            if val == "min":
                return min(args)
            return max(args)
        if kind is None:
            raise ValueError("unexpected end of expression")
        raise ValueError("unexpected token %r" % (val,))


def evaluate(text: str):
    """Parse and evaluate an arithmetic expression, returning int or float."""
    if not isinstance(text, str):
        raise ValueError("expression must be a string")
    return _Parser(_tokenize(text)).parse()
