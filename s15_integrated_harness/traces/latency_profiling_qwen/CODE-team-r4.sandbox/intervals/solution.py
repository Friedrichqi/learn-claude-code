"""Half-open integer interval utilities: merge, free_gaps, max_overlap."""


def _clean(intervals):
    """Filter empty/inverted pairs into a list of (start, end) tuples."""
    cleaned = []
    for pair in intervals:
        start, end = pair
        if start < end:
            cleaned.append((start, end))
    return cleaned


def merge(intervals):
    """Merge overlapping and touching half-open intervals.

    Returns a list of (start, end) tuples sorted by start.
    """
    intervals = sorted(_clean(intervals))
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals, window):
    """Return maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    merged = merge(intervals)
    gaps = []
    pos = ws
    for start, end in merged:
        if end <= pos:
            continue
        if start > pos:
            gap_end = min(start, we)
            gaps.append((pos, gap_end))
            pos = gap_end
        if pos >= we:
            break
        if end > pos:
            pos = end
    if pos < we:
        gaps.append((pos, we))
    return gaps


def max_overlap(intervals):
    """Return the largest number of non-empty intervals covering one point."""
    events = []
    for start, end in _clean(intervals):
        events.append((start, 1))
        events.append((end, -1))
    # Sorting (coord, delta) processes -1 (end) before +1 (start) at the same
    # coordinate, which is correct for half-open intervals: [a, b) and [b, c)
    # do not share point b.
    events.sort()
    depth = 0
    best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
