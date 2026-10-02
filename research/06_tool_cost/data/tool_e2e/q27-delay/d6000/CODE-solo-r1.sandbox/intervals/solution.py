"""Half-open integer interval utilities."""


def _clean(intervals):
    out = []
    for pair in intervals:
        s, e = pair
        if s < e:
            out.append((s, e))
    return out


def merge(intervals):
    """Merge overlapping and touching intervals; nested ones collapse into the outer."""
    cleaned = _clean(intervals)
    if not cleaned:
        return []
    cleaned.sort()
    result = [cleaned[0]]
    for s, e in cleaned[1:]:
        last_s, last_e = result[-1]
        if s <= last_e:  # overlapping or touching
            if e > last_e:
                result[-1] = (last_s, e)
        else:
            result.append((s, e))
    return result


def free_gaps(intervals, window):
    """Maximal sub-ranges of [ws, we) not covered by any interval."""
    ws, we = window
    if ws >= we:
        return []
    clipped = _clean(intervals)
    clipped.sort()
    gaps = []
    cursor = ws
    for s, e in clipped:
        s = max(s, ws)
        e = min(e, we)
        if e <= ws or s >= we:
            continue
        if s > cursor:
            gaps.append((cursor, s))
            cursor = s
        if e > cursor:
            cursor = e
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
    events = []
    for s, e in cleaned:
        events.append((s, 1))
        events.append((e, -1))
    # At equal coordinates, ends (-1) must be processed before starts (1)
    # because ranges are half-open: (1,3) and (3,5) do not overlap.
    events.sort()
    cur = 0
    best = 0
    for _, delta in events:
        cur += delta
        if cur > best:
            best = cur
    return best
