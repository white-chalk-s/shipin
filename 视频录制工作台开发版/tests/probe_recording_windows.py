"""End-to-end local recording with test markers inside both protected windows."""
import ctypes
import argparse
import ctypes.wintypes
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import mss
import numpy as np
from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication, QLabel
from region_recorder import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitor", type=int, default=1)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("save_dir", tmp)
        with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings), \
             patch("region_recorder.QMessageBox.warning") as warning, patch("region_recorder.QMessageBox.critical") as critical:
            window = MainWindow()
            window.open_video_editor = lambda *_args: None
            window.script_input.setPlainText("提词测试内容")
            with mss.mss() as capture:
                monitor = dict(capture.monitors[args.monitor])
            window._start_recording(monitor, "测试")
            if window.recorder is None:
                raise RuntimeError(f"录制未开始: {warning.call_args}")
            worker = window.recorder
            path = Path(worker.output_path)
            window.prompter_window.close()
            hidden_without_stop = not window.prompter_window.isVisible() and worker.isRunning()
            window._toggle_prompter()
            app.processEvents()
            restored_without_stop = window.prompter_window.isVisible() and worker.isRunning()
            screen = app.screens()[args.monitor - 1].availableGeometry()
            window.control_window.move(screen.left() + 80, screen.top() + 80)
            window.prompter_window.move(screen.left() + 700, screen.top() + 100)
            app.processEvents()
            points = []
            markers = []
            for top in (window.control_window, window.prompter_window):
                marker = QLabel(top)
                marker.setGeometry(20, 50, 100, 40)
                marker.setStyleSheet("background: rgb(251, 7, 191); border: none;")
                marker.show()
                marker.raise_()
                markers.append(marker)
                point = ctypes.wintypes.POINT(0, 0)
                ctypes.windll.user32.ClientToScreen(int(top.winId()), ctypes.byref(point))
                ratio = top.devicePixelRatioF()
                points.append((point.x + round(40 * ratio) - monitor["left"],
                               point.y + round(60 * ratio) - monitor["top"]))
            loop = QEventLoop()
            worker.finished.connect(loop.quit)
            QTimer.singleShot(2000, window.stop_recording)
            loop.exec()
            app.processEvents()
            cap = cv2.VideoCapture(str(path))
            samples = 0
            hits = 0
            counts = [0, 0]
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                for index, (x, y) in enumerate(points):
                    if 0 <= y < frame.shape[0] - 8 and 0 <= x < frame.shape[1] - 8:
                        patch_pixels = frame[y:y+8, x:x+8]
                        hits += int(np.mean((patch_pixels[:, :, 0] > 140) & (patch_pixels[:, :, 1] < 70) & (patch_pixels[:, :, 2] > 180)) > .5)
                        samples += 1
                        counts[index] += 1
            cap.release()
            window.close()
            print(json.dumps({"sampled_window_points": samples, "counts_by_window": counts, "marker_hits": hits, "points": points,
                              "file_exists": path.exists(), "error": window._error_message,
                              "hide_during_recording": hidden_without_stop, "restore_during_recording": restored_without_stop,
                              "warnings": warning.call_count, "errors": critical.call_count}, ensure_ascii=False))
            if min(counts) < 1 or hits or not hidden_without_stop or not restored_without_stop or warning.call_count or critical.call_count:
                raise SystemExit("窗口不入镜端到端验证失败")


if __name__ == "__main__":
    main()
