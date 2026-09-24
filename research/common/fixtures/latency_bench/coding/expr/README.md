# Problem: arithmetic expression evaluator

Implement `solution.py` in this directory with one function `evaluate(text: str)` that parses and
evaluates an arithmetic expression string and returns a Python `int` or `float`.

Grammar:

* Number literals: integers (`42`) become `int`, decimals (`1.5`, `.5`) become `float`.
* Binary operators with Python semantics and precedence, lowest to highest:
  `+ -`  <  `* / // %`  <  unary `-` and `+`  <  `**`.
  `+ - * / // %` are left-associative; `**` is right-associative (`2 ** 3 ** 2 == 512`).
  `**` binds tighter than unary minus (`-2 ** 2 == -4`, `(-2) ** 2 == 4`).
  `/` always yields a float (`8 / 4 == 2.0`); `//` and `%` follow Python.
* Parentheses for grouping; unary operators may be nested (`-(-2) == 2`).
* Functions `min(...)` and `max(...)` with one or more comma-separated arguments and `abs(x)` with
  exactly one argument. Arguments are full expressions.
* Whitespace between tokens is ignored.

Errors:

* Division or modulo by zero raises `ZeroDivisionError`.
* Anything malformed raises `ValueError`: empty input, unknown characters or names, a wrong number
  of function arguments, unbalanced parentheses, a missing operand or operator (`1 +`, `1 2`, `2(3)`).

Do not use `eval`, `exec`, `compile` or the `ast` module: write a tokenizer and a recursive-descent or
precedence-climbing parser. Run the tests with `python3 test_expr.py`.
