"""Half-open integer interval utilities.

An interval is a pair ``(start, end)`` of integers denoting the half-open
range ``[start, end)``.  A pair with ``start >= end`` is empty (or inverted)
and is ignored everywhere.  Inputs may be lists or tuples of pairs in any
order; inputs are never mutated.

Public functions:
    merge(intervals) -> list[tuple[int, int]]
    free_gaps(intervals, window) -> list[tuple[int, int]]
    max_overlap(intervals) -> int
"""

__all__ = ["merge", "free_gaps", "max_overlap"]


def _normalised_sorted(intervals):
    """Return non-empty intervals as (start, end) tuples sorted by start.

    Empty/inverted pairs (``start >= end``) are dropped.  A fresh list is
    built, so the caller's container is never mutated.
    """
    cleaned = [(s, e) for s, e in intervals if s < e]
    cleaned.sort()
    return cleaned


def merge(intervals):
    """Merge overlapping and touching intervals into their union.

    Returns a list of disjoint ``(start, end)`` tuples sorted by start.
    Runs in O(n log n) time (a single sort plus a linear sweep).
    """
    ordered = _normalised_sorted(intervals)
    result = []
    for start, end in ordered:
        if result and start <= result[-1][1]:
            # Overlap or mere touch: extend the current run if needed.
            if end > result[-1][1]:
                result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result


def free_gaps(intervals, window):
    """Return the maximal uncovered sub-ranges of ``window``.

    ``window`` is a pair ``(ws, we)`` describing ``[ws, we)``.  Intervals are
    clipped to the window; parts outside are ignored.  Returns a list of
    half-open ``(start, end)`` tuples in increasing order.
    """
    ws, we = window
    if ws >= we:
        return []

    clipped = []
    for start, end in intervals:
        # Clip to the window, then drop anything now empty.
        if start < ws:
            start = ws
        if end > we:
            end = we
        if start < end:
            clipped.append((start, end))

    covered = merge(clipped)
    gaps = []
    cursor = ws
    for start, end in covered:
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Return the largest number of non-empty intervals covering a point.

    Uses half-open semantics, so touching intervals do not overlap.  Runs in
    O(n log n) time via an events sweep.  Returns 0 when nothing overlaps.
    """
    events = []
    for start, end in intervals:
        if start < end:
            events.append((start, 1))
            events.append((end, -1))
    if not events:
        return 0

    # At equal coordinates process ends (-1) before starts (+1) so that
    # touching intervals are not counted as overlapping.
    events.sort()

    depth = 0
    best = 0
    for _coord, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
