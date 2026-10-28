"""Export page following the workbench prototype and V1.3 handoff contract."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget
from timeline import total_frames
from presentation import frame_size
from annotations import crop_frame
import numpy as np
from workbench_ui import card, label


class ExportPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setObjectName('main')
        content = self
        outer = QVBoxLayout(content); outer.setContentsMargins(24, 16, 24, 18); outer.setSpacing(10)
        outer.addWidget(label('导出与交付', 'pageTitle'))
        outer.addWidget(label('打包正常速度的无声视频、口播稿和交付元数据。', 'pageSubtitle'))
        columns = QHBoxLayout(); columns.setSpacing(16); outer.addLayout(columns)
        left = QVBoxLayout(); right = QVBoxLayout(); columns.addLayout(left, 3); columns.addLayout(right, 2)
        frame, body = card('导出与交付', '第三步'); left.addWidget(frame)
        body.addWidget(label('项目名称', 'fieldLabel'))
        self.name_input = QLineEdit(); body.addWidget(self.name_input)
        body.addWidget(label('视频规格', 'fieldLabel'))
        self.spec = label('', 'noteBox'); body.addWidget(self.spec)
        body.addWidget(label('交付清单', 'fieldLabel'))
        body.addWidget(label('✓  video.mp4    无声 H.264 视频\n✓  script.txt    UTF-8 口播稿\n✓  handoff.json    实际视频信息与交付元数据', 'noteBox'))
        body.addWidget(label('交付位置', 'fieldLabel'))
        location = QHBoxLayout(); self.directory = label(''); location.addWidget(self.directory, 1)
        choose = QPushButton('更改位置'); choose.clicked.connect(owner.choose_handoff_directory); location.addWidget(choose); body.addLayout(location)
        self.progress = QProgressBar(); self.progress.setValue(0); body.addWidget(self.progress)
        self.status = label('准备就绪', 'hint'); body.addWidget(self.status)
        self.export_button = QPushButton('导出交付目录'); self.export_button.setObjectName('primaryButton'); self.export_button.clicked.connect(owner.start_handoff_export); body.addWidget(self.export_button)
        self.cancel_button = QPushButton('取消导出'); self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(owner.cancel_handoff_export); body.addWidget(self.cancel_button)
        left.addStretch()
        summary, summary_body = card('交付摘要')
        right.addWidget(summary)
        self.summary = label('请先打开或录制视频。', 'noteBox')
        summary_body.addWidget(self.summary)
        summary_body.addWidget(label('导出不会覆盖原始素材。', 'hint'))
        right.addStretch(); outer.addStretch()

    def refresh(self):
        editor = self.owner.video_editor
        self.directory.setText(str(self.owner.handoff_directory))
        if editor is None:
            self.name_input.setText('')
            self.spec.setText('请先打开或录制视频。')
            self.summary.setText('等待素材')
            self.export_button.setEnabled(False)
            return
        self.name_input.setText(editor.source.stem if editor.project_path is None else editor.project_path.parent.name)
        seconds = total_frames(editor.segments) / editor.fps
        cropped = crop_frame(np.zeros((editor.frame_height, editor.frame_width, 3), dtype=np.uint8), editor.video_crop)
        output_width, output_height = frame_size(cropped.shape[1], cropped.shape[0], editor.presentation)
        self.spec.setText(f'剪辑后时长：约 {seconds:.2f} 秒\n输出尺寸：{output_width} × {output_height}\n帧率：30 FPS  ·  编码：MP4 / H.264  ·  无声')
        self.summary.setText(f'预计时长：{seconds:.2f} 秒\nMP4 · 30 FPS · 无声')
        self.export_button.setEnabled(bool(editor.segments) and editor.export_worker is None and editor.project_worker is None)
