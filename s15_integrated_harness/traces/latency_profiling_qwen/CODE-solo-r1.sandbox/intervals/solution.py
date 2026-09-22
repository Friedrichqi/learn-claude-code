"""Half-open integer interval utilities."""


def _clean(intervals):
    """Yield non-empty (start, end) tuples sorted by start, then end."""
    cleaned = [(s, e) for s, e in intervals if s < e]
    cleaned.sort()
    return cleaned


def merge(intervals):
    """Merge overlapping and touching intervals."""
    out = []
    for s, e in _clean(intervals):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def free_gaps(intervals, window):
    """Return maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    out = []
    cur = ws
    for s, e in _clean(intervals):
        if e <= cur:
            continue
        if s > cur:
            out.append((cur, min(s, we)))
            if min(s, we) >= we:
                break
            cur = s
        if e > cur:
            cur = e
        if cur >= we:
            break
    if cur < we:
        out.append((cur, we))
    return out


def max_overlap(intervals):
    """Largest number of non-empty intervals covering one common point."""
    cleaned = _clean(intervals)
    if not cleaned:
        return 0
    events = []
    for s, e in cleaned:
        events.append((s, 1))
        events.append((e, -1))
    # At equal points, process end events (delta -1) before start events
    # (delta +1): half-open, so (1, 3) and (3, 5) do not overlap.
    events.sort()
    best = cur = 0
    for _, d in events:
        cur += d
        if cur > best:
            best = cur
    return best
