def merge(intervals):
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_intervals[0]]
    for current in sorted_intervals[1:]:
        last = merged[-1]
        if current[0] <= last[1]:
            merged[-1] = (last[0], max(last[1], current[1]))
        else:
            merged.append(current)
    return [interval for interval in merged if interval[0] < interval[1]]


def free_gaps(intervals, window):
    ws, we = window
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    gaps = []
    current_start = ws
    for start, end in sorted_intervals:
        if start > current_start:
            gaps.append((current_start, start))
        current_start = max(current_start, end)
    if current_start < we:
        gaps.append((current_start, we))
    return gaps


def max_overlap(intervals):
    if not intervals:
        return 0
    events = []
    for start, end in intervals:
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda x: (x[0], x[1]))
    max_overlap = 0
    current_overlap = 0
    for _, delta in events:
        current_overlap += delta
        max_overlap = max(max_overlap, current_overlap)
    return max_overlap