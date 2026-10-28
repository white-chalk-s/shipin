"""Bounded live CFR encoder: keep only the latest capture, never an unbounded queue."""
import threading

import cv2


class RealtimeEncoder:
    def __init__(self, path, width, height, fps, clock, stop_event, discard_event):
        self.path, self.width, self.height, self.fps = str(path), width, height, fps
        self.clock, self.stop_event, self.discard_event = clock, stop_event, discard_event
        self._condition = threading.Condition()
        self._frame = None
        self._version = 0
        self._finished = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.error = None
        self.frames_written = 0
        self.duplicate_frames = 0

    def start(self):
        self._thread.start()

    def submit(self, frame):
        with self._condition:
            self._frame = frame
            self._version += 1
            self._condition.notify()

    def finish(self):
        with self._condition:
            self._finished = True
            self._condition.notify()
        self._thread.join()
        if self.error:
            raise RuntimeError(self.error)
        return self.frames_written

    def _run(self):
        writer = None
        try:
            writer = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*"mp4v"),
                                     float(self.fps), (self.width, self.height))
            if not writer.isOpened():
                raise RuntimeError("无法创建录屏文件，请检查保存目录和编码器。")
            last_version = None
            while True:
                with self._condition:
                    frame = self._frame
                    version = self._version
                    finished = self._finished
                if self.discard_event.is_set():
                    return
                elapsed = self.clock.elapsed_ns()
                due = round(elapsed * self.fps / 1_000_000_000) if finished else int(elapsed * self.fps / 1_000_000_000)
                if finished and frame is not None:
                    due = max(1, due)
                if frame is not None and self.frames_written < due:
                    writer.write(frame)
                    self.frames_written += 1
                    if version == last_version:
                        self.duplicate_frames += 1
                    last_version = version
                    continue
                if finished:
                    return
                with self._condition:
                    self._condition.wait(timeout=0.01)
        except Exception as exc:
            self.error = str(exc)
            self.clock.stop()
            self.stop_event.set()
        finally:
            if writer is not None:
                writer.release()
