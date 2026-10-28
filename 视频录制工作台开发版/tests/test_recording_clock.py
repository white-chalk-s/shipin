import unittest
from recording_clock import RecordingClock


class RecordingClockTests(unittest.TestCase):
    def test_pause_resume_and_stop_exclude_inactive_time(self):
        now = [0]
        clock = RecordingClock(lambda: now[0])
        clock.start()
        now[0] = 2_000_000_000
        clock.pause()
        now[0] = 7_000_000_000
        self.assertIsNone(clock.elapsed_ns(only_when_active=True))
        self.assertEqual(clock.elapsed_ns(), 2_000_000_000)
        clock.resume()
        now[0] = 10_000_000_000
        clock.stop()
        now[0] = 20_000_000_000
        self.assertEqual(clock.elapsed_ns(), 5_000_000_000)
        self.assertIsNone(clock.elapsed_ns(only_when_active=True))

    def test_stop_while_paused(self):
        now = [0]
        clock = RecordingClock(lambda: now[0])
        clock.start()
        now[0] = 1_000_000_000
        clock.pause()
        now[0] = 5_000_000_000
        clock.stop()
        self.assertEqual(clock.elapsed_ns(), 1_000_000_000)

    def test_pause_before_capture_starts(self):
        now = [0]
        clock = RecordingClock(lambda: now[0])
        clock.pause()
        clock.start()
        now[0] = 5_000_000_000
        clock.resume()
        now[0] = 7_000_000_000
        clock.stop()
        self.assertEqual(clock.elapsed_ns(), 2_000_000_000)
