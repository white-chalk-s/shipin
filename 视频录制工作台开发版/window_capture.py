"""Find and track visible Windows top-level client rectangles in physical pixels."""
import ctypes
import ctypes.wintypes as wt
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    process_id: int
    title: str


@lru_cache(maxsize=1)
def _api():
    if os.name != "nt":
        raise RuntimeError("指定窗口录制仅支持 Windows。")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
    user.GetClientRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
    user.ClientToScreen.argtypes = (wt.HWND, ctypes.POINTER(wt.POINT))
    user.IsWindow.argtypes = (wt.HWND,)
    user.IsWindowVisible.argtypes = (wt.HWND,)
    user.IsIconic.argtypes = (wt.HWND,)
    user.GetWindowTextLengthW.argtypes = (wt.HWND,)
    user.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
    user.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
    user.GetWindowLongW.argtypes = (wt.HWND, ctypes.c_int)
    return user


def window_region(target, monitor=None):
    user = _api()
    hwnd = wt.HWND(target.hwnd)
    if not user.IsWindow(hwnd) or not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
        raise RuntimeError("目标窗口已关闭、隐藏或最小化。")
    try:
        cloaked = wt.DWORD()
        dwm = ctypes.windll.dwmapi.DwmGetWindowAttribute
        dwm.argtypes = (wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)
        if dwm(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
            raise RuntimeError("目标窗口不可见或位于其他虚拟桌面。")
    except OSError:
        pass
    pid = wt.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value != target.process_id:
        raise RuntimeError("目标窗口已失效，请重新选择。")
    rect = wt.RECT()
    if not user.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("无法读取目标窗口内容区。")
    start, end = wt.POINT(rect.left, rect.top), wt.POINT(rect.right, rect.bottom)
    if not user.ClientToScreen(hwnd, ctypes.byref(start)) or not user.ClientToScreen(hwnd, ctypes.byref(end)):
        raise RuntimeError("无法读取目标窗口屏幕坐标。")
    region = {"left": start.x, "top": start.y, "width": end.x - start.x, "height": end.y - start.y}
    if region["width"] < 20 or region["height"] < 20:
        raise RuntimeError("目标窗口内容区过小。")
    if monitor is not None:
        left, top = monitor["left"], monitor["top"]
        if (region["left"] < left or region["top"] < top or
            region["left"] + region["width"] > left + monitor["width"] or
            region["top"] + region["height"] > top + monitor["height"]):
            raise RuntimeError("目标窗口超出所选显示器，请将窗口完整放回该屏幕。")
    return region


def list_windows(monitor=None):
    user = _api()
    results = []
    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user.EnumWindows.argtypes = (callback_type, wt.LPARAM)
    user.EnumWindows.restype = wt.BOOL

    @callback_type
    def visit(hwnd, _arg):
        if not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
            return True
        if user.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
            return True
        class_name = ctypes.create_unicode_buffer(256)
        user.GetClassNameW(hwnd, class_name, len(class_name))
        if class_name.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return True
        try:
            cloaked = wt.DWORD()
            dwm = ctypes.windll.dwmapi.DwmGetWindowAttribute
            dwm.argtypes = (wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)
            if dwm(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
                return True
        except OSError:
            pass
        pid = wt.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value == os.getpid():
            return True
        length = user.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        target = WindowTarget(int(hwnd), pid.value, title)
        try:
            region = window_region(target, monitor)
        except RuntimeError:
            return True
        results.append((target, region))
        return True

    if not user.EnumWindows(visit, 0):
        raise RuntimeError("无法枚举屏幕窗口。")
    return sorted(results, key=lambda item: item[0].title.casefold())
