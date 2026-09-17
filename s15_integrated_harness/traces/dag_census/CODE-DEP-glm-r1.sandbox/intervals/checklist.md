# Behavioural checklist — half-open integer intervals (`solution.py`)

Deliverable & general contract

1. Provide `solution.py` in this directory exposing three pure functions: `merge`, `free_gaps`, `max_overlap`.
2. An interval is a pair `(start, end)` of integers denoting the half-open range `[start, end)`.
3. Any pair with `start >= end` is empty and must be ignored by every function (never produced, never counted).
4. Inputs may be lists or tuples of pairs, given in any order (no sorting assumption on input).
5. Every function returns a plain `list` of `(start, end)` tuples, sorted by start (output pairs must be tuples even if inputs were lists).
6. Functions must be pure — no mutation of inputs or side effects implied by the spec.

`merge(intervals) -> list[tuple[int, int]]`

7. Merges all overlapping intervals into single covering intervals.
8. Touching intervals also merge: `(1, 3)` and `(3, 5)` become `(1, 5)` (half-open adjacency counts as overlap for merging).
9. Nested intervals collapse into their outer interval (e.g. `(1,10),(2,3),(4,5)` → `(1,10)`).
10. Works on unsorted input, e.g. `[(5,8),(1,3),(2,6)]` → `[(1,8)]`.
11. Empty input list returns `[]`.

`free_gaps(intervals, window) -> list[tuple[int, int]]`

12. `window` is a pair `(ws, we)`; the result is the maximal sub-ranges of `[ws, we)` not covered by any interval, in ascending order.
13. Intervals partly outside the window are clipped to the window; intervals fully outside are ignored.
14. With no intervals, the whole window is returned as a single gap `[(ws, we)]`.
15. A window fully covered by intervals returns `[]`.
16. Overlapping input intervals must not create spurious gaps (coverage is a union, e.g. `(1,4),(2,6)` over window `(0,10)` leaves gaps only `(0,1)` and `(6,10)`).
17. An empty window (`ws >= we`) returns `[]`.

`max_overlap(intervals) -> int`

18. Returns the largest number of non-empty intervals covering one common point.
19. Because ranges are half-open, touching intervals do not overlap: `(1,3)` and `(3,5)` give a max overlap of 1.
20. Empty intervals (`start >= end`) are ignored in the count (e.g. `(1,1),(1,2)` → 1).
21. Identical intervals stack: five copies of `(0,1)` → 5.
22. Empty input returns `0`.

Performance

23. `merge` and `max_overlap` must run in O(n log n); 20,000 intervals must complete well under a second (~1.5 s ceiling in the tests).
