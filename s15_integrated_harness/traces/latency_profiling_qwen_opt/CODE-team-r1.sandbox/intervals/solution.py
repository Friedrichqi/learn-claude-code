"""Half-open integer interval operations."""


def _clean(intervals):
    return sorted((s, e) for s, e in intervals if s < e)


def merge(intervals):
    result = []
    for start, end in _clean(intervals):
        if result and start <= result[-1][1]:
            if end > result[-1][1]:
                result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result


def free_gaps(intervals, window):
    ws, we = window
    if ws >= we:
        return []
    merged = merge((max(s, ws), min(e, we)) for s, e in _clean(intervals) if s < we and e > ws)
    gaps = []
    cur = ws
    for start, end in merged:
        if start > cur:
            gaps.append((cur, start))
        cur = max(cur, end)
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals):
    events = []
    for s, e in _clean(intervals):
        events.append((s, 1))
        events.append((e, -1))
    # at equal coordinates, half-open means closes happen before opens count
    events.sort(key=lambda p: (p[0], p[1]))
    depth = best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
