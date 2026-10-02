import re

def evaluate(text):
    # Tokenize the input
    tokens = tokenize(text)
    # Parse the tokens into an abstract syntax tree
    ast = parse(tokens)
    # Evaluate the AST
    return evaluate_ast(ast)


def tokenize(text):
    # Remove whitespace
    text = text.replace(' ', '')
    # Match numbers
    text = re.sub(r'([0-9]+(?:\.[0-9]+)?|\.[0-9]+)', r'\1', text)
    # Match operators and parentheses
    text = re.sub(r'([+\-*/%\^])', r'\1', text)
    # Match functions and keywords
    text = re.sub(r'(min|max|abs)\(', r'\1(', text)
    # Split into tokens
    tokens = re.findall(r'([0-9]+(?:\.[0-9]+)?|\.[0-9]+|[-+*/%\^]|\(|\)|min|max|abs)', text)
    return tokens


def parse(tokens):
    # Parse the tokens into an abstract syntax tree
    ast = parse_expression(tokens, 0)
    if len(tokens) > 0:
        raise ValueError("Unexpected token: " + tokens[0])
    return ast


def parse_expression(tokens, pos):
    # Parse an expression with the lowest precedence
    # This handles + and -
    left, pos = parse_term(tokens, pos)
    while pos < len(tokens) and tokens[pos] in ['+', '-']:
        op = tokens[pos]
        pos += 1
        right, pos = parse_term(tokens, pos)
        left = ('binop', op, left, right)
    return left, pos


def parse_term(tokens, pos):
    # Parse a term with higher precedence
    # This handles * / // %
    left, pos = parse_factor(tokens, pos)
    while pos < len(tokens) and tokens[pos] in ['*', '/', '//', '%']:
        op = tokens[pos]
        pos += 1
        right, pos = parse_factor(tokens, pos)
        left = ('binop', op, left, right)
    return left, pos


def parse_factor(tokens, pos):
    # Parse a factor with higher precedence
    # This handles unary + and - and **
    if pos < len(tokens) and tokens[pos] in ['+', '-']:
        op = tokens[pos]
        pos += 1
        right, pos = parse_power(tokens, pos)
        return ('unary', op, right), pos
    return parse_power(tokens, pos)


def parse_power(tokens, pos):
    # Parse a power expression with the highest precedence
    left, pos = parse_primary(tokens, pos)
    while pos < len(tokens) and tokens[pos] == '**':
        pos += 1
        right, pos = parse_power(tokens, pos)
        left = ('binop', '**', left, right)
    return left, pos


def parse_primary(tokens, pos):
    # Parse a primary expression
    if pos >= len(tokens):
        raise ValueError("Unexpected end of input")
    token = tokens[pos]
    if token == '(':
        pos += 1
        expr, pos = parse_expression(tokens, pos)
        if pos >= len(tokens) or tokens[pos] != ')':
            raise ValueError("Expected closing parenthesis")
        pos += 1
        return expr, pos
    elif token == 'min' or token == 'max':
        pos += 1
        if pos >= len(tokens) or tokens[pos] != '(':
            raise ValueError("Expected opening parenthesis")
        pos += 1
        args = []
        while pos < len(tokens) and tokens[pos] != ')':
            arg, pos = parse_expression(tokens, pos)
            args.append(arg)
            if pos < len(tokens) and tokens[pos] == ',':
                pos += 1
        if pos >= len(tokens) or tokens[pos] != ')':
            raise ValueError("Expected closing parenthesis")
        pos += 1
        return ('func', token, args), pos
    elif token == 'abs':
        pos += 1
        if pos >= len(tokens) or tokens[pos] != '(':
            raise ValueError("Expected opening parenthesis")
        pos += 1
        arg, pos = parse_expression(tokens, pos)
        if pos >= len(tokens) or tokens[pos] != ')':
            raise ValueError("Expected closing parenthesis")
        pos += 1
        return ('func', token, [arg]), pos
    elif token == '.' or token.isdigit():
        # Parse a number
        num = token
        while pos + 1 < len(tokens) and (tokens[pos + 1].isdigit() or tokens[pos + 1] == '.'): 
            num += tokens[pos + 1]
            pos += 1
        if '.' in num:
            return ('number', float(num)), pos
        else:
            return ('number', int(num)), pos
    else:
        raise ValueError("Unexpected token: " + token)


def evaluate_ast(ast):
    if ast[0] == 'number':
        return ast[1]
    elif ast[0] == 'binop':
        op = ast[1]
        left = evaluate_ast(ast[2])
        right = evaluate_ast(ast[3])
        if op == '+':
            return left + right
        elif op == '-':
            return left - right
        elif op == '*':
            return left * right
        elif op == '/':
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return float(left) / float(right)
        elif op == '//':
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return left // right
        elif op == '%':
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return left % right
        elif op == '**':
            return left ** right
        else:
            raise ValueError("Unknown operator: " + op)
    elif ast[0] == 'unary':
        op = ast[1]
        val = evaluate_ast(ast[2])
        if op == '+':
            return val
        elif op == '-':
            return -val
        else:
            raise ValueError("Unknown unary operator: " + op)
    elif ast[0] == 'func':
        func = ast[1]
        args = ast[2]
        if func == 'min':
            return min(args)
        elif func == 'max':
            return max(args)
        elif func == 'abs':
            return abs(evaluate_ast(args[0]))
        else:
            raise ValueError("Unknown function: " + func)
    else:
        raise ValueError("Unknown AST node: " + ast[0])