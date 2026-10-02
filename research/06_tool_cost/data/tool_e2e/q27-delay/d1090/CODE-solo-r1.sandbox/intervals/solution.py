"""Half-open integer interval utilities.

An interval is a pair (start, end) meaning the half-open range [start, end).
A pair with start >= end is empty and ignored everywhere.
All functions return lists of (start, end) tuples sorted by start.
"""


def _clean(intervals):
    """Filter out empty intervals and sort by start."""
    return sorted((s, e) for s, e in intervals if s < e)


def merge(intervals):
    """Merge overlapping and touching intervals."""
    merged = []
    for s, e in _clean(intervals):
        if merged and s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def free_gaps(intervals, window):
    """Maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    gaps = []
    cur = ws
    for s, e in merge(intervals):
        if e <= ws:
            continue
        if s >= we:
            break
        clipped_start = max(s, ws)
        clipped_end = min(e, we)
        if clipped_start > cur:
            gaps.append((cur, clipped_start))
        if clipped_end > cur:
            cur = clipped_end
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty intervals covering one common point."""
    events = []
    for s, e in intervals:
        if s < e:
            events.append((s, 1))
            events.append((e, -1))
    if not events:
        return 0
    # At the same coordinate, ends (-1) sort before starts (+1), so
    # touching half-open intervals never overlap.
    events.sort(key=lambda ev: (ev[0], ev[1]))
    best = cur = 0
    for _, delta in events:
        cur += delta
        if cur > best:
            best = cur
    return best
