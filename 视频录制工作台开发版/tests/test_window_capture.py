import unittest
from unittest.mock import patch
from window_capture import WindowTarget, window_region


class FakeUser32:
    def __init__(self):
        self.valid = True
        self.iconic = False
        self.pid = 42
        self.left = 10
        self.top = 20

    def IsWindow(self, _hwnd): return self.valid
    def IsWindowVisible(self, _hwnd): return True
    def IsIconic(self, _hwnd): return self.iconic

    def GetWindowThreadProcessId(self, _hwnd, value):
        value._obj.value = self.pid
        return 1

    def GetClientRect(self, _hwnd, value):
        value._obj.left, value._obj.top = 0, 0
        value._obj.right, value._obj.bottom = 200, 100
        return True

    def ClientToScreen(self, _hwnd, value):
        value._obj.x += self.left
        value._obj.y += self.top
        return True


class WindowCaptureTests(unittest.TestCase):
    def test_tracks_movement_and_rejects_cross_screen(self):
        user = FakeUser32()
        monitor = {"left": 0, "top": 0, "width": 300, "height": 200}
        target = WindowTarget(123, 42, "目标")
        with patch("window_capture._api", return_value=user):
            self.assertEqual(window_region(target, monitor)["left"], 10)
            user.left = 70
            self.assertEqual(window_region(target, monitor)["left"], 70)
            user.left = 101
            with self.assertRaisesRegex(RuntimeError, "超出所选显示器"):
                window_region(target, monitor)

    def test_rejects_minimized_and_reused_handle(self):
        user = FakeUser32()
        target = WindowTarget(123, 42, "目标")
        with patch("window_capture._api", return_value=user):
            user.iconic = True
            with self.assertRaisesRegex(RuntimeError, "最小化"):
                window_region(target)
            user.iconic = False
            user.pid = 99
            with self.assertRaisesRegex(RuntimeError, "已失效"):
                window_region(target)
