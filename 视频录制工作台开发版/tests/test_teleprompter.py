import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from teleprompter import TeleprompterWindow


class TeleprompterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_read_only_close_hides_and_font_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            window = TeleprompterWindow("口播内容", settings)
            window.show()
            self.app.processEvents()
            self.assertTrue(window.text_view.isReadOnly())
            self.assertEqual(window.text_view.toPlainText(), "口播内容")
            window.change_font(2)
            window.close()
            self.assertFalse(window.isVisible())
            self.assertEqual(int(settings.value("prompter_font_size")), 24)
            window.finish_and_close()

    def test_restores_separate_saved_size_and_returns_from_offscreen(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            first = TeleprompterWindow("稿", settings)
            first.show()
            first.resize(460, 310)
            self.app.processEvents()
            first.save_state()
            first.finish_and_close()
            second = TeleprompterWindow("稿", settings)
            self.assertTrue(second.restored)
            self.assertEqual(second.size().width(), 460)
            second.move(100000, 100000)
            second.save_state()
            second.finish_and_close()
            third = TeleprompterWindow("稿", settings)
            self.assertTrue(any(s.availableGeometry().intersects(third.frameGeometry()) for s in self.app.screens()))
            third.finish_and_close()
