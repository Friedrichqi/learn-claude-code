"""Half-open integer interval utilities."""


def _clean(intervals):
    """Yield non-empty (start, end) tuples from the input."""
    for start, end in intervals:
        if start < end:
            yield (start, end)


def merge(intervals):
    """Merge overlapping and touching intervals; sorted list of tuples."""
    result = []
    for start, end in sorted(_clean(intervals)):
        if result and start <= result[-1][1]:
            last_start, last_end = result[-1]
            if end > last_end:
                result[-1] = (last_start, end)
        else:
            result.append((start, end))
    return result


def free_gaps(intervals, window):
    """Maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    gaps = []
    cur = ws
    for start, end in merge(intervals):
        if end <= cur:
            continue
        if start > cur:
            gaps.append((cur, start))
        if end >= we:
            return gaps
        cur = end
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals):
    """Largest number of intervals covering one common point."""
    points = []
    for start, end in _clean(intervals):
        points.append((start, 1))
        points.append((end, -1))
    # Process ends before starts at the same coordinate (half-open ranges).
    points.sort(key=lambda p: (p[0], p[1]))
    depth = best = 0
    for _, delta in points:
        depth += delta
        if depth > best:
            best = depth
    return best
