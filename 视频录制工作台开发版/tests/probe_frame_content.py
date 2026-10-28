"""Display colored phases and verify their order in an actual short recording."""
import ctypes
import ctypes.wintypes
import json
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
from region_recorder import RecorderWorker


def main():
    app = QApplication.instance() or QApplication([])
    window = QLabel()
    window.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
    window.resize(320, 220)
    window.setStyleSheet("background: rgb(240, 20, 20)")
    window.show()
    app.processEvents()
    rect = ctypes.wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(int(window.winId()), ctypes.byref(rect)):
        raise RuntimeError("无法获取测试窗口位置")
    region = {"left": rect.left + 30, "top": rect.top + 60, "width": 160, "height": 100}
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "colors.mp4"
        worker = RecorderWorker(region, target, fps=30, cursor_style="不显示")
        errors = []
        worker.recording_error.connect(errors.append)
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        worker.start()
        QTimer.singleShot(1000, lambda: window.setStyleSheet("background: rgb(20, 240, 20)"))
        QTimer.singleShot(1800, worker.pause)
        QTimer.singleShot(2100, lambda: window.setStyleSheet("background: rgb(20, 20, 240)"))
        QTimer.singleShot(2600, worker.resume)
        QTimer.singleShot(3600, worker.stop)
        loop.exec()
        window.close()
        cap = cv2.VideoCapture(str(target))
        colors = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            b, g, r = np.mean(frame, axis=(0, 1))
            colors.append("R" if r > 150 and g < 80 and b < 80 else
                          "G" if g > 150 and r < 80 and b < 80 else
                          "B" if b > 150 and r < 80 and g < 80 else "?")
        cap.release()
        phases = []
        for color in colors:
            if color != "?" and (not phases or color != phases[-1]):
                phases.append(color)
        print(json.dumps({"phases": phases, "frames": len(colors), "errors": errors}, ensure_ascii=False))
        if errors or phases != ["R", "G", "B"]:
            raise SystemExit("录制画面没有按预期呈现三个阶段")


if __name__ == "__main__":
    main()
