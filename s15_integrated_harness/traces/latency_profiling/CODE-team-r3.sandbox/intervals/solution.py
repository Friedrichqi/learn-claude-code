"""Half-open integer interval utilities: merge, free_gaps, max_overlap."""

EMPTY: list[tuple[int, int]] = []


def _normalized(intervals):
    """Yield non-empty (start, end) tuples sorted by start, ties by end."""
    valid = [(int(s), int(e)) for s, e in intervals if e > s]
    valid.sort()
    return valid


def merge(intervals):
    merged: list[tuple[int, int]] = []
    for start, end in _normalized(intervals):
        if merged and start <= merged[-1][1]:
            prev_start, prev_end = merged[-1]
            if end > prev_end:
                merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals, window):
    ws, we = int(window[0]), int(window[1])
    if we <= ws:
        return []
    gaps = []
    cursor = ws
    for start, end in _normalized(intervals):
        if end <= cursor:
            continue
        if start > cursor:
            gaps.append((cursor, min(start, we)))
        cursor = max(cursor, end)
        if cursor >= we:
            break
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    events = []
    for start, end in _normalized(intervals):
        events.append((start, 1))
        events.append((end, 0))
    events.sort()
    depth = 0
    best = 0
    for _, kind in events:
        if kind == 1:  # start (ends, kind 0, sort first at equal points)
            depth += 1
            if depth > best:
                best = depth
        else:
            depth -= 1
    return best
