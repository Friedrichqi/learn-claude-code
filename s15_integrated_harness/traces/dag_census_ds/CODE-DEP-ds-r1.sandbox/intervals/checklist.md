# Intervals specification checklist

Every behaviour the specification requires, one line per behaviour.

## Module / API and general semantics
1. `solution.py` exists in this directory and defines exactly three public pure functions: `merge`, `free_gaps`, `max_overlap`.
2. All three functions are pure (no global/stateful side effects on module-level state) and return a fresh object computed only from their arguments.
3. An interval is a pair `(start, end)` of integers denoting the half-open range `[start, end)`.
4. A pair is empty when `start >= end` (i.e. `start == end` counts as empty and `start > end` is inverted); such pairs must be ignored by every function.
5. Ignoring an empty/inverted pair must not raise an error and must not affect any result.
6. Input containers may be lists or tuples of pairs, and may be given in any order (unsorted, and mixing list/tuple pairs).
7. Inputs must not be mutated in place by any function.
8. `merge` and `free_gaps` return a `list` of `(start, end)` tuples.
9. Every returned pair must be a `tuple` (not a list), i.e. `isinstance(pair, tuple)` holds for all returned pairs.
10. Output lists are sorted by `start` ascending, with pairs in normalised order (`start < end`), non-empty, and pairwise disjoint.
11. `max_overlap` returns an `int` (the maximum depth), not a list.

## `merge(intervals) -> list[tuple[int, int]]`
12. Merges intervals that overlap (share any point) into their union.
13. Merges intervals that merely touch: `(1, 3)` and `(3, 5)` become `(1, 5)` (touching counts as mergeable).
14. Nested/contained intervals collapse into the outer interval (e.g. `[(1, 10), (2, 3), (4, 5)]` -> `[(1, 10)]`).
15. Empty input returns `[]`.
16. Returns `[]` when the input contains only empty/inverted pairs.
17. Handles unsorted input and returns the merged result sorted by start (e.g. `[(5, 8), (1, 3), (2, 6)]` -> `[(1, 8)]`).
18. Disjoint intervals are preserved as separate output pairs sorted by start (e.g. `[(4, 6), (1, 2)]` -> `[(1, 2), (4, 6)]`).
19. Empty/inverted pairs are ignored, e.g. `[(3, 3), (5, 4), (1, 2)]` -> `[(1, 2)]`.
20. Accepts list-form pairs and still returns tuples, e.g. `[[1, 2], [2, 3]]` -> `[(1, 3)]`.
21. A single non-empty interval returns that interval as one tuple.
22. Identical/duplicate intervals merge into one interval.
23. `merge` runs in O(n log n) time; 20,000 intervals complete well under one second.
24. `merge` is correct for the 20,000-interval stress input, producing a fully merged chain (e.g. `[(i*3, i*3+2) ...] + [(i*3+1, i*3+4) ...]` -> `[(0, 59999)]`).

## `free_gaps(intervals, window) -> list[tuple[int, int]]`
25. The second argument `window` is a pair `(ws, we)`.
26. Returns the maximal sub-ranges of `[ws, we)` that are not covered by any interval, in increasing order.
27. Returned gaps are maximal (adjacent gaps are never returned as two gaps) and half-open `[start, end)` tuples.
28. Intervals partly outside the window are clipped to the window before computing coverage.
29. Intervals fully outside the window are ignored.
30. Clipping case: `free_gaps([(-5, 1), (9, 20)], (0, 10))` -> `[(1, 9)]`.
31. With no (non-empty) intervals the whole window is returned as a single gap: `free_gaps([], (0, 10))` -> `[(0, 10)]`.
32. A fully covered window returns `[]` (e.g. `[(0, 5), (5, 10)]` over `(0, 10)` -> `[]`, touching coverage leaves no gap).
33. Gaps between intervals are returned in order: `free_gaps([(2, 4), (6, 8)], (0, 10))` -> `[(0, 2), (4, 6), (8, 10)]`.
34. Overlapping input intervals are unioned before gap computation: `free_gaps([(1, 4), (2, 6), (8, 9)], (0, 10))` -> `[(0, 1), (6, 8), (9, 10)]`.
35. Touching intervals leave no gap between them (e.g. `(2, 4)` and `(4, 6)` produce no `(4, 4)` gap).
36. An empty/degenerate window (`ws >= we`) returns `[]`, e.g. `free_gaps([(1, 2)], (5, 5))` -> `[]`.
37. Empty/inverted intervals and input ordering do not affect the result.
38. Window boundaries are half-open: coverage exactly at `we` does not remove gaps and coverage exactly at `ws` does not count as covering `we`.
39. `free_gaps` returns `list` of `tuple` gaps sorted by start.
40. A single interval covering the whole window returns `[]`.

## `max_overlap(intervals) -> int`
41. Returns the largest number of non-empty intervals that cover one common point.
42. Because ranges are half-open, touching intervals do not overlap: `max_overlap([(1, 3), (3, 5)])` -> `1`.
43. Empty input returns `0`.
44. Returns `0` when only empty/inverted intervals are given (they are ignored and never counted).
45. Empty/inverted intervals are ignored in the count, e.g. `[(1, 1), (1, 2)]` -> `1`.
46. Point-membership uses half-open semantics: interval `[s, e)` covers point `p` iff `s <= p < e`.
47. Nested/identical intervals each count: `[(0, 1)] * 5` -> `5`.
48. Correctly computes the deepest depth across overlaps, e.g. `[(1, 4), (2, 6), (3, 5), (7, 8)]` -> `3`.
49. Disjoint groups do not add depth (only the maximum single-point depth is returned).
50. A single non-empty interval returns `1`.
51. Input ordering and list/tuple container types do not affect the result.
52. `max_overlap` runs in O(n log n) time; 20,000 intervals complete well under one second.
53. `max_overlap` is correct on the 20,000-interval stress input `[(i, i + 100) for i in range(20000)]`, returning `100`.

## Complexity and test invocation
54. `merge` satisfies the O(n log n) requirement (achievable via a single sort plus a linear sweep).
55. `max_overlap` satisfies the O(n log n) requirement (achievable via an events/coordinate sweep, not O(n^2)).
56. `free_gaps` is efficient enough to handle large inputs within the same performance envelope as `merge` (sort-based, no pathological blow-up).
57. Performance targets hold for 20,000 intervals completing well under one second (tests enforce < 1.5 s).
58. The implementation passes all tests run via `python3 test_intervals.py` from this directory or the repository root (the test file locates `solution.py` itself).
59. `test_intervals.py` must not be modified.
