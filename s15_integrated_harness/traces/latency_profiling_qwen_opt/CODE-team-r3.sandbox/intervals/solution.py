"""Half-open integer interval utilities."""


def _clean(intervals):
    return sorted((s, e) for s, e in intervals if s < e)


def merge(intervals):
    out = []
    for s, e in _clean(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def free_gaps(intervals, window):
    ws, we = window
    if ws >= we:
        return []
    gaps = []
    cursor = ws
    for s, e in merge([(a, b) for a, b in _clean(intervals)]):
        if e <= cursor:
            continue
        if s > cursor:
            gaps.append((cursor, min(s, we)))
        cursor = max(cursor, e)
        if cursor >= we:
            break
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    events = []
    for s, e in _clean(intervals):
        events.append((s, 1))
        events.append((e, -1))
    # start events before end events at same coordinate? No: half-open,
    # end at x frees before start at x counts, so sort ends first.
    events.sort(key=lambda t: (t[0], t[1]))
    depth = best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
