import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from script_document import ScriptDocument


class ScriptDocumentTests(unittest.TestCase):
    def test_utf8_persist_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "中文目录" / "draft.txt"
            doc = ScriptDocument(path)
            doc.save("你好，世界\n第二行")
            self.assertEqual(ScriptDocument(path).load(), "你好，世界\n第二行")

    def test_failed_atomic_replace_preserves_previous_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "draft.txt"
            doc = ScriptDocument(path)
            doc.save("原稿")
            with patch("script_document.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    doc.save("新稿")
            self.assertEqual(path.read_text(encoding="utf-8"), "原稿")
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])
