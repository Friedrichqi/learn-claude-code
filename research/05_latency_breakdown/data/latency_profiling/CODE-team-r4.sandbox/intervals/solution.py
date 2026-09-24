"""Solutions for half-open integer interval problems.

An interval is a pair ``(start, end)`` meaning the range ``[start, end)``.
Intervals with ``start >= end`` are empty and are ignored everywhere.
"""


def _valid(intervals):
    """Yield only non-empty (start, end) pairs, normalized to tuples."""
    for interval in intervals:
        start, end = interval
        if start < end:
            yield (start, end)


def merge(intervals):
    """Merge overlapping and touching intervals; result sorted by start."""
    merged = []
    for start, end in sorted(_valid(intervals)):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals, window):
    """Maximal sub-ranges of ``window`` not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    clipped = [
        (max(start, ws), min(end, we))
        for start, end in _valid(intervals)
        if max(start, ws) < min(end, we)
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
    """Largest number of intervals covering one common point (half-open)."""
    events = []
    for start, end in _valid(intervals):
        events.append((start, 1))
        events.append((end, -1))
    best = current = 0
    # Sorting (point, delta) keeps end events (-1) before start events (+1)
    # at the same point, which is correct for half-open ranges.
    for _, delta in sorted(events):
        current += delta
        if current > best:
            best = current
    return best
