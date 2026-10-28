import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from region_recorder import MainWindow


class StartGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_failed_exclusion_blocks_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), \
                 patch("region_recorder.QSettings", return_value=settings), \
                 patch("region_recorder.verify_window_exclusion", side_effect=RuntimeError("test failure")), \
                 patch("region_recorder.QMessageBox.warning") as warning:
                window = MainWindow()
                window.script_input.setPlainText("测试口播")
                window._start_recording({"left": 0, "top": 0, "width": 320, "height": 240})
                self.assertIsNone(window.recorder)
                self.assertIsNone(window.control_window)
                self.assertIsNone(window.prompter_window)
                self.assertTrue(window.start_button.isEnabled())
                warning.assert_called_once()
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
