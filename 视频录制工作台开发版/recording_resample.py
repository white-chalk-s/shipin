"""Convert timestamped captured frames to a constant-rate video on disk."""
import cv2


class RecordingDiscarded(Exception):
    """The user discarded the recording during finalization."""


def write_constant_rate(source, destination, timestamps_ns, duration_ns, fps, width, height, cancelled=None):
    if not timestamps_ns or duration_ns <= 0:
        raise RuntimeError("没有录制到有效画面。")
    count = max(1, round(duration_ns * fps / 1_000_000_000))
    cap = cv2.VideoCapture(str(source))
    writer = cv2.VideoWriter(str(destination), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (width, height))
    if not cap.isOpened() or not writer.isOpened():
        cap.release()
        writer.release()
        raise RuntimeError("无法读取临时录屏或创建最终视频。")
    try:
        if int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) != len(timestamps_ns):
            raise RuntimeError("临时录屏帧数与采集记录不一致。")
        ok, current = cap.read()
        if not ok:
            raise RuntimeError("无法读取临时录屏第一帧。")
        index = 0
        for output_index in range(count):
            if cancelled is not None and cancelled.is_set():
                raise RecordingDiscarded()
            target_ns = output_index * 1_000_000_000 / fps
            while index + 1 < len(timestamps_ns) and timestamps_ns[index + 1] <= target_ns:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("临时录屏帧不完整，无法生成最终视频。")
                current = frame
                index += 1
            writer.write(current)
    finally:
        cap.release()
        writer.release()
    check = cv2.VideoCapture(str(destination))
    try:
        written = int(check.get(cv2.CAP_PROP_FRAME_COUNT)) if check.isOpened() else 0
        ok, _ = check.read() if check.isOpened() else (False, None)
        if written != count or not ok:
            raise RuntimeError("成片校验失败：文件不可播放或输出帧数不完整。")
    finally:
        check.release()
    return count
