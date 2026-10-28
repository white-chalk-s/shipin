import unittest
import numpy as np
from unittest.mock import patch

from region_recorder import MainWindow, RecorderWorker


class Flag:
    def __init__(self, checked):
        self.checked = checked

    def isChecked(self):
        return self.checked


class FakeRecorder:
    def __init__(self):
        self.paused = False
        self.events = []

    def isRunning(self):
        return True

    def is_paused(self):
        return self.paused

    def request_effect(self, *args):
        self.events.append(args)


class EffectTests(unittest.TestCase):
    def test_controls_disable_effects_during_pause_or_own_window_focus(self):
        obj = type("FakeMain", (), {})()
        obj.recorder = FakeRecorder()
        obj.control_window = type("Control", (), {"effects_checkbox": Flag(True)})()
        obj._recording_is_active = lambda: True
        with patch("region_recorder.QApplication.activeWindow", return_value=None), \
             patch("region_recorder.get_cursor_position", return_value=(10, 20)):
            MainWindow._on_effect_hotkey(obj, "c")
            obj.recorder.paused = True
            MainWindow._on_effect_hotkey(obj, "v")
            obj.recorder.paused = False
            obj.control_window.effects_checkbox.checked = False
            MainWindow._on_effect_hotkey(obj, "v")
        self.assertEqual(obj.recorder.events, [("c", 10, 20)])
        obj.control_window.effects_checkbox.checked = True
        with patch("region_recorder.QApplication.activeWindow", return_value=object()):
            MainWindow._on_effect_hotkey(obj, "v")
        self.assertEqual(len(obj.recorder.events), 1)

    def test_worker_drops_events_during_pause_and_over_control(self):
        recorder = RecorderWorker({"left": 0, "top": 0, "width": 100, "height": 100}, "unused.mp4")
        recorder._clock.start()
        recorder.set_control_rect((0, 0, 10, 10))
        recorder.request_effect("c", 5, 5)
        recorder.pause()
        recorder.request_effect("c", 20, 20)
        self.assertTrue(recorder._effect_events.empty())
        recorder.resume()
        recorder.request_effect("v", 20, 20)
        self.assertEqual(recorder._effect_events.get_nowait()[0], "v")

    def test_configured_zoom_and_highlight_change_rendered_output(self):
        recorder = RecorderWorker({"left": 0, "top": 0, "width": 100, "height": 100},
                                  "unused.mp4", zoom_factor=3.0, highlight_style="蓝色圆环")
        recorder._zoom_start_time = 10.0
        self.assertEqual(recorder._zoom_state_at(10.7)[0], 3.0)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        blue = recorder._draw_highlight(frame, 50, 50, 0.0)
        self.assertGreater(int(blue[50, 80, 0]), int(blue[50, 80, 2]))
        self.assertEqual(int(blue[50, 50].sum()), 0)
        recorder.highlight_style = "黄色光圈"
        filled = recorder._draw_highlight(frame, 50, 50, 0.0)
        self.assertGreater(int(filled[50, 50].sum()), 0)
        self.assertEqual(int(recorder._draw_highlight(frame, 50, 50, 1.5).sum()), 0)

    def test_zoom_progress_and_restart(self):
        recorder = RecorderWorker({"left": 0, "top": 0, "width": 100, "height": 100},
                                  "unused.mp4", zoom_hold_seconds=2)
        recorder._zoom_start_time = 10.0
        self.assertEqual(recorder._zoom_state_at(10.0)[0], 1.0)
        self.assertAlmostEqual(recorder._zoom_state_at(10.7)[0], 2.0)
        self.assertAlmostEqual(recorder._zoom_state_at(12.7)[0], 1.0)
        recorder._zoom_start_time = 20.0
        self.assertEqual(recorder._zoom_state_at(20.0)[0], 1.0)
        self.assertAlmostEqual(recorder._zoom_state_at(20.7)[0], 2.0)
