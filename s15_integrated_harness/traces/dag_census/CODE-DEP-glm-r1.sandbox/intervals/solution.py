"""Half-open integer interval utilities: merge, free_gaps, max_overlap.

An interval is a pair ``(start, end)`` denoting the half-open range
``[start, end)``.  Any pair with ``start >= end`` is empty and is ignored
by every function.  Inputs may be lists or tuples of pairs in any order;
every list-returning function yields ``(start, end)`` tuples sorted by
start and never mutates its arguments.
"""


def _valid_pairs(intervals):
    """Yield ``(start, end)`` tuples for non-empty intervals, in input order."""
    for pair in intervals:
        start, end = pair[0], pair[1]
        if start < end:
            yield start, end


def merge(intervals):
    """Merge overlapping and touching intervals; O(n log n)."""
    merged = []
    for start, end in sorted(_valid_pairs(intervals)):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals, window):
    """Maximal sub-ranges of ``window`` covered by no interval, ascending."""
    ws, we = window[0], window[1]
    if ws >= we:
        return []
    clipped = [
        (s, e)
        for s, e in _valid_pairs(intervals)
        if e > ws and s < we
    ]
    gaps = []
    cursor = ws
    for start, end in merge(clipped):
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty intervals covering one point; O(n log n)."""
    events = []
    for start, end in _valid_pairs(intervals):
        events.append((start, 1))
        events.append((end, -1))
    events.sort()  # ends (-1) sort before starts (+1) at equal coordinates
    depth = 0
    best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
