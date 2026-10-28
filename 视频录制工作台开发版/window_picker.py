from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QHBoxLayout
from window_capture import list_windows


class WindowPicker(QDialog):
    def __init__(self, monitor, parent=None):
        super().__init__(parent)
        self.monitor = monitor
        self.selected_target = None
        self.setWindowTitle("选择要录制的窗口")
        self.resize(620, 420)
        root = QVBoxLayout(self)
        hint = QLabel("仅显示完整位于所选显示器的可见窗口。录制时请保持窗口未最小化，并避免其他窗口遮挡。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        root.addWidget(self.list)
        row = QHBoxLayout()
        refresh = QPushButton("刷新列表")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.accept_button = QPushButton("选择窗口")
        self.accept_button.clicked.connect(self._accept)
        row.addWidget(self.accept_button)
        root.addLayout(row)
        self.refresh()

    def refresh(self):
        self.list.clear()
        try:
            windows = list_windows(self.monitor)
        except RuntimeError as exc:
            item = QListWidgetItem(f"无法读取窗口：{exc}")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
            self.accept_button.setEnabled(False)
            return
        for target, region in windows:
            item = QListWidgetItem(f"{target.title}    ·    {region['width']} × {region['height']}  ·  进程 {target.process_id}")
            item.setData(Qt.ItemDataRole.UserRole, target)
            self.list.addItem(item)
        self.accept_button.setEnabled(bool(windows))
        if windows:
            self.list.setCurrentRow(0)
        else:
            item = QListWidgetItem("没有符合条件的窗口，请把目标窗口完整移到所选显示器后刷新。")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)

    def _accept(self):
        item = self.list.currentItem()
        target = item.data(Qt.ItemDataRole.UserRole) if item else None
        if target:
            self.selected_target = target
            self.accept()
