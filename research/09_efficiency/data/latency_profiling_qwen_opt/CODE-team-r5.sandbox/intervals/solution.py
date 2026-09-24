"""Half-open integer interval operations: merge, free_gaps, max_overlap."""


def _clean(intervals):
    return sorted((s, e) for s, e in intervals if s < e)


def merge(intervals):
    out = []
    for s, e in _clean(intervals):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def free_gaps(intervals, window):
    ws, we = window
    if ws >= we:
        return []
    gaps = []
    cur = ws
    for s, e in merge(intervals):
        if s >= we or e <= ws:
            continue
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
    events = []
    for s, e in intervals:
        if s < e:
            events.append((s, 1))
            events.append((e, -1))
    # At equal coordinates, closes (-1) must be processed before opens (+1)
    # because half-open intervals do not overlap at shared endpoints.
    events.sort()
    depth = best = 0
    for _, d in events:
        depth += d
        if depth > best:
            best = depth
    return best
