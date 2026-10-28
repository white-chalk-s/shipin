import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from project_store import create_project, read_project, save_project


class ProjectStoreTests(unittest.TestCase):
    def test_create_save_reopen_and_source_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "原视频.mp4"
            source.write_bytes(b"original bytes")
            path = create_project(tmp, source, 640, 480, 30, 300,
                                  [{"start": 0, "end": 30}, {"start": 90, "end": 300}], "中文稿件")
            loaded = read_project(path)
            self.assertEqual(loaded["segments"], [{"start": 0, "end": 30}, {"start": 90, "end": 300}])
            self.assertEqual(loaded["script"], "中文稿件")
            self.assertEqual(loaded["source"].read_bytes(), source.read_bytes())
            self.assertNotEqual(loaded["source"], source)
            save_project(path, [{"start": 3, "end": 60}], "新稿", 30)
            loaded = read_project(path)
            self.assertEqual(loaded["segments"], [{"start": 3, "end": 60}])
            self.assertEqual(loaded["script"], "新稿")
            self.assertEqual(source.read_bytes(), b"original bytes")

    def test_interrupted_manifest_update_recovers_old_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.mp4"
            source.write_bytes(b"source")
            path = create_project(tmp, source, 2, 2, 30, 60, [{"start": 0, "end": 60}], "旧稿")
            import project_store
            original_atomic = project_store._atomic_bytes
            def fail_manifest(dest, data):
                if Path(dest).name == "project.json":
                    raise OSError("中断")
                return original_atomic(dest, data)
            with patch("project_store._atomic_bytes", side_effect=fail_manifest):
                with self.assertRaises(OSError):
                    save_project(path, [{"start": 0, "end": 30}], "新稿", 30)
            loaded = read_project(path)
            self.assertEqual(loaded["script"], "旧稿")
            self.assertTrue(loaded["recovered"])
            self.assertEqual(loaded["segments"], [{"start": 0, "end": 60}])

    def test_rejects_path_escape_unknown_version_and_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.mp4"
            source.write_bytes(b"source")
            path = create_project(tmp, source, 2, 2, 30, 60, [{"start": 0, "end": 60}], "稿")
            data = json.loads(path.read_text(encoding="utf-8"))
            for mutate in (lambda d: d["source"].update(file="../a.mp4"),
                           lambda d: d.update(schema_version="2.0.0"),
                           lambda d: d["timeline"].update(annotations=[{"type": "text"}])):
                current = json.loads(json.dumps(data))
                mutate(current)
                path.write_text(json.dumps(current), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_project(path)

    def test_failed_copy_does_not_leave_partial_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"source")
            with patch("project_store.shutil.copyfile", side_effect=OSError("磁盘空间不足")):
                with self.assertRaises(OSError):
                    create_project(tmp, source, 2, 2, 30, 60, [{"start": 0, "end": 60}], "稿")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["source.mp4"])

    def test_missing_source_does_not_modify_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.mp4"
            source.write_bytes(b"source")
            path = create_project(tmp, source, 2, 2, 30, 60, [{"start": 0, "end": 60}], "稿")
            project_before = path.read_bytes()
            read_project(path)["source"].unlink()
            with self.assertRaises(FileNotFoundError):
                read_project(path)
            self.assertEqual(path.read_bytes(), project_before)
