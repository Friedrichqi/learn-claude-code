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
    return [interval for interval in merged if interval[0] < interval[1]]


def free_gaps(intervals, window):
    ws, we = window
    # Sort intervals by start
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    gaps = []
    current_start = ws
    for interval in sorted_intervals:
        start, end = interval
        # Clip interval to the window
        start = max(start, ws)
        end = min(end, we)
        if start < end:
            # Add gap before the interval
            if current_start < start:
                gaps.append((current_start, start))
            current_start = end
    # Add gap after the last interval
    if current_start < we:
        gaps.append((current_start, we))
    return gaps


def max_overlap(intervals):
    if not intervals:
        return 0
    # Sort intervals by start
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    # Use a priority queue to track end times
    import heapq
    end_times = []
    max_overlap = 0
    for start, end in sorted_intervals:
        # Remove expired intervals
        while end_times and end_times[0] <= start:
            heapq.heappop(end_times)
        # Add the end time of the current interval
        heapq.heappush(end_times, end)
        # Update max overlap
        max_overlap = max(max_overlap, len(end_times))
    return max_overlap