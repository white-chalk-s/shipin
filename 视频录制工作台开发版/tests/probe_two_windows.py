"""Verify both actual window types and independent hide/move on this display."""
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from capture_guard import enable_exclusion, verify_window_exclusion
from region_recorder import RecordingControlWindow
from teleprompter import TeleprompterWindow


def main():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
        control = RecordingControlWindow({"left": 0, "top": 0, "width": 640, "height": 480}, "测试", settings=settings)
        prompt = TeleprompterWindow("测试稿件\n第二行", settings)
        control.move(80, 80)
        prompt.move(700, 100)
        control.show()
        prompt.show()
        app.processEvents()
        verify_window_exclusion(control)
        verify_window_exclusion(prompt)
        original = prompt.pos()
        control.move(120, 100)
        app.processEvents()
        independent = prompt.pos() == original
        prompt.close()
        hidden = not prompt.isVisible() and control.isVisible()
        restored = enable_exclusion(prompt)
        prompt.show()
        app.processEvents()
        verify_window_exclusion(prompt)
        prompt.finish_and_close()
        control.finish_and_close()
        print(json.dumps({"both_excluded": True, "independent": independent,
                          "hide_keeps_control": hidden, "restore_exclusion": restored}))
        if not all((independent, hidden, restored)):
            raise SystemExit("双窗口测试失败")


if __name__ == "__main__":
    main()
