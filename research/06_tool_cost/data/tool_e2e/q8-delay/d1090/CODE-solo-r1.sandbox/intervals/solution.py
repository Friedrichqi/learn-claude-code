def merge(intervals):
    if not intervals:
        return []
    # Sort intervals by start
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_intervals[0]]
    for current in sorted_intervals[1:]:
        last = merged[-1]
        if current[0] <= last[1]:
            # Overlapping or touching, merge them
            merged[-1] = (last[0], max(last[1], current[1]))
        else:
            merged.append(current)
    return merged


def free_gaps(intervals, window):
    ws, we = window
    # Sort intervals by start
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    gaps = []
    current_start = ws
    for interval in sorted_intervals:
        start, end = interval
        # Clip the interval to the window
        start = max(start, ws)
        end = min(end, we)
        if start < end:
            # Add gap before the interval
            if current_start < start:
                gaps.append((current_start, start))
            current_start = end
    # Add the final gap if any
    if current_start < we:
        gaps.append((current_start, we))
    return gaps


def max_overlap(intervals):
    if not intervals:
        return 0
    # Sort intervals by start
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    # Track the end of the current interval and the overlap count
    max_overlap = 0
    current_overlap = 0
    end = -float('inf')
    for start, current_end in sorted_intervals:
        if start < end:
            # Overlapping
            current_overlap += 1
            end = max(end, current_end)
        else:
            # No overlap, reset
            current_overlap = 1
            end = current_end
        max_overlap = max(max_overlap, current_overlap)
    return max_overlap