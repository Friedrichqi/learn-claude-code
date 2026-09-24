# Problem: half-open integer intervals

Implement `solution.py` in this directory with three pure functions. An interval is a pair
`(start, end)` of integers meaning the half-open range `[start, end)`; a pair with
`start >= end` is empty and must be ignored everywhere. Inputs may be lists or tuples of
pairs in any order. Every function returns a list of `(start, end)` tuples sorted by start.

## `merge(intervals) -> list[tuple[int, int]]`

Merge overlapping **and touching** intervals: `(1, 3)` and `(3, 5)` become `(1, 5)`.
Nested intervals collapse into the outer one. Empty input returns `[]`.

## `free_gaps(intervals, window) -> list[tuple[int, int]]`

`window` is a pair `(ws, we)`. Return the maximal sub-ranges of `[ws, we)` that no interval
covers, in order. Intervals partly or fully outside the window are clipped or ignored.
With no intervals the whole window is one gap; a fully covered window returns `[]`.

## `max_overlap(intervals) -> int`

The largest number of (non-empty) intervals that cover one common point. Because ranges are
half-open, `(1, 3)` and `(3, 5)` do **not** overlap. Empty input returns `0`.

`merge` and `max_overlap` must run in O(n log n); 20,000 intervals should take well under a
second. Run the tests with `python3 test_intervals.py` (from this directory or the repository
root, the test file locates `solution.py` itself).
