import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication
from region_recorder import MainWindow
from project_store import create_project, read_project


class ProjectIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_create_autosave_reopen_and_protect_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "原素材.mp4"
            writer = cv2.VideoWriter(str(original), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            for i in range(20):
                writer.write(np.full((48, 64, 3), i * 10, np.uint8))
            writer.release()
            original_bytes = original.read_bytes()
            settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings):
                window = MainWindow()
                window.script_input.setPlainText("初始稿件")
                window.open_video_editor(original)
                editor = window.video_editor
                editor._show_output_frame(4)
                editor._trim_start_at_playhead()
                with patch("region_recorder.QFileDialog.getExistingDirectory", return_value=tmp):
                    editor.save_current_project()
                self.assertIsNotNone(editor.project_worker)
                loop = QEventLoop()
                editor.project_worker.finished.connect(loop.quit)
                timeout = QTimer(loop)
                timeout.setSingleShot(True)
                timeout.timeout.connect(loop.quit)
                timeout.start(15000)
                loop.exec()
                timeout.stop()
                self.app.processEvents()
                self.assertIsNotNone(editor.project_path)
                self.assertIsNone(editor.project_worker)
                project_path = editor.project_path
                self.assertEqual(window.recent_projects(), [str(project_path)])
                self.assertEqual(window.script_document.path.read_text(encoding="utf-8"), "初始稿件")
                self.assertEqual(original.read_bytes(), original_bytes)
                self.assertNotEqual(Path(editor.source_path), original)
                self.assertEqual(read_project(project_path)["segments"], [{"start": 4, "end": 20}])
                editor.script_input.setPlainText("更新后的稿件")
                editor._show_output_frame(8)
                editor._trim_end_at_playhead()
                autosave_loop = QEventLoop()
                QTimer.singleShot(1200, autosave_loop.quit)
                autosave_loop.exec()
                self.assertFalse(editor.has_unsaved_edits())
                self.assertEqual(read_project(project_path)["script"], "更新后的稿件")
                expected_segments = [dict(s) for s in editor.segments]
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

                reopened = MainWindow()
                with patch("region_recorder.QFileDialog.getOpenFileName", return_value=(str(project_path), "")):
                    reopened.choose_project()
                self.assertIsNotNone(reopened.video_editor)
                self.assertEqual(reopened.video_editor.segments, expected_segments)
                self.assertEqual(reopened.script_input.toPlainText(), "更新后的稿件")
                self.assertFalse(reopened.video_editor.has_unsaved_edits())
                reopened.close()
                reopened.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_opening_project_preserves_separate_pending_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "source.mp4"
            writer = cv2.VideoWriter(str(original), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            for i in range(10):
                writer.write(np.full((48, 64, 3), i * 10, np.uint8))
            writer.release()
            project_path = create_project(root, original, 64, 48, 10, 10, [{"start": 0, "end": 10}], "工程稿件")
            settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings):
                window = MainWindow()
                window.script_input.setPlainText("独立录前草稿")
                with patch("region_recorder.QFileDialog.getOpenFileName", return_value=(str(project_path), "")):
                    window.choose_project()
                self.assertEqual(window.script_input.toPlainText(), "工程稿件")
                self.assertEqual(window.script_document.path.read_text(encoding="utf-8"), "独立录前草稿")
                window.script_input.setPlainText("修改后的工程稿件")
                window.close()
                self.assertEqual(read_project(project_path)["script"], "修改后的工程稿件")
                self.assertEqual(window.script_document.path.read_text(encoding="utf-8"), "独立录前草稿")
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_history_persists_repairs_moved_project_and_removes_only_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "source.mp4"
            writer = cv2.VideoWriter(str(original), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            for i in range(10):
                writer.write(np.full((48, 64, 3), i * 10, np.uint8))
            writer.release()
            project_path = Path(create_project(root, original, 64, 48, 10, 10, [{"start": 0, "end": 10}], "工程稿件"))
            settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings):
                window = MainWindow()
                self.assertTrue(window.open_project_path(project_path))
                self.assertEqual(window.recent_projects(), [str(project_path)])
                window.show_history_page()
                self.assertIs(window.page_stack.currentWidget(), window.history_page)
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                moved_directory = root / "moved"
                shutil.move(str(project_path.parent), str(moved_directory))
                new_path = moved_directory / "project.json"
                reopened = MainWindow()
                reopened.show_history_page()
                self.assertEqual(reopened.recent_projects(), [str(project_path)])
                with patch("region_recorder.QFileDialog.getOpenFileName", return_value=(str(new_path), "")):
                    reopened.relocate_project(str(project_path))
                self.assertEqual(reopened.recent_projects(), [str(new_path)])
                self.assertEqual(reopened.script_input.toPlainText(), "工程稿件")
                reopened.remove_recent_project(str(new_path))
                self.assertEqual(reopened.recent_projects(), [])
                self.assertTrue(new_path.exists())
                self.assertTrue((moved_directory / "media" / "raw.mp4").exists())
                reopened.close()
                reopened.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
