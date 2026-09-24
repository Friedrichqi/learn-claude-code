"""Half-open integer interval utilities: merge, free_gaps, max_overlap.

An interval is a pair (start, end) meaning the half-open range [start, end).
A pair with start >= end is empty and is ignored by every function.
"""


def _valid_pairs(intervals):
    """Yield (start, end) tuples for every non-empty interval, in input order."""
    for pair in intervals:
        start, end = pair[0], pair[1]
        if start < end:  # start >= end means the interval is empty: ignore it.
            yield start, end


def merge(intervals):
    """Merge overlapping and touching intervals; nested ones collapse outward.

    Returns a list of (start, end) tuples sorted by start; empty input -> [].
    """
    merged = []
    for start, end in sorted(_valid_pairs(intervals)):
        if merged and start <= merged[-1][1]:
            # Overlapping (start < last end), touching (start == last end),
            # or nested (end <= last end): extend the outer interval if needed.
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def free_gaps(intervals, window):
    """Return maximal sub-ranges of [ws, we) covered by no interval, in order.

    Intervals are clipped to the window; ones fully outside it vanish.
    """
    ws, we = window[0], window[1]
    if ws >= we:
        return []
    covered = sorted(
        (max(start, ws), min(end, we))
        for start, end in _valid_pairs(intervals)
        if max(start, ws) < min(end, we)
    )
    gaps = []
    cursor = ws
    for start, end in covered:
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < we:
        gaps.append((cursor, we))
    return gaps


def max_overlap(intervals):
    """Largest number of non-empty intervals covering one common point.

    Ranges are half-open, so (1, 3) and (3, 5) do not overlap. Empty input -> 0.
    Runs in O(n log n) via a sorted starts/ends two-pointer sweep; at equal
    coordinates the end event is processed before the start event.
    """
    pairs = list(_valid_pairs(intervals))
    if not pairs:
        return 0
    starts = sorted(start for start, _ in pairs)
    ends = sorted(end for _, end in pairs)
    depth = 0
    best = 0
    i = j = 0
    n = len(pairs)
    while i < n:
        if starts[i] < ends[j]:
            depth += 1
            if depth > best:
                best = depth
            i += 1
        else:
            # starts[i] >= ends[j]: the interval ending at ends[j] has already
            # stopped covering this point (half-open), so close it first.
            depth -= 1
            j += 1
    return best
