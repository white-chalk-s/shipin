import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from realtime_encoder import RealtimeEncoder


class FakeClock:
    def __init__(self):
        self.value = 0
        self.stopped = False

    def elapsed_ns(self):
        return self.value

    def stop(self):
        self.stopped = True


class FakeWriter:
    def __init__(self, *_args):
        self.frames = []
        self.released = False

    def isOpened(self):
        return True

    def write(self, frame):
        self.frames.append(int(frame[0, 0, 0]))

    def release(self):
        self.released = True


def wait_for(predicate):
    limit = time.monotonic() + 2
    while not predicate():
        if time.monotonic() > limit:
            raise AssertionError("编码线程没有及时处理帧")
        time.sleep(.005)


class RealtimeEncoderTests(unittest.TestCase):
    def test_repeats_latest_frame_and_stops_at_active_duration(self):
        clock, writer = FakeClock(), FakeWriter()
        with patch("realtime_encoder.cv2.VideoWriter", return_value=writer):
            encoder = RealtimeEncoder("test.mp4", 2, 2, 10, clock, threading.Event(), threading.Event())
            encoder.start()
            encoder.submit(np.full((2, 2, 3), 20, np.uint8))
            clock.value = 200_000_000
            wait_for(lambda: len(writer.frames) == 2)
            encoder.submit(np.full((2, 2, 3), 80, np.uint8))
            clock.value = 300_000_000
            wait_for(lambda: len(writer.frames) == 3)
            self.assertEqual(encoder.finish(), 3)
            self.assertEqual(encoder.duplicate_frames, 1)
        self.assertEqual(writer.frames, [20, 20, 80])
        self.assertTrue(writer.released)

    def test_encoder_error_stops_recording(self):
        clock, writer, stop = FakeClock(), FakeWriter(), threading.Event()
        writer.isOpened = lambda: False
        with patch("realtime_encoder.cv2.VideoWriter", return_value=writer):
            encoder = RealtimeEncoder("test.mp4", 2, 2, 10, clock, stop, threading.Event())
            encoder.start()
            wait_for(stop.is_set)
            with self.assertRaisesRegex(RuntimeError, "无法创建录屏文件"):
                encoder.finish()
        self.assertTrue(clock.stopped)
        self.assertTrue(writer.released)
