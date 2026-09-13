"""Half-open integer interval utilities: merge, free_gaps, max_overlap."""

from typing import Iterable, List, Sequence, Tuple

Interval = Tuple[int, int]


def _normalize(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Return non-empty intervals as (start, end) tuples sorted by start."""
    out = [(int(s), int(e)) for s, e in intervals if s < e]
    out.sort()
    return out


def merge(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Merge overlapping and touching intervals; drop empty ones."""
    result: List[Interval] = []
    for start, end in _normalize(intervals):
        if result and start <= result[-1][1]:
            if end > result[-1][1]:
                result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result


def free_gaps(intervals: Iterable[Sequence[int]], window: Sequence[int]) -> List[Interval]:
    """Maximal sub-ranges of window not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    gaps: List[Interval] = []
    cursor = ws
    for start, end in merge(intervals):
        if end <= cursor:
            continue
        if start > cursor:
            gaps.append((cursor, min(start, we)))
        cursor = max(cursor, end)
        if cursor >= we:
            break
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals: Iterable[Sequence[int]]) -> int:
    """Largest number of intervals covering one common point (half-open)."""
    events = []
    for start, end in _normalize(intervals):
        events.append((start, 1))
        events.append((end, -1))
    events.sort()  # at equal coordinates, (x, -1) sorts before (x, 1): touching does not overlap

    best = 0
    depth = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
