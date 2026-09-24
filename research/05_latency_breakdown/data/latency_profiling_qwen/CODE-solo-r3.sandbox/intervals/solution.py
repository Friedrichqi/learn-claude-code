"""Half-open integer interval utilities."""


def _clean(intervals):
    """Filter empty/inverted intervals and normalize to tuples of ints."""
    cleaned = []
    for start, end in intervals:
        if start < end:
            cleaned.append((int(start), int(end)))
    return cleaned


def merge(intervals):
    """Merge overlapping and touching intervals; return sorted list of tuples."""
    cleaned = _clean(intervals)
    if not cleaned:
        return []
    cleaned.sort()
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            if end > last_end:
                merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals, window):
    """Maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = int(window[0]), int(window[1])
    if ws >= we:
        return []
    clipped = [(s, e) for s, e in _clean(intervals) if s < we and e > ws]
    clipped.sort()
    gaps = []
    cursor = ws
    for start, end in clipped:
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
        if cursor >= we:
            break
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty intervals covering one common point."""
    cleaned = _clean(intervals)
    if not cleaned:
        return 0
    points = []
    for start, end in cleaned:
        points.append((start, 1))
        points.append((end, -1))
    # Process ends before starts at equal coordinates (half-open semantics).
    points.sort(key=lambda p: (p[0], p[1]))
    depth = best = 0
    for _, delta in points:
        depth += delta
        if depth > best:
            best = depth
    return best
