"""Half-open integer interval utilities."""


def merge(intervals):
    """Merge overlapping and touching half-open intervals; returns sorted list of tuples."""
    filtered = [(s, e) for s, e in intervals if s < e]
    if not filtered:
        return []
    filtered.sort()
    merged = [filtered[0]]
    for s, e in filtered[1:]:
        cs, ce = merged[-1]
        if s <= ce:  # overlapping or touching
            if e > ce:
                merged[-1] = (cs, e)
        else:
            merged.append((s, e))
    return [tuple(pair) for pair in merged]


def free_gaps(intervals, window):
    """Maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    gaps = []
    cur = ws
    for s, e in merge(intervals):
        if e <= cur:
            continue
        if s > we:
            break
        if s > cur:
            gaps.append((cur, s))
        cur = e
        if cur >= we:
            break
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty half-open intervals covering one common point."""
    events = []
    append = events.append
    for s, e in intervals:
        if s < e:
            append((s, 1))
            append((e, -1))
    if not events:
        return 0
    # At equal coordinates, ends (-1) are processed before starts (1): half-open semantics.
    events.sort(key=lambda ev: (ev[0], ev[1]))
    depth = best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
