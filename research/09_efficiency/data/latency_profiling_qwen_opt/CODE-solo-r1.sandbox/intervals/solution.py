"""Half-open integer interval utilities."""


def _clean(intervals):
    return sorted((s, e) for s, e in intervals if s < e)


def merge(intervals):
    result = []
    for s, e in _clean(intervals):
        if result and s <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], e))
        else:
            result.append((s, e))
    return result


def free_gaps(intervals, window):
    ws, we = window
    gaps = []
    cur = ws
    for s, e in _clean(intervals):
        if e <= ws or s >= we:
            continue
        s = max(s, ws)
        e = min(e, we)
        if s > cur:
            gaps.append((cur, s))
        cur = max(cur, e)
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
    if not events:
        return 0
    events.sort(key=lambda t: (t[0], t[1]))
    best = cur = 0
    for _, d in events:
        cur += d
        if cur > best:
            best = cur
    return best
