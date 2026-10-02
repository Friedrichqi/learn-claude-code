"""Arithmetic expression evaluator (tokenizer + recursive-descent parser).

Precedence, lowest to highest: + -  <  * / // %  <  unary - +  <  **
** is right-associative; the other binary operators are left-associative.
"""


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            if j < n and text[j] == "." and j + 1 < n and text[j + 1].isdigit():
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
                tokens.append(("num", float(text[i:j])))
            else:
                tokens.append(("num", int(text[i:j])))
            i = j
            continue
        if ch == "." and i + 1 < n and text[i + 1].isdigit():
            j = i + 1
            while j < n and text[j].isdigit():
                j += 1
            tokens.append(("num", float(text[i:j])))
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("name", text[i:j]))
            i = j
            continue
        if text.startswith("//", i):
            tokens.append(("op", "//"))
            i += 2
            continue
        if text.startswith("**", i):
            tokens.append(("op", "**"))
            i += 2
            continue
        if ch in "+-*/%":
            tokens.append(("op", ch))
            i += 1
            continue
        if ch == "(":
            tokens.append(("lp", ch))
            i += 1
            continue
        if ch == ")":
            tokens.append(("rp", ch))
            i += 1
            continue
        if ch == ",":
            tokens.append(("comma", ch))
            i += 1
            continue
        raise ValueError("unknown character %r" % ch)
    return tokens


class _Parser:
    def __init__(self, tokens):
        self._toks = tokens
        self._i = 0

    def _peek(self):
        return self._toks[self._i] if self._i < len(self._toks) else None

    def _next(self):
        tok = self._peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        self._i += 1
        return tok

    def _parse_additive(self):
        value = self._parse_multiplicative()
        while True:
            tok = self._peek()
            if tok is not None and tok[0] == "op" and tok[1] in ("+", "-"):
                self._i += 1
                rhs = self._parse_multiplicative()
                value = value + rhs if tok[1] == "+" else value - rhs
            else:
                return value

    def _parse_multiplicative(self):
        value = self._parse_unary()
        while True:
            tok = self._peek()
            if tok is not None and tok[0] == "op" and tok[1] in ("*", "/", "//", "%"):
                self._i += 1
                rhs = self._parse_unary()
                if tok[1] == "*":
                    value = value * rhs
                elif tok[1] == "/":
                    value = value / rhs
                elif tok[1] == "//":
                    value = value // rhs
                else:
                    value = value % rhs
            else:
                return value

    def _parse_unary(self):
        tok = self._peek()
        if tok is not None and tok[0] == "op" and tok[1] in ("+", "-"):
            self._i += 1
            operand = self._parse_unary()
            return operand if tok[1] == "+" else -operand
        return self._parse_power()

    def _parse_power(self):
        base = self._parse_primary()
        tok = self._peek()
        if tok is not None and tok[0] == "op" and tok[1] == "**":
            self._i += 1
            exponent = self._parse_unary()
            return base ** exponent
        return base

    def _parse_primary(self):
        tok = self._peek()
        if tok is None:
            raise ValueError("expected an operand")
        kind, val = tok
        if kind == "num":
            self._i += 1
            return val
        if kind == "lp":
            self._i += 1
            value = self._parse_additive()
            close = self._next()
            if close[0] != "rp":
                raise ValueError("expected ')'")
            return value
        if kind == "name":
            self._i += 1
            return self._parse_call(val)
        raise ValueError("unexpected token %r" % val)

    def _parse_call(self, name):
        open_t = self._next()
        if open_t[0] != "lp":
            raise ValueError("expected '(' after function name %r" % name)
        if name not in ("min", "max", "abs"):
            raise ValueError("unknown function %r" % name)
        args = [self._parse_additive()]
        while True:
            tok = self._peek()
            if tok is not None and tok[0] == "comma":
                self._i += 1
                args.append(self._parse_additive())
            else:
                break
        close = self._next()
        if close[0] != "rp":
            raise ValueError("expected ')'")
        if name == "abs":
            if len(args) != 1:
                raise ValueError("abs takes exactly one argument")
            return abs(args[0])
        return min(args) if name == "min" else max(args)


def evaluate(text: str):
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    parser = _Parser(tokens)
    result = parser._parse_additive()
    leftover = parser._peek()
    if leftover is not None:
        raise ValueError("unexpected token %r" % leftover[1])
    return result
