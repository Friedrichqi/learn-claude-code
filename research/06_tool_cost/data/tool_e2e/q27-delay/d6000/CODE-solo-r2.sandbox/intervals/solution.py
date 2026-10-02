"""Half-open integer interval utilities."""


def _clean(intervals):
    return [(int(s), int(e)) for s, e in intervals if s < e]


def merge(intervals) -> list[tuple[int, int]]:
    pairs = sorted(_clean(intervals))
    out: list[tuple[int, int]] = []
    for start, end in pairs:
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def free_gaps(intervals, window) -> list[tuple[int, int]]:
    ws, we = int(window[0]), int(window[1])
    if ws >= we:
        return []
    clipped = []
    for s, e in _clean(intervals):
        cs, ce = max(s, ws), min(e, we)
        if cs < ce:
            clipped.append((cs, ce))
    merged = merge(clipped)
    gaps: list[tuple[int, int]] = []
    cursor = ws
    for s, e in merged:
        if cursor < s:
            gaps.append((cursor, s))
        cursor = e
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals) -> int:
    events = []
    for s, e in _clean(intervals):
        events.append((s, 1))
        events.append((e, -1))
    # at equal points, ends (delta -1) are processed before starts (delta +1)
    events.sort(key=lambda ev: (ev[0], ev[1]))
    depth = best = 0
    for _, delta in events:
        depth += delta
        if depth > best:
            best = depth
    return best
