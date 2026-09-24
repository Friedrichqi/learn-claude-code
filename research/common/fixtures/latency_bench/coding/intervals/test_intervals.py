import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import solution  # noqa: E402


class MergeTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(solution.merge([]), [])

    def test_unsorted_overlapping(self):
        self.assertEqual(solution.merge([(5, 8), (1, 3), (2, 6)]), [(1, 8)])

    def test_touching_intervals_merge(self):
        self.assertEqual(solution.merge([(1, 3), (3, 5)]), [(1, 5)])

    def test_disjoint_sorted_output(self):
        self.assertEqual(solution.merge([(4, 6), (1, 2)]), [(1, 2), (4, 6)])

    def test_empty_and_inverted_intervals_ignored(self):
        self.assertEqual(solution.merge([(3, 3), (5, 4), (1, 2)]), [(1, 2)])

    def test_nested(self):
        self.assertEqual(solution.merge([(1, 10), (2, 3), (4, 5)]), [(1, 10)])

    def test_returns_tuples_and_accepts_lists(self):
        out = solution.merge([[1, 2], [2, 3]])
        self.assertEqual(out, [(1, 3)])
        self.assertTrue(all(isinstance(pair, tuple) for pair in out))

    def test_performance(self):
        data = [(i * 3, i * 3 + 2) for i in range(20000)] + [(i * 3 + 1, i * 3 + 4) for i in range(19999)]
        started = time.perf_counter()
        merged = solution.merge(data)
        self.assertLess(time.perf_counter() - started, 1.5)
        self.assertEqual(merged, [(0, 59999)])


class FreeGapTests(unittest.TestCase):
    def test_no_intervals(self):
        self.assertEqual(solution.free_gaps([], (0, 10)), [(0, 10)])

    def test_gaps_between(self):
        self.assertEqual(solution.free_gaps([(2, 4), (6, 8)], (0, 10)), [(0, 2), (4, 6), (8, 10)])

    def test_fully_covered(self):
        self.assertEqual(solution.free_gaps([(0, 5), (5, 10)], (0, 10)), [])

    def test_clipping_outside_window(self):
        self.assertEqual(solution.free_gaps([(-5, 1), (9, 20)], (0, 10)), [(1, 9)])

    def test_empty_window(self):
        self.assertEqual(solution.free_gaps([(1, 2)], (5, 5)), [])

    def test_overlapping_input(self):
        self.assertEqual(solution.free_gaps([(1, 4), (2, 6), (8, 9)], (0, 10)), [(0, 1), (6, 8), (9, 10)])


class MaxOverlapTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(solution.max_overlap([]), 0)

    def test_touching_do_not_overlap(self):
        self.assertEqual(solution.max_overlap([(1, 3), (3, 5)]), 1)

    def test_three_deep(self):
        self.assertEqual(solution.max_overlap([(1, 4), (2, 6), (3, 5), (7, 8)]), 3)

    def test_empty_intervals_ignored(self):
        self.assertEqual(solution.max_overlap([(1, 1), (1, 2)]), 1)

    def test_identical_intervals(self):
        self.assertEqual(solution.max_overlap([(0, 1)] * 5), 5)

    def test_performance(self):
        data = [(i, i + 100) for i in range(20000)]
        started = time.perf_counter()
        depth = solution.max_overlap(data)
        self.assertLess(time.perf_counter() - started, 1.5)
        self.assertEqual(depth, 100)


if __name__ == "__main__":
    unittest.main(verbosity=1)
