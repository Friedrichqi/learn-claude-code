"""Half-open integer interval utilities: merge, free_gaps, max_overlap.

An interval is a pair ``(start, end)`` meaning ``[start, end)``; pairs with
``start >= end`` are empty and ignored. All functions return lists of
``(start, end)`` tuples sorted by start and run in O(n log n).
"""

from typing import Iterable, List, Sequence, Tuple

Interval = Tuple[int, int]


def _normalize(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Filter out empty intervals and return sorted copies as tuples."""
    valid = [(int(s), int(e)) for s, e in intervals if e > s]
    valid.sort()
    return valid


def merge(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Merge overlapping and touching intervals into a sorted, disjoint list."""
    merged: List[Interval] = []
    for start, end in _normalize(intervals):
        if merged and start <= merged[-1][1]:  # touching (start == end) merges too
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals: Iterable[Sequence[int]],
              window: Sequence[int]) -> List[Interval]:
    """Return maximal sub-ranges of ``window`` not covered by any interval."""
    ws, we = window
    if we <= ws:
        return []
    covered = merge(
        (max(int(s), ws), min(int(e), we)) for s, e in intervals
    )
    gaps: List[Interval] = []
    cursor = ws
    for start, end in covered:
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals: Iterable[Sequence[int]]) -> int:
    """Largest number of intervals covering one common point (half-open)."""
    events: List[Tuple[int, int]] = []
    for start, end in _normalize(intervals):
        events.append((start, +1))
        events.append((end, -1))
    events.sort()  # (t, -1) sorts before (t, +1): touching ranges don't overlap

    best = current = 0
    for _, delta in events:
        current += delta
        if current > best:
            best = current
    return best
