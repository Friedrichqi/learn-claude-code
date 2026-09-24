"""Half-open integer interval utilities.

An interval is a pair (start, end) meaning the half-open range [start, end).
Pairs with start >= end are empty and are ignored everywhere.
"""

from __future__ import annotations


def _clean(intervals):
    """Yield non-empty (start, end) tuples from the input pairs."""
    for start, end in intervals:
        if start < end:
            yield (start, end)


def merge(intervals):
    """Merge overlapping and touching intervals; return sorted list of tuples."""
    result = []
    for start, end in sorted(_clean(intervals)):
        if result and start <= result[-1][1]:
            # Overlapping or touching: extend the current interval.
            if end > result[-1][1]:
                result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result


def free_gaps(intervals, window):
    """Maximal sub-ranges of [ws, we) not covered by any interval, in order."""
    ws, we = window
    if ws >= we:
        return []
    clipped = []
    for start, end in sorted(_clean(intervals)):
        if end <= ws or start >= we:
            continue
        s = max(start, ws)
        e = min(end, we)
        if s < e:
            clipped.append((s, e))
    gaps = []
    cursor = ws
    for start, end in merge(clipped):
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty intervals covering one common point."""
    starts = []
    ends = []
    for start, end in _clean(intervals):
        starts.append(start)
        ends.append(end)
    if not starts:
        return 0
    # Sweep line: process each start point; drop intervals that have already
    # ended (half-open: an interval ending at `s` does not cover `s`).
    starts.sort()
    ends.sort()
    depth = 0
    best = 0
    ended = 0
    for s in starts:
        while ended < len(ends) and ends[ended] <= s:
            ended += 1
            depth -= 1
        depth += 1
        if depth > best:
            best = depth
    return best
