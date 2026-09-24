"""Half-open integer interval utilities.

An interval is a pair (start, end) meaning the half-open range [start, end).
A pair with start >= end is empty and ignored everywhere.
All public functions return lists of (start, end) tuples sorted by start.
"""

from typing import Iterable, List, Sequence, Tuple

Interval = Tuple[int, int]


def _clean(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Return non-empty intervals as (start, end) tuples, sorted by start."""
    out: List[Interval] = []
    for a, b in intervals:
        if a < b:
            out.append((a, b))
    out.sort(key=lambda p: (p[0], p[1]))
    return out


def merge(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Merge overlapping and touching intervals."""
    result: List[Interval] = []
    for start, end in _clean(intervals):
        if result and start <= result[-1][1]:
            # Overlapping or touching: extend previous interval's end if needed.
            if end > result[-1][1]:
                result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result


def free_gaps(intervals: Iterable[Sequence[int]], window: Sequence[int]) -> List[Interval]:
    """Maximal sub-ranges of [ws, we) not covered by any interval, in order."""
    ws, we = window
    if ws >= we:
        return []

    merged = []
    for start, end in merge(intervals):
        # Clip to the window; ignore fully outside ones.
        s = max(start, ws)
        e = min(end, we)
        if s < e:
            merged.append((s, e))

    gaps: List[Interval] = []
    cursor = ws
    for s, e in merged:
        if cursor < s:
            gaps.append((cursor, s))
        cursor = e
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals: Iterable[Sequence[int]]) -> int:
    """Largest number of non-empty intervals covering one common point."""
    events: List[Tuple[int, int]] = []
    for a, b in intervals:
        if a < b:
            # Ends (-1) must sort before starts (+1) at equal positions so
            # half-open touching intervals do not count as overlapping.
            events.append((a, 1))
            events.append((b, -1))
    if not events:
        return 0
    events.sort(key=lambda e: (e[0], e[1]))
    depth = 0
    best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
