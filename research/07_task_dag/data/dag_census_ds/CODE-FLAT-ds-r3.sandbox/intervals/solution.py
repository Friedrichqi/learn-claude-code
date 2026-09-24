"""Half-open integer interval utilities.

An interval is a pair ``(start, end)`` of integers meaning the half-open range
``[start, end)``.  A pair with ``start >= end`` is empty and is ignored
everywhere.  Inputs may be lists or tuples of pairs, in any order; results are
lists of ``(start, end)`` tuples sorted by start.
"""


def _normalize(intervals):
    """Yield ``(start, end)`` tuples, dropping empty/inverted intervals."""
    out = []
    for pair in intervals:
        start, end = pair[0], pair[1]
        if start < end:
            out.append((start, end))
    return out


def merge(intervals):
    """Merge overlapping and touching intervals, sorted by start.

    ``(1, 3)`` and ``(3, 5)`` merge into ``(1, 5)`` because the ranges are
    half-open.  Nested intervals collapse into the outer one.
    """
    items = _normalize(intervals)
    if not items:
        return []
    items.sort()
    result = []
    cur_start, cur_end = items[0]
    for start, end in items[1:]:
        if start <= cur_end:  # overlapping or touching
            if end > cur_end:
                cur_end = end
        else:
            result.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    result.append((cur_start, cur_end))
    return result


def free_gaps(intervals, window):
    """Maximal sub-ranges of half-open ``window`` covered by no interval."""
    if not hasattr(window, "__len__") or len(window) != 2:
        raise ValueError("window must be a (start, end) pair")
    ws, we = window[0], window[1]
    if ws >= we:
        return []

    clipped = []
    for start, end in _normalize(intervals):
        if end <= ws or start >= we:
            continue
        clipped.append((max(start, ws), min(end, we)))

    covered = merge(clipped)
    gaps = []
    cursor = ws
    for start, end in covered:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = end
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Largest number of intervals covering one common point (or 0)."""
    events = []
    for start, end in _normalize(intervals):
        events.append((start, 1))
        events.append((end, -1))
    if not events:
        return 0
    # At an equal coordinate a closing event must be processed first: since the
    # ranges are half-open, the point itself belongs to the interval that is
    # *starting* there, so touching intervals never count as overlapping.
    events.sort(key=lambda e: (e[0], e[1]))
    depth = 0
    best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
