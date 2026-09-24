"""Half-open integer interval utilities.

An interval is a pair (start, end) meaning the half-open range [start, end).
Pairs with start >= end are empty and ignored everywhere.
"""

from typing import Iterable, List, Sequence, Tuple

Interval = Tuple[int, int]


def _clean(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Return non-empty intervals as a list of (start, end) tuples."""
    out: List[Interval] = []
    for s, e in intervals:
        if s < e:
            out.append((s, e))
    return out


def merge(intervals: Iterable[Sequence[int]]) -> List[Interval]:
    """Merge overlapping and touching intervals; sorted by start."""
    cleaned = _clean(intervals)
    if not cleaned:
        return []
    cleaned.sort()
    merged: List[Interval] = [cleaned[0]]
    for s, e in cleaned[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:  # overlapping or touching (nested handled by max)
            if e > last_e:
                merged[-1] = (last_s, e)
        else:
            merged.append((s, e))
    return merged


def free_gaps(intervals: Iterable[Sequence[int]], window: Sequence[int]) -> List[Interval]:
    """Maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    clipped: List[Interval] = []
    for s, e in _clean(intervals):
        if e <= ws or s >= we:
            continue
        clipped.append((max(s, ws), min(e, we)))
    if not clipped:
        return [(ws, we)]
    merged = merge(clipped)
    gaps: List[Interval] = []
    cur = ws
    for s, e in merged:
        if s > cur:
            gaps.append((cur, s))
        if e > cur:
            cur = e
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals: Iterable[Sequence[int]]) -> int:
    """Largest number of non-empty intervals covering one common point."""
    events: List[Tuple[int, int]] = []
    for s, e in _clean(intervals):
        events.append((s, 1))
        events.append((e, -1))
    if not events:
        return 0
    # Sorting by (point, delta) makes end events (-1) precede start events (+1)
    # at the same point, matching half-open semantics.
    events.sort()
    best = depth = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
