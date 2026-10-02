"""Half-open integer interval utilities."""


def merge(intervals):
    """Merge overlapping and touching intervals; returns sorted list of tuples."""
    pairs = [(a, b) for a, b in intervals if a < b]
    if not pairs:
        return []
    pairs.sort()
    out = [list(pairs[0])]
    for s, e in pairs[1:]:
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
    for a, b in intervals:
        if a >= b:
            continue
        s, e = max(a, ws), min(b, we)
        if s < e:
            clipped.append((s, e))
    merged = merge(clipped)
    gaps = []
    cur = ws
    for s, e in merged:
        if s > cur:
            gaps.append((cur, s))
        cur = e
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty intervals covering one common point."""
    events = []
    for a, b in intervals:
        if a < b:
            events.append((a, 1))
            events.append((b, -1))
    if not events:
        return 0
    events.sort()
    cur = best = 0
    for _, d in events:
        cur += d
        if cur > best:
            best = cur
    return best
