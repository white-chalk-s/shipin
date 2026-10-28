"""Interactive Windows probe; displays a temporary test window for a few seconds.

Run on each target display with the recording app closed. No screenshots are saved.
This is evidence for this machine only, not a guarantee for other configurations.
"""
import ctypes
import json
import sys
import time

import mss
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel


def main():
    if sys.platform != "win32":
        raise SystemExit("This probe requires Windows.")
    app = QApplication([])
    window = QLabel("CAPTURE EXCLUSION TEST")
    window.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
    window.setStyleSheet("background: rgb(251, 7, 191); color: black; font-size: 22px;")
    window.resize(360, 160)
    window.show()
    app.processEvents()
    time.sleep(0.5)
    app.processEvents()
    hwnd = int(window.winId())
    user32 = ctypes.windll.user32
    rect = ctypes.wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Could not read test window bounds")
    area = {"left": rect.left + 30, "top": rect.top + 45, "width": 100, "height": 70}

    def magenta_fraction():
        with mss.mss() as capture:
            pixels = np.asarray(capture.grab(area))
        return float(np.mean((pixels[:, :, 0] > 160) & (pixels[:, :, 1] < 40) & (pixels[:, :, 2] > 220)))

    before = magenta_fraction()
    ctypes.set_last_error(0)
    result = user32.SetWindowDisplayAffinity(hwnd, 0x11)
    error = ctypes.get_last_error() if not result else 0
    app.processEvents()
    time.sleep(0.5)
    after = magenta_fraction()
    window.close()
    print(json.dumps({"affinity_call_succeeded": bool(result), "error": error,
                      "visible_marker_before": round(before, 3), "visible_marker_after": round(after, 3),
                      "interpretation": "excluded" if result and before > .5 and after < .05 else "inconclusive_or_visible"}))


if __name__ == "__main__":
    import ctypes.wintypes
    main()
