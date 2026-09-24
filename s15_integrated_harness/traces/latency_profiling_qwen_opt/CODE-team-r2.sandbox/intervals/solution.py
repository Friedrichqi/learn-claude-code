"""Half-open integer interval utilities."""


def _clean(intervals):
    return sorted((s, e) for s, e in intervals if s < e)


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
    for s, e in _clean(intervals):
        if e <= cursor:
            continue
        if s > cursor:
            gaps.append((cursor, s))
        if e > cursor:
            cursor = e
        if cursor >= we:
            break
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    events = []
    for s, e in intervals:
        if s < e:
            events.append((s, 1))
            events.append((e, -1))
    events.sort()
    best = 0
    depth = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
