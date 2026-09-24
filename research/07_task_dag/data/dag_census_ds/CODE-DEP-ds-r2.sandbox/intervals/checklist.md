1. An interval is a pair (start, end) of integers meaning the half-open range [start, end).
2. A pair with start >= end is empty and must be ignored everywhere (in all three functions).
3. Inputs may be lists or tuples of pairs.
4. Input pairs may be given in any order.
5. Every function returns a list of (start, end) tuples.
6. Every returned list is sorted by start.
7. Every returned element is a tuple, even when the input pairs were lists.
8. merge(intervals) merges overlapping intervals.
9. merge merges touching intervals, e.g. (1, 3) and (3, 5) become (1, 5).
10. merge collapses nested intervals into the outer one.
11. merge discards empty and inverted (start >= end) intervals.
12. merge([]) returns [].
13. merge accepts unsorted input and returns a sorted, disjoint list of tuples.
14. free_gaps(intervals, window) takes window as a pair (ws, we).
15. free_gaps returns the maximal sub-ranges of [ws, we) that no interval covers.
16. free_gaps returns those sub-ranges in increasing order.
17. free_gaps clips intervals that are partly outside the window.
18. free_gaps ignores intervals that are fully outside the window.
19. free_gaps ignores empty intervals.
20. free_gaps with no intervals returns the whole window as one gap.
21. free_gaps returns [] when the window is fully covered.
22. free_gaps returns [] when the window is empty (ws >= we).
23. max_overlap(intervals) returns the largest number of non-empty intervals covering one common point.
24. max_overlap treats half-open ranges as non-overlapping at a shared endpoint: (1, 3) and (3, 5) do not overlap.
25. max_overlap counts identical intervals towards the depth (e.g. five copies of (0, 1) give 5).
26. max_overlap ignores empty intervals.
27. max_overlap([]) returns 0.
28. max_overlap returns an int.
29. merge must run in O(n log n) time.
30. max_overlap must run in O(n log n) time.
31. Performance: 20,000 intervals must complete well under a second (test allows < 1.5s).
32. Tests are run with `python3 test_intervals.py` from this directory or the repository root.
