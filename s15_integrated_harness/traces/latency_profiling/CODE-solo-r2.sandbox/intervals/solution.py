"""Half-open integer interval utilities."""

EMPTY = ()


def _normalize(intervals):
    """Yield non-empty (start, end) pairs sorted by start."""
    return sorted((int(s), int(e)) for s, e in intervals if s < e)


def merge(intervals):
    """Merge overlapping and touching half-open intervals."""
    merged = []
    for start, end in _normalize(intervals):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def free_gaps(intervals, window):
    """Maximal sub-ranges of the window covered by no interval."""
    ws, we = window
    if ws >= we:
        return []
    gaps = []
    cursor = ws
    for start, end in _normalize(intervals):
        if end <= cursor:
            continue
        if start > cursor:
            gaps.append((cursor, min(start, we)))
        if end > cursor:
            cursor = end
        if cursor >= we:
            break
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Largest number of half-open intervals covering one common point."""
    events = []
    for start, end in _normalize(intervals):
        events.append((start, 1))
        events.append((end, -1))
    # At equal points, process ends (-1) before starts (+1): half-open.
    events.sort()
    best = current = 0
    for _, delta in events:
        current += delta
        if current > best:
            best = current
    return best
