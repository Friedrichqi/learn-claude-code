"""Half-open integer interval utilities.

An interval is a pair (start, end) meaning the half-open range [start, end).
A pair with start >= end is empty and is ignored. Inputs may be lists or
tuples of pairs in any order; every function returns a list of
(start, end) tuples sorted by start.
"""

from typing import Iterable, List, Sequence, Tuple

Interval = Tuple[int, int]


def _valid_sorted(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Filter out empty/inverted intervals, coerce to tuples, sort by start."""
    pairs = []
    for start, end in intervals:
        if start < end:
            pairs.append((start, end))
    pairs.sort()
    return pairs


def merge(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Merge overlapping and touching intervals; O(n log n)."""
    pairs = _valid_sorted(intervals)
    merged: List[Interval] = []
    for start, end in pairs:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(
    intervals: Iterable[Sequence[int]], window: Sequence[int]
) -> List[Interval]:
    """Maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    ws, we = int(ws), int(we)
    clipped = []
    for start, end in intervals:
        if start < end:
            s = max(start, ws)
            e = min(end, we)
            if s < e:
                clipped.append((s, e))
    merged = merge(clipped)
    gaps: List[Interval] = []
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
    """Largest number of non-empty intervals covering one common point."""
    events = []
    for start, end in intervals:
        if start < end:
            events.append((start, 1))
            events.append((end, -1))
    # End events sort before start events at the same point, so touching
    # half-open intervals such as (1, 3) and (3, 5) do not count as overlapping.
    events.sort()
    depth = 0
    best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
