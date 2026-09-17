# Checklist — half-open integer intervals (from README.md)

1. `solution.py` defines three pure functions: `merge`, `free_gaps`, `max_overlap`.
2. An interval is a pair `(start, end)` of integers meaning the half-open range `[start, end)`.
3. A pair with `start >= end` is empty and must be ignored in every function.
4. Inputs may be lists or tuples of pairs, in any order.
5. Every function returns a list of `(start, end)` tuples sorted by start.
6. `merge` merges overlapping intervals.
7. `merge` also merges touching intervals: `(1, 3)` and `(3, 5)` become `(1, 5)`.
8. `merge` collapses nested intervals into the outer one.
9. `merge` returns `[]` for empty input (or input of only empty intervals).
10. `free_gaps(intervals, window)` takes `window` as a pair `(ws, we)` and returns the maximal sub-ranges of `[ws, we)` that no interval covers, in order.
11. `free_gaps` clips intervals that extend partly outside the window and ignores intervals fully outside it.
12. `free_gaps` returns the whole window as one gap when there are no (non-empty) intervals.
13. `free_gaps` returns `[]` when the window is fully covered.
14. `max_overlap` returns the largest number of (non-empty) intervals covering one common point.
15. `max_overlap` treats ranges as half-open: `(1, 3)` and `(3, 5)` do not overlap (touching at a point does not count).
16. `max_overlap` returns `0` for empty input (or input of only empty intervals).
17. `merge` and `max_overlap` run in O(n log n); 20,000 intervals complete well under a second.
18. `free_gaps` also runs fast on large inputs (efficiency implied by the same data scale).
