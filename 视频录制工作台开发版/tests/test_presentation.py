import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from presentation import render_frame, validate_options
from project_store import create_project, read_project, save_project
from handoff import export_handoff
from region_recorder import MainWindow

class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_stage_style_and_ratio_survive_project_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "source.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 90))
            self.assertTrue(writer.isOpened())
            for _ in range(10): writer.write(np.full((90, 160, 3), 180, np.uint8))
            writer.release()
            stage = {"id": "one", "type": "stage", "text": "第一阶段", "start": 2, "end": 7,
                     "x": 0.0, "y": 0.0, "size": .05, "color": "#ffffff"}
            options = {"ratio": [1, 1], "fit": "contain", "background": "#ff0000", "foreground": "#ffffff", "align": "center"}
            project = create_project(root, source, 160, 90, 10, 10, [{"start": 0, "end": 10}], "稿件", [stage], None, options)
            loaded = read_project(project)
            self.assertEqual(loaded["presentation"], options)
            self.assertEqual(loaded["annotations"], [stage])
            save_project(project, loaded["segments"], "稿件", 10)
            self.assertEqual(read_project(project)["presentation"], options)
            preview = render_frame(np.full((90, 160, 3), 180, np.uint8), 3, [stage], None, options)
            self.assertEqual(preview.shape[:2], (160, 160))
            self.assertGreater(int(preview[2, 2, 2]), int(preview[2, 2, 0]))
            absent = render_frame(np.full((90, 160, 3), 180, np.uint8), 8, [stage], None, options)
            self.assertEqual(int(absent[2, 2].sum()), 0)
            target = export_handoff(root, source, [{"start": 0, "end": 10}], 10, "稿件", "id", "演示", [stage], None, options)
            cap = cv2.VideoCapture(str(target / "video.mp4"))
            self.assertEqual((int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))), (160, 160))
            cap.set(cv2.CAP_PROP_POS_FRAMES, 9); ok, frame = cap.read(); cap.release()
            self.assertTrue(ok)
            self.assertGreater(int(frame[2, 2, 2]), int(frame[2, 2, 0]) + 80)

    def test_stage_ui_and_old_recording_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root / "source.mp4"; settings=QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
            settings.setValue("save_dir", tmp)
            writer=cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 90))
            for _ in range(30): writer.write(np.full((90,160,3), 100, np.uint8))
            writer.release()
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch("region_recorder.QSettings", return_value=settings):
                window=MainWindow(); window.open_video_editor(source); editor=window.video_editor
                editor._add_stage(); self.assertEqual(editor.annotations[0]["type"], "stage")
                editor.stage_text.setText("第二阶段"); editor._update_stage_text()
                editor._change_annotation_range(0, 5, 20)
                self.assertEqual((editor.annotations[0]["text"], editor.annotations[0]["start"], editor.annotations[0]["end"]), ("第二阶段", 5, 20))
                editor._set_presentation(ratio=[9,16], align="left")
                self.assertTrue(editor.has_unsaved_edits())
                self.assertEqual(editor.presentation["ratio"], [9,16])
                editor._undo_edit()
                self.assertEqual(editor.annotations[0]["text"], "新阶段")
                with patch("region_recorder.QMessageBox.question", return_value=__import__("PySide6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes): window.close()

if __name__ == "__main__": unittest.main()
