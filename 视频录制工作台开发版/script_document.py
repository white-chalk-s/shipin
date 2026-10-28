"""UTF-8 draft persisted independently of the working directory."""
import os
import tempfile
from pathlib import Path


class ScriptDocument:
    def __init__(self, path):
        self.path = Path(path)
        self.text = ""

    def load(self):
        if self.path.exists():
            self.text = self.path.read_text(encoding="utf-8-sig")
        return self.text

    def save(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix="draft-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            self.text = text
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
