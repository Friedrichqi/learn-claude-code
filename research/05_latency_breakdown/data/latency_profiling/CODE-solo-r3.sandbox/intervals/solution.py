"""Half-open integer interval utilities: merge, free gaps, max overlap."""


def _normalize(intervals):
    """Return sorted list of non-empty (start, end) tuples."""
    cleaned = [(int(s), int(e)) for s, e in intervals if s < e]
    cleaned.sort()
    return cleaned


def merge(intervals):
    """Merge overlapping and touching half-open intervals."""
    ivs = _normalize(intervals)
    out = []
    for s, e in ivs:
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def free_gaps(intervals, window):
    """Maximal uncovered sub-ranges of the window, in order."""
    ws, we = window
    if ws >= we:
        return []
    merged = merge(intervals)
    gaps = []
    prev = ws
    for s, e in merged:
        if e <= prev:
            continue
        if s > prev:
            gaps.append((prev, min(s, we)))
            prev = min(s, we)
        if e > prev:
            prev = e
        if prev >= we:
            break
    if prev < we:
        gaps.append((prev, we))
    return gaps


def max_overlap(intervals):
    """Largest number of half-open intervals covering a common point."""
    ivs = _normalize(intervals)
    events = []
    for s, e in ivs:
        events.append((s, 1))
        events.append((e, -1))
    events.sort()  # starts before ends at the same coordinate (end = -1 sorts first)
    best = cur = 0
    for _, delta in events:
        cur += delta
        if cur > best:
            best = cur
    return best
