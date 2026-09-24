"""Solutions for half-open integer interval problems.

An interval is a pair ``(start, end)`` meaning the half-open range
``[start, end)``. Pairs with ``start >= end`` are empty and ignored.
All functions return lists of ``(start, end)`` tuples sorted by start.
"""


def _normalized(intervals):
    """Yield sorted (start, end) tuples for non-empty intervals."""
    cleaned = [(int(s), int(e)) for s, e in intervals if s < e]
    cleaned.sort()
    return cleaned


def merge(intervals):
    """Merge overlapping and touching intervals into disjoint blocks."""
    merged = []
    for start, end in _normalized(intervals):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals, window):
    """Return maximal sub-ranges of window not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    gaps = []
    cursor = ws
    for start, end in merge(intervals):
        start = max(start, ws)
        end = min(end, we)
        if start >= end:
            continue
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
        if cursor >= we:
            break
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Largest number of intervals covering one common point."""
    events = []
    for start, end in intervals:
        if start < end:
            events.append((start, 1))
            events.append((end, -1))
    # Sort by coordinate; -1 (interval end) before +1 (interval start)
    # so that touching intervals do not count as overlapping.
    events.sort()
    depth = 0
    best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
