"""Half-open integer interval operations."""


def _clean(intervals):
    """Filter out empty intervals (start >= end)."""
    return [(s, e) for (s, e) in intervals if s < e]


def merge(intervals):
    """Merge overlapping and touching half-open intervals."""
    ivs = sorted(_clean(intervals))
    if not ivs:
        return []
    out = [list(ivs[0])]
    for s, e in ivs[1:]:
        if s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1][1] = e
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def free_gaps(intervals, window):
    """Maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    clipped = []
    for s, e in _clean(intervals):
        cs, ce = max(s, ws), min(e, we)
        if cs < ce:
            clipped.append((cs, ce))
    covered = merge(clipped)
    gaps = []
    cur = ws
    for s, e in covered:
        if s > cur:
            gaps.append((cur, s))
        cur = max(cur, e)
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty intervals covering one common point."""
    events = []
    for s, e in _clean(intervals):
        events.append((s, 1))
        events.append((e, -1))
    # Sort so that at equal positions, ends (-1) are processed before starts
    # (+1); correct for half-open intervals: (1,3) and (3,5) do not overlap.
    events.sort()
    best = cur = 0
    for _, delta in events:
        cur += delta
        if cur > best:
            best = cur
    return best
