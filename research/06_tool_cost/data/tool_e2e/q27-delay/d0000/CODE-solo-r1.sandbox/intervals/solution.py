"""Half-open integer interval utilities."""


def _clean(intervals):
    """Return only the non-empty intervals as (start, end) tuples."""
    return [(s, e) for (s, e) in intervals if s < e]


def merge(intervals):
    """Merge overlapping and touching half-open intervals.

    Returns a list of (start, end) tuples sorted by start.
    """
    cleaned = sorted(_clean(intervals))
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
    """Return the maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    clipped = [
        (max(s, ws), min(e, we))
        for (s, e) in _clean(intervals)
        if s < we and e > ws
    ]
    merged = merge(clipped)
    gaps = []
    cur = ws
    for s, e in merged:
        if s > cur:
            gaps.append((cur, s))
        if e > cur:
            cur = e
        if cur >= we:
            break
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals):
    """Return the largest number of non-empty intervals covering one common point."""
    events = []
    for s, e in _clean(intervals):
        events.append((s, 1))
        events.append((e, -1))
    if not events:
        return 0
    # Sort by coordinate; at equal coordinates, ends (-1) before starts (+1)
    # so half-open intervals sharing only a boundary do not overlap.
    events.sort(key=lambda ev: (ev[0], ev[1]))
    best = cur = 0
    for _, delta in events:
        cur += delta
        if cur > best:
            best = cur
    return best
