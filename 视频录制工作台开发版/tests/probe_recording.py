"""Record the primary display briefly, inspect it, then delete the temporary video."""
import json
import argparse
import tempfile
import time
from pathlib import Path

import cv2
import mss
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from region_recorder import RecorderWorker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--pause", action="store_true", help="Pause for one second during the run")
    parser.add_argument("--discard", action="store_true", help="Discard instead of saving")
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])
    with mss.mss() as capture:
        monitor = dict(capture.monitors[1])
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "录制测试.mp4"
        worker = RecorderWorker(monitor, target, fps=30, cursor_style="不显示")
        errors = []
        worker.recording_error.connect(errors.append)
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        started = time.perf_counter()
        worker.start()
        if args.pause:
            QTimer.singleShot(1000, worker.pause)
            QTimer.singleShot(2000, worker.resume)
        QTimer.singleShot(int(args.seconds * 1000), worker.discard if args.discard else worker.stop)
        loop.exec()
        elapsed = time.perf_counter() - started
        active = worker._clock.elapsed_ns() / 1_000_000_000
        if args.discard:
            print(json.dumps({"discarded": not target.exists(), "errors": errors}, ensure_ascii=False))
            if target.exists() or errors:
                raise SystemExit(1)
            return
        cap = cv2.VideoCapture(str(target))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if cap.isOpened() else 0
        fps = float(cap.get(cv2.CAP_PROP_FPS)) if cap.isOpened() else 0
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        cap.release()
        print(json.dumps({"screen": [monitor["width"], monitor["height"]],
                          "total_seconds_including_finalization": round(elapsed, 3),
                          "active_seconds": round(active, 3), "frames": frame_count,
                          "fps": fps, "video_seconds": round(frame_count / fps, 3) if fps else None,
                          "readable_first_frame": bool(ok), "diagnostics": worker.diagnostics,
                          "errors": errors}, ensure_ascii=False))
        if errors or not ok:
            raise SystemExit(1)
        if abs(frame_count / fps - active) > 1 / fps + 0.01:
            raise SystemExit("视频时长与有效录制时间不一致")


if __name__ == "__main__":
    main()
