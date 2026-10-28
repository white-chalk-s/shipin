import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication
from handoff import export_handoff, _executable, _probe_video, ExportControl, HandoffCancelled
from region_recorder import MainWindow
from project_store import create_project


@unittest.skipUnless(_executable('ffmpeg') and _executable('ffprobe'), 'FFmpeg and ffprobe required')
class HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _source(self, root):
        path = root / 'source.mp4'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 48))
        self.assertTrue(writer.isOpened())
        for i in range(20):
            writer.write(np.full((48, 64, 3), i * 10, np.uint8))
        writer.release()
        return path

    def test_real_handoff_and_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self._source(root); original = source.read_bytes()
            result = export_handoff(root, source, [{'start': 2, 'end': 8}, {'start': 12, 'end': 18}], 10, '测试稿件', 'id-1', '测试')
            meta = json.loads((result / 'handoff.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['video']['video_codec'], 'h264')
            self.assertEqual(meta['video']['fps_num'], 30)
            self.assertFalse(meta['video']['has_audio'])
            self.assertAlmostEqual(meta['video']['duration_ms'], 1200, delta=40)
            self.assertEqual((result / 'script.txt').read_text(encoding='utf-8'), '测试稿件')
            self.assertEqual(source.read_bytes(), original)
            before = set(root.iterdir())
            with self.assertRaises(ValueError):
                export_handoff(root, source, [{'start': 0, 'end': 99}], 10, '稿件', 'id-1', '错误')
            self.assertEqual(set(root.iterdir()), before)
            with self.assertRaises(ValueError):
                export_handoff(root, source, [{'start': 0, 'end': 1}], 10, '  ', 'id-1', '错误')
            self.assertEqual(set(root.iterdir()), before)
            with patch('handoff._probe_video', side_effect=RuntimeError('probe failed')):
                with self.assertRaisesRegex(RuntimeError, 'probe failed'):
                    export_handoff(root, source, [{'start': 0, 'end': 10}], 10, '稿件', 'id-1', '错误')
            self.assertEqual(set(root.iterdir()), before)

    def test_page_runs_worker_and_records_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self._source(root)
            project = create_project(root, source, 64, 48, 10, 20, [{'start': 0, 'end': 20}], '项目稿')
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            settings.setValue('save_dir', tmp)
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}), patch('region_recorder.QSettings', return_value=settings), patch('region_recorder.QMessageBox.information'):
                window = MainWindow(); self.assertTrue(window.open_project_path(project)); window.show_export_page()
                self.assertIs(window.page_stack.currentWidget(), window.export_page)
                window.start_handoff_export(); worker = window.video_editor.export_worker
                self.assertIsNotNone(worker)
                loop = QEventLoop(); worker.finished.connect(loop.quit)
                timeout = QTimer(loop); timeout.setSingleShot(True); timeout.timeout.connect(loop.quit); timeout.start(30000)
                loop.exec(); timeout.stop(); self.app.processEvents()
                self.assertIsNone(window.video_editor.export_worker)
                result = Path(window.recent_export(project))
                self.assertTrue((result / 'handoff.json').is_file())
                self.assertTrue(window.script_input.isEnabled())
                window.show_history_page()
                window.close(); window.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_cancel_during_encoding_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self._source(root); before = set(root.iterdir()); source_bytes = source.read_bytes()
            control = ExportControl()
            def cancel_at_progress(value):
                if value > 0:
                    control.cancel()
            with self.assertRaises(HandoffCancelled):
                export_handoff(root, source, [{'start': 0, 'end': 20}], 10, '稿件', 'id', '测试',
                               progress=cancel_at_progress, control=control)
            self.assertEqual(set(root.iterdir()), before)
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_cancel_interrupts_media_probe(self):
        ready, stopped = threading.Event(), threading.Event()
        class FakeProbe:
            returncode = -1
            def communicate(self):
                ready.set()
                stopped.wait(5)
                return b'', b''
            def poll(self):
                return -1 if stopped.is_set() else None
            def terminate(self):
                stopped.set()
        control, failures = ExportControl(), []
        def run_probe():
            try:
                _probe_video('ffprobe', 'video.mp4', control)
            except Exception as exc:
                failures.append(exc)
        with patch('handoff.subprocess.Popen', return_value=FakeProbe()):
            thread = threading.Thread(target=run_probe)
            thread.start()
            self.assertTrue(ready.wait(5))
            control.cancel()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], HandoffCancelled)

    def test_cancel_from_page_restores_controls_and_skips_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = self._source(root)
            project = create_project(root, source, 64, 48, 10, 20, [{'start': 0, 'end': 20}], '项目稿')
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat); settings.setValue('save_dir', tmp)
            with patch.dict(os.environ, {'LOCALAPPDATA': tmp}), patch('region_recorder.QSettings', return_value=settings):
                window = MainWindow(); self.assertTrue(window.open_project_path(project)); window.show_export_page()
                window.start_handoff_export(); worker = window.video_editor.export_worker
                self.assertTrue(window.export_page.cancel_button.isEnabled())
                loop = QEventLoop(); worker.finished.connect(loop.quit)
                timeout = QTimer(loop); timeout.setSingleShot(True); timeout.timeout.connect(loop.quit); timeout.start(15000)
                window.cancel_handoff_export(); loop.exec(); timeout.stop(); self.app.processEvents()
                self.assertIsNone(window.video_editor.export_worker)
                self.assertIn('已取消', window.export_page.status.text())
                self.assertTrue(window.export_page.export_button.isEnabled())
                self.assertTrue(window.script_input.isEnabled())
                self.assertFalse(window.recent_export(project))
                self.assertFalse(list(root.glob('.vcw-export-*')))
                window.close(); window.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
