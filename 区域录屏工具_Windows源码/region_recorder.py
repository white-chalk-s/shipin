# -*- coding: utf-8 -*-
"""Windows 区域录屏工具：框选区域后自动录制画面，按 F9 停止并保存 MP4。"""

import ctypes
import os
import sys
import json
import time
import threading
import ctypes.wintypes as wintypes
from datetime import datetime
from pathlib import Path

# 在创建 Qt 应用前启用 Windows 高 DPI 感知，尽量确保框选坐标与实际屏幕像素一致。
def set_windows_dpi_awareness():
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


set_windows_dpi_awareness()

import cv2
import mss
import numpy as np
from pynput import keyboard, mouse
from PySide6.QtCore import Qt, QRect, QSize, QTimer, Signal, QObject, QThread
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QFileDialog, QMessageBox, QSystemTrayIcon, QMenu, QStyle,
    QFrame, QInputDialog, QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox, QRadioButton, QButtonGroup,
    QSlider, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QAbstractItemView
)
from PySide6.QtCore import QSettings


APP_NAME = "区域录屏工具"
FPS = 30


def apply_normalized_crop(region, crop_rect=None):
    """
    Apply a remembered crop rectangle (x, y, width, height), each normalized 0..1.
    The crop is relative to the detected window client area or the current monitor.
    """
    result = dict(region)
    if not crop_rect:
        crop_rect = (0.0, 0.0, 1.0, 1.0)

    try:
        x, y, w, h = [float(v) for v in crop_rect]
    except Exception:
        x, y, w, h = (0.0, 0.0, 1.0, 1.0)

    full_left = int(result["left"])
    full_top = int(result["top"])
    full_width = max(20, int(result["width"]))
    full_height = max(20, int(result["height"]))

    min_w_ratio = min(1.0, 20.0 / full_width)
    min_h_ratio = min(1.0, 20.0 / full_height)
    x = min(max(x, 0.0), 1.0 - min_w_ratio)
    y = min(max(y, 0.0), 1.0 - min_h_ratio)
    w = min(max(w, min_w_ratio), 1.0 - x)
    h = min(max(h, min_h_ratio), 1.0 - y)

    left = full_left + int(round(full_width * x))
    top = full_top + int(round(full_height * y))
    right = min(full_left + full_width, left + int(round(full_width * w)))
    bottom = min(full_top + full_height, top + int(round(full_height * h)))
    width = max(20, right - left)
    height = max(20, bottom - top)

    result.update({
        "left": left,
        "top": top,
        "width": min(width, full_left + full_width - left),
        "height": min(height, full_top + full_height - top),
    })
    return result


def get_foreground_client_region(crop_rect=None):
    """Get the foreground app's client area and apply the remembered visual crop."""
    if os.name != "nt":
        raise RuntimeError("自动识别当前窗口目前仅支持 Windows。")

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("没有检测到前台窗口。")

    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if process_id.value == os.getpid():
        raise RuntimeError(
            "检测到的仍是录屏工具自身。请先打开要录制的软件，再尝试操作。"
        )

    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        raise RuntimeError("当前窗口不可见或已最小化，请先恢复目标窗口。")

    class_name_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_name_buffer, len(class_name_buffer))
    if class_name_buffer.value in (
        "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"
    ):
        raise RuntimeError("当前检测到的是桌面或任务栏，请先打开要录制的软件。")

    client_rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
        raise RuntimeError("读取窗口区域失败。")

    top_left = wintypes.POINT(client_rect.left, client_rect.top)
    bottom_right = wintypes.POINT(client_rect.right, client_rect.bottom)
    if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
        raise RuntimeError("读取窗口屏幕坐标失败。")
    if not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
        raise RuntimeError("读取窗口屏幕坐标失败。")

    region = {
        "left": int(top_left.x),
        "top": int(top_left.y),
        "width": int(bottom_right.x - top_left.x),
        "height": int(bottom_right.y - top_left.y),
    }
    if region["width"] < 20 or region["height"] < 20:
        raise RuntimeError("目标窗口的可录制区域太小。")
    return apply_normalized_crop(region, crop_rect)


def get_window_client_region_at_point(x, y, monitor, crop_rect=None):
    """
    Find the top-level app window under the mouse and return its client area
    intersected with the monitor under the mouse. Desktop/taskbar/tool windows
    return None so the caller can fall back to that monitor.
    """
    if os.name != "nt":
        return None

    user32 = ctypes.windll.user32
    point = wintypes.POINT(int(x), int(y))
    try:
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
    except Exception:
        pass

    hwnd = user32.WindowFromPoint(point)
    if not hwnd:
        return None
    root_hwnd = user32.GetAncestor(hwnd, 2)  # GA_ROOT
    if not root_hwnd:
        root_hwnd = hwnd

    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(root_hwnd, ctypes.byref(process_id))
    if process_id.value == os.getpid():
        return None
    if not user32.IsWindowVisible(root_hwnd) or user32.IsIconic(root_hwnd):
        return None

    class_name_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(root_hwnd, class_name_buffer, len(class_name_buffer))
    if class_name_buffer.value in (
        "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"
    ):
        return None

    client_rect = wintypes.RECT()
    if not user32.GetClientRect(root_hwnd, ctypes.byref(client_rect)):
        return None

    top_left = wintypes.POINT(client_rect.left, client_rect.top)
    bottom_right = wintypes.POINT(client_rect.right, client_rect.bottom)
    if not user32.ClientToScreen(root_hwnd, ctypes.byref(top_left)):
        return None
    if not user32.ClientToScreen(root_hwnd, ctypes.byref(bottom_right)):
        return None

    region = {
        "left": int(top_left.x),
        "top": int(top_left.y),
        "width": int(bottom_right.x - top_left.x),
        "height": int(bottom_right.y - top_left.y),
    }
    if region["width"] < 20 or region["height"] < 20:
        return None

    region = apply_normalized_crop(region, crop_rect)
    mon_left = int(monitor["left"])
    mon_top = int(monitor["top"])
    mon_right = mon_left + int(monitor["width"])
    mon_bottom = mon_top + int(monitor["height"])

    left = max(int(region["left"]), mon_left)
    top = max(int(region["top"]), mon_top)
    right = min(int(region["left"]) + int(region["width"]), mon_right)
    bottom = min(int(region["top"]) + int(region["height"]), mon_bottom)
    if right - left < 20 or bottom - top < 20:
        return None

    return {
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top,
    }


def get_monitor_capture_region(monitor, crop_rect=None):
    """Return a monitor capture area with the saved visual crop applied."""
    region = {
        "left": int(monitor["left"]),
        "top": int(monitor["top"]),
        "width": int(monitor["width"]),
        "height": int(monitor["height"]),
    }
    return apply_normalized_crop(region, crop_rect)


def get_cursor_position():
    """返回 Windows 鼠标物理屏幕坐标，优先使用 GetPhysicalCursorPos。"""
    if os.name != "nt":
        return (0, 0)
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    try:
        if user32.GetPhysicalCursorPos(ctypes.byref(point)):
            return (int(point.x), int(point.y))
    except Exception:
        pass
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise RuntimeError("无法读取鼠标位置。")
    return (int(point.x), int(point.y))


def find_monitor_for_point(monitors, x, y):
    """返回包含屏幕坐标的显示器列表索引；找不到时返回 None。"""
    for index, monitor in enumerate(monitors):
        left = int(monitor["left"])
        top = int(monitor["top"])
        right = left + int(monitor["width"])
        bottom = top + int(monitor["height"])
        if left <= x < right and top <= y < bottom:
            return index
    return None


class HotkeySignals(QObject):
    stop_requested = Signal()


class RegionSelector(QWidget):
    """覆盖虚拟桌面的透明框选层。"""
    region_selected = Signal(object)
    cancelled = Signal(str)

    def __init__(self):
        super().__init__()
        screens = QApplication.screens()
        if screens:
            virtual_rect = screens[0].geometry()
            for screen in screens[1:]:
                virtual_rect = virtual_rect.united(screen.geometry())
        else:
            virtual_rect = QApplication.primaryScreen().geometry()

        self.setGeometry(virtual_rect)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.start_point = None
        self.end_point = None
        self._finished = False

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 使用奇偶填充规则，使选区内部透明、外部半透明变暗。
        shade_path = QPainterPath()
        shade_path.setFillRule(Qt.FillRule.OddEvenFill)
        shade_path.addRect(self.rect())

        selection = None
        if self.start_point is not None and self.end_point is not None:
            selection = QRect(self.start_point, self.end_point).normalized()
            shade_path.addRect(selection)

        painter.fillPath(shade_path, QColor(0, 0, 0, 125))

        painter.setPen(QPen(QColor(255, 255, 255, 230), 1))
        painter.drawText(
            QRect(0, 12, self.width(), 36),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "拖动鼠标框选录制区域 · 松开后显示实时预览 · Esc 取消"
        )

        if selection is not None:
            painter.setPen(QPen(QColor(0, 190, 120), 2))
            painter.drawRect(selection)
            label = f"{selection.width()} × {selection.height()}"
            label_rect = QRect(
                selection.left(),
                max(52, selection.top() - 28),
                150,
                24
            )
            painter.fillRect(label_rect, QColor(0, 0, 0, 185))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                label_rect.adjusted(7, 0, -5, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_point = event.position().toPoint()
            self.end_point = self.start_point
            self.update()

    def mouseMoveEvent(self, event):
        if self.start_point is not None:
            self.end_point = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.start_point is None:
            return

        self.end_point = event.position().toPoint()
        local_rect = QRect(self.start_point, self.end_point).normalized()
        if local_rect.width() < 20 or local_rect.height() < 20:
            self._finished = True
            self.hide()
            self.cancelled.emit("所选区域太小，请重新框选。")
            return

        top_left = self.mapToGlobal(local_rect.topLeft())
        region = {
            "left": int(top_left.x()),
            "top": int(top_left.y()),
            "width": int(local_rect.width()),
            "height": int(local_rect.height()),
        }
        self._finished = True
        self.hide()
        self.region_selected.emit(region)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if not self._finished:
                self._finished = True
                self.hide()
                self.cancelled.emit("已取消框选。")
            return
        super().keyPressEvent(event)


class CropSelector(QWidget):
    """Overlay on the target app's client area; drag the rectangle to keep."""
    crop_selected = Signal(object)
    cancelled = Signal(str)

    def __init__(self, region):
        super().__init__()
        self.region = dict(region)
        self.setGeometry(
            int(region["left"]), int(region["top"]),
            int(region["width"]), int(region["height"])
        )
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.start_point = None
        self.end_point = None
        self._finished = False

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shade = QPainterPath()
        shade.setFillRule(Qt.FillRule.OddEvenFill)
        shade.addRect(self.rect())
        selected = None
        if self.start_point is not None and self.end_point is not None:
            selected = QRect(self.start_point, self.end_point).normalized()
            shade.addRect(selected)
        painter.fillPath(shade, QColor(0, 0, 0, 135))

        painter.setPen(QPen(QColor(255, 255, 255, 235), 1))
        painter.drawText(
            QRect(8, 8, max(0, self.width() - 16), 44),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "拖动框出最终要保留的内容；只裁上下时，左右尽量贴边。松开保存，Esc 取消。"
        )
        if selected is not None:
            painter.setPen(QPen(QColor(0, 210, 125), 2))
            painter.drawRect(selected)
            text = f"保留区域：{selected.width()} × {selected.height()}"
            label_rect = QRect(
                selected.left(),
                max(54, selected.top() - 27),
                min(260, max(160, selected.width())),
                23
            )
            painter.fillRect(label_rect, QColor(0, 0, 0, 190))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                label_rect.adjusted(7, 0, -4, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_point = event.position().toPoint()
            self.end_point = self.start_point
            self.update()

    def mouseMoveEvent(self, event):
        if self.start_point is not None:
            self.end_point = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.start_point is None:
            return
        self.end_point = event.position().toPoint()
        selected = QRect(self.start_point, self.end_point).normalized()
        if selected.width() < 20 or selected.height() < 20:
            self._finished = True
            self.hide()
            self.cancelled.emit("框选区域太小，已取消。")
            return

        full_w = max(1, self.width())
        full_h = max(1, self.height())
        x = selected.left() / full_w
        y = selected.top() / full_h
        w = selected.width() / full_w
        h = selected.height() / full_h
        # Keep values within the region and avoid invalid tiny captures.
        x = min(max(x, 0.0), 0.98)
        y = min(max(y, 0.0), 0.98)
        w = min(max(w, 0.02), 1.0 - x)
        h = min(max(h, 0.02), 1.0 - y)
        self._finished = True
        self.hide()
        self.crop_selected.emit((x, y, w, h))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if not self._finished:
                self._finished = True
                self.hide()
                self.cancelled.emit("已取消框选。")
            return
        super().keyPressEvent(event)



class RecorderWorker(QThread):
    file_saved = Signal(str)
    recording_error = Signal(str)
    preview_frame = Signal(QImage)
    monitor_changed = Signal(int, int, int, int, int, int, int, str)

    def __init__(
        self, region, output_path, fps=FPS, follow_mouse=False,
        monitors=None, initial_monitor_index=0, auto_window=False,
        crop_rect=None, fixed_monitor_index=None,
        cursor_style="箭头", zoom_hold_seconds=3.0
    ):
        super().__init__()
        self.region = dict(region)
        self.output_path = str(output_path)
        self.fps = fps
        self.follow_mouse = bool(follow_mouse)
        self.auto_window = bool(auto_window)
        self.crop_rect = tuple(crop_rect or (0.0, 0.0, 1.0, 1.0))
        self.monitors = [dict(m) for m in (monitors or [])]
        self.current_monitor_index = int(initial_monitor_index)
        self.fixed_monitor_index = fixed_monitor_index
        self.cursor_style = str(cursor_style or "箭头")
        self.zoom_hold_seconds = max(0.5, min(30.0, float(zoom_hold_seconds)))
        self._stop_event = threading.Event()
        self._paused_event = threading.Event()
        self._control_rect_lock = threading.Lock()
        self._control_rect = None
        self._click_lock = threading.Lock()
        self._last_click = None
        self._processed_click_time = 0.0
        self._mouse_listener = None
        # “停留时长”不含推进/拉远动画；两段动画各自单独计时。
        self.zoom_factor = 2.0
        self.zoom_transition_seconds = 0.35
        self._zoom_start_time = 0.0
        self._zoom_until = 0.0
        self._zoom_global_point = None
        self._target_kind = "启动中"

    def stop(self):
        self._stop_event.set()

    def pause(self):
        self._paused_event.set()

    def resume(self):
        self._paused_event.clear()

    def is_paused(self):
        return self._paused_event.is_set()

    def set_control_rect(self, rect):
        with self._control_rect_lock:
            self._control_rect = tuple(rect) if rect is not None else None

    def _cursor_over_control_panel(self, x, y):
        with self._control_rect_lock:
            rect = self._control_rect
        if rect is None:
            return False
        left, top, right, bottom = rect
        return left <= x < right and top <= y < bottom

    def _on_mouse_click(self, x, y, button, pressed):
        if not pressed or button != mouse.Button.left:
            return
        with self._click_lock:
            self._last_click = (time.perf_counter(), int(x), int(y))

    def _get_last_click(self):
        with self._click_lock:
            return self._last_click

    @staticmethod
    def _fit_to_output(frame, output_width, output_height):
        source_height, source_width = frame.shape[:2]
        if source_width == output_width and source_height == output_height:
            return frame

        scale = min(
            output_width / float(source_width),
            output_height / float(source_height)
        )
        new_width = max(1, min(output_width, int(round(source_width * scale))))
        new_height = max(1, min(output_height, int(round(source_height * scale))))
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(frame, (new_width, new_height), interpolation=interpolation)
        canvas = np.zeros((output_height, output_width, 3), dtype=np.uint8)
        x_offset = (output_width - new_width) // 2
        y_offset = (output_height - new_height) // 2
        canvas[y_offset:y_offset + new_height, x_offset:x_offset + new_width] = resized
        return canvas

    @staticmethod
    def _smoothstep(value):
        value = min(1.0, max(0.0, float(value)))
        return value * value * (3.0 - 2.0 * value)

    def _zoom_state_at(self, now):
        """平滑推进 + 用户设定的停留时长 + 平滑拉远。"""
        if not self._zoom_start_time:
            return 1.0, 0.0

        elapsed = max(0.0, now - self._zoom_start_time)
        transition = self.zoom_transition_seconds
        hold = self.zoom_hold_seconds
        total = transition + hold + transition
        if elapsed >= total:
            return 1.0, 0.0

        if elapsed < transition:
            progress = self._smoothstep(elapsed / transition)
        elif elapsed < transition + hold:
            progress = 1.0
        else:
            progress = self._smoothstep((total - elapsed) / transition)

        factor = 1.0 + (self.zoom_factor - 1.0) * progress
        return factor, progress

    def _apply_click_zoom(self, frame, global_x, global_y, region, factor, ring_alpha):
        """围绕点击点做连续缩放，并淡入/淡出定位圆环。"""
        h, w = frame.shape[:2]
        local_x = int(global_x) - int(region["left"])
        local_y = int(global_y) - int(region["top"])
        if not (0 <= local_x < w and 0 <= local_y < h):
            return frame

        factor = max(1.0, min(float(self.zoom_factor), float(factor)))
        crop_w = max(2, min(w, int(round(w / factor))))
        crop_h = max(2, min(h, int(round(h / factor))))
        crop_x = min(max(0, local_x - crop_w // 2), max(0, w - crop_w))
        crop_y = min(max(0, local_y - crop_h // 2), max(0, h - crop_h))
        crop = frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
        if crop.size == 0:
            return frame

        if crop_w == w and crop_h == h:
            zoomed = frame.copy()
        else:
            zoomed = cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)

        if ring_alpha > 0.01:
            mark_x = int((local_x - crop_x) * w / crop_w)
            mark_y = int((local_y - crop_y) * h / crop_h)
            radius = max(12, int(min(w, h) * (0.018 + 0.012 * ring_alpha)))
            overlay = zoomed.copy()
            cv2.circle(
                overlay, (mark_x, mark_y), radius,
                (0, 0, 255), max(2, int(3 * ring_alpha)), cv2.LINE_AA
            )
            # Subtle translucent ring, with opacity following the zoom animation.
            alpha = min(0.82, 0.82 * ring_alpha)
            zoomed = cv2.addWeighted(overlay, alpha, zoomed, 1.0 - alpha, 0)
        return zoomed

    def _draw_cursor(self, frame, x, y, style):
        """Draw a configurable cursor overlay into the video frame (not the Windows pointer)."""
        if style == "不显示":
            return frame

        h, w = frame.shape[:2]
        x, y = int(x), int(y)
        if x < -40 or y < -55 or x >= w + 40 or y >= h + 55:
            return frame

        scale = max(0.75, min(1.6, min(w, h) / 720.0))
        def pts(offsets):
            return np.array([
                [x + int(round(px * scale)), y + int(round(py * scale))]
                for px, py in offsets
            ], dtype=np.int32)

        if style == "箭头":
            polygon = pts([
                (0, 0), (0, 31), (8, 23), (14, 36),
                (19, 33), (13, 21), (25, 20)
            ])
            cv2.fillPoly(frame, [polygon], (245, 245, 245))
            cv2.polylines(frame, [polygon], True, (20, 20, 20), max(1, int(2 * scale)), cv2.LINE_AA)

        elif style == "手型":
            # Stylized pointing hand: fingertip is the cursor hotspot.
            polygon = pts([
                (9, 0), (16, 0), (16, 21), (20, 17), (25, 18),
                (28, 22), (32, 21), (35, 25), (34, 30), (28, 40),
                (23, 47), (16, 48), (10, 44), (5, 36), (2, 29),
                (2, 25), (5, 22), (9, 25)
            ])
            cv2.fillPoly(frame, [polygon], (245, 245, 245))
            cv2.polylines(frame, [polygon], True, (15, 15, 15), max(1, int(2 * scale)), cv2.LINE_AA)
            cv2.line(
                frame,
                (x + int(16 * scale), y + int(22 * scale)),
                (x + int(16 * scale), y + int(36 * scale)),
                (60, 60, 60), max(1, int(scale)), cv2.LINE_AA
            )

        elif style == "十字准星":
            radius = max(10, int(15 * scale))
            thickness = max(1, int(2 * scale))
            cv2.circle(frame, (x, y), radius, (15, 15, 15), thickness + 2, cv2.LINE_AA)
            cv2.circle(frame, (x, y), radius, (0, 230, 255), thickness, cv2.LINE_AA)
            arm = max(17, int(24 * scale))
            cv2.line(frame, (x - arm, y), (x + arm, y), (15, 15, 15), thickness + 2, cv2.LINE_AA)
            cv2.line(frame, (x, y - arm), (x, y + arm), (15, 15, 15), thickness + 2, cv2.LINE_AA)
            cv2.line(frame, (x - arm, y), (x + arm, y), (0, 230, 255), thickness, cv2.LINE_AA)
            cv2.line(frame, (x, y - arm), (x, y + arm), (0, 230, 255), thickness, cv2.LINE_AA)

        elif style == "高亮圆圈":
            radius = max(15, int(24 * scale))
            overlay = frame.copy()
            cv2.circle(overlay, (x, y), radius, (0, 215, 255), -1, cv2.LINE_AA)
            cv2.addWeighted(overlay, 0.24, frame, 0.76, 0, frame)
            cv2.circle(frame, (x, y), radius, (0, 165, 255), max(2, int(3 * scale)), cv2.LINE_AA)
            cv2.circle(frame, (x, y), max(3, int(4 * scale)), (255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(frame, (x, y), max(3, int(4 * scale)), (25, 25, 25), 1, cv2.LINE_AA)

        elif style == "实心圆点":
            radius = max(5, int(7 * scale))
            cv2.circle(frame, (x, y), radius + 2, (10, 10, 10), -1, cv2.LINE_AA)
            cv2.circle(frame, (x, y), radius, (0, 235, 255), -1, cv2.LINE_AA)

        return frame

    def _map_cursor_for_zoom(self, cursor_x, cursor_y, region, frame_shape, factor):
        """Map cursor coordinates into the zoomed frame so the icon stays crisp and readable."""
        h, w = frame_shape[:2]
        local_x = int(cursor_x) - int(region["left"])
        local_y = int(cursor_y) - int(region["top"])
        if factor <= 1.001 or self._zoom_global_point is None:
            return local_x, local_y

        click_x = int(self._zoom_global_point[0]) - int(region["left"])
        click_y = int(self._zoom_global_point[1]) - int(region["top"])
        crop_w = max(2, min(w, int(round(w / factor))))
        crop_h = max(2, min(h, int(round(h / factor))))
        crop_x = min(max(0, click_x - crop_w // 2), max(0, w - crop_w))
        crop_y = min(max(0, click_y - crop_h // 2), max(0, h - crop_h))
        return (
            int(round((local_x - crop_x) * w / crop_w)),
            int(round((local_y - crop_y) * h / crop_h)),
        )

    def _resolve_capture_region(self, x, y):
        monitor_index = find_monitor_for_point(self.monitors, x, y)
        if monitor_index is None:
            return None

        monitor = self.monitors[monitor_index]
        if self.auto_window:
            region = get_window_client_region_at_point(
                x, y, monitor, self.crop_rect
            )
            if region is not None:
                target_kind = "窗口内容区"
            else:
                region = get_monitor_capture_region(monitor, self.crop_rect)
                target_kind = "屏幕背景"
        else:
            region = get_monitor_capture_region(monitor, self.crop_rect)
            target_kind = "整屏"

        return monitor_index, region, target_kind

    def run(self):
        writer = None
        frames_written = 0
        try:
            output_width = int(self.region["width"]) // 2 * 2
            output_height = int(self.region["height"]) // 2 * 2
            if output_width < 2 or output_height < 2:
                raise RuntimeError("录制区域尺寸无效，请重新选择。")

            capture_region = {
                "left": int(self.region["left"]),
                "top": int(self.region["top"]),
                "width": int(self.region["width"]),
                "height": int(self.region["height"]),
            }

            try:
                self._mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
                self._mouse_listener.daemon = True
                self._mouse_listener.start()
            except Exception:
                self._mouse_listener = None

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(
                self.output_path, fourcc, float(self.fps),
                (output_width, output_height)
            )
            if not writer.isOpened():
                raise RuntimeError(
                    "无法初始化 MP4 视频编码器。请确认 OpenCV 安装正常，"
                    "并检查保存目录是否可写。"
                )

            frame_period = 1.0 / float(self.fps)
            next_frame_time = time.perf_counter()
            current_monitor_index = self.current_monitor_index
            current_key = (
                capture_region["left"], capture_region["top"],
                capture_region["width"], capture_region["height"],
                current_monitor_index, "启动中"
            )
            last_cursor = (0, 0)
            previous_click_time = 0.0

            with mss.mss() as screen_capture:
                while not self._stop_event.is_set():
                    if self._paused_event.is_set():
                        self._stop_event.wait(0.05)
                        next_frame_time = time.perf_counter()
                        continue

                    cursor_x = cursor_y = None
                    over_control = False
                    try:
                        cursor_x, cursor_y = get_cursor_position()
                        over_control = self._cursor_over_control_panel(cursor_x, cursor_y)
                    except Exception:
                        pass

                    # Resolve the target on every frame:
                    # - follow mode: monitor and optional window both follow the mouse.
                    # - fixed-screen mode + auto-window: only track windows while the mouse
                    #   is on the chosen monitor; moving to another monitor does not switch it.
                    if self.monitors and cursor_x is not None and not over_control:
                        try:
                            resolved = None
                            if self.follow_mouse:
                                resolved = self._resolve_capture_region(cursor_x, cursor_y)
                            elif self.auto_window and self.fixed_monitor_index is not None:
                                fixed_index = int(self.fixed_monitor_index)
                                if 0 <= fixed_index < len(self.monitors):
                                    fixed_monitor = self.monitors[fixed_index]
                                    fx = int(fixed_monitor["left"])
                                    fy = int(fixed_monitor["top"])
                                    fw = int(fixed_monitor["width"])
                                    fh = int(fixed_monitor["height"])
                                    if fx <= cursor_x < fx + fw and fy <= cursor_y < fy + fh:
                                        window_region = get_window_client_region_at_point(
                                            cursor_x, cursor_y, fixed_monitor, self.crop_rect
                                        )
                                        if window_region is not None:
                                            resolved = (
                                                fixed_index, window_region, "窗口内容区"
                                            )
                                        else:
                                            resolved = (
                                                fixed_index,
                                                get_monitor_capture_region(
                                                    fixed_monitor, self.crop_rect
                                                ),
                                                "屏幕背景"
                                            )

                            if resolved is not None:
                                new_index, candidate_region, target_kind = resolved
                                candidate_key = (
                                    int(candidate_region["left"]),
                                    int(candidate_region["top"]),
                                    int(candidate_region["width"]),
                                    int(candidate_region["height"]),
                                    int(new_index), target_kind
                                )
                                if candidate_key != current_key:
                                    capture_region = dict(candidate_region)
                                    current_monitor_index = new_index
                                    self.current_monitor_index = new_index
                                    self._target_kind = target_kind
                                    current_key = candidate_key
                                    self.monitor_changed.emit(
                                        new_index + 1,
                                        capture_region["left"],
                                        capture_region["top"],
                                        capture_region["width"],
                                        capture_region["height"],
                                        output_width,
                                        output_height,
                                        target_kind
                                    )
                        except Exception:
                            # Transient cursor/window API failures should not stop the recording.
                            pass

                    # A click triggers a smooth 3-second zoom-in / hold / zoom-out.
                    click = self._get_last_click()
                    if click is not None and click[0] > self._processed_click_time:
                        click_time, click_x, click_y = click
                        self._processed_click_time = click_time
                        if (
                            not self._cursor_over_control_panel(click_x, click_y)
                            and capture_region["left"] <= click_x < capture_region["left"] + capture_region["width"]
                            and capture_region["top"] <= click_y < capture_region["top"] + capture_region["height"]
                        ):
                            self._zoom_start_time = click_time
                            self._zoom_until = click_time + self.zoom_hold_seconds + 2.0 * self.zoom_transition_seconds
                            self._zoom_global_point = (click_x, click_y)

                    screenshot = screen_capture.grab(capture_region)
                    raw_frame = np.asarray(screenshot)
                    raw_frame = cv2.cvtColor(raw_frame, cv2.COLOR_BGRA2BGR)

                    now = time.perf_counter()
                    zoom_factor = 1.0
                    if self._zoom_global_point is not None:
                        if now < self._zoom_until:
                            zx, zy = self._zoom_global_point
                            zoom_factor, ring_alpha = self._zoom_state_at(now)
                            raw_frame = self._apply_click_zoom(
                                raw_frame, zx, zy, capture_region, zoom_factor, ring_alpha
                            )
                        else:
                            self._zoom_global_point = None
                            self._zoom_start_time = 0.0
                            self._zoom_until = 0.0

                    # MSS does not consistently capture the OS pointer on Windows,
                    # so render the selected cursor style directly into the video.
                    if cursor_x is not None and cursor_y is not None:
                        cursor_local_x, cursor_local_y = self._map_cursor_for_zoom(
                            cursor_x, cursor_y, capture_region, raw_frame.shape, zoom_factor
                        )
                        raw_frame = self._draw_cursor(
                            raw_frame, cursor_local_x, cursor_local_y, self.cursor_style
                        )

                    frame = self._fit_to_output(raw_frame, output_width, output_height)
                    writer.write(frame)
                    frames_written += 1

                    if frames_written == 1 or frames_written % 3 == 0:
                        preview = frame
                        max_w, max_h = 560, 315
                        scale = min(
                            max_w / preview.shape[1],
                            max_h / preview.shape[0],
                            1.0
                        )
                        if scale < 1.0:
                            preview = cv2.resize(
                                preview,
                                (max(1, int(preview.shape[1] * scale)),
                                 max(1, int(preview.shape[0] * scale))),
                                interpolation=cv2.INTER_AREA
                            )
                        rgb = cv2.cvtColor(preview, cv2.COLOR_BGR2RGB)
                        qimage = QImage(
                            rgb.data, rgb.shape[1], rgb.shape[0],
                            rgb.strides[0], QImage.Format.Format_RGB888
                        ).copy()
                        self.preview_frame.emit(qimage)

                    next_frame_time += frame_period
                    delay = next_frame_time - time.perf_counter()
                    if delay > 0:
                        self._stop_event.wait(delay)
                    else:
                        next_frame_time = time.perf_counter()

            writer.release()
            writer = None

            if frames_written <= 0 or not os.path.exists(self.output_path):
                raise RuntimeError("没有录制到有效画面。")
            if os.path.getsize(self.output_path) <= 0:
                raise RuntimeError("视频文件为空，未能完成导出。")
            self.file_saved.emit(self.output_path)

        except Exception as exc:
            self.recording_error.emit(str(exc))
        finally:
            if self._mouse_listener is not None:
                try:
                    self._mouse_listener.stop()
                    self._mouse_listener.join(timeout=0.5)
                except Exception:
                    pass
                self._mouse_listener = None
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass


class RecordingControlWindow(QWidget):
    """独立的实时预览和录制控制面板。"""
    pause_requested = Signal()
    reselect_requested = Signal()
    save_requested = Signal()
    discard_requested = Signal()

    def __init__(self, region, mode, cursor_style="箭头", zoom_hold_seconds=3.0):
        super().__init__(
            None,
            Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
        )
        self._finished = False
        self._paused = False
        self._latest_image = None
        self.setWindowTitle("正在录制 · 区域录屏工具")
        self.setMinimumWidth(470)
        self.setMaximumWidth(540)
        self.resize(490, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        header_row = QHBoxLayout()
        title = QLabel("录制预览")
        title.setStyleSheet("font-size:17px; font-weight:700;")
        header_row.addWidget(title)
        header_row.addStretch(1)
        self.live_status = QLabel("● 正在录制")
        self.live_status.setStyleSheet("color:#d93025; font-weight:700;")
        header_row.addWidget(self.live_status)
        layout.addLayout(header_row)

        self.preview_label = QLabel("正在获取画面预览…")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(430, 242)
        self.preview_label.setStyleSheet(
            "QLabel { background:#161616; color:#eeeeee; "
            "border:1px solid #555; border-radius:6px; }"
        )
        layout.addWidget(self.preview_label)

        self.region_label = QLabel(self._format_region(region, mode))
        self.region_label.setWordWrap(True)
        self.region_label.setStyleSheet("color:#444; font-size:12px;")
        layout.addWidget(self.region_label)

        self.notice_label = QLabel(
            "如果画面不对，点“重新选择区域”。重新选择会先保存当前片段，再开始新视频。"
        )
        self.notice_label.setWordWrap(True)
        self.notice_label.setStyleSheet("color:#666; font-size:11px;")
        layout.addWidget(self.notice_label)

        buttons_top = QHBoxLayout()
        self.pause_button = QPushButton("暂停")
        self.pause_button.setMinimumHeight(36)
        self.pause_button.clicked.connect(self.pause_requested.emit)
        buttons_top.addWidget(self.pause_button)

        self.reselect_button = QPushButton("重新选择区域")
        self.reselect_button.setMinimumHeight(36)
        self.reselect_button.clicked.connect(self.reselect_requested.emit)
        buttons_top.addWidget(self.reselect_button)
        layout.addLayout(buttons_top)

        buttons_bottom = QHBoxLayout()
        self.save_button = QPushButton("停止并保存")
        self.save_button.setMinimumHeight(38)
        self.save_button.setStyleSheet(
            "QPushButton { background:#16803c; color:white; border:0; "
            "border-radius:5px; font-weight:600; padding:5px 10px; }"
            "QPushButton:hover { background:#11632e; }"
        )
        self.save_button.clicked.connect(self.save_requested.emit)
        buttons_bottom.addWidget(self.save_button)

        self.discard_button = QPushButton("停止并删除本次视频")
        self.discard_button.setMinimumHeight(38)
        self.discard_button.setStyleSheet(
            "QPushButton { background:#f3f4f6; color:#9b1c1c; border:1px solid #e0b4b4; "
            "border-radius:5px; font-weight:600; padding:5px 10px; }"
            "QPushButton:hover { background:#fee2e2; }"
        )
        self.discard_button.clicked.connect(self.discard_requested.emit)
        buttons_bottom.addWidget(self.discard_button)
        layout.addLayout(buttons_bottom)

        foot = QLabel(
            f"鼠标样式：{cursor_style} · 点击放大后停留 {zoom_hold_seconds:g} 秒 · F9 停止并保存"
        )
        foot.setWordWrap(True)
        foot.setStyleSheet("color:#777; font-size:11px;")
        layout.addWidget(foot)

    @staticmethod
    def _format_region(region, mode):
        left = int(region["left"])
        top = int(region["top"])
        width = int(region["width"])
        height = int(region["height"])
        monitor_text = "显示器位置待确认"
        try:
            with mss.mss() as capture:
                monitors = capture.monitors[1:]
            intersections = []
            for idx, monitor in enumerate(monitors, start=1):
                x1 = max(left, int(monitor["left"]))
                y1 = max(top, int(monitor["top"]))
                x2 = min(left + width, int(monitor["left"]) + int(monitor["width"]))
                y2 = min(top + height, int(monitor["top"]) + int(monitor["height"]))
                area = max(0, x2 - x1) * max(0, y2 - y1)
                if area:
                    intersections.append((area, idx, monitor))
            if intersections:
                intersections.sort(reverse=True, key=lambda item: item[0])
                area, idx, monitor = intersections[0]
                if area >= width * height * 0.98:
                    monitor_text = f"显示器 {idx}"
                elif len(intersections) > 1:
                    monitor_text = "跨显示器区域"
                else:
                    monitor_text = f"显示器 {idx}（部分区域）"
        except Exception:
            pass
        return (
            f"模式：{mode}　|　目标：{monitor_text}\n"
            f"分辨率：{width} × {height}　|　屏幕坐标：X={left}, Y={top}"
        )

    def set_active_monitor(self, index, left, top, width, height, output_width, output_height, target_kind):
        self.region_label.setText(
            f"模式：鼠标跟随　|　当前目标：显示器 {index} / {target_kind}\n"
            f"捕获区域：{width} × {height}　|　输出视频：{output_width} × {output_height}\n"
            f"屏幕坐标：X={left}, Y={top}　|　鼠标移动后自动更新目标"
        )

    def set_preview(self, image):
        self._latest_image = image
        self._refresh_preview()

    def _refresh_preview(self):
        if self._latest_image is None:
            return
        pixmap = QPixmap.fromImage(self._latest_image)
        target_size = self.preview_label.size()
        self.preview_label.setPixmap(
            pixmap.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_preview()

    def set_paused(self, paused):
        self._paused = bool(paused)
        if self._paused:
            self.pause_button.setText("继续录制")
            self.live_status.setText("Ⅱ 已暂停")
            self.live_status.setStyleSheet("color:#b26a00; font-weight:700;")
            self.notice_label.setText("当前已暂停；点击“继续录制”后继续写入视频。")
        else:
            self.pause_button.setText("暂停")
            self.live_status.setText("● 正在录制")
            self.live_status.setStyleSheet("color:#d93025; font-weight:700;")
            self.notice_label.setText(
                "如果画面不对，点“重新选择区域”。重新选择会先保存当前片段，再开始新视频。"
            )

    def set_message(self, message):
        self.live_status.setText(message)
        self.live_status.setStyleSheet("color:#b26a00; font-weight:700;")
        self.pause_button.setEnabled(False)
        self.reselect_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.discard_button.setEnabled(False)

    def finish_and_close(self):
        self._finished = True
        self.close()

    def showEvent(self, event):
        super().showEvent(event)
        # 尝试告知 Windows 不要把控制面板本身纳入屏幕捕获。
        # 实际效果取决于 Windows 版本和捕获 API；仍会优先把面板放到未录制的屏幕。
        if os.name == "nt":
            try:
                hwnd = int(self.winId())
                ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
            except Exception:
                pass

    def closeEvent(self, event):
        if not self._finished:
            event.ignore()
            self.set_message("正在停止并保存…")
            self.save_requested.emit()
            return
        event.accept()




class VideoExportWorker(QThread):
    """Re-encode the kept frame ranges into one final MP4 file."""
    progress_changed = Signal(int)
    export_succeeded = Signal(str)
    export_failed = Signal(str)

    def __init__(self, source_path, segments, output_path):
        super().__init__()
        self.source_path = str(source_path)
        self.segments = [
            {"start": int(seg["start"]), "end": int(seg["end"])}
            for seg in segments
        ]
        self.output_path = str(output_path)

    def run(self):
        cap = None
        writer = None
        temp_path = None
        try:
            cap = cv2.VideoCapture(self.source_path)
            if not cap.isOpened():
                raise RuntimeError("无法打开原始视频文件。")

            fps = float(cap.get(cv2.CAP_PROP_FPS) or FPS)
            if not np.isfinite(fps) or fps <= 0:
                fps = float(FPS)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_source_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if width < 2 or height < 2 or total_source_frames < 1:
                raise RuntimeError("原始视频信息不完整，无法剪辑。")

            kept_segments = []
            for seg in self.segments:
                start = max(0, min(total_source_frames, seg["start"]))
                end = max(0, min(total_source_frames, seg["end"]))
                if end > start:
                    kept_segments.append((start, end))
            if not kept_segments:
                raise RuntimeError("没有可导出的片段。请先恢复或保留至少一个片段。")

            total_kept_frames = sum(end - start for start, end in kept_segments)
            output = Path(self.output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output.with_name(output.stem + ".__exporting__" + output.suffix)
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(
                str(temp_path), fourcc, fps, (width, height)
            )
            if not writer.isOpened():
                raise RuntimeError(
                    "无法创建最终 MP4 文件。请检查保存目录和磁盘空间。"
                )

            written = 0
            for start, end in kept_segments:
                cap.set(cv2.CAP_PROP_POS_FRAMES, start)
                for frame_index in range(start, end):
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        raise RuntimeError(
                            f"读取原视频第 {frame_index} 帧失败，导出已中止。"
                        )
                    if frame.shape[1] != width or frame.shape[0] != height:
                        frame = cv2.resize(frame, (width, height))
                    writer.write(frame)
                    written += 1
                    if written % 5 == 0 or written == total_kept_frames:
                        self.progress_changed.emit(
                            min(100, int(written * 100 / total_kept_frames))
                        )

            writer.release()
            writer = None
            cap.release()
            cap = None

            if written <= 0 or not temp_path.exists() or temp_path.stat().st_size <= 0:
                raise RuntimeError("导出失败，未生成有效的视频文件。")

            # Replace destination only after a successful complete encode.
            os.replace(str(temp_path), self.output_path)
            temp_path = None
            self.progress_changed.emit(100)
            self.export_succeeded.emit(self.output_path)

        except Exception as exc:
            self.export_failed.emit(str(exc))
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if temp_path is not None:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass


class VideoEditorWindow(QMainWindow):
    """Simple frame-based editor: split the source into ranges, delete ranges, then export."""
    def __init__(self, source_path, parent=None):
        super().__init__(parent)
        self.source_path = str(Path(source_path).resolve())
        self.source = Path(self.source_path)
        self.cap = cv2.VideoCapture(self.source_path)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError("无法打开这个视频文件。")

        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS) or FPS)
        if not np.isfinite(self.fps) or self.fps <= 0:
            self.fps = float(FPS)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if self.total_frames < 1 or self.frame_width < 2 or self.frame_height < 2:
            self.cap.release()
            raise RuntimeError("视频时长或分辨率读取失败。")

        self.duration = self.total_frames / self.fps
        self.current_frame = 0
        self.playing = False
        self.export_worker = None
        self.undo_stack = []
        self.segments = [{"start": 0, "end": self.total_frames}]

        self.setWindowTitle(f"视频剪辑 - {self.source.name}")
        self.resize(1120, 790)
        self.setMinimumSize(900, 650)
        self._build_editor_ui()
        self._refresh_segments_table(select_row=0)
        self._show_frame_at(0)

        self.play_timer = QTimer(self)
        self.play_timer.setInterval(max(15, int(round(1000.0 / min(self.fps, 60.0)))))
        self.play_timer.timeout.connect(self._on_play_tick)

    @staticmethod
    def _format_time(seconds):
        seconds = max(0.0, float(seconds))
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"

    def _build_editor_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(9)

        title = QLabel("简单视频剪辑")
        title.setStyleSheet("font-size:20px; font-weight:700;")
        root.addWidget(title)

        source_label = QLabel(f"原始视频：{self.source_path}")
        source_label.setWordWrap(True)
        source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        source_label.setStyleSheet("color:#555;")
        root.addWidget(source_label)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)

        preview_column = QVBoxLayout()
        body.addLayout(preview_column, 3)

        self.preview_label = QLabel("正在读取视频…")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(500, 300)
        self.preview_label.setStyleSheet(
            "background:#111; color:#ddd; border:1px solid #333; border-radius:5px;"
        )
        preview_column.addWidget(self.preview_label, 1)

        self.timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_slider.setRange(0, max(0, self.total_frames - 1))
        self.timeline_slider.setSingleStep(1)
        self.timeline_slider.setPageStep(max(1, int(self.fps * 5)))
        self.timeline_slider.setTracking(False)
        self.timeline_slider.sliderReleased.connect(self._seek_from_slider)
        self.timeline_slider.sliderMoved.connect(self._update_time_from_slider)
        preview_column.addWidget(self.timeline_slider)

        self.time_label = QLabel("")
        self.time_label.setStyleSheet("color:#555; font-family:Consolas, monospace;")
        preview_column.addWidget(self.time_label)

        playback_row = QHBoxLayout()
        self.play_button = QPushButton("播放")
        self.play_button.setMinimumHeight(34)
        self.play_button.clicked.connect(self._toggle_playback)
        playback_row.addWidget(self.play_button)

        self.previous_frame_button = QPushButton("上一帧")
        self.previous_frame_button.clicked.connect(
            lambda: self._show_frame_at(self.current_frame - 1)
        )
        playback_row.addWidget(self.previous_frame_button)

        self.next_frame_button = QPushButton("下一帧")
        self.next_frame_button.clicked.connect(
            lambda: self._show_frame_at(self.current_frame + 1)
        )
        playback_row.addWidget(self.next_frame_button)

        self.jump_start_button = QPushButton("回到开头")
        self.jump_start_button.clicked.connect(lambda: self._show_frame_at(0))
        playback_row.addWidget(self.jump_start_button)
        preview_column.addLayout(playback_row)

        right_column = QVBoxLayout()
        body.addLayout(right_column, 2)

        segments_heading = QLabel("片段列表")
        segments_heading.setStyleSheet("font-size:15px; font-weight:700;")
        right_column.addWidget(segments_heading)

        self.segment_table = QTableWidget(0, 4)
        self.segment_table.setHorizontalHeaderLabels(["片段", "开始", "结束", "时长"])
        self.segment_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.segment_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.segment_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.segment_table.verticalHeader().setVisible(False)
        self.segment_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        for col in (1, 2, 3):
            self.segment_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch
            )
        self.segment_table.itemSelectionChanged.connect(self._on_segment_selected)
        right_column.addWidget(self.segment_table, 1)

        self.segment_summary = QLabel("")
        self.segment_summary.setWordWrap(True)
        self.segment_summary.setStyleSheet(
            "background:#f5f6f8; border:1px solid #e5e7eb; "
            "border-radius:5px; padding:8px; color:#444;"
        )
        right_column.addWidget(self.segment_summary)

        self.split_button = QPushButton("在当前位置分割")
        self.split_button.clicked.connect(self._split_at_current_frame)
        right_column.addWidget(self.split_button)

        self.delete_segment_button = QPushButton("删除选中片段")
        self.delete_segment_button.clicked.connect(self._delete_selected_segment)
        right_column.addWidget(self.delete_segment_button)

        edit_row = QHBoxLayout()
        self.undo_button = QPushButton("撤销")
        self.undo_button.clicked.connect(self._undo_edit)
        edit_row.addWidget(self.undo_button)
        self.restore_button = QPushButton("恢复全部")
        self.restore_button.clicked.connect(self._restore_all_segments)
        edit_row.addWidget(self.restore_button)
        right_column.addLayout(edit_row)

        hint = QLabel(
            "操作：播放或拖动进度条定位 → 分割 → 选中不需要的片段并删除。"
            "导出时剩余片段会按原始顺序自动拼接。原始视频不会被修改。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666; font-size:12px;")
        root.addWidget(hint)

        export_row = QHBoxLayout()
        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 100)
        self.export_progress.setValue(0)
        export_row.addWidget(self.export_progress, 1)

        self.export_button = QPushButton("导出最终 MP4")
        self.export_button.setMinimumHeight(42)
        self.export_button.setStyleSheet(
            "QPushButton { background:#16803c; color:white; border:0; "
            "border-radius:5px; font-weight:700; padding:7px 16px; }"
            "QPushButton:hover { background:#11632e; }"
            "QPushButton:disabled { background:#999; }"
        )
        self.export_button.clicked.connect(self._choose_export_path)
        export_row.addWidget(self.export_button)
        root.addLayout(export_row)

        self.editor_status = QLabel(
            f"原视频 {self.frame_width}×{self.frame_height} · "
            f"{self.fps:.2f} FPS · 时长 {self._format_time(self.duration)}"
        )
        self.editor_status.setStyleSheet("color:#555;")
        root.addWidget(self.editor_status)

    def _refresh_segments_table(self, select_row=None):
        self.segment_table.blockSignals(True)
        self.segment_table.setRowCount(len(self.segments))
        for row, seg in enumerate(self.segments):
            start_seconds = seg["start"] / self.fps
            end_seconds = seg["end"] / self.fps
            duration_seconds = max(0, seg["end"] - seg["start"]) / self.fps
            values = [
                f"片段 {row + 1}",
                self._format_time(start_seconds),
                self._format_time(end_seconds),
                self._format_time(duration_seconds),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.segment_table.setItem(row, col, item)

        if select_row is not None and 0 <= select_row < len(self.segments):
            self.segment_table.selectRow(select_row)
        elif self.segments:
            self.segment_table.clearSelection()
        self.segment_table.blockSignals(False)

        kept_frames = sum(max(0, seg["end"] - seg["start"]) for seg in self.segments)
        kept_duration = kept_frames / self.fps
        self.segment_summary.setText(
            f"保留 {len(self.segments)} 段 · 合计 {self._format_time(kept_duration)}"
            f"\n删除片段后，导出时会直接拼接剩余内容。"
        )
        self.export_button.setEnabled(
            bool(self.segments) and self.export_worker is None
        )
        self.undo_button.setEnabled(bool(self.undo_stack))
        self.delete_segment_button.setEnabled(bool(self.segments))
        self.split_button.setEnabled(bool(self.segments))

    def _snapshot_undo(self):
        self.undo_stack.append([dict(seg) for seg in self.segments])
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)

    def _selected_row(self):
        return self.segment_table.currentRow()

    def _on_segment_selected(self):
        row = self._selected_row()
        if 0 <= row < len(self.segments):
            self._show_frame_at(self.segments[row]["start"])

    def _update_time_label(self):
        current_seconds = self.current_frame / self.fps
        self.time_label.setText(
            f"{self._format_time(current_seconds)} / {self._format_time(self.duration)}"
        )

    def _update_time_from_slider(self, value):
        self.time_label.setText(
            f"{self._format_time(value / self.fps)} / {self._format_time(self.duration)}"
        )

    def _show_frame_at(self, frame_index):
        frame_index = max(0, min(self.total_frames - 1, int(frame_index)))
        self.current_frame = frame_index
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = self.cap.read()
        if ok and frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = QImage(
                rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                QImage.Format.Format_RGB888
            ).copy()
            pixmap = QPixmap.fromImage(image)
            self.preview_label.setPixmap(
                pixmap.scaled(
                    self.preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            )
        self.timeline_slider.blockSignals(True)
        self.timeline_slider.setValue(frame_index)
        self.timeline_slider.blockSignals(False)
        self._update_time_label()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # resizeEvent can fire during construction, before the preview widgets exist.
        if (
            hasattr(self, "preview_label")
            and hasattr(self, "timeline_slider")
            and hasattr(self, "current_frame")
            and not getattr(self, "playing", False)
        ):
            self._show_frame_at(self.current_frame)

    def _seek_from_slider(self):
        self._show_frame_at(self.timeline_slider.value())

    def _toggle_playback(self):
        if self.playing:
            self.play_timer.stop()
            self.playing = False
            self.play_button.setText("播放")
        else:
            if self.current_frame >= self.total_frames - 1:
                self._show_frame_at(0)
            self.playing = True
            self.play_button.setText("暂停")
            self.play_timer.start()

    def _on_play_tick(self):
        next_frame = self.current_frame + 1
        if next_frame >= self.total_frames:
            self.play_timer.stop()
            self.playing = False
            self.play_button.setText("播放")
            return
        self._show_frame_at(next_frame)

    def _stop_playback(self):
        if self.playing:
            self.play_timer.stop()
            self.playing = False
            self.play_button.setText("播放")

    def _split_at_current_frame(self):
        self._stop_playback()
        row = self._selected_row()
        if not (0 <= row < len(self.segments)):
            QMessageBox.information(self, "先选择片段", "请先在右侧列表中选择要分割的片段。")
            return
        seg = self.segments[row]
        split_frame = int(self.current_frame)
        if split_frame <= seg["start"] or split_frame >= seg["end"]:
            QMessageBox.information(
                self, "无法分割",
                "当前播放位置不在选中片段的内部。\n请先选中片段，再拖动进度条到片段中间。"
            )
            return

        self._snapshot_undo()
        self.segments[row:row + 1] = [
            {"start": seg["start"], "end": split_frame},
            {"start": split_frame, "end": seg["end"]},
        ]
        self._refresh_segments_table(select_row=row + 1)
        self.editor_status.setText(
            f"已分割：{self._format_time(split_frame / self.fps)}。"
            "如需移除某一段，请选中该行并点击“删除选中片段”。"
        )

    def _delete_selected_segment(self):
        self._stop_playback()
        row = self._selected_row()
        if not (0 <= row < len(self.segments)):
            QMessageBox.information(self, "先选择片段", "请先在右侧列表中选择要删除的片段。")
            return
        seg = self.segments[row]
        duration = (seg["end"] - seg["start"]) / self.fps
        reply = QMessageBox.question(
            self,
            "删除片段",
            f"确定从最终视频中删除片段 {row + 1} 吗？\n"
            f"时长：{self._format_time(duration)}\n\n原始视频不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._snapshot_undo()
        self.segments.pop(row)
        next_row = min(row, len(self.segments) - 1)
        self._refresh_segments_table(select_row=next_row if next_row >= 0 else None)
        self.editor_status.setText("已移除选中片段；导出时会自动拼接其余片段。")

    def _undo_edit(self):
        if not self.undo_stack:
            return
        self.segments = self.undo_stack.pop()
        self._refresh_segments_table(select_row=0 if self.segments else None)
        self.editor_status.setText("已撤销上一步编辑。")

    def _restore_all_segments(self):
        if len(self.segments) == 1 and self.segments[0]["start"] == 0 and self.segments[0]["end"] == self.total_frames:
            return
        self._snapshot_undo()
        self.segments = [{"start": 0, "end": self.total_frames}]
        self._refresh_segments_table(select_row=0)
        self._show_frame_at(0)
        self.editor_status.setText("已恢复完整原视频；可重新分割。")

    def _choose_export_path(self):
        if self.export_worker is not None:
            return
        if not self.segments:
            QMessageBox.warning(self, "没有可导出的片段", "请至少保留一个片段，或点击“恢复全部”。")
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = self.source.with_name(f"{self.source.stem}_剪辑_{stamp}.mp4")
        output_path, _ = QFileDialog.getSaveFileName(
            self, "导出最终视频", str(default_path), "MP4 视频 (*.mp4)"
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".mp4"):
            output_path += ".mp4"

        normalized_source = os.path.normcase(os.path.abspath(self.source_path))
        normalized_output = os.path.normcase(os.path.abspath(output_path))
        if normalized_source == normalized_output:
            QMessageBox.warning(
                self, "不能覆盖原视频",
                "最终导出文件不能与原始录屏同名，请使用另一个文件名。"
            )
            return
        if os.path.exists(output_path):
            reply = QMessageBox.question(
                self, "覆盖文件",
                f"文件已存在：\n{output_path}\n\n要覆盖它吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._stop_playback()
        self.export_progress.setValue(0)
        self.editor_status.setText("正在拼接片段并导出，请稍候…")
        self.export_button.setEnabled(False)
        self.split_button.setEnabled(False)
        self.delete_segment_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self.undo_button.setEnabled(False)
        self.play_button.setEnabled(False)
        self.previous_frame_button.setEnabled(False)
        self.next_frame_button.setEnabled(False)
        self.timeline_slider.setEnabled(False)

        self.export_worker = VideoExportWorker(
            self.source_path, [dict(seg) for seg in self.segments], output_path
        )
        self.export_worker.progress_changed.connect(self.export_progress.setValue)
        self.export_worker.export_succeeded.connect(self._on_export_succeeded)
        self.export_worker.export_failed.connect(self._on_export_failed)
        self.export_worker.finished.connect(self._on_export_thread_finished)
        self.export_worker.start()

    def _set_export_controls_enabled(self, enabled):
        self.export_button.setEnabled(enabled and bool(self.segments))
        self.split_button.setEnabled(enabled and bool(self.segments))
        self.delete_segment_button.setEnabled(enabled and bool(self.segments))
        self.restore_button.setEnabled(enabled)
        self.undo_button.setEnabled(enabled and bool(self.undo_stack))
        self.play_button.setEnabled(enabled)
        self.previous_frame_button.setEnabled(enabled)
        self.next_frame_button.setEnabled(enabled)
        self.jump_start_button.setEnabled(enabled)
        self.timeline_slider.setEnabled(enabled)
        self.segment_table.setEnabled(enabled)

    def _on_export_succeeded(self, output_path):
        # Keep controls disabled until QThread.finished confirms the worker has exited.
        self.export_progress.setValue(100)
        self.editor_status.setText(f"导出完成：{output_path}")
        QMessageBox.information(
            self, "导出完成",
            f"最终视频已生成：\n{output_path}\n\n原始录屏未被修改。"
        )

    def _on_export_failed(self, message):
        # Controls are re-enabled by _on_export_thread_finished.
        self.editor_status.setText("导出失败")
        QMessageBox.critical(self, "导出失败", message)

    def _on_export_thread_finished(self):
        worker = self.export_worker
        self.export_worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_export_controls_enabled(True)

    def closeEvent(self, event):
        self._stop_playback()
        if self.export_worker is not None and self.export_worker.isRunning():
            QMessageBox.information(
                self, "正在导出", "视频仍在导出，请等待导出完成后再关闭窗口。"
            )
            event.ignore()
            return
        try:
            self.cap.release()
        except Exception:
            pass
        event.accept()



class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(510)
        self.setMaximumWidth(510)

        self.settings = QSettings("RegionRecorder", "RegionRecorder")
        default_dir = Path.home() / "Videos" / "区域录屏"
        configured_dir = self.settings.value("save_dir", str(default_dir))
        self.save_dir = Path(str(configured_dir)).expanduser()
        self.save_dir.mkdir(parents=True, exist_ok=True)

        self.selector = None
        self.crop_selector = None
        self.recorder = None
        self.control_window = None
        self._reselect_after_stop = False
        self._recording_mode = "区域"
        self._completed_path = None
        self._error_message = None
        self._discard_after_stop = False
        self._recording_output_path = None
        self._exit_after_recording = False
        self._invalid_selection_message = None
        self._monitor_list = []
        self._region_presets = {}
        self._custom_selection_mode = False
        self.video_editor = None

        self._build_ui()
        self._build_tray()
        self.control_rect_timer = QTimer(self)
        self.control_rect_timer.setInterval(200)
        self.control_rect_timer.timeout.connect(self._sync_control_exclusion_rect)
        self.control_rect_timer.start()

        self.hotkey_signals = HotkeySignals()
        self.hotkey_signals.stop_requested.connect(self.stop_recording)
        self.hotkey_listener = None
        try:
            self.hotkey_listener = keyboard.Listener(on_press=self._on_global_key)
            self.hotkey_listener.daemon = True
            self.hotkey_listener.start()
        except Exception as exc:
            self.status_label.setText("状态：全局快捷键启动失败，请检查系统权限")
            self.status_label.setToolTip(str(exc))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel("区域录屏工具")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        subtitle = QLabel("只录画面，不录系统声音或麦克风。")
        subtitle.setStyleSheet("color:#666; font-size:13px;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color:#dddddd;")
        layout.addWidget(divider)

        screen_heading = QLabel("录制屏幕")
        screen_heading.setStyleSheet("font-weight:700;")
        layout.addWidget(screen_heading)

        screen_row = QHBoxLayout()
        self.monitor_options_layout = QHBoxLayout()
        self.monitor_button_group = QButtonGroup(self)
        self.monitor_button_group.setExclusive(True)
        self.monitor_options_layout.setSpacing(8)
        screen_row.addLayout(self.monitor_options_layout, 1)

        self.monitor_refresh_button = QPushButton("刷新")
        self.monitor_refresh_button.setToolTip("重新检测当前连接的显示器")
        self.monitor_refresh_button.clicked.connect(self._refresh_monitor_choices)
        screen_row.addWidget(self.monitor_refresh_button)
        layout.addLayout(screen_row)

        self.follow_mouse_checkbox = QCheckBox(
            "跟随鼠标自动切换显示器（勾选后覆盖上面的固定屏幕选择）"
        )
        self.follow_mouse_checkbox.setChecked(
            self.settings.value("follow_mouse", False, type=bool)
        )
        self.follow_mouse_checkbox.setStyleSheet("font-size:13px; padding:2px 0;")
        self.follow_mouse_checkbox.toggled.connect(self._on_follow_mouse_toggled)
        layout.addWidget(self.follow_mouse_checkbox)

        self.auto_window_checkbox = QCheckBox(
            "自动识别窗口内容区（排除窗口标题栏和边框）"
        )
        self.auto_window_checkbox.setChecked(
            self.settings.value("auto_window", True, type=bool)
        )
        self.auto_window_checkbox.setStyleSheet("font-size:13px; padding:2px 0;")
        self.auto_window_checkbox.toggled.connect(self._on_auto_window_toggled)
        layout.addWidget(self.auto_window_checkbox)

        mode_hint = QLabel(
            "不勾选跟随鼠标：固定录制选中的屏幕。若同时启用窗口识别，"
            "只在所选屏幕内跟踪窗口；鼠标移到其他屏幕不会切走。"
        )
        mode_hint.setWordWrap(True)
        mode_hint.setStyleSheet("color:#666; font-size:12px;")
        layout.addWidget(mode_hint)

        preset_heading = QLabel("自定义区域预设")
        preset_heading.setStyleSheet("font-weight:700;")
        layout.addWidget(preset_heading)

        preset_row = QHBoxLayout()
        self.region_preset_combo = QComboBox()
        self.region_preset_combo.setMinimumHeight(34)
        self.region_preset_combo.currentIndexChanged.connect(self._on_region_preset_changed)
        preset_row.addWidget(self.region_preset_combo, 1)

        self.save_region_preset_button = QPushButton("框选并保存为习惯…")
        self.save_region_preset_button.setMinimumHeight(34)
        self.save_region_preset_button.clicked.connect(self.start_custom_region_selection)
        preset_row.addWidget(self.save_region_preset_button)

        self.delete_region_preset_button = QPushButton("删除")
        self.delete_region_preset_button.setMinimumHeight(34)
        self.delete_region_preset_button.clicked.connect(self.delete_region_preset)
        preset_row.addWidget(self.delete_region_preset_button)
        layout.addLayout(preset_row)

        self.preset_hint_label = QLabel(
            "选择已保存的习惯后，开始录制会直接使用该固定区域，不受鼠标跨屏影响。"
        )
        self.preset_hint_label.setWordWrap(True)
        self.preset_hint_label.setStyleSheet("color:#666; font-size:12px;")
        layout.addWidget(self.preset_hint_label)

        crop_heading = QLabel("通用裁切范围（可选）")
        crop_heading.setStyleSheet("font-weight:700;")
        layout.addWidget(crop_heading)

        self.crop_info_label = QLabel(self._crop_description())
        self.crop_info_label.setWordWrap(True)
        self.crop_info_label.setStyleSheet(
            "background:#f5f6f8; border:1px solid #e5e7eb; "
            "border-radius:5px; padding:7px; color:#444; font-size:12px;"
        )
        layout.addWidget(self.crop_info_label)

        crop_buttons = QHBoxLayout()
        self.crop_content_button = QPushButton("框选窗口内部要保留的内容")
        self.crop_content_button.setMinimumHeight(34)
        self.crop_content_button.clicked.connect(self.start_crop_selection)
        crop_buttons.addWidget(self.crop_content_button, 1)

        self.reset_crop_button = QPushButton("恢复完整区域")
        self.reset_crop_button.setMinimumHeight(34)
        self.reset_crop_button.clicked.connect(self.reset_crop_selection)
        crop_buttons.addWidget(self.reset_crop_button)
        layout.addLayout(crop_buttons)

        effect_heading = QLabel("鼠标样式与点击放大")
        effect_heading.setStyleSheet("font-weight:700;")
        layout.addWidget(effect_heading)

        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel("录制中的鼠标"))
        self.cursor_style_combo = QComboBox()
        self.cursor_style_combo.addItems([
            "箭头", "手型", "十字准星", "高亮圆圈", "实心圆点", "不显示"
        ])
        saved_cursor_style = str(self.settings.value("cursor_style", "箭头"))
        saved_cursor_index = self.cursor_style_combo.findText(saved_cursor_style)
        if saved_cursor_index >= 0:
            self.cursor_style_combo.setCurrentIndex(saved_cursor_index)
        self.cursor_style_combo.currentTextChanged.connect(
            lambda value: self.settings.setValue("cursor_style", value)
        )
        effect_row.addWidget(self.cursor_style_combo, 1)

        effect_row.addWidget(QLabel("放大停留"))
        self.zoom_hold_spin = QDoubleSpinBox()
        self.zoom_hold_spin.setRange(0.5, 30.0)
        self.zoom_hold_spin.setDecimals(1)
        self.zoom_hold_spin.setSingleStep(0.5)
        self.zoom_hold_spin.setSuffix(" 秒")
        self.zoom_hold_spin.setValue(
            float(self.settings.value("zoom_hold_seconds", 3.0))
        )
        self.zoom_hold_spin.valueChanged.connect(
            lambda value: self.settings.setValue("zoom_hold_seconds", float(value))
        )
        effect_row.addWidget(self.zoom_hold_spin)
        layout.addLayout(effect_row)

        self.zoom_hint_label = QLabel()
        self.zoom_hint_label.setWordWrap(True)
        self.zoom_hint_label.setStyleSheet("color:#666; font-size:12px;")
        self.zoom_hold_spin.valueChanged.connect(self._update_zoom_hint)
        layout.addWidget(self.zoom_hint_label)
        self._update_zoom_hint()

        self.start_button = QPushButton("开始录制")
        self.start_button.setMinimumHeight(46)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setStyleSheet(
            "QPushButton { background:#1677ff; color:white; border:0; "
            "border-radius:7px; font-size:15px; font-weight:600; }"
            "QPushButton:hover { background:#0958d9; }"
            "QPushButton:disabled { background:#a0a0a0; }"
        )
        self.start_button.clicked.connect(self.start_recording_by_options)
        layout.addWidget(self.start_button)

        path_heading = QLabel("视频保存位置")
        path_heading.setStyleSheet("font-weight:600;")
        layout.addWidget(path_heading)

        path_row = QHBoxLayout()
        self.path_label = QLabel(str(self.save_dir))
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.path_label.setStyleSheet(
            "background:#f5f6f8; border:1px solid #e5e7eb; "
            "border-radius:5px; padding:8px; color:#333;"
        )
        path_row.addWidget(self.path_label, 1)
        self.change_path_button = QPushButton("更改")
        self.change_path_button.clicked.connect(self.choose_save_dir)
        path_row.addWidget(self.change_path_button)
        layout.addLayout(path_row)

        self.status_label = QLabel("状态：准备就绪")
        self.status_label.setStyleSheet("color:#26734d; font-weight:600;")
        layout.addWidget(self.status_label)

        open_folder_button = QPushButton("打开保存文件夹")
        open_folder_button.clicked.connect(self.open_save_dir)
        layout.addWidget(open_folder_button)

        self.edit_video_button = QPushButton("剪辑已有视频…")
        self.edit_video_button.setMinimumHeight(36)
        self.edit_video_button.clicked.connect(self.choose_video_to_edit)
        layout.addWidget(self.edit_video_button)

        self._refresh_monitor_choices()
        self._refresh_region_preset_combo(
            str(self.settings.value("selected_region_preset", ""))
        )

    def _update_zoom_hint(self, *_args):
        if not hasattr(self, "zoom_hold_spin"):
            return
        hold = float(self.zoom_hold_spin.value())
        transition = 0.35
        total = hold + 2 * transition
        self.zoom_hint_label.setText(
            f"点击录制画面后，平滑推进 {transition:.2f} 秒，"
            f"放大停留 {hold:g} 秒，再平滑拉远 {transition:.2f} 秒；"
            f"一次效果总时长约 {total:.2f} 秒。"
        )

    def _read_connected_monitors(self):
        with mss.mss() as screen_capture:
            return [dict(monitor) for monitor in screen_capture.monitors[1:]]

    def _refresh_monitor_choices(self):
        try:
            monitors = self._read_connected_monitors()
        except Exception as exc:
            self.status_label.setText("状态：无法检测显示器")
            QMessageBox.warning(self, "显示器检测失败", str(exc))
            return

        primary_index = 0
        primary_screen = QApplication.primaryScreen()
        if primary_screen is not None:
            pg = primary_screen.geometry()
            best_score = -1
            for idx, monitor in enumerate(monitors):
                mx, my = int(monitor["left"]), int(monitor["top"])
                mw, mh = int(monitor["width"]), int(monitor["height"])
                overlap_w = max(0, min(pg.right() + 1, mx + mw) - max(pg.left(), mx))
                overlap_h = max(0, min(pg.bottom() + 1, my + mh) - max(pg.top(), my))
                score = overlap_w * overlap_h
                if score > best_score:
                    best_score = score
                    primary_index = idx

        saved_index = self.settings.value("selected_monitor", primary_index)
        try:
            saved_index = int(saved_index)
        except Exception:
            saved_index = primary_index
        if not (0 <= saved_index < len(monitors)):
            saved_index = primary_index

        self._monitor_list = monitors
        while self.monitor_options_layout.count():
            item = self.monitor_options_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self.monitor_button_group.removeButton(widget)
                widget.deleteLater()

        self.monitor_button_group.setExclusive(True)
        for idx, monitor in enumerate(monitors):
            label = (
                f"屏幕 {idx + 1}\n"
                f"{int(monitor['width'])} × {int(monitor['height'])}\n"
                f"({int(monitor['left'])}, {int(monitor['top'])})"
            )
            if idx == primary_index:
                label = "主屏\n" + label.split("\n", 1)[1]
            radio = QRadioButton(label)
            radio.setMinimumWidth(125)
            radio.setStyleSheet(
                "QRadioButton { border:1px solid #d7dce2; border-radius:6px; "
                "padding:7px; spacing:5px; }"
                "QRadioButton:checked { background:#eaf2ff; border:1px solid #1677ff; }"
            )
            self.monitor_button_group.addButton(radio, idx)
            self.monitor_options_layout.addWidget(radio)
            radio.toggled.connect(
                lambda checked, i=idx: self._on_monitor_selected(i) if checked else None
            )
            radio.setChecked(idx == saved_index)

        if not monitors:
            self.status_label.setText("状态：没有检测到显示器")
        else:
            self.settings.setValue("selected_monitor", saved_index)

    def _selected_monitor_index(self):
        idx = self.monitor_button_group.checkedId()
        if idx < 0 or idx >= len(self._monitor_list):
            return 0 if self._monitor_list else -1
        return idx

    def _on_monitor_selected(self, index):
        self.settings.setValue("selected_monitor", int(index))

    def _load_region_presets(self):
        raw = self.settings.value("region_presets", "{}")
        try:
            presets = raw if isinstance(raw, dict) else json.loads(str(raw))
        except Exception:
            presets = {}
        valid = {}
        if isinstance(presets, dict):
            for name, region in presets.items():
                if not isinstance(region, dict):
                    continue
                try:
                    normalized = {
                        "left": int(region["left"]),
                        "top": int(region["top"]),
                        "width": int(region["width"]),
                        "height": int(region["height"]),
                    }
                    if normalized["width"] >= 20 and normalized["height"] >= 20:
                        valid[str(name)] = normalized
                except Exception:
                    continue
        self._region_presets = valid
        return valid

    def _refresh_region_preset_combo(self, selected_name=None):
        presets = self._load_region_presets()
        if selected_name is None:
            selected_name = str(self.settings.value("selected_region_preset", ""))
        self.region_preset_combo.blockSignals(True)
        self.region_preset_combo.clear()
        self.region_preset_combo.addItem("不使用自定义区域", "")
        for name, region in presets.items():
            self.region_preset_combo.addItem(
                f"{name}  ·  {region['width']} × {region['height']}",
                name
            )
        idx = self.region_preset_combo.findData(selected_name)
        if idx < 0:
            idx = 0
        self.region_preset_combo.setCurrentIndex(idx)
        self.region_preset_combo.blockSignals(False)
        self.settings.setValue(
            "selected_region_preset",
            str(self.region_preset_combo.currentData() or "")
        )
        self._update_preset_hint()

    def _update_preset_hint(self):
        name = self.region_preset_combo.currentData()
        if name and name in self._region_presets:
            region = self._region_presets[name]
            self.preset_hint_label.setText(
                f"当前选择：{name}（{region['width']} × {region['height']}，"
                f"坐标 {region['left']}, {region['top']}）。开始录制会固定使用此区域。"
            )
        else:
            self.preset_hint_label.setText(
                "选择已保存的习惯后，开始录制会直接使用该固定区域，不受鼠标跨屏影响。"
            )

    def _on_region_preset_changed(self, index):
        if not hasattr(self, "region_preset_combo"):
            return
        name = str(self.region_preset_combo.currentData() or "")
        self.settings.setValue("selected_region_preset", name)
        self._update_preset_hint()

    def start_custom_region_selection(self):
        if self._recording_is_active():
            return
        self._custom_selection_mode = True
        self.status_label.setText("状态：请拖动鼠标框选要保存的固定区域…")
        self.hide()
        QTimer.singleShot(250, self._show_selector)

    def _on_custom_region_selected(self, region):
        self.selector = None
        self.show_main_window()
        self._load_region_presets()
        default_name = "习惯1" if "习惯1" not in self._region_presets else "习惯2"
        name, ok = QInputDialog.getText(
            self, "保存自定义录制区域",
            "给这个区域起个名字（例如：习惯1）：",
            text=default_name
        )
        self._custom_selection_mode = False
        if not ok:
            self.status_label.setText("状态：区域已框选，但未保存预设")
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "名称不能为空", "请输入一个预设名称。")
            return
        self._region_presets[name] = {
            "left": int(region["left"]),
            "top": int(region["top"]),
            "width": int(region["width"]),
            "height": int(region["height"]),
        }
        self.settings.setValue(
            "region_presets",
            json.dumps(self._region_presets, ensure_ascii=False)
        )
        self._refresh_region_preset_combo(name)
        self.status_label.setText(f"状态：已保存区域预设“{name}”，下次可直接沿用")

    def delete_region_preset(self):
        name = str(self.region_preset_combo.currentData() or "")
        if not name or name not in self._region_presets:
            QMessageBox.information(self, "没有选中预设", "请先选择一个已保存的区域预设。")
            return
        reply = QMessageBox.question(
            self, "删除区域预设", f"确定删除“{name}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._region_presets.pop(name, None)
        self.settings.setValue(
            "region_presets",
            json.dumps(self._region_presets, ensure_ascii=False)
        )
        self._refresh_region_preset_combo("")
        self.status_label.setText(f"状态：已删除区域预设“{name}”")

    def _on_auto_window_toggled(self, checked):
        self.settings.setValue("auto_window", bool(checked))

    def _on_follow_mouse_toggled(self, checked):
        self.settings.setValue("follow_mouse", bool(checked))

    def _crop_rect(self):
        return (
            float(self.settings.value("crop_x", 0.0)),
            float(self.settings.value("crop_y", 0.0)),
            float(self.settings.value("crop_w", 1.0)),
            float(self.settings.value("crop_h", 1.0)),
        )

    def _crop_description(self):
        x, y, w, h = self._crop_rect()
        if x <= 0.01 and y <= 0.01 and w >= 0.98 and h >= 0.98:
            return "当前范围：完整窗口/屏幕（未裁切）。框选后会记住，下次自动沿用。"
        return (
            f"已记住范围：横向 {x * 100:.0f}%–{(x + w) * 100:.0f}%，"
            f"纵向 {y * 100:.0f}%–{(y + h) * 100:.0f}%。"
            "该比例会应用到后续识别到的窗口或显示器。"
        )

    def start_crop_selection(self):
        if self._recording_is_active():
            return
        self.status_label.setText("状态：请在目标软件窗口上框选要保留的内容…")
        self.hide()
        QTimer.singleShot(400, self._show_crop_selector)

    def _show_crop_selector(self):
        try:
            # Main window is hidden, so the previous foreground application becomes active.
            region = get_foreground_client_region()
        except Exception as exc:
            self.show_main_window()
            self.status_label.setText("状态：无法打开范围框选")
            QMessageBox.warning(
                self, "无法设置录制范围",
                f"{exc}\n\n请先打开要录制的软件窗口，再点击“框选要保留的内容”。"
            )
            return

        self.crop_selector = CropSelector(region)
        self.crop_selector.crop_selected.connect(self._on_crop_selected)
        self.crop_selector.cancelled.connect(self._on_crop_cancelled)
        self.crop_selector.show()

    def _on_crop_selected(self, crop_rect):
        x, y, w, h = crop_rect
        self.settings.setValue("crop_x", float(x))
        self.settings.setValue("crop_y", float(y))
        self.settings.setValue("crop_w", float(w))
        self.settings.setValue("crop_h", float(h))
        if self.crop_selector is not None:
            self.crop_selector.deleteLater()
            self.crop_selector = None
        self.crop_info_label.setText(self._crop_description())
        self.status_label.setText("状态：录制范围已保存，下次会自动沿用")
        self.show_main_window()

    def _on_crop_cancelled(self, message):
        if self.crop_selector is not None:
            self.crop_selector.deleteLater()
            self.crop_selector = None
        self.status_label.setText(f"状态：{message}")
        self.show_main_window()

    def reset_crop_selection(self):
        self.settings.setValue("crop_x", 0.0)
        self.settings.setValue("crop_y", 0.0)
        self.settings.setValue("crop_w", 1.0)
        self.settings.setValue("crop_h", 1.0)
        self.crop_info_label.setText(self._crop_description())
        self.status_label.setText("状态：已恢复完整录制范围")

    def start_recording_by_options(self):
        """预设区域优先；否则按固定显示器或鼠标跟随模式开始录制。"""
        if self._recording_is_active():
            return

        preset_name = str(self.region_preset_combo.currentData() or "")
        if preset_name:
            region = self._region_presets.get(preset_name)
            if region is None:
                QMessageBox.warning(self, "区域预设不可用", "请重新选择或保存这个区域。")
                return
            self._start_recording(
                dict(region), f"预设_{preset_name}",
                follow_mouse=False, monitors=None,
                auto_window=False, crop_rect=(0.0, 0.0, 1.0, 1.0)
            )
            return

        if not self._monitor_list:
            self._refresh_monitor_choices()
        if not self._monitor_list:
            QMessageBox.warning(self, "没有显示器", "没有检测到可录制的显示器。")
            return

        if self.follow_mouse_checkbox.isChecked():
            self.start_follow_mouse(
                auto_window=self.auto_window_checkbox.isChecked()
            )
        else:
            self.start_fixed_monitor(
                self._selected_monitor_index(),
                auto_window=self.auto_window_checkbox.isChecked()
            )

    def start_selection(self):
        """手动框选一个区域，不保存为预设。"""
        if self._recording_is_active():
            return
        self._custom_selection_mode = False
        self.hide()
        QTimer.singleShot(250, self._show_selector)

    def _show_selector(self):
        self.selector = RegionSelector()
        self.selector.region_selected.connect(self._on_region_selected)
        self.selector.cancelled.connect(self._on_region_selection_cancelled)
        self.selector.show()

    def _on_region_selected(self, region):
        self.selector = None
        if self._custom_selection_mode:
            self._on_custom_region_selected(region)
            return
        self._start_recording(
            dict(region), "手动区域",
            follow_mouse=False, auto_window=False,
            crop_rect=(0.0, 0.0, 1.0, 1.0)
        )

    def _on_region_selection_cancelled(self, message):
        self.selector = None
        self._custom_selection_mode = False
        self.show_main_window()
        self.status_label.setText(f"状态：{message}")

    def start_fixed_monitor(self, monitor_index, auto_window=False):
        """录制选中的显示器；不随鼠标移到其他显示器而切换。"""
        if self._recording_is_active():
            return
        try:
            monitors = self._read_connected_monitors()
            if not monitors:
                raise RuntimeError("没有检测到可用显示器。")
            if not (0 <= int(monitor_index) < len(monitors)):
                raise RuntimeError("所选显示器已不存在，请点击“刷新”后重新选择。")
            monitor_index = int(monitor_index)
            monitor = monitors[monitor_index]
            crop_rect = self._crop_rect()
            region = get_monitor_capture_region(monitor, crop_rect)

            # If window recognition is enabled and the cursor is on this monitor,
            # start from the window beneath the cursor. The worker remains locked
            # to this monitor even when the cursor moves to another display.
            if auto_window:
                try:
                    mouse_x, mouse_y = get_cursor_position()
                    mx, my = int(monitor["left"]), int(monitor["top"])
                    mw, mh = int(monitor["width"]), int(monitor["height"])
                    if mx <= mouse_x < mx + mw and my <= mouse_y < my + mh:
                        candidate = get_window_client_region_at_point(
                            mouse_x, mouse_y, monitor, crop_rect
                        )
                        if candidate is not None:
                            region = candidate
                except Exception:
                    pass

            self.hide()
            QTimer.singleShot(
                250,
                lambda: self._start_recording(
                    region, f"显示器{monitor_index + 1}",
                    follow_mouse=False, monitors=monitors,
                    initial_monitor_index=monitor_index,
                    auto_window=auto_window, crop_rect=crop_rect,
                    fixed_monitor_index=monitor_index
                )
            )
        except Exception as exc:
            self.show_main_window()
            QMessageBox.critical(self, "无法启动录制", str(exc))

    def _build_tray(self):
        self.tray = None
        self.stop_action = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray = QSystemTrayIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self
        )
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()

        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_main_window)
        menu.addAction(show_action)

        self.stop_action = QAction("停止录制（F9）", self)
        self.stop_action.setEnabled(False)
        self.stop_action.triggered.connect(self.stop_recording)
        menu.addAction(self.stop_action)

        open_action = QAction("打开保存文件夹", self)
        open_action.triggered.connect(self.open_save_dir)
        menu.addAction(open_action)

        menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        menu.addAction(exit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_main_window()

    def _on_global_key(self, key):
        if key == keyboard.Key.f9:
            self.hotkey_signals.stop_requested.emit()

    def _recording_is_active(self):
        return self.recorder is not None and self.recorder.isRunning()

    def _set_record_buttons_enabled(self, enabled):
        self.auto_window_checkbox.setEnabled(enabled)
        self.follow_mouse_checkbox.setEnabled(enabled)
        self.crop_content_button.setEnabled(enabled)
        self.reset_crop_button.setEnabled(enabled)
        self.save_region_preset_button.setEnabled(enabled)
        self.delete_region_preset_button.setEnabled(enabled)
        self.region_preset_combo.setEnabled(enabled)
        self.monitor_refresh_button.setEnabled(enabled)
        for button in self.monitor_button_group.buttons():
            button.setEnabled(enabled)
        self.start_button.setEnabled(enabled)
        self.change_path_button.setEnabled(enabled)
        self.edit_video_button.setEnabled(enabled)
        self.cursor_style_combo.setEnabled(enabled)
        self.zoom_hold_spin.setEnabled(enabled)

    def start_auto_window(self):
        """隐藏本工具后，获取恢复到前台的目标应用客户区并立即录制。"""
        if self._recording_is_active():
            return
        self.status_label.setText("状态：正在识别当前窗口…")
        self.hide()
        # 给 Windows 时间将焦点切回先前的应用窗口。
        QTimer.singleShot(650, self._detect_active_window_and_start)

    def _detect_active_window_and_start(self):
        try:
            region = get_foreground_client_region(self._crop_rect())
        except Exception as exc:
            self.show_main_window()
            self.status_label.setText("状态：未能识别目标窗口")
            QMessageBox.warning(
                self, "自动识别失败",
                f"{exc}\n\n建议：先打开要录制的软件窗口，再返回本工具勾选“自动识别当前窗口”并点击“开始录制”。"
            )
            return
        self._start_recording(
            region, "窗口", auto_window=True, crop_rect=self._crop_rect()
        )

    def start_follow_mouse(self, auto_window=False):
        """鼠标决定当前显示器；可同时识别鼠标所在窗口并应用记忆裁切框。"""
        if self._recording_is_active():
            return
        if os.name != "nt":
            QMessageBox.warning(
                self, "仅支持 Windows", "跟随鼠标自动切换显示器目前仅支持 Windows。"
            )
            return

        try:
            monitors = self._read_connected_monitors()
            if not monitors:
                raise RuntimeError("没有检测到可用显示器。")
        except Exception as exc:
            QMessageBox.critical(self, "无法启动自动切屏录制", str(exc))
            return

        self.status_label.setText("状态：正在识别鼠标所在显示器…")
        self.hide()
        QTimer.singleShot(
            350,
            lambda: self._begin_follow_mouse_recording(
                monitors, bool(auto_window), self._crop_rect()
            )
        )

    def _begin_follow_mouse_recording(self, monitors, auto_window, crop_rect):
        try:
            mouse_x, mouse_y = get_cursor_position()
            initial_index = find_monitor_for_point(monitors, mouse_x, mouse_y)
            if initial_index is None:
                initial_index = 0
            monitor = monitors[initial_index]

            if auto_window:
                region = get_window_client_region_at_point(
                    mouse_x, mouse_y, monitor, crop_rect
                )
                if region is None:
                    region = get_monitor_capture_region(monitor, crop_rect)
                mode = "鼠标跟随窗口"
            else:
                region = get_monitor_capture_region(monitor, crop_rect)
                mode = "跟随鼠标"

            self._start_recording(
                region, mode, True, monitors, initial_index,
                auto_window=auto_window, crop_rect=crop_rect
            )
        except Exception as exc:
            self.show_main_window()
            self.status_label.setText("状态：未能识别鼠标所在屏幕")
            QMessageBox.critical(self, "无法启动录制", str(exc))

    def start_fullscreen_crop(self):
        # Backward-compatible entry point; visual selection replaces pixel prompts.
        self.start_crop_selection()

    def _start_recording(self, region, mode="区域", follow_mouse=False, monitors=None, initial_monitor_index=0, auto_window=False, crop_rect=None, fixed_monitor_index=None):
        if self.selector is not None:
            self.selector.deleteLater()
            self.selector = None

        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_mode = "".join(
                "_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch
                for ch in str(mode)
            ).strip(" .") or "录屏"
            output_path = self.save_dir / f"录屏_{safe_mode}_{stamp}.mp4"
            suffix = 1
            while output_path.exists():
                output_path = self.save_dir / f"录屏_{safe_mode}_{stamp}_{suffix}.mp4"
                suffix += 1
        except Exception as exc:
            self.show_main_window()
            QMessageBox.critical(self, "无法保存", f"无法使用保存目录：\n{exc}")
            return

        self._completed_path = None
        self._error_message = None
        self._discard_after_stop = False
        self._recording_output_path = str(output_path)
        self._recording_mode = mode
        self._reselect_after_stop = False
        self.status_label.setText(f"状态：正在录制{mode}，按 F9 停止")
        self._set_record_buttons_enabled(False)
        self.path_label.setText(str(self.save_dir))
        if self.stop_action is not None:
            self.stop_action.setEnabled(True)
        if self.tray is not None:
            self.tray.setToolTip(f"正在录制{mode}画面 · 按 F9 停止")

        # 先显示预览控制器，尽量放在未被录制的显示器上，便于及时发现选错屏幕。
        if self.control_window is not None:
            self.control_window.finish_and_close()
            self.control_window.deleteLater()
        self.control_window = RecordingControlWindow(
            region, mode,
            cursor_style=str(self.cursor_style_combo.currentText()),
            zoom_hold_seconds=float(self.zoom_hold_spin.value())
        )
        self.control_window.pause_requested.connect(self.toggle_pause_recording)
        self.control_window.reselect_requested.connect(self.reselect_region)
        self.control_window.save_requested.connect(self.stop_recording)
        self.control_window.discard_requested.connect(self.discard_recording)
        self._place_control_window(region)
        self.control_window.show()

        self.recorder = RecorderWorker(
            region, output_path, follow_mouse=follow_mouse,
            monitors=monitors, initial_monitor_index=initial_monitor_index,
            auto_window=auto_window, crop_rect=crop_rect or self._crop_rect(),
            fixed_monitor_index=fixed_monitor_index,
            cursor_style=str(self.cursor_style_combo.currentText()),
            zoom_hold_seconds=float(self.zoom_hold_spin.value())
        )
        self.recorder.preview_frame.connect(self._on_preview_frame)
        self.recorder.monitor_changed.connect(self._on_monitor_changed)
        self.recorder.file_saved.connect(self._on_file_saved)
        self.recorder.recording_error.connect(self._on_recording_error)
        self.recorder.finished.connect(self._on_worker_finished)
        self.recorder.start()

    def _place_control_window(self, region):
        if self.control_window is None:
            return

        panel_w, panel_h = 500, 520
        self.control_window.resize(panel_w, panel_h)
        region_rect = QRect(
            int(region["left"]), int(region["top"]),
            int(region["width"]), int(region["height"])
        )
        screens = QApplication.screens()
        candidates = []

        # 第一优先级：放到完全不与录制区域相交的显示器。
        for screen in screens:
            geometry = screen.availableGeometry()
            if not geometry.intersects(region_rect):
                candidates.append((screen, geometry))
        if candidates:
            screen, geometry = candidates[0]
            self.control_window.move(
                geometry.right() - panel_w - 18,
                geometry.top() + 18
            )
            return

        # 单屏或录制区域覆盖所有屏幕时，尝试放到选区外的角落。
        for screen in screens:
            geometry = screen.availableGeometry()
            positions = [
                (geometry.right() - panel_w - 18, geometry.top() + 18),
                (geometry.right() - panel_w - 18, geometry.bottom() - panel_h - 18),
                (geometry.left() + 18, geometry.top() + 18),
                (geometry.left() + 18, geometry.bottom() - panel_h - 18),
            ]
            for x, y in positions:
                panel_rect = QRect(x, y, panel_w, panel_h)
                if not panel_rect.intersects(region_rect):
                    self.control_window.move(x, y)
                    return

        # 若是单屏全屏录制，Windows 的捕获排除设置会被尝试启用；
        # 如果环境不支持，用户仍可把面板拖到不录制的区域或另一台显示器。
        screen = QApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            self.control_window.move(
                geometry.right() - panel_w - 18,
                geometry.top() + 18
            )

    def _on_monitor_changed(self, index, left, top, width, height, output_width, output_height, target_kind):
        if self.control_window is not None:
            self.control_window.set_active_monitor(
                index, left, top, width, height, output_width, output_height, target_kind
            )
            new_capture_rect = QRect(left, top, width, height)
            if self.control_window.frameGeometry().intersects(new_capture_rect):
                self._place_control_window({
                    "left": left, "top": top, "width": width, "height": height
                })
        self.status_label.setText(f"状态：已切换到显示器 {index} / {target_kind}")

    def _sync_control_exclusion_rect(self):
        if self.recorder is None:
            return
        if self.control_window is None or not self.control_window.isVisible():
            self.recorder.set_control_rect(None)
            return
        rect = self.control_window.frameGeometry()
        self.recorder.set_control_rect(
            (rect.left(), rect.top(), rect.right() + 1, rect.bottom() + 1)
        )

    def _on_preview_frame(self, image):
        if self.control_window is not None:
            self.control_window.set_preview(image)

    def toggle_pause_recording(self):
        if self.recorder is None or not self.recorder.isRunning():
            return
        if self.recorder.is_paused():
            self.recorder.resume()
            if self.control_window is not None:
                self.control_window.set_paused(False)
            self.status_label.setText(f"状态：正在录制{self._recording_mode}")
        else:
            self.recorder.pause()
            if self.control_window is not None:
                self.control_window.set_paused(True)
            self.status_label.setText("状态：录制已暂停")

    def reselect_region(self):
        if self.recorder is not None and self.recorder.isRunning():
            self._reselect_after_stop = True
            if self.control_window is not None:
                self.control_window.set_message("正在保存当前片段，随后重新选择区域…")
            self.status_label.setText("状态：正在保存当前片段…")
            self.recorder.stop()
        else:
            self.start_selection()

    def _on_file_saved(self, path):
        self._completed_path = path

    def _on_recording_error(self, message):
        self._error_message = message

    def stop_recording(self):
        if self.recorder is not None and self.recorder.isRunning():
            self.status_label.setText("状态：正在停止并保存视频…")
            if self.stop_action is not None:
                self.stop_action.setEnabled(False)
            if self.control_window is not None:
                self.control_window.set_message("正在停止并保存…")
            self.recorder.stop()

    def discard_recording(self):
        """Stop recording and discard the current file without a modal confirmation dialog."""
        if self.recorder is None:
            self.status_label.setText("状态：当前没有正在录制的视频")
            return

        if not self.recorder.isRunning():
            # The worker may have finished while its finished signal is still queued.
            # Let Qt deliver that signal instead of blocking the UI waiting for it.
            self._discard_after_stop = True
            self._reselect_after_stop = False
            self.status_label.setText("状态：正在清理本次视频…")
            QTimer.singleShot(0, self._finalize_discard_if_worker_stopped)
            return

        self._discard_after_stop = True
        self._reselect_after_stop = False
        self.status_label.setText("状态：正在停止并删除本次视频…")
        if self.stop_action is not None:
            self.stop_action.setEnabled(False)
        if self.control_window is not None:
            self.control_window.set_message("正在停止并删除本次视频，请稍候…")
        self.recorder.stop()

    def _finalize_discard_if_worker_stopped(self):
        """Fallback for a click that arrives just after the recording thread exits."""
        if self.recorder is None:
            return
        if self.recorder.isRunning():
            return
        # Usually the QThread.finished signal will run _on_worker_finished.
        # This fallback prevents the discard path from depending on signal timing.
        self._on_worker_finished()

    def _on_worker_finished(self):
        # QThread.finished may already be queued when the fallback finalizer runs.
        if self.recorder is None:
            return
        worker = self.recorder
        self.recorder = None
        if worker is not None:
            worker.deleteLater()

        if self.control_window is not None:
            self.control_window.finish_and_close()
            self.control_window.deleteLater()
            self.control_window = None

        if self.stop_action is not None:
            self.stop_action.setEnabled(False)
        if self.tray is not None:
            self.tray.setToolTip(APP_NAME)

        self._set_record_buttons_enabled(True)

        if self._discard_after_stop:
            self._discard_after_stop = False
            self._reselect_after_stop = False
            discard_path = self._recording_output_path or self._completed_path
            deletion_error = None
            if discard_path:
                try:
                    Path(discard_path).unlink(missing_ok=True)
                except Exception as exc:
                    deletion_error = str(exc)
            self._completed_path = None
            self._recording_output_path = None
            self.show_main_window()
            if deletion_error:
                self.status_label.setText("状态：停止完成，但临时视频删除失败")
                QMessageBox.warning(
                    self, "删除失败",
                    f"本次录制已停止，但文件未能删除：\n{discard_path}\n\n{deletion_error}"
                )
            else:
                self.status_label.setText("状态：已停止，本次视频未保存")
                if self.tray is not None:
                    self.tray.showMessage(
                        APP_NAME, "本次录制已丢弃，没有保留视频文件。",
                        QSystemTrayIcon.MessageIcon.Information, 3000
                    )
            if self._exit_after_recording:
                self._shutdown()
                QApplication.quit()
            return

        if self._reselect_after_stop and not self._error_message:
            self._reselect_after_stop = False
            self.status_label.setText("状态：上一段已保存，请重新选择录制区域")
            if self._completed_path and self.tray is not None:
                self.tray.showMessage(
                    APP_NAME,
                    "当前片段已保存。请重新框选区域；新区域将录制为另一个 MP4 文件。",
                    QSystemTrayIcon.MessageIcon.Information,
                    3500
                )
            self.hide()
            QTimer.singleShot(250, self._show_selector)
            return

        self._reselect_after_stop = False
        self._recording_output_path = None
        self.show_main_window()

        if self._error_message:
            self.status_label.setText("状态：录制失败")
            QMessageBox.critical(
                self, "录制失败",
                f"{self._error_message}\n\n请检查保存路径、磁盘空间和系统权限。"
            )
        elif self._completed_path:
            completed_path = str(self._completed_path)
            self.status_label.setText("状态：录制完成，可剪辑后导出最终视频")
            self.path_label.setText(completed_path)
            if self.tray is not None:
                self.tray.showMessage(
                    APP_NAME,
                    f"录制完成，正在打开剪辑界面：{completed_path}",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000
                )
            if not self._exit_after_recording:
                QTimer.singleShot(
                    180, lambda path=completed_path: self.open_video_editor(path)
                )
        else:
            self.status_label.setText("状态：录制已结束")

        if self._exit_after_recording:
            self._shutdown()
            QApplication.quit()

    def choose_video_to_edit(self):
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择要剪辑的视频",
            str(self.save_dir),
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*.*)"
        )
        if selected:
            self.open_video_editor(selected)

    def _forget_video_editor(self, editor):
        if self.video_editor is editor:
            self.video_editor = None

    def open_video_editor(self, video_path):
        if not video_path or not Path(video_path).exists():
            QMessageBox.warning(self, "找不到视频", f"视频文件不存在：\n{video_path}")
            return

        if self.video_editor is not None:
            try:
                if self.video_editor.isVisible():
                    if os.path.normcase(os.path.abspath(self.video_editor.source_path)) == os.path.normcase(os.path.abspath(video_path)):
                        self.video_editor.showNormal()
                        self.video_editor.raise_()
                        self.video_editor.activateWindow()
                        return
                    if self.video_editor.export_worker is not None and self.video_editor.export_worker.isRunning():
                        QMessageBox.information(
                            self, "正在导出",
                            "当前剪辑窗口正在导出视频，请完成后再打开另一个文件。"
                        )
                        self.video_editor.raise_()
                        return
                    self.video_editor.close()
                    self.video_editor.deleteLater()
            except RuntimeError:
                pass
            self.video_editor = None

        try:
            editor = VideoEditorWindow(video_path, self)
        except Exception as exc:
            QMessageBox.critical(self, "无法打开剪辑界面", str(exc))
            return

        self.video_editor = editor
        editor.destroyed.connect(
            lambda *_args, ed=editor: self._forget_video_editor(ed)
        )
        editor.show()
        editor.raise_()
        editor.activateWindow()

    def choose_save_dir(self):
        selected = QFileDialog.getExistingDirectory(
            self, "选择视频保存目录", str(self.save_dir)
        )
        if selected:
            self.save_dir = Path(selected)
            self.settings.setValue("save_dir", str(self.save_dir))
            self.path_label.setText(str(self.save_dir))

    def open_save_dir(self):
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(self.save_dir))
            else:
                QMessageBox.information(
                    self, "保存目录", f"视频保存目录：\n{self.save_dir}"
                )
        except Exception as exc:
            QMessageBox.warning(self, "无法打开目录", str(exc))

    def show_main_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _shutdown(self):
        if self.hotkey_listener is not None:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass
            self.hotkey_listener = None
        if self.tray is not None:
            self.tray.hide()

    def closeEvent(self, event):
        if self.recorder is not None and self.recorder.isRunning():
            reply = QMessageBox.question(
                self,
                "正在录制",
                "录制正在进行。停止录制并退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._exit_after_recording = True
                self.stop_recording()
            event.ignore()
            return

        if self.selector is not None:
            self.selector.hide()
            self.selector.deleteLater()
            self.selector = None
        self._shutdown()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(True)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
