"""Frame-based mapping between the edited timeline and the original video.

Segments are half-open source frame ranges, in playback order.
"""


def total_frames(segments):
    return sum(max(0, segment["end"] - segment["start"]) for segment in segments)


def output_to_source(segments, output_frame):
    """Return the source frame at an output position, or None for an empty edit."""
    count = total_frames(segments)
    if not count:
        return None
    offset = max(0, min(count - 1, int(output_frame)))
    for segment in segments:
        length = max(0, segment["end"] - segment["start"])
        if offset < length:
            return segment["start"] + offset
        offset -= length
    return None


def source_to_output(segments, source_frame):
    """Map a retained source frame to the edited timeline; None if removed."""
    offset = 0
    for segment in segments:
        start, end = segment["start"], segment["end"]
        if start <= source_frame < end:
            return offset + source_frame - start
        offset += max(0, end - start)
    return None


def trim_to_output_range(segments, start, end):
    """Keep [start, end) on the edited timeline without modifying the source ranges."""
    total = total_frames(segments)
    start, end = max(0, int(start)), min(total, int(end))
    if end <= start:
        return []
    result = []
    offset = 0
    for segment in segments:
        length = segment["end"] - segment["start"]
        overlap_start, overlap_end = max(start, offset), min(end, offset + length)
        if overlap_start < overlap_end:
            result.append({"start": segment["start"] + overlap_start - offset,
                           "end": segment["start"] + overlap_end - offset})
        offset += length
    return result
