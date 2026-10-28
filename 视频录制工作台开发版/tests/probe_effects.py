"""Record a test pattern and verify C highlight and V zoom are present in video."""
import ctypes
import ctypes.wintypes
import json
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget
from region_recorder import RecorderWorker


class Pattern(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202020"))
        for x in range(0, self.width(), 20):
            for y in range(0, self.height(), 20):
                if (x // 20 + y // 20) % 2:
                    painter.fillRect(x, y, 20, 20, QColor("#cccccc"))


def main():
    app = QApplication.instance() or QApplication([])
    window = Pattern()
    window.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
    window.resize(360, 260)
    window.show()
    app.processEvents()
    rect = ctypes.wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(int(window.winId()), ctypes.byref(rect)):
        raise RuntimeError("无法获取测试窗口位置")
    region = {"left": rect.left + 50, "top": rect.top + 70, "width": 200, "height": 160}
    center = (region["left"] + 100, region["top"] + 80)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "effects.mp4"
        worker = RecorderWorker(region, target, fps=30, cursor_style="不显示", zoom_hold_seconds=2)
        errors = []
        worker.recording_error.connect(errors.append)
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        worker.start()
        QTimer.singleShot(600, lambda: worker.request_effect("c", *center))
        QTimer.singleShot(2400, lambda: worker.request_effect("v", *center))
        QTimer.singleShot(3200, lambda: worker.request_effect("c", *center))
        QTimer.singleShot(5600, worker.stop)
        loop.exec()
        window.close()
        cap = cv2.VideoCapture(str(target))
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
        cap.release()
        yellow = [int(np.count_nonzero((f[:, :, 0] < 100) & (f[:, :, 1] > 140) & (f[:, :, 2] > 140))) for f in frames]
        first = frames[min(5, len(frames)-1)] if frames else None
        differences = [float(np.mean(cv2.absdiff(first, f))) for f in frames] if frames else []
        max_yellow = max(yellow, default=0)
        yellow_during_zoom = max(yellow[100:], default=0)
        max_diff_after_zoom = max(differences[85:], default=0)
        print(json.dumps({"frames": len(frames), "highlight_pixels": max_yellow,
                          "highlight_during_zoom": yellow_during_zoom,
                          "zoom_difference": round(max_diff_after_zoom, 2), "errors": errors}))
        if errors or max_yellow < 50 or yellow_during_zoom < 50 or max_diff_after_zoom < 15:
            raise SystemExit("特效录制验证失败")


if __name__ == "__main__":
    main()
