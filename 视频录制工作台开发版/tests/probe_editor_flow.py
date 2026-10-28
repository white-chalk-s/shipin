"""Short real recording followed by the embedded editor and shared draft."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import mss
from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication
from region_recorder import MainWindow


def main():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("save_dir", tmp)
        with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings), \
             patch("region_recorder.QMessageBox.warning") as warning, patch("region_recorder.QMessageBox.critical") as error:
            window = MainWindow()
            window.show()
            window.script_input.setPlainText("录制前的口播稿")
            with mss.mss() as capture:
                monitor = dict(capture.monitors[1])
            window._start_recording(monitor, "流程测试")
            if window.recorder is None:
                raise RuntimeError(f"录制未开始: {warning.call_args}")
            loop = QEventLoop()
            window.recorder.finished.connect(lambda: QTimer.singleShot(600, loop.quit))
            QTimer.singleShot(1500, window.stop_recording)
            loop.exec()
            app.processEvents()
            editor = window.video_editor
            result = {"entered_editor": bool(editor and window.page_stack.currentWidget() is editor),
                      "script_synced": bool(editor and editor.script_input.toPlainText() == "录制前的口播稿"),
                      "source_exists": bool(editor and Path(editor.source_path).exists()),
                      "warnings": warning.call_count, "errors": error.call_count}
            print(json.dumps(result, ensure_ascii=False))
            window.close()
            if not all((result["entered_editor"], result["script_synced"], result["source_exists"])) or result["warnings"] or result["errors"]:
                raise SystemExit("录制到剪辑流程验证失败")


if __name__ == "__main__":
    main()
