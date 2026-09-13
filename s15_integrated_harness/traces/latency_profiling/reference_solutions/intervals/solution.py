def _clean(intervals):
    out = []
    for pair in intervals:
        s, e = pair
        if s < e:
            out.append((int(s), int(e)))
    out.sort()
    return out

def merge(intervals):
    merged = []
    for s, e in _clean(intervals):
        if merged and s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged

def free_gaps(intervals, window):
    ws, we = window
    if ws >= we:
        return []
    gaps = []
    cursor = ws
    for s, e in merge(intervals):
        if e <= ws or s >= we:
            continue
        s = max(s, ws); e = min(e, we)
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < we:
        gaps.append((cursor, we))
    return gaps

def max_overlap(intervals):
    events = []
    for s, e in _clean(intervals):
        events.append((s, 1)); events.append((e, -1))
    events.sort()  # (x, -1) sorts before (x, +1): half-open
    best = cur = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best
