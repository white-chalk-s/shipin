import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from region_recorder import VideoEditorWindow, VideoExportWorker


class EditorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_and_export_skip_removed_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "原视频.mp4"
            output = Path(tmp) / "导出.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            for i in range(10):
                writer.write(np.full((48, 64, 3), i * 20, dtype=np.uint8))
            writer.release()

            editor = VideoEditorWindow(source)
            editor.segments = [{"start": 0, "end": 3}, {"start": 6, "end": 10}]
            editor._refresh_segments_table(select_row=0)
            self.assertEqual(editor.timeline_slider.maximum(), 6)
            editor._show_output_frame(2)
            editor._on_play_tick()
            self.assertEqual(editor.current_frame, 6)
            editor._show_output_frame(6)
            self.assertEqual(editor.current_frame, 9)
            editor._show_output_frame(3)
            self.assertEqual(editor.current_frame, 6)
            editor.close()
            editor.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

            worker = VideoExportWorker(source, [{"start": 0, "end": 3}, {"start": 6, "end": 10}], output)
            failures = []
            worker.export_failed.connect(failures.append)
            worker.run()
            self.assertEqual(failures, [])
            cap = cv2.VideoCapture(str(output))
            values = []
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                values.append(frame.mean())
            cap.release()
            self.assertEqual(len(values), 7)
            self.assertGreater(values[3] - values[2], 45)
            self.assertTrue(source.exists())

    def test_split_delete_trim_undo_and_export_use_same_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "原视频.mp4", Path(tmp) / "剪辑.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            for i in range(10):
                writer.write(np.full((48, 64, 3), i * 20, dtype=np.uint8))
            writer.release()
            editor = VideoEditorWindow(source)
            editor._on_timeline_clicked(0, 3)
            editor._split_at_current_frame()
            editor._on_timeline_clicked(1, 6)
            editor._split_at_current_frame()
            editor._on_timeline_clicked(1, 4)
            with patch("region_recorder.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                editor._delete_selected_segment()
            self.assertEqual(editor.segments, [{"start": 0, "end": 3}, {"start": 6, "end": 10}])
            editor._show_output_frame(1)
            editor._trim_start_at_playhead()
            self.assertEqual(editor.segments, [{"start": 1, "end": 3}, {"start": 6, "end": 10}])
            editor._show_output_frame(4)
            editor._trim_end_at_playhead()
            self.assertEqual(editor.segments, [{"start": 1, "end": 3}, {"start": 6, "end": 9}])
            self.assertEqual(editor.timeline_slider.maximum(), 4)
            worker = VideoExportWorker(source, editor.segments, output)
            failures = []
            worker.export_failed.connect(failures.append)
            worker.run()
            cap = cv2.VideoCapture(str(output))
            self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 5)
            cap.release()
            self.assertEqual(failures, [])
            editor._undo_edit()
            self.assertEqual(editor.segments, [{"start": 1, "end": 3}, {"start": 6, "end": 10}])
            editor.close()
            editor.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_click_on_track_selects_clip_and_seeks_edited_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "video.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            for _ in range(10):
                writer.write(np.zeros((48, 64, 3), np.uint8))
            writer.release()
            editor = VideoEditorWindow(source)
            editor.segments = [{"start": 0, "end": 3}, {"start": 6, "end": 10}]
            editor._refresh_segments_table(select_row=0)
            editor.timeline_track.resize(700, 66)
            QTest.mouseClick(editor.timeline_track, Qt.MouseButton.LeftButton, pos=QPoint(450, 25))
            self.assertEqual(editor._selected_row(), 1)
            self.assertEqual(editor.current_frame, 7)
            editor.close()
            editor.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
