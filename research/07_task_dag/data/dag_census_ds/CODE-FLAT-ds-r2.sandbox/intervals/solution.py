"""Half-open integer interval utilities.

An interval is a pair ``(start, end)`` of integers denoting the half-open
range ``[start, end)``.  Pairs with ``start >= end`` are empty and are ignored.
"""


def _clean(intervals):
    """Yield (start, end) tuples for non-empty intervals only."""
    for pair in intervals:
        start, end = pair[0], pair[1]
        if start < end:
            yield (start, end)


def _merge_sorted(sorted_intervals):
    """Merge an iterable of (start, end) tuples that is already sorted by start."""
    merged = []
    for start, end in sorted_intervals:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def merge(intervals):
    """Merge overlapping and touching non-empty intervals.

    Returns a list of ``(start, end)`` tuples sorted by start.
    """
    return _merge_sorted(sorted(_clean(intervals)))


def free_gaps(intervals, window):
    """Return maximal uncovered sub-ranges of ``[ws, we)`` in order."""
    ws, we = window[0], window[1]
    if ws >= we:
        return []

    clipped = []
    for start, end in _clean(intervals):
        if end <= ws or start >= we:
            continue
        if start < ws:
            start = ws
        if end > we:
            end = we
        clipped.append((start, end))

    clipped.sort()
    gaps = []
    cursor = ws
    for start, end in _merge_sorted(clipped):
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty intervals covering one common point."""
    starts = []
    ends = []
    for start, end in _clean(intervals):
        starts.append(start)
        ends.append(end)

    starts.sort()
    ends.sort()

    best = 0
    current = 0
    i = 0
    j = 0
    n = len(starts)
    while i < n:
        if starts[i] < ends[j]:
            current += 1
            if current > best:
                best = current
            i += 1
        else:
            current -= 1
            j += 1
    return best
