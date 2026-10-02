def merge(intervals):
    if not intervals:
        return []
    # Sort intervals by start
    intervals.sort()
    merged = [intervals[0]]
    for current in intervals[1:]:
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
    intervals.sort()
    gaps = []
    # Add the initial gap from ws to the first interval's start
    if intervals:
        first_start = intervals[0][0]
        if first_start > ws:
            gaps.append((ws, first_start))
    else:
        gaps.append((ws, we))
    # Process intervals to find gaps
    for i in range(len(intervals) - 1):
        current_end = intervals[i][1]
        next_start = intervals[i + 1][0]
        if current_end < next_start:
            gaps.append((current_end, next_start))
    # Add the gap after the last interval
    if intervals:
        last_end = intervals[-1][1]
        if last_end < we:
            gaps.append((last_end, we))
    return gaps


def max_overlap(intervals):
    if not intervals:
        return 0
    # Sort intervals by start
    intervals.sort()
    # Initialize max overlap and current overlap
    max_overlap = 1
    current_overlap = 1
    # Iterate through intervals
    for i in range(1, len(intervals)):
        # If the current interval starts before or at the end of the previous one, it overlaps
        if intervals[i][0] <= intervals[i - 1][1]:
            current_overlap += 1
            max_overlap = max(max_overlap, current_overlap)
        else:
            current_overlap = 1
    return max_overlap