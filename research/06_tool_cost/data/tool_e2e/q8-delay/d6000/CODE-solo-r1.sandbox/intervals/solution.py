def merge(intervals):
    if not intervals:
        return []
    # Sort intervals by start time
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_intervals[0]]
    for current in sorted_intervals[1:]:
        last = merged[-1]
        # Ignore intervals where start >= end
        if current[0] >= current[1]:
            continue
        if current[0] <= last[1]:
            # Overlapping or touching, merge them
            merged[-1] = (last[0], max(last[1], current[1]))
        else:
            merged.append(current)
    return merged


def free_gaps(intervals, window):
    ws, we = window
    if ws >= we:
        return []
    # Filter intervals that overlap with the window
    relevant_intervals = []
    for start, end in intervals:
        # Ignore intervals where start >= end
        if start >= end:
            continue
        if start < we and end > ws:
            relevant_intervals.append((start, end))
    # Sort intervals by start time
    relevant_intervals.sort()
    gaps = []
    prev_end = ws
    for start, end in relevant_intervals:
        if start > prev_end:
            gaps.append((prev_end, start))
        prev_end = max(prev_end, end)
    if prev_end < we:
        gaps.append((prev_end, we))
    return gaps


def max_overlap(intervals):
    if not intervals:
        return 0
    # Sort intervals by start time
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    # Create a list of events: start and end
    events = []
    for start, end in sorted_intervals:
        # Ignore intervals where start >= end
        if start >= end:
            continue
        events.append((start, 1))  # Start of interval
        events.append((end, -1))   # End of interval
    # Sort events by time, and by type (end before start if same time)
    events.sort(key=lambda x: (x[0], x[1]))
    max_overlap = 0
    current_overlap = 0
    for time, delta in events:
        current_overlap += delta
        max_overlap = max(max_overlap, current_overlap)
    return max_overlap