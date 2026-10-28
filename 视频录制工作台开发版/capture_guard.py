"""Verify exclusion of each actual top-level window with the MSS capture path."""
import ctypes
import ctypes.wintypes
import os
import time

import mss
import numpy as np
from PySide6.QtWidgets import QApplication, QLabel


def enable_exclusion(window):
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowDisplayAffinity.argtypes = (ctypes.wintypes.HWND, ctypes.wintypes.DWORD)
    user32.SetWindowDisplayAffinity.restype = ctypes.wintypes.BOOL
    return bool(user32.SetWindowDisplayAffinity(int(window.winId()), 0x11))


def verify_window_exclusion(window):
    if os.name != "nt":
        raise RuntimeError("窗口捕获排除仅支持 Windows。")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowDisplayAffinity.argtypes = (ctypes.wintypes.HWND, ctypes.wintypes.DWORD)
    user32.SetWindowDisplayAffinity.restype = ctypes.wintypes.BOOL
    user32.ClientToScreen.argtypes = (ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.POINT))
    hwnd = int(window.winId())
    marker = QLabel(window)
    marker.setGeometry(12, 12, 100, 36)
    marker.setStyleSheet("background: rgb(251, 7, 191); border: none;")
    marker.show()
    marker.raise_()
    window.raise_()
    app = QApplication.instance()

    def settle():
        app.processEvents()
        time.sleep(.15)
        app.processEvents()

    def marker_fraction():
        point = ctypes.wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
            raise RuntimeError("无法确定窗口屏幕坐标。")
        ratio = window.devicePixelRatioF()
        region = {"left": point.x + round(30 * ratio), "top": point.y + round(20 * ratio),
                  "width": max(4, round(24 * ratio)), "height": max(4, round(16 * ratio))}
        with mss.mss() as capture:
            pixels = np.asarray(capture.grab(region))
        return float(np.mean((pixels[:, :, 0] > 160) & (pixels[:, :, 1] < 40) & (pixels[:, :, 2] > 220)))

    try:
        if not user32.SetWindowDisplayAffinity(hwnd, 0):
            raise RuntimeError("无法重置窗口捕获保护。")
        settle()
        visible = marker_fraction()
        if visible < .8:
            raise RuntimeError("无法看清校验标记；窗口可能被遮挡或位于屏幕外。")
        if not user32.SetWindowDisplayAffinity(hwnd, 0x11):
            raise RuntimeError("Windows 未接受窗口捕获排除设置。")
        settle()
        hidden = marker_fraction()
        if hidden > .05:
            raise RuntimeError("捕获排除验证失败，窗口仍可能出现在视频中。")
    finally:
        marker.hide()
        marker.deleteLater()
        app.processEvents()
