import tempfile
import threading
import unittest
from pathlib import Path

import cv2
import numpy as np
from recording_resample import RecordingDiscarded, write_constant_rate


class ResampleTests(unittest.TestCase):
    def test_fills_missing_capture_slots_and_validates_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "samples.mp4", Path(tmp) / "output.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            for value in (20, 100, 200):
                writer.write(np.full((48, 64, 3), value, np.uint8))
            writer.release()
            count = write_constant_rate(source, output, [0, 200_000_000, 500_000_000],
                                        700_000_000, 10, 64, 48)
            cap = cv2.VideoCapture(str(output))
            values = []
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                values.append(frame.mean())
            cap.release()
            self.assertEqual(count, 7)
            self.assertEqual(len(values), 7)
            self.assertLess(abs(values[0] - values[1]), 5)
            self.assertGreater(values[2] - values[1], 50)
            self.assertGreater(values[5] - values[4], 70)

    def test_discard_interrupts_finalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "samples.mp4", Path(tmp) / "output.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            writer.write(np.zeros((48, 64, 3), np.uint8))
            writer.release()
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(RecordingDiscarded):
                write_constant_rate(source, output, [0], 1_000_000_000, 10, 64, 48, cancelled)
