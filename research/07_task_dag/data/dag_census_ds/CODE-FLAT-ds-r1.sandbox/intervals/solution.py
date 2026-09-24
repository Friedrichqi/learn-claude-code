"""Half-open integer interval utilities.

An interval is a pair ``(start, end)`` of integers meaning the half-open
range ``[start, end)``.  A pair with ``start >= end`` is empty and is ignored
everywhere.  Inputs may be lists or tuples of pairs in any order.
"""

from typing import Iterable, List, Sequence, Tuple


def _clean(intervals: Iterable[Sequence[int]]) -> List[Tuple[int, int]]:
    """Return the non-empty intervals as tuples sorted by (start, end)."""
    out: List[Tuple[int, int]] = []
    for pair in intervals:
        start, end = pair[0], pair[1]
        if start < end:
            out.append((start, end))
    out.sort()
    return out


def merge(intervals: Iterable[Sequence[int]]) -> List[Tuple[int, int]]:
    """Merge overlapping and touching intervals; nested ones collapse."""
    cleaned = _clean(intervals)
    if not cleaned:
        return []

    merged: List[Tuple[int, int]] = []
    cur_start, cur_end = cleaned[0]
    for start, end in cleaned[1:]:
        if start <= cur_end:  # overlapping or touching
            if end > cur_end:
                cur_end = end
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))
    return merged


def free_gaps(
    intervals: Iterable[Sequence[int]], window: Sequence[int]
) -> List[Tuple[int, int]]:
    """Maximal sub-ranges of ``window`` not covered by any interval."""
    ws, we = window[0], window[1]
    if ws >= we:
        return []

    clipped: List[Tuple[int, int]] = []
    for pair in intervals:
        start, end = pair[0], pair[1]
        start = max(start, ws)
        end = min(end, we)
        if start < end:
            clipped.append((start, end))
    if not clipped:
        return [(ws, we)]

    merged = merge(clipped)

    gaps: List[Tuple[int, int]] = []
    cursor = ws
    for start, end in merged:
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals: Iterable[Sequence[int]]) -> int:
    """Largest number of intervals covering one common point."""
    events: List[Tuple[int, int]] = []
    for pair in intervals:
        start, end = pair[0], pair[1]
        if start < end:
            events.append((start, 1))
            events.append((end, -1))
    if not events:
        return 0
    # Sort by position; for equal positions process ends before starts.
    events.sort(key=lambda item: (item[0], item[1]))

    best = 0
    current = 0
    for _, delta in events:
        current += delta
        if current > best:
            best = current
    return best
