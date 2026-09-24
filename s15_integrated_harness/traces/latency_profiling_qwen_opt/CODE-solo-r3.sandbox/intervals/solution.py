def _clean(intervals):
    out = []
    for iv in intervals:
        s, e = int(iv[0]), int(iv[1])
        if s < e:
            out.append((s, e))
    return sorted(out)


def merge(intervals):
    ivs = _clean(intervals)
    result = []
    for s, e in ivs:
        if result and s <= result[-1][1]:
            if e > result[-1][1]:
                result[-1] = (result[-1][0], e)
        else:
            result.append((s, e))
    return result


def free_gaps(intervals, window):
    ws, we = int(window[0]), int(window[1])
    if ws >= we:
        return []
    ivs = [(max(s, ws), min(e, we)) for s, e in merge(intervals)]
    ivs = [(s, e) for s, e in ivs if s < e]
    gaps = []
    cur = ws
    for s, e in ivs:
        if s > cur:
            gaps.append((cur, s))
        cur = max(cur, e)
        if cur >= we:
            return gaps
    if cur < we:
        gaps.append((cur, we))
    return gaps


def max_overlap(intervals):
    pts = []
    for s, e in _clean(intervals):
        pts.append((s, 1))
        pts.append((e, -1))
    pts.sort()
    # At same point, ends (-1) must be processed before starts (1)
    # since intervals are half-open: (1,3) and (3,5) don't overlap.
    pts.sort(key=lambda p: (p[0], p[1]))
    best = 0
    cur = 0
    for _, d in pts:
        cur += d
        if cur > best:
            best = cur
    return best
