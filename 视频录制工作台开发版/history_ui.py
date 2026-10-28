"""Recent project page, styled to match the workbench prototype."""
from pathlib import Path

import cv2
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from project_store import read_project
from workbench_ui import card, label


class ProjectRow(QFrame):
    activated = Signal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mouseDoubleClickEvent(event)


class HistoryPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setObjectName("main")
        content = self
        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 16, 24, 18)
        outer.setSpacing(16)
        outer.addWidget(label("历史项目", "pageTitle"))
        outer.addWidget(label("管理保存在本机的录屏工程。", "pageSubtitle"))
        frame, self.list_layout = card("历史项目", "最近打开的工程")
        list_scroll = QScrollArea()
        list_scroll.setObjectName("panelScroll")
        list_scroll.setWidgetResizable(True)
        list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_scroll.setWidget(frame)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        outer.addWidget(list_scroll, 1)

    def refresh(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        paths = self.owner.recent_projects()
        if not paths:
            self.list_layout.addWidget(label("暂无历史工程。保存或打开工程后，它会显示在这里。", "hint"))
            return
        for path in paths:
            self.list_layout.addWidget(self._row(path))

    def _row(self, path):
        row = ProjectRow()
        row.setStyleSheet("ProjectRow { border-top: 1px solid #e5e9f1; padding-top: 8px; }")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 8, 0, 4)
        preview = QLabel("▦")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setFixedSize(120, 68)
        preview.setStyleSheet("background: #263a5b; color: white; border-radius: 6px; font-size: 22px;")
        layout.addWidget(preview)
        details = QVBoxLayout()
        try:
            project = read_project(path)
            meta = project["metadata"]
            title = str(meta.get("name") or Path(path).parent.name)
            details.addWidget(label(title, "cardTitle"))
            details.addWidget(label(f"最近修改：{meta.get('updated_at', '未知')}", "hint"))
            exported = self.owner.recent_export(path)
            delivery = ("最近交付：" + exported + ("" if Path(exported).is_dir() else "（目录失效）")) if exported else "暂无交付版本"
            details.addWidget(label(delivery, "hint"))
            row.setToolTip(str(path))
            self._thumbnail(preview, project["source"])
            usable = True
        except Exception:
            details.addWidget(label(Path(path).parent.name or "无法读取的工程", "cardTitle"))
            details.addWidget(label("工程路径失效或文件不可读取，请重新定位或移出列表。", "hint"))
            details.addWidget(label(str(path), "hint"))
            usable = False
        layout.addLayout(details, 1)
        actions = QVBoxLayout()
        if usable:
            open_button = QPushButton("打开剪辑")
            open_button.clicked.connect(lambda _checked=False, p=path: self.owner.open_project_path(p))
            actions.addWidget(open_button)
            row.activated.connect(lambda p=path: self.owner.open_project_path(p))
            locate = QPushButton("定位目录")
            locate.clicked.connect(lambda _checked=False, p=path: self.owner.locate_project(p))
            actions.addWidget(locate)
        else:
            repair = QPushButton("重新定位")
            repair.clicked.connect(lambda _checked=False, p=path: self.owner.relocate_project(p))
            actions.addWidget(repair)
        remove = QPushButton("移出列表")
        remove.clicked.connect(lambda _checked=False, p=path: self.owner.remove_recent_project(p))
        actions.addWidget(remove)
        layout.addLayout(actions)
        return row

    @staticmethod
    def _thumbnail(label_widget, source):
        cap = cv2.VideoCapture(str(source))
        try:
            ok, frame = cap.read()
            if ok:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                height, width, _ = rgb.shape
                image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
                label_widget.setPixmap(QPixmap.fromImage(image).scaled(label_widget.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        finally:
            cap.release()
