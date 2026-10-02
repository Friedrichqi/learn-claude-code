import re

def evaluate(text):
    # Tokenize the input
    tokens = tokenize(text)
    # Parse the tokens into an abstract syntax tree
    ast = parse(tokens)
    # Evaluate the AST
    return evaluate_ast(ast)


def tokenize(text):
    # Remove all whitespace
    text = text.replace(' ', '').replace('\n', '').replace('\t', '')
    # Match numbers, operators, parentheses, and function names
    tokens = re.findall(r"(-?\d+\.?\d*|\+|-|\*|/|//|%|\^|\(|\)|min|max|abs|\.)", text)
    # Handle unary operators and function calls
    tokens = handle_unary(tokens)
    return tokens


def parse(tokens):
    # Parse the tokens into an AST
    ast = parse_expression(tokens)
    return ast


def evaluate_ast(ast):
    # Evaluate the AST and return the result
    if isinstance(ast, int) or isinstance(ast, float):
        return ast
    if isinstance(ast, str):
        if ast == 'min':
            return min(ast[1:])
        if ast == 'max':
            return max(ast[1:])
        if ast == 'abs':
            return abs(ast[1])
    if isinstance(ast, tuple):
        if ast[0] == '+' or ast[0] == '-':
            return evaluate_ast(ast[1]) if ast[0] == '+' else -evaluate_ast(ast[1])
        if ast[0] == '*':
            return evaluate_ast(ast[1]) * evaluate_ast(ast[2])
        if ast[0] == '/':
            return evaluate_ast(ast[1]) / evaluate_ast(ast[2])
        if ast[0] == '//':
            return evaluate_ast(ast[1]) // evaluate_ast(ast[2])
        if ast[0] == '%':
            return evaluate_ast(ast[1]) % evaluate_ast(ast[2])
        if ast[0] == '**':
            return evaluate_ast(ast[1]) ** evaluate_ast(ast[2])
        if ast[0] == '(':  # This is for function calls
            return evaluate_function(ast[1], ast[2])
    raise ValueError("Invalid expression")


def handle_unary(tokens):
    # Handle unary operators and function calls
    i = 0
    while i < len(tokens):
        if tokens[i] == '-' or tokens[i] == '+':
            # Unary operator, check if it's at the start or after an operator or parenthesis
            if i == 0 or (i > 0 and (tokens[i-1] in '+-*/%**' or tokens[i-1] == '(')):
                tokens[i] = ('unary', tokens[i])
            i += 1
        elif tokens[i] == '(':
            # Function call, check if it's a function name
            if i + 1 < len(tokens) and tokens[i+1] == 'min' or tokens[i+1] == 'max' or tokens[i+1] == 'abs':
                # Function call
                tokens[i] = ('function', tokens[i+1])
                tokens.pop(i+1)
                i += 1
            else:
                i += 1
        else:
            i += 1
    return tokens


def parse_expression(tokens):
    # Parse the expression into an AST
    # This is a simplified version, assuming the tokens are already properly grouped
    # In a real implementation, this would be more complex
    # For the purpose of this problem, we'll assume that the tokens are properly grouped
    # and that the parser can handle them in a simple way
    return tokens


def evaluate_function(func, args):
    # Evaluate a function call
    if func == 'min':
        return min(args)
    if func == 'max':
        return max(args)
    if func == 'abs':
        return abs(args[0])
    raise ValueError("Invalid function call")