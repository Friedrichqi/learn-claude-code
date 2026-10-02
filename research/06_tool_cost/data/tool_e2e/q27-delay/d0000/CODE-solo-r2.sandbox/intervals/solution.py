"""Half-open integer interval utilities: merge, free gaps, max overlap."""


def _clean(intervals):
    return sorted(
        (start, end)
        for start, end in intervals
        if start < end
    )


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
    gaps = []
    pos = ws
    for start, end in _clean(intervals):
        if end <= ws or start >= we:
            continue
        s = max(start, ws)
        e = min(end, we)
        if s > pos:
            gaps.append((pos, s))
        pos = e
        if pos >= we:
            break
    if pos < we:
        gaps.append((pos, we))
    return gaps


def max_overlap(intervals):
    points = []
    for start, end in intervals:
        if start < end:
            points.append((start, 1))
            points.append((end, -1))
    # Sort so that an ending point comes before a starting point at the same
    # coordinate (half-open: touching intervals do not overlap).
    points.sort(key=lambda p: (p[0], p[1]))
    depth = best = 0
    for _, delta in points:
        depth += delta
        if depth > best:
            best = depth
    return best
