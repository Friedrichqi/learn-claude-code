"""Half-open integer interval utilities."""


def _clean(intervals):
    """Filter out empty/inverted intervals and sort by start."""
    return sorted((s, e) for s, e in intervals if s < e)


def merge(intervals):
    """Merge overlapping and touching intervals; return sorted list of tuples."""
    cleaned = _clean(intervals)
    if not cleaned:
        return []
    out = [list(cleaned[0])]
    for s, e in cleaned[1:]:
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
    merged = merge(intervals)
    gaps = []
    cur = ws
    for s, e in merged:
        if e <= cur:
            continue
        if s > cur:
            gaps.append((cur, s))
        cur = max(cur, e)
        if cur >= we:
            break
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty intervals sharing a common point."""
    events = []
    for s, e in intervals:
        if s < e:
            events.append((s, 1))
            events.append((e, -1))
    if not events:
        return 0
    # Half-open: at the same point, an end (-1) must sort before a start (+1).
    events.sort(key=lambda p: (p[0], p[1]))
    best = 0
    cur = 0
    for _, d in events:
        cur += d
        if cur > best:
            best = cur
    return best
