# -*- coding: utf-8 -*-
"""Windows 区域录屏工具：框选区域后自动录制画面，按 F9 停止并保存 MP4。"""

import ctypes
import os
import sys
import json
import time
import threading
import tempfile
import shutil
import queue
import uuid
from timeline import total_frames as edited_frame_count, output_to_source, source_to_output, trim_to_output_range
from recording_clock import RecordingClock
from recording_resample import RecordingDiscarded
from realtime_encoder import RealtimeEncoder
from hotkeys import HotkeyController
from script_document import ScriptDocument
from teleprompter import TeleprompterWindow, restore_visible_geometry
from capture_guard import enable_exclusion, verify_window_exclusion
from window_capture import window_region
from window_picker import WindowPicker
from project_store import create_project, read_project, save_project
from handoff import HandoffWorker
from annotations import render_annotations, crop_frame
from presentation import render_frame, validate_options, frame_size
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
from pynput import keyboard
from PySide6.QtCore import Qt, QRect, QSize, QPoint, QTimer, QEvent, Signal, QObject, QThread
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QAction, QImage, QPixmap, QCursor, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QFileDialog, QMessageBox, QSystemTrayIcon, QMenu, QStyle,
    QFrame, QInputDialog, QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox, QRadioButton, QButtonGroup,
    QSlider, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QAbstractItemView, QPlainTextEdit, QScrollArea, QProgressDialog
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
    effect_requested = Signal(str)


def _screen_pairs():
    """Match Qt logical screen geometries to MSS physical monitor rectangles."""
    with mss.mss() as capture:
        monitors = [dict(m) for m in capture.monitors[1:]]
    pairs = []
    for screen in QApplication.screens():
        g = screen.geometry()
        ratio = screen.devicePixelRatio()
        if not monitors:
            break
        monitor = min(monitors, key=lambda m: (
            abs(m["left"] - g.left()) + abs(m["top"] - g.top()) +
            abs(m["width"] - g.width() * ratio) + abs(m["height"] - g.height() * ratio)))
        monitors.remove(monitor)
        pairs.append((g, monitor))
    return pairs


def logical_rect_to_physical(rect, pairs):
    """Convert each screen intersection separately, including mixed-DPI desktops."""
    parts = []
    for geometry, monitor in pairs:
        part = rect.intersected(geometry)
        if part.isEmpty():
            continue
        sx = monitor["width"] / geometry.width()
        sy = monitor["height"] / geometry.height()
        left = monitor["left"] + round((part.left() - geometry.left()) * sx)
        top = monitor["top"] + round((part.top() - geometry.top()) * sy)
        right = monitor["left"] + round((part.left() + part.width() - geometry.left()) * sx)
        bottom = monitor["top"] + round((part.top() + part.height() - geometry.top()) * sy)
        parts.append((left, top, right, bottom))
    if not parts:
        raise ValueError("所选区域不在显示器内")
    left = min(p[0] for p in parts); top = min(p[1] for p in parts)
    right = max(p[2] for p in parts); bottom = max(p[3] for p in parts)
    return {"left": left, "top": top, "width": right-left, "height": bottom-top}


def physical_rect_to_logical(region, pairs):
    physical = QRect(region["left"], region["top"], region["width"], region["height"])
    parts = []
    for g, m in pairs:
        part = physical.intersected(QRect(m["left"], m["top"], m["width"], m["height"]))
        if part.isEmpty():
            continue
        sx, sy = g.width() / m["width"], g.height() / m["height"]
        x = g.left() + round((part.left() - m["left"]) * sx)
        y = g.top() + round((part.top() - m["top"]) * sy)
        right = g.left() + round((part.left() + part.width() - m["left"]) * sx)
        bottom = g.top() + round((part.top() + part.height() - m["top"]) * sy)
        parts.append(QRect(x, y, right-x, bottom-y))
    if not parts:
        raise ValueError("目标区域不在显示器内")
    result = parts[0]
    for part in parts[1:]:
        result = result.united(part)
    return result


class RegionSelector(QWidget):
    """Interactive selection overlay; coordinates are converted on confirmation."""
    region_selected = Signal(object)
    cancelled = Signal(str)

    def __init__(self, geometry=None):
        super().__init__()
        self._pairs = _screen_pairs()
        if geometry is None:
            screens = QApplication.screens()
            geometry = screens[0].geometry()
            for screen in screens[1:]:
                geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        cursor = QPixmap(31, 31)
        cursor.fill(Qt.GlobalColor.transparent)
        painter = QPainter(cursor)
        painter.setPen(QPen(QColor('black'), 5))
        painter.drawLine(15, 1, 15, 29); painter.drawLine(1, 15, 29, 15)
        painter.setPen(QPen(QColor('white'), 2))
        painter.drawLine(15, 1, 15, 29); painter.drawLine(1, 15, 29, 15)
        painter.end()
        self.setCursor(QCursor(cursor, 15, 15))
        self.selection = None
        self._action = None
        self._finished = False
        self.confirm_button = QPushButton("保存选区", self)
        self.confirm_button.setStyleSheet("background:#1677ff;color:white;border:2px solid white;border-radius:5px;padding:6px 14px;font-weight:bold;")
        self.confirm_button.clicked.connect(self.confirm_selection)
        self.confirm_button.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_(); self.activateWindow(); self.setFocus()

    def _position_button(self):
        if self.selection is None:
            self.confirm_button.hide(); return
        self.confirm_button.adjustSize()
        x = min(max(0, self.selection.right() - self.confirm_button.width()), max(0, self.width()-self.confirm_button.width()))
        y = self.selection.bottom() + 10
        if y + self.confirm_button.height() > self.height():
            y = max(0, self.selection.top() - self.confirm_button.height() - 10)
        self.confirm_button.move(x, y)
        self.confirm_button.show()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        shade = QPainterPath(); shade.setFillRule(Qt.FillRule.OddEvenFill); shade.addRect(self.rect())
        if self.selection is not None:
            shade.addRect(self.selection)
        p.fillPath(shade, QColor(0, 0, 0, 135))
        p.setPen(QColor('white'))
        p.drawText(QRect(0, 10, self.width(), 35), Qt.AlignmentFlag.AlignCenter,
                   "拖动框选 · 拖动内部移动 · 拖动边缘或角调整 · 点击保存选区或按 Enter · Esc 取消")
        if self.selection is not None:
            r = self.selection
            p.setPen(QPen(QColor('black'), 5)); p.drawRect(r)
            p.setPen(QPen(QColor('#00e89d'), 2)); p.drawRect(r)
            for x in (r.left(), r.center().x(), r.right()):
                for y in (r.top(), r.center().y(), r.bottom()):
                    if x == r.center().x() and y == r.center().y(): continue
                    p.setBrush(QColor('white')); p.setPen(QPen(QColor('#006b50'), 2))
                    p.drawRect(x-4, y-4, 8, 8)
            p.fillRect(QRect(r.left(), max(48, r.top()-26), 165, 24), QColor(0,0,0,200))
            p.setPen(QColor('white'))
            p.drawText(QRect(r.left()+6, max(48, r.top()-26), 155, 24), Qt.AlignmentFlag.AlignVCenter, f"{r.width()} × {r.height()}")

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton: return
        pos = event.position().toPoint()
        self._origin = pos
        self._initial = QRect(self.selection) if self.selection is not None else None
        self._edges = ''
        if self.selection is not None:
            r = self.selection; margin = 9
            if r.top()-margin <= pos.y() <= r.bottom()+margin:
                if abs(pos.x()-r.left()) <= margin: self._edges += 'l'
                elif abs(pos.x()-r.right()) <= margin: self._edges += 'r'
            if r.left()-margin <= pos.x() <= r.right()+margin:
                if abs(pos.y()-r.top()) <= margin: self._edges += 't'
                elif abs(pos.y()-r.bottom()) <= margin: self._edges += 'b'
            if self._edges: self._action = 'resize'
            elif r.contains(pos): self._action = 'move'
            else: self._action = 'draw'
        else: self._action = 'draw'
        if self._action == 'draw': self.selection = QRect(pos, pos)
        self.confirm_button.hide(); self.update()

    def mouseMoveEvent(self, event):
        if self._action is None: return
        pos = event.position().toPoint()
        pos.setX(min(max(pos.x(), 0), self.width()-1)); pos.setY(min(max(pos.y(), 0), self.height()-1))
        if self._action == 'draw': self.selection = QRect(self._origin, pos).normalized()
        elif self._action == 'move':
            r = QRect(self._initial); delta = pos-self._origin
            r.moveLeft(min(max(r.left()+delta.x(), 0), self.width()-r.width()))
            r.moveTop(min(max(r.top()+delta.y(), 0), self.height()-r.height()))
            self.selection = r
        else:
            r = QRect(self._initial)
            if 'l' in self._edges: r.setLeft(min(pos.x(), r.right()-19))
            if 'r' in self._edges: r.setRight(max(pos.x(), r.left()+19))
            if 't' in self._edges: r.setTop(min(pos.y(), r.bottom()-19))
            if 'b' in self._edges: r.setBottom(max(pos.y(), r.top()+19))
            self.selection = r
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._action is None: return
        self.mouseMoveEvent(event); self._action = None
        if self.selection.width() < 20 or self.selection.height() < 20:
            self.selection = None
        self._position_button(); self.update()

    def confirm_selection(self):
        if self.selection is None or self._finished: return
        global_rect = QRect(self.mapToGlobal(self.selection.topLeft()), self.selection.size())
        try: region = logical_rect_to_physical(global_rect, self._pairs)
        except ValueError: return
        self._finished = True; self.hide(); self.region_selected.emit(region)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if not self._finished:
                self._finished = True; self.hide(); self.cancelled.emit("已取消框选。")
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter): self.confirm_selection()
        else: super().keyPressEvent(event)


class CropSelector(RegionSelector):
    crop_selected = Signal(object)

    def __init__(self, region):
        self.region = dict(region)
        super().__init__(physical_rect_to_logical(region, _screen_pairs()))
        self.region_selected.connect(self._emit_crop)

    def _emit_crop(self, selected):
        region = self.region
        x = max(0.0, (selected['left'] - region['left']) / region['width'])
        y = max(0.0, (selected['top'] - region['top']) / region['height'])
        w = min(1.0-x, selected['width'] / region['width'])
        h = min(1.0-y, selected['height'] / region['height'])
        self.crop_selected.emit((x, y, w, h))


class RecorderWorker(QThread):
    file_saved = Signal(str)
    recording_error = Signal(str)
    preview_frame = Signal(QImage)
    monitor_changed = Signal(int, int, int, int, int, int, int, str)
    window_region_changed = Signal(int, int, int, int)

    def __init__(
        self, region, output_path, fps=FPS, follow_mouse=False,
        monitors=None, initial_monitor_index=0, auto_window=False,
        crop_rect=None, fixed_monitor_index=None,
        cursor_style="箭头", zoom_hold_seconds=3.0, window_target=None, window_monitor=None,
        zoom_factor=2.0, highlight_style="黄色圆环"
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
        self.window_target = window_target
        self.window_monitor = dict(window_monitor) if window_monitor is not None else None
        self.cursor_style = str(cursor_style or "箭头")
        self.zoom_hold_seconds = max(0.5, min(30.0, float(zoom_hold_seconds)))
        self._stop_event = threading.Event()
        self._discard_event = threading.Event()
        self._paused_event = threading.Event()
        self._clock = RecordingClock()
        self.diagnostics = {}
        self._control_rect_lock = threading.Lock()
        self._control_rects = []
        self._effect_events = queue.Queue(maxsize=64)
        self._highlight = None
        # “停留时长”不含推进/拉远动画；两段动画各自单独计时。
        self.zoom_factor = max(1.0, min(3.0, float(zoom_factor)))
        self.highlight_style = str(highlight_style)
        self.zoom_transition_seconds = 0.35
        self._zoom_start_time = None
        self._zoom_until = 0.0
        self._zoom_global_point = None
        self._target_kind = "启动中"

    def stop(self):
        self._clock.stop()
        self._stop_event.set()

    def discard(self):
        self._discard_event.set()
        self.stop()

    def pause(self):
        self._clock.pause()
        self._paused_event.set()

    def resume(self):
        self._clock.resume()
        self._paused_event.clear()

    def is_paused(self):
        return self._paused_event.is_set()

    def set_control_rect(self, rect):
        with self._control_rect_lock:
            self._control_rects = [tuple(rect)] if rect is not None else []

    def set_control_rects(self, rects):
        with self._control_rect_lock:
            self._control_rects = [tuple(rect) for rect in rects]

    def _cursor_over_control_panel(self, x, y):
        with self._control_rect_lock:
            rects = tuple(self._control_rects)
        return any(left <= x < right and top <= y < bottom for left, top, right, bottom in rects)

    def request_effect(self, kind, x, y):
        timestamp = self._clock.elapsed_ns(only_when_active=True)
        if kind not in ("c", "v") or timestamp is None or self._cursor_over_control_panel(x, y):
            return
        try:
            self._effect_events.put_nowait((kind, timestamp / 1_000_000_000, int(x), int(y)))
        except queue.Full:
            pass

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
        if self._zoom_start_time is None:
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

    def _draw_highlight(self, frame, x, y, elapsed):
        colors = {"黄色圆环": (0, 220, 255), "蓝色圆环": (255, 130, 30),
                  "红色圆环": (40, 40, 255), "黄色光圈": (0, 220, 255)}
        overlay = frame.copy()
        color = colors.get(self.highlight_style, colors["黄色圆环"])
        filled = self.highlight_style == "黄色光圈"
        cv2.circle(overlay, (int(x), int(y)), 30, color, -1 if filled else 4, cv2.LINE_AA)
        alpha = (0.45 if filled else 0.9) * max(0.0, 1.0 - elapsed / 1.5)
        return cv2.addWeighted(overlay, alpha, frame, 1.0 - alpha, 0)

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
        encoder = None
        frames_written = 0
        temp_dir = None
        keep_temp = False
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

            temp_dir = tempfile.mkdtemp(prefix="vcw-recording-", dir=str(Path(self.output_path).parent))
            final_path = str(Path(temp_dir) / "final.mp4")
            encoder = RealtimeEncoder(final_path, output_width, output_height, self.fps,
                                      self._clock, self._stop_event, self._discard_event)
            encoder.start()

            frame_period = 1.0 / float(self.fps)
            next_frame_time = time.perf_counter()
            current_monitor_index = self.current_monitor_index
            current_key = (
                capture_region["left"], capture_region["top"],
                capture_region["width"], capture_region["height"],
                current_monitor_index, "启动中"
            )
            last_cursor = (0, 0)

            with mss.mss() as screen_capture:
                self._clock.start()
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
                    if self.window_target is not None:
                        candidate_region = apply_normalized_crop(window_region(self.window_target, self.window_monitor), self.crop_rect)
                        if candidate_region != capture_region:
                            capture_region = candidate_region
                            self.window_region_changed.emit(capture_region["left"], capture_region["top"],
                                                            capture_region["width"], capture_region["height"])
                    elif self.monitors and cursor_x is not None and not over_control:
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
                                        cursor_window_region = get_window_client_region_at_point(
                                            cursor_x, cursor_y, fixed_monitor, self.crop_rect
                                        )
                                        if cursor_window_region is not None:
                                            resolved = (
                                                fixed_index, cursor_window_region, "窗口内容区"
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

                    while True:
                        try:
                            kind, event_time, event_x, event_y = self._effect_events.get_nowait()
                        except queue.Empty:
                            break
                        if not (capture_region["left"] <= event_x < capture_region["left"] + capture_region["width"] and
                                capture_region["top"] <= event_y < capture_region["top"] + capture_region["height"]):
                            continue
                        if kind == "v":
                            self._zoom_start_time = event_time
                            self._zoom_until = event_time + self.zoom_hold_seconds + 2.0 * self.zoom_transition_seconds
                            self._zoom_global_point = (event_x, event_y)
                        else:
                            self._highlight = (event_time, event_x, event_y)

                    screenshot = screen_capture.grab(capture_region)
                    captured_at = self._clock.elapsed_ns(only_when_active=True)
                    if captured_at is None:
                        continue
                    raw_frame = np.asarray(screenshot)
                    raw_frame = cv2.cvtColor(raw_frame, cv2.COLOR_BGRA2BGR)

                    now = captured_at / 1_000_000_000
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
                            self._zoom_start_time = None
                            self._zoom_until = 0.0

                    if self._highlight is not None:
                        start, hx, hy = self._highlight
                        elapsed = max(0.0, now - start)
                        if elapsed >= 1.5:
                            self._highlight = None
                        else:
                            px, py = self._map_cursor_for_zoom(hx, hy, capture_region, raw_frame.shape, zoom_factor)
                            raw_frame = self._draw_highlight(raw_frame, px, py, elapsed)


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
                    encoder.submit(frame)
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

            self._clock.stop()
            encoded_frames = encoder.finish()
            self.diagnostics = {"captured_frames": frames_written, "encoded_frames": encoded_frames,
                                "duplicate_frames": encoder.duplicate_frames,
                                "duration_seconds": self._clock.elapsed_ns() / 1_000_000_000}
            encoder = None
            if self._discard_event.is_set():
                raise RecordingDiscarded()
            duration_ns = self._clock.elapsed_ns()
            if frames_written <= 0 or encoded_frames <= 0 or not os.path.exists(final_path):
                raise RuntimeError("没有录制到有效画面。")
            if os.path.getsize(final_path) <= 0:
                raise RuntimeError("视频文件为空，未能完成导出。")
            cap = cv2.VideoCapture(final_path)
            try:
                expected_frames = max(1, round(duration_ns * self.fps / 1_000_000_000))
                actual_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if cap.isOpened() else 0
                ok, _ = cap.read() if cap.isOpened() else (False, None)
                if not ok or actual_frames != expected_frames:
                    raise RuntimeError("成片校验失败：文件不可播放或输出帧数不完整。")
            finally:
                cap.release()
            if self._discard_event.is_set():
                raise RecordingDiscarded()
            if not os.path.exists(final_path) or os.path.getsize(final_path) <= 0:
                raise RuntimeError("录屏成片生成失败。")
            os.replace(final_path, self.output_path)
            self.file_saved.emit(self.output_path)

        except RecordingDiscarded:
            pass
        except Exception as exc:
            keep_temp = temp_dir is not None
            detail = str(exc)
            if keep_temp:
                try:
                    with open(Path(temp_dir) / "recovery.json", "w", encoding="utf-8") as recovery:
                        json.dump({"fps": self.fps, "duration_ns": self._clock.elapsed_ns()}, recovery)
                except OSError:
                    pass
                detail += f"\n临时素材保存在：{temp_dir}"
            self.recording_error.emit(detail)
        finally:
            if encoder is not None:
                try:
                    self._clock.stop()
                    encoder.finish()
                except Exception:
                    pass
            if temp_dir is not None and not keep_temp:
                shutil.rmtree(temp_dir, ignore_errors=True)


class RecordingControlWindow(QWidget):
    """独立的实时预览和录制控制面板。"""
    pause_requested = Signal()
    reselect_requested = Signal()
    save_requested = Signal()
    discard_requested = Signal()
    prompter_toggle_requested = Signal()

    def __init__(self, region, mode, cursor_style="箭头", zoom_hold_seconds=3.0, settings=None):
        super().__init__(
            None,
            Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
        )
        self._finished = False
        self._settings = settings
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
        self.timer_label = QLabel("00:00:00")
        header_row.addWidget(self.timer_label)
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

        self.effects_checkbox = QCheckBox("启用 C 高亮 / V 放大")
        self.effects_checkbox.setChecked(True)
        self.effects_checkbox.setToolTip("输入文字时建议关闭；不会拦截目标程序接收按键。")
        layout.addWidget(self.effects_checkbox)
        self.prompter_button = QPushButton("显示/隐藏提词框")
        self.prompter_button.clicked.connect(self.prompter_toggle_requested.emit)
        layout.addWidget(self.prompter_button)

        foot = QLabel(
            f"C 高亮 · V 放大（停留 {zoom_hold_seconds:g} 秒）· F9 停止并保存"
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

    def set_elapsed(self, seconds):
        total = max(0, int(seconds))
        self.timer_label.setText(f"{total // 3600:02d}:{(total // 60) % 60:02d}:{total % 60:02d}")

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
        if self._settings is not None and self.isVisible():
            self._settings.setValue("control_geometry", self.saveGeometry())
        self._refresh_preview()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._settings is not None and self.isVisible():
            self._settings.setValue("control_geometry", self.saveGeometry())

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

    def __init__(self, source_path, segments, output_path, annotations=None, crop=None, presentation=None):
        super().__init__()
        self.source_path = str(source_path)
        self.segments = [
            {"start": int(seg["start"]), "end": int(seg["end"])}
            for seg in segments
        ]
        self.output_path = str(output_path)
        self.annotations = [dict(a) for a in (annotations or [])]
        self.crop = crop
        self.presentation = validate_options(presentation)

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

            output_height, output_width = render_frame(np.zeros((height, width, 3), dtype=np.uint8), 0, [], self.crop, self.presentation).shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(
                str(temp_path), fourcc, fps, (output_width, output_height)
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
                    writer.write(render_frame(frame, frame_index, self.annotations, self.crop, self.presentation))
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


class ProjectCreateWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, parent_dir, source, width, height, fps, frame_count, segments, script, annotations=None, crop=None, presentation=None):
        super().__init__()
        self.arguments = (parent_dir, source, width, height, fps, frame_count, segments, script, annotations, crop, presentation)

    def run(self):
        try:
            self.succeeded.emit(str(create_project(*self.arguments)))
        except Exception as exc:
            self.failed.emit(str(exc))


class VideoEditorWindow(QMainWindow):
    """Simple frame-based editor: split the source into ranges, delete ranges, then export."""
    def __init__(self, source_path, parent=None):
        super().__init__(parent)
        self.workbench = parent
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
        self.project_worker = None
        self.project_path = None
        self._saved_signature = None
        self._loading_project = False
        self.undo_stack = []
        self.segments = [{"start": 0, "end": self.total_frames}]
        self.annotations = []
        self.video_crop = (0.0, 0.0, 1.0, 1.0)
        self.presentation = validate_options(None)
        self.pending_crop = None
        self.crop_mode = False

        self.setWindowTitle(f"视频剪辑 - {self.source.name}")
        self.resize(1120, 790)
        self.setMinimumSize(900, 650)
        self._build_editor_ui()
        QApplication.instance().installEventFilter(self)
        self.project_timer = QTimer(self)
        self.project_timer.setSingleShot(True)
        self.project_timer.setInterval(700)
        self.project_timer.timeout.connect(self._autosave_project)
        self.script_input.textChanged.connect(self._on_project_changed)
        self._refresh_annotations()
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

    def has_unsaved_edits(self):
        if self.project_path is not None:
            return self._project_signature() != self._saved_signature
        return self.presentation != validate_options(None) or bool(self.annotations) or self.video_crop != (0.0, 0.0, 1.0, 1.0) or self.segments != [{"start": 0, "end": self.total_frames}]

    def _project_signature(self):
        return (tuple((segment["start"], segment["end"]) for segment in self.segments),
                self.script_input.toPlainText(), json.dumps(self.annotations, sort_keys=True, ensure_ascii=False), self.video_crop, json.dumps(self.presentation, sort_keys=True))

    def _on_project_changed(self):
        if self.project_path is not None and not self._loading_project and self._project_signature() != self._saved_signature:
            self.project_timer.start()
            self.project_state_label.setText("工程有未保存的修改…")

    def _autosave_project(self):
        self._save_existing_project(show_error=False)

    def _save_existing_project(self, show_error=True):
        if self.project_path is None:
            return False
        self.project_timer.stop()
        try:
            save_project(self.project_path, self.segments, self.script_input.toPlainText(), self.fps, self.annotations, self.video_crop, self.presentation)
            self._saved_signature = self._project_signature()
            self.project_state_label.setText("工程已保存")
            return True
        except Exception as exc:
            self.project_state_label.setText(f"工程保存失败：{exc}")
            if show_error:
                QMessageBox.warning(self, "工程保存失败", str(exc))
            return False

    def flush_project(self):
        if self.project_path is None or not self.has_unsaved_edits():
            return True
        return self._save_existing_project()

    def save_current_project(self):
        if self.project_worker is not None:
            return
        if self.export_worker is not None and self.export_worker.isRunning():
            QMessageBox.information(self, "正在导出", "请等待导出完成后再保存工程。")
            return
        if self.project_path is not None:
            self._save_existing_project()
            return
        directory = QFileDialog.getExistingDirectory(self, "选择新工程的父目录", str(self.source.parent))
        if not directory:
            return
        owner = self.workbench
        if owner is not None and hasattr(owner, "_save_draft"):
            owner.draft_timer.stop()
            if not owner._save_draft():
                return
        self._stop_playback()
        self._creation_signature = self._project_signature()
        self.project_worker = ProjectCreateWorker(directory, self.source_path, self.frame_width, self.frame_height,
                                                  self.fps, self.total_frames, [dict(s) for s in self.segments],
                                                  self.script_input.toPlainText(), [dict(a) for a in self.annotations], self.video_crop, dict(self.presentation))
        self.project_progress = QProgressDialog("正在复制原始视频并创建工程…", "", 0, 0, self)
        self.project_progress.setCancelButton(None)
        self.project_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.project_progress.setMinimumDuration(0)
        self.project_worker.succeeded.connect(self._project_created)
        self.project_worker.failed.connect(self._project_creation_failed)
        self.project_worker.finished.connect(self._project_creation_finished)
        self.project_progress.show()
        self.project_worker.start()

    def _project_created(self, path):
        try:
            project = read_project(path)
            replacement = cv2.VideoCapture(str(project["source"]))
            if not replacement.isOpened():
                replacement.release()
                raise RuntimeError("工程已创建，但无法打开复制后的原始视频。")
            self.cap.release()
            self.cap = replacement
            self.source_path = str(project["source"])
            self.source = project["source"]
            self.project_path = Path(path)
            self._saved_signature = self._creation_signature
            self.project_state_label.setText(f"工程已保存：{self.project_path.parent.name}")
            self.source_name_label.setText(self.source.name)
            self.source_name_label.setToolTip(self.source_path)
            self._show_frame_at(self.current_frame)
            self._on_project_changed()
            owner = self.workbench
            if owner is not None and hasattr(owner, "add_recent_project"):
                owner.add_recent_project(self.project_path)
        except Exception as exc:
            QMessageBox.warning(self, "工程已创建，但未能切换", f"{exc}\n工程位置：{path}")

    def _project_creation_failed(self, message):
        QMessageBox.warning(self, "工程创建失败", message)

    def _project_creation_finished(self):
        self.project_progress.close()
        self.project_progress.deleteLater()
        worker = self.project_worker
        self.project_worker = None
        if worker is not None:
            worker.deleteLater()

    def _build_editor_ui(self):
        from editor_ui import build_editor
        build_editor(self)

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
        if hasattr(self, "timeline_ruler"):
            self.timeline_ruler.set_duration(kept_duration)
        output_position = source_to_output(self.segments, self.current_frame)
        if output_position is None:
            output_position = min(self.timeline_slider.value(), max(0, kept_frames - 1))
        self.timeline_slider.setRange(0, max(0, kept_frames - 1))
        for control in (self.timeline_slider, self.play_button, self.previous_frame_button,
                        self.next_frame_button, self.jump_start_button):
            control.setEnabled(kept_frames > 0)
        if kept_frames:
            self._show_output_frame(output_position)
        else:
            self.time_label.setText("00:00:00.000 / 00:00:00.000")
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
        self.trim_start_button.setEnabled(bool(self.segments))
        self.trim_end_button.setEnabled(bool(self.segments))
        self.edited_stat.setText(f"剪辑后时长\n{self._format_time(kept_duration)}")
        self.count_stat.setText(f"当前保留片段\n{len(self.segments)} 段")
        if hasattr(self, "timeline_track"):
            self.timeline_track.set_state(self.segments, self.timeline_slider.value(), self._selected_row())
        if hasattr(self, "annotation_track"):
            self.annotation_track.set_state(self.segments, self.annotations, self.annotation_list.currentRow())
        if hasattr(self, "project_timer"):
            self._on_project_changed()

    def _snapshot_undo(self):
        self.undo_stack.append(([dict(seg) for seg in self.segments], [dict(a) for a in self.annotations], self.video_crop))
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)

    def _refresh_annotations(self, selected=-1):
        self.annotation_list.blockSignals(True)
        self.annotation_list.clear()
        names = {"text": "文字", "arrow": "箭头", "box": "矩形", "sticker": "贴纸", "stage": "阶段"}
        for annotation in self.annotations:
            self.annotation_list.addItem(f"{names[annotation['type']]} · {annotation['start'] / self.fps:.2f} 秒")
        self.annotation_list.setCurrentRow(selected if 0 <= selected < len(self.annotations) else -1)
        self.annotation_list.blockSignals(False)
        self._select_annotation(self.annotation_list.currentRow())

    def _select_annotation(self, row):
        enabled = 0 <= row < len(self.annotations) and self.export_worker is None
        for widget in (self.annotation_text, self.annotation_start, self.annotation_duration, self.annotation_x,
                       self.annotation_y, self.annotation_size, self.annotation_color, self.annotation_apply, self.annotation_delete,
                       self.annotation_lower, self.annotation_raise):
            widget.setEnabled(enabled)
        self.annotation_track.set_state(self.segments, self.annotations, row)
        self.preview_label.update()
        if hasattr(self, "stage_text"):
            is_stage = enabled and self.annotations[row]["type"] == "stage"
            self.stage_text.setEnabled(is_stage)
            self.stage_delete.setEnabled(is_stage)
            self.stage_text.blockSignals(True)
            self.stage_text.setText(self.annotations[row]["text"] if is_stage else "")
            self.stage_text.blockSignals(False)
        if not enabled:
            return
        annotation = self.annotations[row]
        self.annotation_text.setText(annotation['text'])
        self.annotation_start.setValue(annotation['start'] / self.fps)
        self.annotation_duration.setValue((annotation['end'] - annotation['start']) / self.fps)
        self.annotation_x.setValue(round(annotation['x'] * 100))
        self.annotation_y.setValue(round(annotation['y'] * 100))
        self.annotation_size.setValue(round(annotation['size'] * 100))
        self.annotation_color.setCurrentIndex(max(0, self.annotation_color.findData(annotation['color'])))

    def _select_annotation_from_track(self, row):
        if hasattr(self, "stage_text") and self.stage_text.hasFocus():
            self._update_stage_text()
        self.annotation_list.setCurrentRow(row)
        if 0 <= row < len(self.annotations):
            a = self.annotations[row]
            if not a['start'] <= self.current_frame < a['end']:
                for segment in self.segments:
                    frame = max(a['start'], segment['start'])
                    if frame < min(a['end'], segment['end']):
                        self._stop_playback(); self._show_frame_at(frame); break

    def _change_annotation_range(self, row, start, end):
        if self.export_worker is not None or not 0 <= row < len(self.annotations): return
        self.annotations[row].update(start=start, end=end)
        self._select_annotation(row)
        self.annotation_track.set_state(self.segments, self.annotations, row)
        self._on_project_changed()
        self._show_frame_at(self.current_frame)

    def _deselect_annotation(self):
        self.annotation_list.setCurrentRow(-1)
        self.annotation_tool.setCurrentIndex(self.annotation_tool.findData('none'))
        self.preview_label.update()

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget)
                and self.isVisible() and (watched is self or self.isAncestorOf(watched))):
            def within(widget):
                return watched is widget or widget.isAncestorOf(watched)
            if self.crop_mode and not (within(self.preview_label) or within(self.crop_card)):
                self._cancel_video_crop()
        return super().eventFilter(watched, event)

    def _start_video_crop(self):
        if self.export_worker is not None: return
        self._stop_playback(); self._deselect_annotation()
        self.crop_mode = True; self.pending_crop = tuple(self.video_crop)
        self.preview_label._crop_edge = None
        self.crop_apply_button.setEnabled(False)
        self.editor_status.setText("拖动预览画面的上下左右边缘，点击应用裁剪；点击空白处取消调整。")
        self._show_frame_at(self.current_frame)

    def _cancel_video_crop(self):
        if not self.crop_mode: return
        self.crop_mode = False; self.pending_crop = None
        self.preview_label._crop_edge = None
        self.preview_label.unsetCursor()
        self.crop_apply_button.setEnabled(False)
        self._show_frame_at(self.current_frame)
        self.editor_status.setText("已退出裁剪调整。")

    def _apply_video_crop(self):
        if self.pending_crop is None: return
        self._snapshot_undo(); self.video_crop = self.pending_crop
        self.crop_mode = False; self.pending_crop = None; self.crop_apply_button.setEnabled(False)
        self.preview_label.unsetCursor()
        self._show_frame_at(self.current_frame); self._on_project_changed()
        self.editor_status.setText("画面裁剪已应用；原素材不变。")

    def _reset_video_crop(self):
        if self.export_worker is not None: return
        if self.video_crop != (0.0, 0.0, 1.0, 1.0):
            self._snapshot_undo(); self.video_crop = (0.0, 0.0, 1.0, 1.0); self._on_project_changed()
        self.crop_mode = False; self.pending_crop = None; self.crop_apply_button.setEnabled(False)
        self._show_frame_at(self.current_frame)

    def _add_stage(self):
        if self.export_worker is not None or not self.segments:
            return
        self._stop_playback()
        self._snapshot_undo()
        start = self.current_frame
        self.annotations.append({"id": str(uuid.uuid4()), "type": "stage", "text": "新阶段",
                                 "start": start, "end": min(self.total_frames, start + max(1, round(3 * self.fps))),
                                 "x": 0.0, "y": 0.0, "size": .05, "color": "#ffffff"})
        self._refresh_annotations(len(self.annotations) - 1)
        self._refresh_segments_table(select_row=self._selected_row())
        self._show_frame_at(start)

    def _update_stage_text(self):
        if self.export_worker is not None:
            return
        row = self.annotation_list.currentRow()
        if 0 <= row < len(self.annotations) and self.annotations[row]["type"] == "stage":
            text = self.stage_text.text()
            if text != self.annotations[row]["text"]:
                self._snapshot_undo()
                self.annotations[row]["text"] = text
                self._on_project_changed()
                self._show_frame_at(self.current_frame)

    def _set_presentation(self, **changes):
        if self.export_worker is not None:
            return
        try:
            options = validate_options({**self.presentation, **changes})
            cropped = crop_frame(np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8), self.video_crop)
            frame_size(cropped.shape[1], cropped.shape[0], options)
        except ValueError as exc:
            QMessageBox.warning(self, "设置无效", str(exc))
            self._sync_presentation_controls()
            return
        self.presentation = options
        self._on_project_changed()
        self._show_frame_at(self.current_frame)

    def _sync_presentation_controls(self):
        if not hasattr(self, "ratio_combo"):
            return
        ratio = self.presentation["ratio"]
        index = self.ratio_combo.findData(ratio)
        for widget in (self.ratio_combo, self.fit_combo, self.align_combo):
            widget.blockSignals(True)
        self.ratio_combo.setCurrentIndex(index if index >= 0 else self.ratio_combo.findData("custom"))
        self.fit_combo.setCurrentIndex(self.fit_combo.findData(self.presentation["fit"]))
        self.align_combo.setCurrentIndex(self.align_combo.findData(self.presentation["align"]))
        for widget in (self.ratio_combo, self.fit_combo, self.align_combo):
            widget.blockSignals(False)
        if ratio:
            self.ratio_width.setValue(ratio[0]); self.ratio_height.setValue(ratio[1])
        self.ratio_custom.setVisible(self.ratio_combo.currentData() == "custom")
        if hasattr(self, "ratio_buttons"):
            button = self.ratio_buttons.button(self.ratio_combo.currentIndex())
            if button is not None: button.setChecked(True)
        if hasattr(self, "align_buttons"):
            button = self.align_buttons.button(self.align_combo.currentIndex())
            if button is not None: button.setChecked(True)
        for button, key in ((self.background_button, "background"), (self.foreground_button, "foreground")):
            swatch = QPixmap(14, 14)
            swatch.fill(QColor(self.presentation[key]))
            button.setIcon(QIcon(swatch))
            button.setText("自定义")
            button.setToolTip(self.presentation[key])

    def _add_annotation_at(self, x, y):
        kind = self.annotation_tool.currentData()
        if kind == 'none' or self.export_worker is not None or not self.segments:
            return
        self._stop_playback()
        self._snapshot_undo()
        defaults = {'text': '文字说明', 'arrow': '', 'box': '', 'sticker': '重点提示'}
        self.annotations.append({'id': str(uuid.uuid4()), 'type': kind, 'text': defaults[kind],
                                 'start': self.current_frame, 'end': min(self.total_frames, self.current_frame + max(1, round(3 * self.fps))),
                                 'x': x, 'y': y, 'size': .05, 'color': '#4269e8'})
        self._refresh_annotations(len(self.annotations) - 1)
        self.annotation_tool.setCurrentIndex(self.annotation_tool.findData('none'))
        self._refresh_segments_table(select_row=self._selected_row())
        self._show_frame_at(self.current_frame)

    def _apply_annotation(self):
        row = self.annotation_list.currentRow()
        if not 0 <= row < len(self.annotations) or self.export_worker is not None:
            return
        start = min(self.total_frames - 1, max(0, round(self.annotation_start.value() * self.fps)))
        end = min(self.total_frames, start + max(1, round(self.annotation_duration.value() * self.fps)))
        self._snapshot_undo()
        self.annotations[row].update(text=self.annotation_text.text(), start=start, end=end,
                                     x=self.annotation_x.value() / 100, y=self.annotation_y.value() / 100,
                                     size=self.annotation_size.value() / 100, color=self.annotation_color.currentData())
        self._refresh_annotations(row)
        self._refresh_segments_table(select_row=self._selected_row())
        self._show_frame_at(self.current_frame)

    def _delete_annotation(self):
        row = self.annotation_list.currentRow()
        if not 0 <= row < len(self.annotations) or self.export_worker is not None:
            return
        self._snapshot_undo()
        self.annotations.pop(row)
        self._refresh_annotations(min(row, len(self.annotations) - 1))
        self._refresh_segments_table(select_row=self._selected_row())
        self._show_frame_at(self.current_frame)

    def _move_annotation(self, direction):
        row = self.annotation_list.currentRow()
        target = row + direction
        if self.export_worker is not None or not (0 <= row < len(self.annotations) and 0 <= target < len(self.annotations)):
            return
        self._snapshot_undo()
        self.annotations[row], self.annotations[target] = self.annotations[target], self.annotations[row]
        self._refresh_annotations(target)
        self._refresh_segments_table(select_row=self._selected_row())
        self._show_frame_at(self.current_frame)

    def _selected_row(self):
        return self.segment_table.currentRow()

    def _on_segment_selected(self):
        row = self._selected_row()
        if 0 <= row < len(self.segments):
            self._show_frame_at(self.segments[row]["start"])

    def _on_timeline_clicked(self, row, position):
        self._stop_playback()
        self.segment_table.selectRow(row)
        self._show_output_frame(position)

    def _update_time_label(self):
        current_seconds = (source_to_output(self.segments, self.current_frame) or 0) / self.fps
        duration = edited_frame_count(self.segments) / self.fps
        self.time_label.setText(
            f"{self._format_time(current_seconds)} / {self._format_time(duration)}"
        )

    def _update_time_from_slider(self, value):
        self.time_label.setText(
            f"{self._format_time(value / self.fps)} / {self._format_time(edited_frame_count(self.segments) / self.fps)}"
        )

    def _show_output_frame(self, output_frame):
        source_frame = output_to_source(self.segments, output_frame)
        if source_frame is not None:
            self._show_frame_at(source_frame)

    def _show_frame_at(self, frame_index):
        frame_index = max(0, min(self.total_frames - 1, int(frame_index)))
        self.current_frame = frame_index
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = self.cap.read()
        if ok and frame is not None:
            if self.crop_mode:
                frame = render_annotations(frame, frame_index, [a for a in self.annotations if a["type"] != "stage"])
            else:
                frame = render_frame(frame, frame_index, self.annotations, self.video_crop, self.presentation)
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
        self.timeline_slider.setValue(source_to_output(self.segments, frame_index) or 0)
        self.timeline_slider.blockSignals(False)
        self._update_time_label()
        if hasattr(self, "timeline_track"):
            self.timeline_track.set_state(self.segments, self.timeline_slider.value(), self._selected_row())

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
        self._show_output_frame(self.timeline_slider.value())

    def _toggle_playback(self):
        if self.playing:
            self.play_timer.stop()
            self.playing = False
            self.play_button.setText("播放")
        else:
            if self.timeline_slider.value() >= edited_frame_count(self.segments) - 1:
                self._show_output_frame(0)
            self.playing = True
            self.play_button.setText("暂停")
            self.play_timer.start()

    def _on_play_tick(self):
        next_frame = self.timeline_slider.value() + 1
        if next_frame >= edited_frame_count(self.segments):
            self.play_timer.stop()
            self.playing = False
            self.play_button.setText("播放")
            return
        self._show_output_frame(next_frame)

    def _stop_playback(self):
        if self.playing:
            self.play_timer.stop()
            self.playing = False
            self.play_button.setText("播放")

    def _split_at_current_frame(self):
        self._stop_playback()
        row = self._selected_row()
        if not (0 <= row < len(self.segments)):
            QMessageBox.information(self, "先选择片段", "请先在时间线上选择要分割的片段。")
            return
        seg = self.segments[row]
        split_frame = int(self.current_frame)
        if split_frame <= seg["start"] or split_frame >= seg["end"]:
            QMessageBox.information(
                self, "无法分割",
                "当前播放位置不在选中片段的内部。\n请先在时间线上选中并定位到片段中间。"
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
            "如需移除某一段，请选中该片段并点击“删除片段”。"
        )

    def _trim_start_at_playhead(self):
        self._stop_playback()
        position = self.timeline_slider.value()
        if not self.segments or position == 0:
            return
        self._snapshot_undo()
        self.segments = trim_to_output_range(self.segments, position, edited_frame_count(self.segments))
        self._refresh_segments_table(select_row=0)
        self._show_output_frame(0)
        self.editor_status.setText("已将播放头设为起点，原素材未被修改。")

    def _trim_end_at_playhead(self):
        self._stop_playback()
        end = self.timeline_slider.value() + 1
        if not self.segments or end >= edited_frame_count(self.segments):
            return
        self._snapshot_undo()
        self.segments = trim_to_output_range(self.segments, 0, end)
        self._refresh_segments_table(select_row=len(self.segments) - 1)
        self._show_output_frame(end - 1)
        self.editor_status.setText("已将播放头设为终点，原素材未被修改。")

    def _delete_selected_segment(self):
        self._stop_playback()
        row = self._selected_row()
        if not (0 <= row < len(self.segments)):
            QMessageBox.information(self, "先选择片段", "请先在时间线上选择要删除的片段。")
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
        self.segments, self.annotations, self.video_crop = self.undo_stack.pop()
        self._refresh_annotations()
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
        self.timeline_track.setEnabled(False)
        self.trim_start_button.setEnabled(False)
        self.trim_end_button.setEnabled(False)
        self._set_export_controls_enabled(False)

        self.export_worker = VideoExportWorker(
            self.source_path, [dict(seg) for seg in self.segments], output_path, self.annotations, self.video_crop, self.presentation
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
        self.timeline_track.setEnabled(enabled and bool(self.segments))
        self.trim_start_button.setEnabled(enabled and bool(self.segments))
        self.trim_end_button.setEnabled(enabled and bool(self.segments))
        self.annotation_tool.setEnabled(enabled)
        self.annotation_list.setEnabled(enabled)
        for control in (self.ratio_combo, self.ratio_custom, self.fit_combo, self.align_combo,
                        self.background_button, self.foreground_button, self.stage_add):
            control.setEnabled(enabled)
        self.ratio_buttons.setExclusive(True)
        for button in self.ratio_buttons.buttons() + self.align_buttons.buttons():
            button.setEnabled(enabled)
        self.annotation_track.setEnabled(enabled)
        self._select_annotation(self.annotation_list.currentRow() if enabled else -1)

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
        if self.project_worker is not None:
            QMessageBox.information(self, "正在保存工程", "请等待工程创建完成后再关闭。")
            event.ignore()
            return
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
        self.setWindowTitle("视频录制工作台")
        self.setMinimumSize(900, 580)

        self.settings = QSettings("RegionRecorder", "RegionRecorder")
        default_dir = Path.home() / "Videos" / "区域录屏"
        configured_dir = self.settings.value("save_dir", str(default_dir))
        self.save_dir = Path(str(configured_dir)).expanduser()
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.handoff_directory = Path(str(self.settings.value("handoff_directory", str(self.save_dir))))

        self.selector = None
        self.crop_selector = None
        self.recorder = None
        self.control_window = None
        self.prompter_window = None
        self.preview_prompter = None
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
        self.selected_window_target = None
        self.selected_window_monitor = None
        self._custom_selection_mode = False
        self.video_editor = None
        data_root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "VideoCaptureWorkbench"
        self.script_document = ScriptDocument(data_root / "draft.txt")
        self._draft_error = None
        self._draft_load_failed = False
        try:
            self.script_document.load()
        except (OSError, UnicodeError) as exc:
            self._draft_error = str(exc)
            self._draft_load_failed = True
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.setInterval(500)
        self.draft_timer.timeout.connect(self._save_draft)

        self._build_ui()
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.resize(max(900, int(screen.availableGeometry().width() * .94)),
                        max(580, int(screen.availableGeometry().height() * .92)))
        if self._draft_error:
            self.status_label.setText("状态：口播草稿读取失败，请检查文件")
            self.status_label.setToolTip(self._draft_error)
        self._build_tray()
        self.control_rect_timer = QTimer(self)
        self.control_rect_timer.setInterval(200)
        self.control_rect_timer.timeout.connect(self._sync_control_exclusion_rect)
        self.control_rect_timer.start()

        self.hotkey_signals = HotkeySignals()
        self.hotkey_signals.stop_requested.connect(self.stop_recording)
        self.hotkey_signals.effect_requested.connect(self._on_effect_hotkey)
        self.hotkey_controller = HotkeyController()
        self.hotkey_listener = None
        try:
            self.hotkey_listener = keyboard.Listener(on_press=self._on_global_key, on_release=self._on_global_key_release)
            self.hotkey_listener.daemon = True
            self.hotkey_listener.start()
        except Exception as exc:
            self.status_label.setText("状态：全局快捷键启动失败，请检查系统权限")
            self.status_label.setToolTip(str(exc))

    def _build_ui(self):
        from workbench_ui import build_workbench
        build_workbench(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        editor = getattr(self, "video_editor", None)
        if editor is not None and hasattr(editor, "editor_stats"):
            editor.editor_stats.hide()
            editor.editor_subtitle.hide()
            editor.annotation_hint.hide()
            editor.segment_summary.setVisible(self.height() >= 740)

    def _update_zoom_hint(self, *_args):
        if not hasattr(self, "zoom_hold_spin"):
            return
        hold = float(self.zoom_hold_spin.value())
        transition = 0.35
        total = hold + 2 * transition
        self.zoom_hint_label.setText(
            f"按 V 后，平滑推进 {transition:.2f} 秒，"
            f"放大停留 {hold:g} 秒，再平滑拉远 {transition:.2f} 秒；"
            f"一次效果总时长约 {total:.2f} 秒。C 高亮；输入文字时可关闭效果。"
        )

    def _on_script_changed(self):
        self.script_count.setText(f"{len(self.script_input.toPlainText())} 字")
        if self.video_editor is not None and self.video_editor.project_path is not None:
            self.draft_timer.stop()
            return
        self.draft_timer.start()

    def _save_draft(self):
        if self.video_editor is not None and self.video_editor.project_path is not None:
            return self.video_editor.flush_project()
        if self._draft_load_failed:
            self.status_label.setText("状态：草稿读取失败，请修复文件后重启，避免覆盖")
            return False
        try:
            self.script_document.save(self.script_input.toPlainText())
            self._draft_error = None
            return True
        except OSError as exc:
            self._draft_error = str(exc)
            self.status_label.setText("状态：口播草稿保存失败，请检查路径和空间")
            self.status_label.setToolTip(self._draft_error)
            return False

    def _import_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入口播稿", "", "文本文件 (*.txt)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(self, "导入失败", f"请使用 UTF-8 编码的 TXT 文件。\n{exc}")
            return
        self.script_input.setPlainText(text)

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
        self.monitor_combo.blockSignals(True)
        self.monitor_combo.clear()
        for idx, monitor in enumerate(monitors):
            screen_name = "主显示器" if idx == primary_index else f"显示器 {idx + 1}"
            self.monitor_combo.addItem(f"{screen_name}  ·  {monitor['width']} × {monitor['height']}", idx)
        self.monitor_combo.setCurrentIndex(saved_index if monitors else -1)
        self.monitor_combo.blockSignals(False)
        if monitors:
            self._on_monitor_selected(saved_index)
        else:
            self.status_label.setText("状态：没有检测到显示器")

    def _selected_monitor_index(self):
        idx = self.monitor_combo.currentIndex()
        return idx if 0 <= idx < len(self._monitor_list) else -1

    def _on_monitor_selected(self, index):
        if not 0 <= index < len(self._monitor_list):
            return
        self.settings.setValue("selected_monitor", int(index))
        monitor = self._monitor_list[index]
        if self.selected_window_monitor is not None and self.selected_window_monitor != monitor:
            self.selected_window_target = None
            self.selected_window_monitor = None
            self.window_target_label.setText("显示器已改变，请重新选择窗口")
        self._invalidate_record_preview()

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
        if self.full_region.isChecked():
            self.preset_hint_label.setText("当前选择整屏；已保存的区域预设不会覆盖该选择。")
        elif self.window_region.isChecked():
            self.preset_hint_label.setText("当前选择指定窗口；区域预设不会覆盖窗口选择。")
        elif name and name in self._region_presets:
            region = self._region_presets[name]
            self.preset_hint_label.setText(
                f"当前选择：{name}（{region['width']} × {region['height']}，"
                f"坐标 {region['left']}, {region['top']}）。开始录制会固定使用此区域。"
            )
        else:
            self.preset_hint_label.setText(
                "开始录制时框选区域；也可以选择或保存区域预设。"
            )

    def _on_region_preset_changed(self, index):
        if not hasattr(self, "region_preset_combo"):
            return
        name = str(self.region_preset_combo.currentData() or "")
        self.settings.setValue("selected_region_preset", name)
        if name and hasattr(self, "custom_region"):
            self.custom_region.setChecked(True)
        self._update_preset_hint()
        self._invalidate_record_preview()

    def _on_range_mode_changed(self):
        if hasattr(self, "window_controls"):
            self.window_controls.setVisible(self.window_region.isChecked())
        if hasattr(self, "preset_controls"):
            self.preset_controls.setVisible(self.custom_region.isChecked())
        if hasattr(self, "preset_hint_label") and hasattr(self, "region_preset_combo"):
            self._update_preset_hint()
        if hasattr(self, "preview_caption"):
            self.preview_caption.setText("所选窗口的可见内容" if self.window_region.isChecked() else
                                         "所选区域的可见内容" if self.custom_region.isChecked() else "当前选中的显示器")
        self._invalidate_record_preview()

    def _invalidate_record_preview(self):
        if not hasattr(self, "preview_label"):
            return
        self.preview_label._image = None
        self.preview_label.clear()
        self.preview_label.setText("目标已变更，点击“刷新预览”查看当前画面")
        preset = str(self.region_preset_combo.currentData() or "")
        if self.window_region.isChecked():
            if self.selected_window_target is None:
                self.preview_resolution.setText("尺寸：请先选择窗口")
            else:
                try:
                    region = window_region(self.selected_window_target, self.selected_window_monitor)
                    self.preview_resolution.setText(f"尺寸：{region['width']} × {region['height']}")
                except RuntimeError:
                    self.preview_resolution.setText("尺寸：目标不可用")
        elif self.custom_region.isChecked() and preset in self._region_presets:
            region = self._region_presets[preset]
            self.preview_resolution.setText(f"尺寸：{region['width']} × {region['height']}")
        elif self.custom_region.isChecked():
            self.preview_resolution.setText("尺寸：开始后框选")
        elif self._monitor_list:
            region = self._monitor_list[self._selected_monitor_index()]
            self.preview_resolution.setText(f"尺寸：{region['width']} × {region['height']}")

    def refresh_record_preview(self):
        if self._recording_is_active():
            return
        try:
            preset = str(self.region_preset_combo.currentData() or "")
            if self.window_region.isChecked():
                if self.selected_window_target is None:
                    raise RuntimeError("请先选择窗口。")
                region = window_region(self.selected_window_target, self.selected_window_monitor)
            elif self.custom_region.isChecked() and preset:
                region = dict(self._region_presets[preset])
            elif self.custom_region.isChecked():
                self.preview_label._image = None
                self.preview_label.clear()
                self.preview_label.setText("自定义区域将在开始录制后框选；也可先选择已保存的区域预设。")
                return
            else:
                index = self._selected_monitor_index()
                if index < 0:
                    raise RuntimeError("没有检测到可用显示器。")
                region = dict(self._monitor_list[index])
            with mss.mss() as capture:
                screen = np.asarray(capture.grab(region))
            rgb = cv2.cvtColor(screen, cv2.COLOR_BGRA2RGB)
            image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
            self.preview_label.set_image(image)
            self.preview_resolution.setText(f"尺寸：{region['width']} × {region['height']}")
        except Exception as exc:
            self.preview_label._image = None
            self.preview_label.clear()
            self.preview_label.setText(f"无法获取预览：{exc}")

    def choose_record_window(self):
        if self._recording_is_active():
            return
        index = self._selected_monitor_index()
        if index < 0:
            QMessageBox.warning(self, "没有显示器", "请先刷新并选择显示器。")
            return
        monitor = dict(self._monitor_list[index])
        picker = WindowPicker(monitor, self)
        if not picker.exec() or picker.selected_target is None:
            return
        self.selected_window_target = picker.selected_target
        self.selected_window_monitor = monitor
        self.window_target_label.setText(f"目标：{picker.selected_target.title}\n请保持窗口显示且避免遮挡。")
        self.window_region.setChecked(True)
        self._invalidate_record_preview()

    def _preview_prompter(self):
        if self._recording_is_active():
            return
        if self.preview_prompter is not None:
            self.preview_prompter.finish_and_close()
            self.preview_prompter.deleteLater()
        self.preview_prompter = TeleprompterWindow(self.script_input.toPlainText(), self.settings)
        self.preview_prompter.setWindowTitle("提词预览")
        self.preview_prompter.show()

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
        """按所选录制范围开始，避免预设覆盖整屏选择。"""
        if self._recording_is_active():
            return
        if self.video_editor is not None and self.video_editor.project_worker is not None:
            QMessageBox.information(self, "正在保存工程", "请等待工程创建完成后再开始录制。")
            return
        if self.video_editor is not None and self.video_editor.export_worker is not None and self.video_editor.export_worker.isRunning():
            QMessageBox.information(self, "正在导出", "请等待当前视频导出完成后再开始录制。")
            return

        if self.window_region.isChecked():
            if self.selected_window_target is None:
                QMessageBox.warning(self, "尚未选择窗口", "请先选择要录制的窗口。")
                return
            try:
                region = window_region(self.selected_window_target, self.selected_window_monitor)
            except RuntimeError as exc:
                QMessageBox.warning(self, "窗口不可用", str(exc))
                return
            self.hide()
            target, monitor = self.selected_window_target, dict(self.selected_window_monitor)
            QTimer.singleShot(250, lambda: self._start_recording(
                region, f"窗口_{target.title}", crop_rect=(0.0, 0.0, 1.0, 1.0),
                window_target=target, window_monitor=monitor))
            return

        preset_name = str(self.region_preset_combo.currentData() or "")
        if self.custom_region.isChecked() and not preset_name:
            self.start_selection()
            return
        if self.custom_region.isChecked() and preset_name:
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

        self.start_fixed_monitor(self._selected_monitor_index(), auto_window=False)

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
            crop_rect = self._crop_rect() if auto_window else (0.0, 0.0, 1.0, 1.0)
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
        action = self.hotkey_controller.press(key)
        if action == "f9":
            self.hotkey_signals.stop_requested.emit()
        elif action in ("c", "v"):
            if os.name == "nt" and any(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x11, 0x12, 0x5B, 0x5C)):
                return
            self.hotkey_signals.effect_requested.emit(action)

    def _on_global_key_release(self, key):
        self.hotkey_controller.release(key)

    def _on_effect_hotkey(self, action):
        if not self._recording_is_active() or self.recorder.is_paused():
            return
        if self.control_window is None or not self.control_window.effects_checkbox.isChecked():
            return
        if QApplication.activeWindow() is not None:
            return
        try:
            x, y = get_cursor_position()
            self.recorder.request_effect(action, x, y)
        except Exception:
            pass

    def _recording_is_active(self):
        return self.recorder is not None and self.recorder.isRunning()

    def _set_record_buttons_enabled(self, enabled):
        self.save_region_preset_button.setEnabled(enabled)
        self.delete_region_preset_button.setEnabled(enabled)
        self.region_preset_combo.setEnabled(enabled)
        self.monitor_refresh_button.setEnabled(enabled)
        self.monitor_combo.setEnabled(enabled)
        self.start_button.setEnabled(enabled)
        self.change_path_button.setEnabled(enabled)
        self.edit_video_button.setEnabled(enabled)
        self.open_project_button.setEnabled(enabled)
        self.history_nav_button.setEnabled(enabled)
        self.export_nav_button.setEnabled(enabled)
        self.full_region.setEnabled(enabled)
        self.custom_region.setEnabled(enabled)
        self.window_region.setEnabled(enabled)
        self.select_window_button.setEnabled(enabled)
        self.shortcut_checkbox.setEnabled(enabled)
        self.cursor_style_combo.setEnabled(enabled)
        self.zoom_factor_combo.setEnabled(enabled)
        self.zoom_hold_spin.setEnabled(enabled)
        self.highlight_style_combo.setEnabled(enabled)
        self.script_input.setEnabled(enabled)
        self.import_script_button.setEnabled(enabled)
        self.clear_script_button.setEnabled(enabled)
        self.preview_prompt_button.setEnabled(enabled)

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
                monitors, bool(auto_window), self._crop_rect() if auto_window else (0.0, 0.0, 1.0, 1.0)
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

    def _start_recording(self, region, mode="区域", follow_mouse=False, monitors=None, initial_monitor_index=0, auto_window=False, crop_rect=None, fixed_monitor_index=None, window_target=None, window_monitor=None):
        if self.selector is not None:
            self.selector.deleteLater()
            self.selector = None

        if self.preview_prompter is not None:
            self.preview_prompter.finish_and_close()
            self.preview_prompter.deleteLater()
            self.preview_prompter = None

        self.draft_timer.stop()
        if not self._save_draft():
            self.show_main_window()
            QMessageBox.warning(self, "无法保存口播稿", f"录制未开始。\n{self._draft_error or '请先修复草稿文件。'}")
            return

        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_mode = "".join(
                "_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch
                for ch in str(mode)
            ).strip(" .")[:80] or "录屏"
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
            cursor_style=self.cursor_style_combo.currentText(),
            zoom_hold_seconds=self.zoom_hold_spin.value(), settings=self.settings
        )
        self.control_window.effects_checkbox.setChecked(self.shortcut_checkbox.isChecked())
        self.control_window.pause_requested.connect(self.toggle_pause_recording)
        self.control_window.reselect_requested.connect(self.reselect_region)
        self.control_window.save_requested.connect(self.stop_recording)
        self.control_window.discard_requested.connect(self.discard_recording)
        self.control_window.prompter_toggle_requested.connect(self._toggle_prompter)
        if not restore_visible_geometry(self.control_window, self.settings, "control_geometry"):
            self._place_control_window(region)
        self.control_window.show()

        self.prompter_window = TeleprompterWindow(self.script_input.toPlainText(), self.settings)
        if not self.prompter_window.restored:
            control = self.control_window.frameGeometry()
            self.prompter_window.move(control.left() - self.prompter_window.width() - 16, control.top())
            restore_visible_geometry(self.prompter_window, self.settings, "prompter_geometry")
        self.prompter_window.show()
        try:
            verify_window_exclusion(self.control_window)
            verify_window_exclusion(self.prompter_window)
        except Exception as exc:
            self.prompter_window.finish_and_close()
            self.prompter_window.deleteLater()
            self.prompter_window = None
            self.control_window.finish_and_close()
            self.control_window.deleteLater()
            self.control_window = None
            self._recording_output_path = None
            self._set_record_buttons_enabled(True)
            if self.stop_action is not None:
                self.stop_action.setEnabled(False)
            self.show_main_window()
            self.status_label.setText("状态：窗口捕获排除验证失败，未开始录制")
            QMessageBox.warning(self, "无法安全开始录制", f"悬浮窗可能被录入视频，录制已阻止。\n{exc}")
            return

        self.recorder = RecorderWorker(
            region, output_path, follow_mouse=follow_mouse,
            monitors=monitors, initial_monitor_index=initial_monitor_index,
            auto_window=auto_window, crop_rect=crop_rect or (0.0, 0.0, 1.0, 1.0),
            fixed_monitor_index=fixed_monitor_index,
            cursor_style=self.cursor_style_combo.currentText(),
            zoom_hold_seconds=self.zoom_hold_spin.value(),
            window_target=window_target, window_monitor=window_monitor,
            zoom_factor=self.zoom_factor_combo.currentData(),
            highlight_style=self.highlight_style_combo.currentText()
        )
        self.recorder.preview_frame.connect(self._on_preview_frame)
        self.recorder.monitor_changed.connect(self._on_monitor_changed)
        self.recorder.window_region_changed.connect(self._on_window_region_changed)
        self.recorder.file_saved.connect(self._on_file_saved)
        self.recorder.recording_error.connect(self._on_recording_error)
        self.recorder.finished.connect(self._on_worker_finished)
        self.recorder.start()

    def _on_window_region_changed(self, left, top, width, height):
        if self.control_window is not None:
            self.control_window.region_label.setText(
                f"目标窗口内容区：{width} × {height}　|　屏幕坐标：X={left}, Y={top}")

    def _toggle_prompter(self):
        if self.prompter_window is None:
            return
        if self.prompter_window.isVisible():
            self.prompter_window.hide()
        else:
            if not enable_exclusion(self.prompter_window):
                QMessageBox.warning(self, "无法显示提词框", "窗口捕获排除不可用，为避免入镜已保持隐藏。")
                return
            self.prompter_window.show()
            self.prompter_window.raise_()

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
        rects = []
        for window in (self.control_window, self.prompter_window):
            if window is None or not window.isVisible():
                continue
            rect = wintypes.RECT()
            if os.name == "nt" and ctypes.windll.user32.GetWindowRect(int(window.winId()), ctypes.byref(rect)):
                rects.append((rect.left, rect.top, rect.right, rect.bottom))
        self.recorder.set_control_rects(rects)
        if self.control_window is not None:
            self.control_window.set_elapsed(self.recorder._clock.elapsed_ns() / 1_000_000_000)

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
        self.recorder.discard()

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
        if self.prompter_window is not None:
            self.prompter_window.finish_and_close()
            self.prompter_window.deleteLater()
            self.prompter_window = None

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
            self.path_label.setText(str(self.save_dir))
            if self.tray is not None:
                self.tray.showMessage(
                    APP_NAME,
                    f"录制完成，正在打开剪辑界面：{completed_path}",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000
                )
            if not self._exit_after_recording:
                diagnostics = dict(worker.diagnostics)
                QTimer.singleShot(
                    180, lambda path=completed_path, stats=diagnostics: self.open_video_editor(path, stats)
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

    def choose_project(self):
        selected, _ = QFileDialog.getOpenFileName(self, "打开工程", str(self.save_dir), "工程文件 (project.json *.json)")
        if not selected:
            return
        self.open_project_path(selected)

    def open_project_path(self, selected):
        try:
            project = read_project(selected)
        except Exception as exc:
            QMessageBox.warning(self, "无法打开工程", str(exc))
            return False
        self.open_video_editor(project["source"], project_data=project)
        return self.video_editor is not None and self.video_editor.project_path == project["path"]

    def recent_projects(self):
        try:
            paths = json.loads(str(self.settings.value("recent_projects", "[]")))
            if not isinstance(paths, list):
                return []
            return [p for p in paths if isinstance(p, str) and p][:30]
        except (ValueError, TypeError):
            return []

    def recent_export(self, path):
        try:
            exports = json.loads(str(self.settings.value("recent_exports", "{}")))
            result = exports.get(str(path), "") if isinstance(exports, dict) else ""
            return result if isinstance(result, str) else ""
        except (ValueError, TypeError):
            return ""

    def add_recent_project(self, path):
        path = str(Path(path).resolve())
        key = os.path.normcase(path)
        paths = [p for p in self.recent_projects() if os.path.normcase(os.path.abspath(p)) != key]
        self.settings.setValue("recent_projects", json.dumps([path, *paths][:30], ensure_ascii=False))

    def remove_recent_project(self, path):
        self.settings.setValue("recent_projects", json.dumps([p for p in self.recent_projects() if p != path], ensure_ascii=False))
        self.history_page.refresh()

    def relocate_project(self, old_path):
        selected, _ = QFileDialog.getOpenFileName(self, "重新定位工程", str(self.save_dir), "工程文件 (project.json *.json)")
        if selected and self.open_project_path(selected):
            if os.path.normcase(os.path.abspath(selected)) != os.path.normcase(os.path.abspath(old_path)):
                self.remove_recent_project(old_path)

    def locate_project(self, path):
        directory = Path(path).parent
        if not directory.is_dir():
            QMessageBox.warning(self, "无法定位工程", "工程目录不存在，请重新定位工程。")
            self.history_page.refresh()
            return
        try:
            if os.name == "nt":
                os.startfile(str(directory))
            else:
                QMessageBox.information(self, "工程目录", str(directory))
        except OSError as exc:
            QMessageBox.warning(self, "无法打开目录", str(exc))

    def _select_workbench_page(self, page):
        self.record_nav_button.setObjectName("activeNav" if page == "record" else "")
        self.edit_video_button.setObjectName("activeNav" if page == "edit" else "")
        self.history_nav_button.setObjectName("activeNav" if page == "history" else "")
        self.export_nav_button.setObjectName("activeNav" if page == "export" else "")
        for button in (self.record_nav_button, self.edit_video_button, self.history_nav_button, self.export_nav_button):
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def show_record_page(self):
        if self.video_editor is not None:
            self.video_editor._stop_playback()
        self.page_stack.setCurrentWidget(self.main_scroll)
        self._select_workbench_page("record")

    def show_history_page(self):
        if self.video_editor is not None:
            self.video_editor._stop_playback()
        self.history_page.refresh()
        self.page_stack.setCurrentWidget(self.history_page)
        self._select_workbench_page("history")

    def show_export_page(self):
        if self.video_editor is not None:
            self.video_editor._stop_playback()
        self.export_page.refresh()
        self.page_stack.setCurrentWidget(self.export_page)
        self._select_workbench_page("export")

    def choose_handoff_directory(self):
        selected = QFileDialog.getExistingDirectory(self, "选择交付目录的父文件夹", str(self.handoff_directory))
        if selected:
            self.handoff_directory = Path(selected)
            self.settings.setValue("handoff_directory", str(self.handoff_directory))
            self.export_page.directory.setText(selected)

    def start_handoff_export(self):
        editor = self.video_editor
        if editor is None or editor.export_worker is not None or editor.project_worker is not None:
            return
        script = self.script_input.toPlainText()
        if not script.strip():
            QMessageBox.warning(self, "口播稿为空", "请先填写口播稿，再导出交付目录。")
            return
        if not editor.segments or not self.handoff_directory.is_dir():
            QMessageBox.warning(self, "无法导出", "请确认已保留视频片段，且交付位置存在。")
            return
        if editor.project_path is not None and not editor.flush_project():
            return
        try:
            project_id = read_project(editor.project_path)["metadata"]["project_id"] if editor.project_path else str(uuid.uuid4())
        except Exception as exc:
            QMessageBox.warning(self, "无法读取工程", str(exc))
            return
        name = self.export_page.name_input.text().strip() or editor.source.stem
        editor._stop_playback()
        editor._set_export_controls_enabled(False)
        self.script_input.setEnabled(False)
        editor.script_input.setEnabled(False)
        self.export_page.name_input.setEnabled(False)
        self.export_page.export_button.setEnabled(False)
        self.export_page.cancel_button.setEnabled(True)
        self.export_page.progress.setValue(0)
        self.export_page.status.setText("正在编码和校验，请稍候…")
        worker = HandoffWorker(self.handoff_directory, editor.source_path, [dict(s) for s in editor.segments], editor.fps, script, project_id, name, [dict(a) for a in editor.annotations], editor.video_crop, dict(editor.presentation))
        editor.export_worker = worker
        worker.progress_changed.connect(self.export_page.progress.setValue)
        worker.succeeded.connect(self._handoff_succeeded)
        worker.failed.connect(self._handoff_failed)
        worker.cancelled.connect(self._handoff_cancelled)
        worker.finished.connect(editor._on_export_thread_finished)
        worker.finished.connect(self._handoff_finished)
        worker.start()

    def cancel_handoff_export(self):
        editor = self.video_editor
        if editor is not None and isinstance(editor.export_worker, HandoffWorker):
            self.export_page.cancel_button.setEnabled(False)
            self.export_page.status.setText("正在取消导出并清理临时文件…")
            editor.export_worker.cancel()

    def _handoff_cancelled(self):
        self.export_page.status.setText("已取消导出，未生成交付目录。")
        self.export_page.progress.setValue(0)

    def _handoff_succeeded(self, path):
        self.export_page.status.setText(f"交付完成：{path}")
        if self.video_editor and self.video_editor.project_path:
            try:
                exports = json.loads(str(self.settings.value("recent_exports", "{}")))
                if not isinstance(exports, dict):
                    exports = {}
            except (ValueError, TypeError):
                exports = {}
            exports[str(self.video_editor.project_path)] = path
            self.settings.setValue("recent_exports", json.dumps(exports, ensure_ascii=False))
        QMessageBox.information(self, "交付完成", f"交付目录已生成：\n{path}\n\n视频保持正常速度，口播时长匹配由下游处理。")

    def _handoff_failed(self, message):
        self.export_page.status.setText("交付失败，请检查错误后重试。")
        QMessageBox.critical(self, "交付失败", message)

    def _handoff_finished(self):
        self.export_page.cancel_button.setEnabled(False)
        self.script_input.setEnabled(True)
        if self.video_editor is not None:
            self.video_editor.script_input.setEnabled(True)
        self.export_page.name_input.setEnabled(True)
        self.export_page.export_button.setEnabled(self.video_editor is not None and bool(self.video_editor.segments))

    def show_editor_page(self):
        if self.video_editor is None:
            self.choose_video_to_edit()
            return
        self.page_stack.setCurrentWidget(self.video_editor)
        self._select_workbench_page("edit")

    def _forget_video_editor(self, editor):
        if self.video_editor is editor:
            self.video_editor = None

    def open_video_editor(self, video_path, recording_stats=None, project_data=None):
        if not video_path or not Path(video_path).exists():
            QMessageBox.warning(self, "找不到视频", f"视频文件不存在：\n{video_path}")
            return

        previous = self.video_editor
        if previous is not None:
            try:
                same_source = os.path.normcase(os.path.abspath(previous.source_path)) == os.path.normcase(os.path.abspath(video_path))
                same_project = project_data is None or (previous.project_path is not None and previous.project_path == project_data["path"])
                if same_source and same_project:
                    if project_data is not None:
                        self.add_recent_project(project_data["path"])
                    self.show_editor_page()
                    return
                if previous.project_worker is not None:
                    QMessageBox.information(self, "正在保存工程", "请等待工程创建完成后再打开其他文件。")
                    return
                if previous.export_worker is not None and previous.export_worker.isRunning():
                    QMessageBox.information(self, "正在导出", "请等待当前视频导出完成后再打开其他文件。")
                    return
                if previous.project_path is not None:
                    if not previous.flush_project():
                        return
                elif previous.has_unsaved_edits():
                    reply = QMessageBox.question(self, "剪辑尚未保存", "打开其他视频会丢失当前剪辑操作；原素材和已导出视频不受影响。继续吗？",
                                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                                 QMessageBox.StandardButton.No)
                    if reply != QMessageBox.StandardButton.Yes:
                        return
            except RuntimeError:
                previous = None

        try:
            editor = VideoEditorWindow(video_path, self)
            if project_data is not None:
                source_info = project_data["metadata"]["source"]
                if (editor.total_frames != project_data["frame_count"] or abs(editor.fps - project_data["fps"]) > .01 or
                    editor.frame_width != int(source_info["width"]) or editor.frame_height != int(source_info["height"])):
                    editor.close()
                    editor.deleteLater()
                    raise ValueError("工程记录与原始视频的帧数、帧率或尺寸不一致。")
        except Exception as exc:
            QMessageBox.critical(self, "无法打开剪辑界面", str(exc))
            return

        if project_data is not None:
            self.draft_timer.stop()
            if not self._save_draft():
                editor.close()
                editor.deleteLater()
                return

        if previous is not None:
            previous.project_timer.stop()
            previous.project_path = None
            self.page_stack.removeWidget(previous)
            previous.close()
            previous.deleteLater()
        self.video_editor = editor
        editor.setWindowFlags(Qt.WindowType.Widget)
        editor.setMinimumSize(0, 0)
        self.page_stack.addWidget(editor)
        editor.editor_stats.hide()
        editor.editor_subtitle.hide()
        editor.annotation_hint.hide()
        editor.segment_summary.setVisible(self.height() >= 740)
        if project_data is not None:
            editor._loading_project = True
            editor.project_path = project_data["path"]
            editor.segments = [dict(segment) for segment in project_data["segments"]]
            editor.annotations = [dict(a) for a in project_data["annotations"]]
            editor.video_crop = tuple(project_data["crop"])
            editor.presentation = validate_options(project_data.get("presentation"))
            editor._sync_presentation_controls()
            editor._show_frame_at(editor.current_frame)
            editor._refresh_annotations()
            self.script_input.setPlainText(project_data["script"])
            editor._saved_signature = editor._project_signature()
            editor._refresh_segments_table(select_row=0 if editor.segments else None)
            editor._loading_project = False
            editor.project_state_label.setText(
                "已恢复上次稿件快照；点击保存工程修复文件。" if project_data["recovered"] else f"工程已打开：{editor.project_path.parent.name}"
            )
            if project_data["recovered"]:
                QMessageBox.information(self, "已恢复工程稿件", "检测到稿件与工程不一致，已恢复上一次成功保存的稿件快照。")
        if recording_stats and recording_stats.get("duration_seconds", 0) > 0:
            capture_fps = recording_stats["captured_frames"] / recording_stats["duration_seconds"]
            editor.editor_status.setText(
                f"录制完成：实际采集约 {capture_fps:.1f} 帧/秒；"
                f"补帧 {recording_stats['duplicate_frames']} 帧。采集率偏低时画面可能不够流畅。"
            )
        editor.destroyed.connect(
            lambda *_args, ed=editor: self._forget_video_editor(ed)
        )
        self.show_editor_page()
        if project_data is not None:
            self.add_recent_project(project_data["path"])

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
        self.draft_timer.stop()
        self._save_draft()
        if self.video_editor is not None:
            self.video_editor.close()
            self.video_editor = None
        if self.preview_prompter is not None:
            self.preview_prompter.finish_and_close()
            self.preview_prompter.deleteLater()
            self.preview_prompter = None
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
            if self.video_editor is not None and self.video_editor.project_path is not None and not self.video_editor.flush_project():
                event.ignore()
                return
            unsaved = "\n当前未保存的剪辑操作也会丢失。" if self.video_editor is not None and self.video_editor.project_path is None and self.video_editor.has_unsaved_edits() else ""
            reply = QMessageBox.question(
                self,
                "正在录制",
                "录制正在进行。停止录制并退出吗？" + unsaved,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._exit_after_recording = True
                self.stop_recording()
            event.ignore()
            return

        if self.video_editor is not None:
            if self.video_editor.project_worker is not None:
                QMessageBox.information(self, "正在保存工程", "请等待工程创建完成后再退出。")
                event.ignore()
                return
            if self.video_editor.export_worker is not None and self.video_editor.export_worker.isRunning():
                QMessageBox.information(self, "正在导出", "请等待视频导出完成后再退出。")
                event.ignore()
                return
            if self.video_editor.project_path is not None and not self.video_editor.flush_project():
                event.ignore()
                return
            if self.video_editor.project_path is None and self.video_editor.has_unsaved_edits():
                reply = QMessageBox.question(self, "剪辑尚未保存", "退出会丢失当前剪辑操作；原素材和已导出视频不受影响。确定退出吗？",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                             QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
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
    if len(sys.argv) == 3 and sys.argv[1] == "--build-smoke-test":
        from build_smoke import run_smoke
        sys.exit(run_smoke(sys.argv[2]))
    else:
        main()
