import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from annotations import render_annotations
from handoff import _executable, export_handoff
from project_store import create_project, read_project, save_project
from region_recorder import MainWindow, VideoEditorWindow, VideoExportWorker


class AnnotationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _source(self, root):
        source = root / 'source.mp4'
        writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'), 10, (160, 90))
        self.assertTrue(writer.isOpened())
        for _ in range(20): writer.write(np.zeros((90, 160, 3), np.uint8))
        writer.release()
        return source

    def test_types_render_only_during_source_interval(self):
        frame = np.zeros((90, 160, 3), np.uint8)
        for kind in ('text', 'arrow', 'box', 'sticker'):
            a = {'id': kind, 'type': kind, 'text': '文字', 'start': 5, 'end': 10, 'x': .1, 'y': .2, 'size': .1, 'color': '#ff0000'}
            self.assertFalse(render_annotations(frame, 4, [a]).any())
            self.assertTrue(render_annotations(frame, 5, [a]).any(), kind)
            self.assertFalse(render_annotations(frame, 10, [a]).any())

    def test_ui_undo_autosave_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self._source(root)
            project = create_project(root, source, 160, 90, 10, 20, [{'start': 0, 'end': 20}], '稿件')
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat); settings.setValue('save_dir', tmp)
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}), patch('region_recorder.QSettings', return_value=settings):
                window = MainWindow(); self.assertTrue(window.open_project_path(project)); editor = window.video_editor
                editor._show_output_frame(5); editor.annotation_tool.setCurrentIndex(editor.annotation_tool.findData('text'))
                QTest.mouseClick(editor.preview_label, Qt.MouseButton.LeftButton, pos=editor.preview_label.rect().center())
                self.assertEqual(editor.annotations[0]['start'], 5)
                editor.annotation_text.setText('中文说明'); editor.annotation_duration.setValue(.7); editor._apply_annotation()
                self.assertEqual(editor.annotations[0]['text'], '中文说明')
                editor._undo_edit(); self.assertEqual(editor.annotations[0]['text'], '文字说明')
                editor.annotation_list.setCurrentRow(0); editor.annotation_text.setText('中文说明'); editor._apply_annotation()
                window.close(); window.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                restored = read_project(project)
                self.assertEqual(restored['annotations'][0]['text'], '中文说明')
                self.assertEqual(restored['annotations'][0]['start'], 5)
                reopened = MainWindow(); self.assertTrue(reopened.open_project_path(project))
                self.assertEqual(reopened.video_editor.annotations, restored['annotations'])
                reopened.close(); reopened.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_direct_edit_timeline_crop_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self._source(root)
            editor = VideoEditorWindow(source); editor.show(); self.app.processEvents()
            editor.annotation_tool.setCurrentIndex(editor.annotation_tool.findData('text'))
            r = editor.preview_label.image_rect()
            QTest.mouseClick(editor.preview_label, Qt.MouseButton.LeftButton, pos=r.center().toPoint())
            self.assertEqual(len(editor.annotations), 1)
            preview = editor.preview_label
            bounds = preview._bounds(editor.annotations[0])
            start = bounds.center().toPoint(); end = start + QPoint(15, 8)
            old_x = editor.annotations[0]['x']
            QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(preview, end)
            QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=end)
            self.assertGreater(editor.annotations[0]['x'], old_x)
            bounds = preview._bounds(editor.annotations[0]); old_size = editor.annotations[0]['size']
            handle = bounds.bottomRight().toPoint()
            QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=handle)
            QTest.mouseMove(preview, handle + QPoint(0, 15))
            QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=handle + QPoint(0, 15))
            self.assertGreater(editor.annotations[0]['size'], old_size)
            editor.annotations[0]['end'] = 10
            editor.annotation_track.set_state(editor.segments, editor.annotations, 0)
            track = editor.annotation_track; x = round(track.width() / 2)
            QTest.mousePress(track, Qt.MouseButton.LeftButton, pos=QPoint(x, track.height()//2))
            QTest.mouseMove(track, QPoint(round(track.width()*.75), track.height()//2))
            QTest.mouseRelease(track, Qt.MouseButton.LeftButton, pos=QPoint(round(track.width()*.75), track.height()//2))
            self.assertGreater(editor.annotations[0]['end'], 10)
            # Clicking blank clears selection without adding another annotation.
            QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=QPoint(round(r.left()+5), round(r.top()+5)))
            self.assertEqual(editor.annotation_list.currentRow(), -1)
            self.assertEqual(len(editor.annotations), 1)
            editor._start_video_crop(); r = preview.image_rect()
            for start, end in [
                (QPoint(round(r.left()+1), round(r.center().y())), QPoint(round(r.left()+r.width()*.25), round(r.center().y()))),
                (QPoint(round(r.right()-1), round(r.center().y())), QPoint(round(r.left()+r.width()*.75), round(r.center().y()))),
                (QPoint(round(r.center().x()), round(r.top()+1)), QPoint(round(r.center().x()), round(r.top()+r.height()*.25))),
                (QPoint(round(r.center().x()), round(r.bottom()-1)), QPoint(round(r.center().x()), round(r.top()+r.height()*.75))),
            ]:
                QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=start)
                QTest.mouseMove(preview, end); QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, pos=end)
            self.assertTrue(editor.crop_apply_button.isEnabled()); editor._apply_video_crop()
            self.assertLess(editor.video_crop[2], .6)
            crop = editor.video_crop
            editor._start_video_crop()
            QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=preview.image_rect().center().toPoint())
            self.assertFalse(editor.crop_mode)
            self.assertEqual(editor.video_crop, crop)
            editor._start_video_crop()
            self.assertEqual(editor.pending_crop, crop)
            QTest.mouseClick(editor.script_input.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
            self.assertFalse(editor.crop_mode)
            project = create_project(root, source, 160, 90, 10, 20, editor.segments, '稿件', editor.annotations, crop)
            self.assertEqual(read_project(project)['crop'], crop)
            output = root / 'edited.mp4'; worker = VideoExportWorker(source, editor.segments, output, editor.annotations, crop)
            errors = []; worker.export_failed.connect(errors.append); worker.run()
            self.assertEqual(errors, [])
            cap = cv2.VideoCapture(str(output)); self.assertTrue(cap.isOpened())
            self.assertLess(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), 160); cap.release()
            editor._undo_edit(); self.assertEqual(editor.video_crop, (0., 0., 1., 1.))
            editor.annotation_list.setCurrentRow(0); QTest.keyClick(preview, Qt.Key.Key_Delete)
            self.assertEqual(editor.annotations, [])
            editor.project_path = None
            with patch.object(editor, 'has_unsaved_edits', return_value=False): editor.close()

    @unittest.skipUnless(_executable('ffmpeg') and _executable('ffprobe'), 'FFmpeg required')
    def test_handoff_uses_crop_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self._source(root)
            result = export_handoff(root, source, [{'start': 0, 'end': 10}], 10, '稿件', 'id', '裁剪', crop=(.25, .2, .5, .6))
            cap = cv2.VideoCapture(str(result / 'video.mp4'))
            self.assertEqual((int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))), (80, 54))
            cap.release()

    @unittest.skipUnless(_executable('ffmpeg') and _executable('ffprobe'), 'FFmpeg required')
    def test_handoff_preserves_source_time_across_deleted_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self._source(root)
            annotation = {'id': 'a', 'type': 'box', 'text': '', 'start': 5, 'end': 11, 'x': .1, 'y': .2, 'size': .1, 'color': '#ff0000'}
            result = export_handoff(root, source, [{'start': 0, 'end': 4}, {'start': 8, 'end': 14}], 10, '稿件', 'project', '演示', [annotation])
            cap = cv2.VideoCapture(str(result / 'video.mp4'))
            cap.set(cv2.CAP_PROP_POS_FRAMES, 3); ok, before = cap.read(); self.assertTrue(ok)
            cap.set(cv2.CAP_PROP_POS_FRAMES, 14); ok, active = cap.read(); self.assertTrue(ok)
            cap.set(cv2.CAP_PROP_POS_FRAMES, 25); ok, after = cap.read(); self.assertTrue(ok)
            cap.release()
            self.assertLess(before.mean(), 2)
            self.assertGreater(active[:, :, 2].max(), 100)
            self.assertLess(after.mean(), 2)
