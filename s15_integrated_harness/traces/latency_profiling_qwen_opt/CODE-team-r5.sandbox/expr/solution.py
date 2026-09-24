"""Arithmetic expression evaluator (tokenizer + recursive-descent parser).

Precedence, lowest to highest:
    additive      : '+' '-'
    multiplicative: '*' '/' '//' '%'
    unary         : unary '+' '-'  (looser than '**')
    power         : '**' (right-associative)
    primary       : number | min(...) | max(...) | abs(...) | '(' expr ')'
"""


def evaluate(text: str):
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty expression")
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def advance():
        tok = peek()
        if tok is None:
            raise ValueError("unexpected end of input")
        pos[0] += 1
        return tok

    def expect_op(op):
        tok = peek()
        if tok is None or tok[0] != 'op' or tok[1] != op:
            raise ValueError("expected %r" % op)
        pos[0] += 1

    def parse_expr():
        return parse_additive()

    def parse_additive():
        val = parse_multiplicative()
        while peek() is not None and peek()[0] == 'op' and peek()[1] in ('+', '-'):
            op = advance()[1]
            rhs = parse_multiplicative()
            val = val + rhs if op == '+' else val - rhs
        return val

    def parse_multiplicative():
        val = parse_power()
        while peek() is not None and peek()[0] == 'op' and peek()[1] in ('*', '/', '//', '%'):
            op = advance()[1]
            rhs = parse_power()
            if op == '*':
                val = val * rhs
            elif op == '/':
                val = val / rhs
            elif op == '//':
                val = val // rhs
            else:
                val = val % rhs
        return val

    def parse_power():
        # '**' binds tighter than unary minus: -2 ** 2 == -(2**2).
        # Handle optional unary prefix here so that '2 ** -3' works while
        # '-2 ** 2' still parses as -(2**2) via the unary level.
        sign = 1
        while peek() is not None and peek()[0] == 'op' and peek()[1] in ('+', '-'):
            if advance()[1] == '-':
                sign = -sign
        base = parse_operand()
        if peek() is not None and peek()[0] == 'op' and peek()[1] == '**':
            advance()
            return sign * (base ** parse_power())
        return sign * base

    def parse_unary():
        sign = 1
        while peek() is not None and peek()[0] == 'op' and peek()[1] in ('+', '-'):
            if advance()[1] == '-':
                sign = -sign
        return sign * parse_operand()

    def parse_operand():
        tok = peek()
        if tok is None:
            raise ValueError("missing operand")
        if tok[0] == 'num':
            pos[0] += 1
            return tok[1]
        if tok[0] == 'name':
            pos[0] += 1
            return parse_funcall(tok[1])
        if tok[0] == 'op' and tok[1] == '(':
            pos[0] += 1
            val = parse_expr()
            expect_op(')')
            return val
        raise ValueError("unexpected token %r" % (tok[1],))

    def parse_funcall(name):
        if name not in ('min', 'max', 'abs'):
            raise ValueError("unknown name %r" % name)
        expect_op('(')
        args = [parse_expr()]
        while peek() is not None and peek()[0] == 'op' and peek()[1] == ',':
            advance()
            if peek() is not None and peek()[0] == 'op' and peek()[1] == ')':
                raise ValueError("trailing comma in %r call" % name)
            args.append(parse_expr())
        expect_op(')')
        if name == 'abs':
            if len(args) != 1:
                raise ValueError("abs() takes exactly one argument")
            return args[0] if args[0] >= 0 else -args[0]
        return (min if name == 'min' else max)(args)

    result = parse_expr()
    if peek() is not None:
        raise ValueError("unexpected token %r" % (peek()[1],))
    return result


def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c.isdigit() or c == '.':
            start = i
            if c == '.':
                i += 1
                while i < n and text[i].isdigit():
                    i += 1
                raw = text[start:i]
                if raw == '.':
                    raise ValueError("malformed number")
                tokens.append(('num', float(raw)))
            else:
                while i < n and text[i].isdigit():
                    i += 1
                value = int(text[start:i])
                if i < n and text[i] == '.':
                    # '1.' alone is malformed; require digits after the dot
                    j = i + 1
                    while j < n and text[j].isdigit():
                        j += 1
                    if j == i + 1:
                        raise ValueError("malformed number %r" % text[start:j])
                    i = j
                    value = float(text[start:i])
                tokens.append(('num', value))
            continue
        if c.isalpha():
            start = i
            while i < n and (text[i].isalnum() or text[i] == '_'):
                i += 1
            tokens.append(('name', text[start:i]))
            continue
        two = text[i:i + 2]
        if two in ('//', '**'):
            tokens.append(('op', two))
            i += 2
            continue
        if c in '+-*/()%,' :
            tokens.append(('op', c))
            i += 1
            continue
        raise ValueError("unknown character %r" % c)
    return tokens
