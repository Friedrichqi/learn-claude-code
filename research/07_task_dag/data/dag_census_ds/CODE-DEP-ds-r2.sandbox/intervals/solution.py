"""Half-open integer interval utilities.

An interval is a pair ``(start, end)`` of integers meaning the half-open range
``[start, end)``.  A pair with ``start >= end`` is empty and is ignored by every
function.  Inputs may be lists or tuples of pairs in any order; every function
returns a list of ``(start, end)`` tuples sorted by start.
"""


def _normalize(intervals):
    """Return the non-empty intervals as sorted ``(start, end)`` int tuples."""
    clean = []
    for pair in intervals:
        start, end = pair[0], pair[1]
        if start < end:
            clean.append((start, end))
    clean.sort()
    return clean


def merge(intervals):
    """Merge overlapping and touching intervals into a sorted disjoint list.

    Touching intervals merge, e.g. ``(1, 3)`` and ``(3, 5)`` become ``(1, 5)``;
    nested intervals collapse into the outer one.  Runs in O(n log n).
    """
    clean = _normalize(intervals)
    result = []
    for start, end in clean:
        if result and start <= result[-1][1]:
            last_start, last_end = result[-1]
            if end > last_end:
                result[-1] = (last_start, end)
        else:
            result.append((start, end))
    return result


def free_gaps(intervals, window):
    """Return the maximal sub-ranges of ``[ws, we)`` covered by no interval.

    ``window`` is a pair ``(ws, we)``.  Intervals partly outside the window are
    clipped; intervals fully outside are ignored.  Results are returned in
    increasing order.  An empty window (``ws >= we``) yields ``[]``.
    """
    ws, we = window[0], window[1]
    if ws >= we:
        return []

    merged = merge(intervals)
    gaps = []
    cursor = ws
    for start, end in merged:
        if end <= ws:
            continue
        if start >= we:
            break
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
        if cursor >= we:
            break
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Return the largest number of non-empty intervals covering one point.

    Ranges are half-open, so ``(1, 3)`` and ``(3, 5)`` do not overlap.  Identical
    intervals each count towards the depth.  Runs in O(n log n).
    """
    events = []
    for start, end in _normalize(intervals):
        events.append((start, 1))
        events.append((end, -1))
    # At a shared endpoint, ends (-1) are processed before starts (+1) so that
    # touching half-open ranges are not counted as overlapping.
    events.sort()
    depth = 0
    best = 0
    for _coord, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
