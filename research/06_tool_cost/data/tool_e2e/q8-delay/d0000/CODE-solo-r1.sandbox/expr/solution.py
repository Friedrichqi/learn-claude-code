def evaluate(text):
    # Tokenize the input
    tokens = tokenize(text)
    # Parse the tokens into an abstract syntax tree
    ast = parse(tokens)
    # Evaluate the abstract syntax tree
    return evaluate_ast(ast)


def tokenize(text):
    # Remove whitespace
    text = text.replace(' ', '')
    # Tokenize numbers
    tokens = []
    i = 0
    while i < len(text):
        if text[i].isdigit() or (text[i] == '.' and i + 1 < len(text) and text[i + 1].isdigit()):
            # Parse number
            j = i
            if text[j] == '.':
                j += 1
            while j < len(text) and (text[j].isdigit() or text[j] == '.'): 
                j += 1
            tokens.append(('number', float(text[i:j])))
            i = j
        elif text[i] in '+-*/%**()':
            tokens.append(('operator', text[i]))
            i += 1
        elif text[i] == '(': 
            tokens.append(('lparen', '('))
            i += 1
        elif text[i] == ')': 
            tokens.append(('rparen', ')'))
            i += 1
        elif text[i] in 'minmaxabs':
            # Parse function
            j = i
            while j < len(text) and text[j] not in '()':
                j += 1
            func_name = text[i:j]
            tokens.append(('function', func_name))
            i = j
            if text[i] == '(':
                tokens.append(('lparen', '('))
                i += 1
            else:
                raise ValueError("Invalid function call")
        else:
            raise ValueError("Invalid character")
    return tokens


def parse(tokens):
    # Parse expression
    def parse_expression():
        left = parse_term()
        while tokens and tokens[0][0] in ('operator', 'function') and tokens[0][1] in '+-':
            op = tokens.pop(0)
            right = parse_term()
            left = ('binary_op', op[1], left, right)
        return left

    def parse_term():
        left = parse_factor()
        while tokens and tokens[0][0] in ('operator', 'function') and tokens[0][1] in '*/%**':
            op = tokens.pop(0)
            right = parse_factor()
            left = ('binary_op', op[1], left, right)
        return left

    def parse_factor():
        if tokens[0][0] == 'number':
            return tokens.pop(0)[1]
        elif tokens[0][0] == 'function':
            func = tokens.pop(0)[1]
            if func == 'min' or func == 'max':
                # Parse arguments
                if tokens[0][0] != 'lparen':
                    raise ValueError("Expected '(' after function")
                tokens.pop(0)  # consume '('
                args = []
                while tokens and tokens[0][0] != 'rparen':
                    args.append(parse_expression())
                    if tokens and tokens[0][0] == 'comma':
                        tokens.pop(0)  # consume ','
                if tokens and tokens[0][0] == 'rparen':
                    tokens.pop(0)  # consume ')'
                else:
                    raise ValueError("Expected ')' after function arguments")
                return ('function_call', func, args)
            elif func == 'abs':
                # Parse arguments
                if tokens[0][0] != 'lparen':
                    raise ValueError("Expected '(' after function")
                tokens.pop(0)  # consume '('
                if tokens and tokens[0][0] == 'number':
                    arg = tokens.pop(0)[1]
                else:
                    arg = parse_expression()
                if tokens and tokens[0][0] == 'rparen':
                    tokens.pop(0)  # consume ')'
                else:
                    raise ValueError("Expected ')' after function arguments")
                return ('function_call', func, [arg])
            else:
                raise ValueError("Unknown function")
        elif tokens[0][0] == 'lparen':
            tokens.pop(0)  # consume '('
            expr = parse_expression()
            if tokens and tokens[0][0] == 'rparen':
                tokens.pop(0)  # consume ')'
            else:
                raise ValueError("Expected ')' after expression")
            return expr
        else:
            raise ValueError("Unexpected token")

    return parse_expression()


def evaluate_ast(ast):
    if isinstance(ast, tuple) and ast[0] == 'binary_op':
        op, left, right = ast[1], evaluate_ast(ast[2]), evaluate_ast(ast[3])
        if op == '+':
            return left + right
        elif op == '-':
            return left - right
        elif op == '*':
            return left * right
        elif op == '/':
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return float(left) / right
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
            raise ValueError("Unknown operator")
    elif isinstance(ast, tuple) and ast[0] == 'function_call':
        func, args = ast[1], ast[2]
        if func == 'min':
            return min(args)
        elif func == 'max':
            return max(args)
        elif func == 'abs':
            return abs(args[0])
        else:
            raise ValueError("Unknown function")
    else:
        return ast