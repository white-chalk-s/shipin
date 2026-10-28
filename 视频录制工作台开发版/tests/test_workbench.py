import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from region_recorder import MainWindow
from window_capture import WindowTarget


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_range_controls_recording_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings):
                window = MainWindow()
                region = {"left": 10, "top": 20, "width": 400, "height": 300}
                window._region_presets["示例"] = region
                window.region_preset_combo.addItem("示例", "示例")
                window.region_preset_combo.setCurrentIndex(window.region_preset_combo.count() - 1)
                self.assertTrue(window.custom_region.isChecked())
                with patch.object(window, "_start_recording") as start:
                    window.start_recording_by_options()
                    self.assertEqual(start.call_args.args[0], region)
                window.full_region.setChecked(True)
                with patch.object(window, "start_fixed_monitor") as start:
                    window.start_recording_by_options()
                    start.assert_called_once()
                window.region_preset_combo.setCurrentIndex(0)
                window.custom_region.setChecked(True)
                with patch.object(window, "start_selection") as start:
                    window.start_recording_by_options()
                    start.assert_called_once()
                window.resize(900, 600)
                window.show()
                self.app.processEvents()
                self.assertEqual(window.workbench_sidebar.height(), 62)
                self.assertEqual(window.main_scroll.width(), window.page_stack.width())
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_simplified_capture_ignores_legacy_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            settings.setValue("follow_mouse", True)
            settings.setValue("auto_window", True)
            settings.setValue("crop_w", 0.5)
            settings.setValue("crop_h", 0.5)
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings):
                window = MainWindow()
                window.show()
                self.app.processEvents()
                self.assertFalse(window.window_controls.isVisible())
                self.assertFalse(window.preset_controls.isVisible())
                window.custom_region.setChecked(True)
                self.assertTrue(window.preset_controls.isVisible())
                self.assertFalse(window.window_controls.isVisible())
                window.full_region.setChecked(True)
                with patch.object(window, "start_fixed_monitor") as start:
                    window.start_recording_by_options()
                    start.assert_called_once_with(window._selected_monitor_index(), auto_window=False)
                window.window_region.setChecked(True)
                self.assertTrue(window.window_controls.isVisible())
                self.assertFalse(window.preset_controls.isVisible())
                target = WindowTarget(123, 456, "演示窗口")
                window.selected_window_target = target
                window.selected_window_monitor = dict(window._monitor_list[window._selected_monitor_index()])
                region = {"left": 30, "top": 40, "width": 500, "height": 300}
                with patch("region_recorder.window_region", return_value=region), \
                     patch("region_recorder.QTimer.singleShot", side_effect=lambda _ms, callback: callback()), \
                     patch.object(window, "_start_recording") as start:
                    window.start_recording_by_options()
                    self.assertEqual(start.call_args.args[0], region)
                    self.assertEqual(start.call_args.kwargs["crop_rect"], (0.0, 0.0, 1.0, 1.0))
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_full_screen_ignores_saved_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            settings.setValue("crop_w", 0.5)
            settings.setValue("crop_h", 0.5)
            monitor = {"left": 0, "top": 0, "width": 1920, "height": 1200}
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings):
                window = MainWindow()
                with patch.object(window, "_read_connected_monitors", return_value=[monitor]), \
                     patch("region_recorder.QTimer.singleShot", side_effect=lambda _ms, callback: callback()), \
                     patch.object(window, "_start_recording") as start:
                    window.start_fixed_monitor(0, auto_window=False)
                    self.assertEqual(start.call_args.args[0], monitor)
                    self.assertEqual(start.call_args.kwargs["crop_rect"], (0.0, 0.0, 1.0, 1.0))
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_window_mode_uses_selected_handle_and_blocks_empty_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings):
                window = MainWindow()
                window.window_region.setChecked(True)
                with patch("region_recorder.QMessageBox.warning") as warning:
                    window.start_recording_by_options()
                    warning.assert_called_once()
                target = WindowTarget(123, 456, "演示窗口")
                monitor = dict(window._monitor_list[window._selected_monitor_index()])
                window.selected_window_target = target
                window.selected_window_monitor = monitor
                region = {"left": 30, "top": 40, "width": 500, "height": 300}
                with patch("region_recorder.window_region", return_value=region), \
                     patch("region_recorder.QTimer.singleShot", side_effect=lambda _ms, callback: callback()), \
                     patch.object(window, "_start_recording") as start:
                    window.start_recording_by_options()
                    self.assertEqual(start.call_args.kwargs["window_target"], target)
                    self.assertEqual(start.call_args.kwargs["window_monitor"], monitor)
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_embedded_editor_navigation_shared_script_and_unsaved_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            files = [Path(tmp) / "第一段.mp4", Path(tmp) / "第二段.mp4"]
            for path in files:
                writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
                self.assertTrue(writer.isOpened())
                for _ in range(10):
                    writer.write(np.zeros((48, 64, 3), np.uint8))
                writer.release()
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings):
                window = MainWindow()
                window.script_input.setPlainText("录前内容")
                window.open_video_editor(files[0])
                editor = window.video_editor
                self.assertIs(window.page_stack.currentWidget(), editor)
                self.assertEqual(editor.script_input.toPlainText(), "录前内容")
                editor.script_input.setPlainText("剪辑页修改")
                self.assertEqual(window.script_input.toPlainText(), "剪辑页修改")
                editor._show_output_frame(2)
                editor._trim_start_at_playhead()
                window.show_record_page()
                self.assertIs(window.page_stack.currentWidget(), window.main_scroll)
                window.show_editor_page()
                self.assertIs(window.page_stack.currentWidget(), editor)
                with patch("region_recorder.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                    window.open_video_editor(files[1])
                    self.assertIs(window.video_editor, editor)
                with patch("region_recorder.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                    window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
