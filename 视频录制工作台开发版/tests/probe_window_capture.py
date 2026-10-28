"""Record a moving test window, then confirm minimization stops safely."""
import ctypes
import ctypes.wintypes as wt
import json
import os
import tempfile
from pathlib import Path

import cv2
import mss
import numpy as np
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QLabel
from region_recorder import RecorderWorker
from window_capture import WindowTarget, window_region


def main():
    app = QApplication.instance() or QApplication([])
    test_window = QLabel("窗口跟随测试")
    test_window.setWindowTitle("录制目标测试")
    test_window.setStyleSheet("background: rgb(20, 220, 30); color: black; font-size: 18px")
    test_window.resize(400, 240)
    test_window.move(100, 100)
    test_window.show()
    app.processEvents()
    with mss.mss() as capture:
        monitor = dict(capture.monitors[1])
    target = WindowTarget(int(test_window.winId()), os.getpid(), "录制目标测试")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "移动窗口.mp4"
        worker = RecorderWorker(window_region(target, monitor), path, fps=30, cursor_style="不显示",
                                window_target=target, window_monitor=monitor)
        moves, errors = [], []
        worker.window_region_changed.connect(lambda x, y, w, h: moves.append((x, y, w, h)))
        worker.recording_error.connect(errors.append)
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        worker.start()
        QTimer.singleShot(850, lambda: test_window.move(250, 160))
        QTimer.singleShot(2300, worker.stop)
        loop.exec()
        app.processEvents()
        cap = cv2.VideoCapture(str(path))
        fractions = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            fractions.append(float(np.mean((frame[:, :, 1] > 140) & (frame[:, :, 0] < 80) & (frame[:, :, 2] < 80))))
        cap.release()

        second = RecorderWorker(window_region(target, monitor), Path(tmp) / "最小化.mp4", fps=30,
                                window_target=target, window_monitor=monitor)
        stopped_errors = []
        second.recording_error.connect(stopped_errors.append)
        loop2 = QEventLoop()
        second.finished.connect(loop2.quit)
        second.start()
        QTimer.singleShot(700, test_window.showMinimized)
        QTimer.singleShot(5000, second.stop)
        loop2.exec()
        app.processEvents()
        recovered = [p for p in Path(tmp).glob("vcw-recording-*") if (p / "final.mp4").exists() and (p / "recovery.json").exists()]
        test_window.close()
        mean_green = sum(fractions) / len(fractions) if fractions else 0
        print(json.dumps({"frames": len(fractions), "mean_target_fraction": round(mean_green, 3),
                          "moves_detected": len(moves), "errors": errors,
                          "minimize_reported": bool(stopped_errors and "最小化" in stopped_errors[0]),
                          "temporary_recording_preserved": bool(recovered)}, ensure_ascii=False))
        if not fractions or mean_green < .7 or not moves or errors or not stopped_errors or "最小化" not in stopped_errors[0] or not recovered:
            raise SystemExit("窗口跟随测试失败")


if __name__ == "__main__":
    main()
