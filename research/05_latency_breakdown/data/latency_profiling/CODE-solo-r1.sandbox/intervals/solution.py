"""Half-open integer intervals: merge, free gaps, max overlap."""


def _clean(intervals):
    """Return sorted list of non-empty (start, end) tuples."""
    out = []
    for start, end in intervals:
        if start < end:
            out.append((start, end))
    out.sort()
    return out


def merge(intervals):
    merged = []
    for start, end in _clean(intervals):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals, window):
    ws, we = window
    if ws >= we:
        return []
    gaps = []
    cursor = ws
    for start, end in merge(intervals):
        if end <= cursor:
            continue
        if start >= we:
            break
        if start > cursor:
            gaps.append((cursor, min(start, we)))
        cursor = max(cursor, end)
        if cursor >= we:
            break
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    events = []
    for start, end in _clean(intervals):
        events.append((start, 1))
        events.append((end, -1))
    if not events:
        return 0
    # At a shared timestamp, process departures (-1) before arrivals (+1):
    # half-open intervals that only touch do not overlap.
    events.sort()
    best = 0
    count = 0
    for _, delta in events:
        count += delta
        if count > best:
            best = count
    return best
