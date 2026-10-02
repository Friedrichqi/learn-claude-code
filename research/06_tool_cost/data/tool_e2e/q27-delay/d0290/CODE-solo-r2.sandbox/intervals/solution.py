"""Half-open integer interval operations."""


def _clean(intervals):
    return [(s, e) for s, e in intervals if s < e]


def merge(intervals):
    cleaned = _clean(intervals)
    if not cleaned:
        return []
    cleaned.sort()
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            if end > last_end:
                merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals, window):
    ws, we = window
    if ws >= we:
        return []
    clipped = [(s, e) for s, e in _clean(intervals) if s < we and e > ws]
    if not clipped:
        return [(ws, we)]
    clipped.sort()
    merged = [clipped[0]]
    for start, end in clipped[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            if end > last_end:
                merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    gaps = []
    cursor = ws
    for start, end in merged:
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    cleaned = _clean(intervals)
    if not cleaned:
        return 0
    # Sweep line over events. For half-open [s, e), an interval ending at
    # point p does not cover p, so at equal coordinates ends (-1) must be
    # processed before starts (+1); sorting on (point, delta) achieves that.
    events = []
    for s, e in cleaned:
        events.append((s, 1))
        events.append((e, -1))
    events.sort()
    best = 0
    current = 0
    for _, delta in events:
        current += delta
        if current > best:
            best = current
    return best
