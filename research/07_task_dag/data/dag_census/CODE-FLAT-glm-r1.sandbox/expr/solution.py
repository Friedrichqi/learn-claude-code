"""Arithmetic expression evaluator.

Implements a small tokenizer plus a recursive-descent parser for the grammar
described in README.md.  No use of eval/exec/compile/ast.
"""

_FUNCTIONS = ("min", "max", "abs")
_DIGITS = "0123456789"
_SINGLE_CHAR_OPS = "+-*/%(),"


def _is_digit(ch):
    return ch in _DIGITS


def _is_name_start(ch):
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch == "_"


def _is_name_char(ch):
    return _is_name_start(ch) or _is_digit(ch)


def _tokenize(text):
    """Turn the input string into a list of (kind, value) tokens.

    Kinds: "number" (int/float payload), "name" (function name),
    "op" (operator or punctuation), "end" (sentinel).
    """
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if _is_digit(ch):
            j = i
            while j < n and _is_digit(text[j]):
                j += 1
            is_float = False
            if j < n and text[j] == "." and j + 1 < n and _is_digit(text[j + 1]):
                is_float = True
                j += 1
                while j < n and _is_digit(text[j]):
                    j += 1
            raw = text[i:j]
            tokens.append(("number", float(raw) if is_float else int(raw)))
            i = j
            continue
        if ch == ".":
            if i + 1 < n and _is_digit(text[i + 1]):
                j = i + 1
                while j < n and _is_digit(text[j]):
                    j += 1
                tokens.append(("number", float(text[i:j])))
                i = j
                continue
            raise ValueError("unexpected '.' at position %d" % i)
        if _is_name_start(ch):
            j = i
            while j < n and _is_name_char(text[j]):
                j += 1
            name = text[i:j]
            if name not in _FUNCTIONS:
                raise ValueError("unknown name %r" % name)
            tokens.append(("name", name))
            i = j
            continue
        if text.startswith("**", i):
            tokens.append(("op", "**"))
            i += 2
            continue
        if text.startswith("//", i):
            tokens.append(("op", "//"))
            i += 2
            continue
        if ch in _SINGLE_CHAR_OPS:
            tokens.append(("op", ch))
            i += 1
            continue
        raise ValueError("unexpected character %r at position %d" % (ch, i))
    tokens.append(("end", None))
    return tokens


class _Parser:
    """Recursive-descent parser; precedence from lowest to highest:

    additive (+ -)  <  multiplicative (* / // %)  <  unary (+ -)  <  power (**)
    """

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def match_op(self, *ops):
        kind, value = self.peek()
        if kind == "op" and value in ops:
            self.pos += 1
            return value
        return None

    def expect_op(self, op):
        if not self.match_op(op):
            kind, value = self.peek()
            raise ValueError("expected %r, found %r" % (op, value))

    # ---- grammar levels -------------------------------------------------

    def parse_expression(self):
        return self.parse_additive()

    def parse_additive(self):
        value = self.parse_multiplicative()
        while True:
            op = self.match_op("+", "-")
            if op is None:
                return value
            rhs = self.parse_multiplicative()
            value = value + rhs if op == "+" else value - rhs

    def parse_multiplicative(self):
        value = self.parse_unary()
        while True:
            op = self.match_op("*", "/", "//", "%")
            if op is None:
                return value
            rhs = self.parse_unary()
            if op == "*":
                value = value * rhs
            elif op == "/":
                value = value / rhs  # always float; ZeroDivisionError on 0
            elif op == "//":
                value = value // rhs
            else:
                value = value % rhs

    def parse_unary(self):
        op = self.match_op("+", "-")
        if op is not None:
            operand = self.parse_unary()
            return operand if op == "+" else -operand
        return self.parse_power()

    def parse_power(self):
        base = self.parse_primary()
        if self.match_op("**"):
            # Right-associative; the exponent may itself carry unary operators
            # (e.g. `2 ** -3`).  Because unary descent goes through
            # parse_power, `**` binds tighter than a prefix sign, so
            # `-2 ** 2` parses as `-(2 ** 2)`.
            exponent = self.parse_unary()
            return base ** exponent
        return base

    def parse_primary(self):
        kind, value = self.peek()
        if kind == "number":
            self.advance()
            return value
        if kind == "op" and value == "(":
            self.advance()
            result = self.parse_expression()
            self.expect_op(")")
            return result
        if kind == "name":
            self.advance()
            return self.parse_call(value)
        raise ValueError("expected a number, function call, or '('")

    def parse_call(self, name):
        self.expect_op("(")
        if self.match_op(")"):
            raise ValueError("%s() requires at least 1 argument" % name)
        args = [self.parse_expression()]
        while self.match_op(","):
            args.append(self.parse_expression())
        self.expect_op(")")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs() takes exactly 1 argument")
            return abs(args[0])
        return min(args) if name == "min" else max(args)

    # ---- top level -------------------------------------------------------

    def parse_program(self):
        result = self.parse_expression()
        kind, value = self.peek()
        if kind != "end":
            raise ValueError("unexpected trailing token %r" % (value,))
        return result


def evaluate(text: str):
    """Parse and evaluate an arithmetic expression string."""
    if not isinstance(text, str):
        raise ValueError("expected a string")
    parser = _Parser(_tokenize(text))
    return parser.parse_program()
