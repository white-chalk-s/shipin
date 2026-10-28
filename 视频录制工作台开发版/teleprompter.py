"""Independent read-only prompt window with separately persisted geometry."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit


def restore_visible_geometry(window, settings, key):
    geometry = settings.value(key)
    if geometry is not None:
        window.restoreGeometry(geometry)
    screens = QApplication.screens()
    if screens:
        frame = window.frameGeometry()
        screen = next((s for s in screens if s.availableGeometry().intersects(frame)), screens[0])
        available = screen.availableGeometry()
        x = max(available.left(), min(frame.left(), available.right() - min(frame.width(), available.width()) + 1))
        y = max(available.top(), min(frame.top(), available.bottom() - min(frame.height(), available.height()) + 1))
        window.move(x, y)
    return geometry is not None


class TeleprompterWindow(QWidget):
    def __init__(self, text, settings):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.settings = settings
        self._closing = False
        self.setWindowTitle("口播提词")
        self.setMinimumSize(280, 180)
        self.resize(420, 360)
        root = QVBoxLayout(self)
        heading = QLabel("口播提词（只读）")
        root.addWidget(heading)
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setPlainText(text)
        self.font_size = max(12, min(48, int(settings.value("prompter_font_size", 22))))
        self._apply_font()
        root.addWidget(self.text_view, 1)
        controls = QHBoxLayout()
        for label, callback in (("字号−", lambda: self.change_font(-2)),
                                ("字号+", lambda: self.change_font(2)),
                                ("回到开头", lambda: self.text_view.verticalScrollBar().setValue(0)),
                                ("隐藏", self.hide)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        root.addLayout(controls)
        self.restored = restore_visible_geometry(self, settings, "prompter_geometry")
        scroll = int(settings.value("prompter_scroll", 0))
        self._scroll_restore_timer = QTimer(self)
        self._scroll_restore_timer.setSingleShot(True)
        self._scroll_restore_timer.timeout.connect(lambda: self.text_view.verticalScrollBar().setValue(scroll))
        self._scroll_restore_timer.start(0)

    def _apply_font(self):
        font = self.text_view.font()
        font.setPointSize(self.font_size)
        self.text_view.setFont(font)

    def change_font(self, amount):
        self.font_size = max(12, min(48, self.font_size + amount))
        self._apply_font()
        self.settings.setValue("prompter_font_size", self.font_size)

    def save_state(self):
        self.settings.setValue("prompter_geometry", self.saveGeometry())
        self.settings.setValue("prompter_scroll", self.text_view.verticalScrollBar().value())

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "text_view") and self.isVisible():
            self.save_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "text_view") and self.isVisible():
            self.save_state()

    def hideEvent(self, event):
        self.save_state()
        super().hideEvent(event)

    def closeEvent(self, event):
        if not self._closing:
            event.ignore()
            self.hide()
            return
        self.save_state()
        event.accept()

    def finish_and_close(self):
        self._closing = True
        self.close()
