import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import solution  # noqa: E402

evaluate = solution.evaluate


class ArithmeticTests(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(evaluate("1 + 2 * 3"), 7)

    def test_parentheses(self):
        self.assertEqual(evaluate("(1 + 2) * 3"), 9)

    def test_left_associative_subtraction(self):
        self.assertEqual(evaluate("10 - 2 - 3"), 5)

    def test_right_associative_power(self):
        self.assertEqual(evaluate("2 ** 3 ** 2"), 512)

    def test_unary_minus_binds_looser_than_power(self):
        self.assertEqual(evaluate("-2 ** 2"), -4)
        self.assertEqual(evaluate("(-2) ** 2"), 4)

    def test_floor_div_and_mod(self):
        self.assertEqual(evaluate("7 // 2 % 3"), 0)
        self.assertEqual(evaluate("-7 // 2"), -4)
        self.assertEqual(evaluate("-7 % 3"), 2)

    def test_true_division_is_float(self):
        result = evaluate("8 / 4")
        self.assertEqual(result, 2.0)
        self.assertIsInstance(result, float)

    def test_int_stays_int(self):
        result = evaluate("6 * 7")
        self.assertEqual(result, 42)
        self.assertIsInstance(result, int)

    def test_decimals(self):
        self.assertEqual(evaluate("1.5 * 4"), 6.0)
        self.assertAlmostEqual(evaluate(".5 + .25"), 0.75)
        self.assertAlmostEqual(evaluate("2 ** 0.5"), 1.4142135623730951)

    def test_unary_forms(self):
        self.assertEqual(evaluate("2 * -3"), -6)
        self.assertEqual(evaluate("-(2 + 3)"), -5)
        self.assertEqual(evaluate("-(-2)"), 2)
        self.assertEqual(evaluate("+4"), 4)

    def test_whitespace(self):
        self.assertEqual(evaluate("  3+4 "), 7)
        self.assertEqual(evaluate("\t(2*\n3)"), 6)

    def test_functions(self):
        self.assertEqual(evaluate("max(1, 2, min(5, 3)) * 2"), 6)
        self.assertEqual(evaluate("abs(-3.5)"), 3.5)
        self.assertEqual(evaluate("min(4)"), 4)
        self.assertEqual(evaluate("max(1 + 1, 2 * 2, 3 - 1)"), 4)


class ErrorTests(unittest.TestCase):
    def test_division_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            evaluate("1 / 0")
        with self.assertRaises(ZeroDivisionError):
            evaluate("1 // (2 - 2)")
        with self.assertRaises(ZeroDivisionError):
            evaluate("5 % 0")

    def test_malformed(self):
        for text in ["", "   ", "1 +", "1 2", "2(3)", "(1 + 2", "1 + 2)", "foo(1)", "abs(1, 2)",
                     "max()", "1 $ 2", "* 3", "1..2", "min(1,)"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    evaluate(text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
