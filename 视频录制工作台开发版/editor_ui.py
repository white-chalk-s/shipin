"""Prototype-aligned editor surface and an output-time-based video track."""
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QTableWidget, QAbstractItemView, QProgressBar, QPlainTextEdit, QScrollArea, QFrame,
                               QListWidget, QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QFormLayout, QTabWidget, QSizePolicy, QColorDialog, QButtonGroup, QGridLayout)
from workbench_ui import card, label, STYLE
from timeline import total_frames, output_to_source
from annotations import annotation_bounds


class TimelineStrip(QWidget):
    seek_requested = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.segments = []
        self.position = 0
        self.selected = -1
        self.setMinimumHeight(66)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击片段选择并定位播放头")

    def set_state(self, segments, position, selected):
        self.segments = [dict(s) for s in segments]
        self.position = position
        self.selected = selected
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f2f5fb"))
        count = total_frames(self.segments)
        if not count:
            painter.setPen(QColor("#8997ad"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "没有保留片段")
            return
        used = 0
        for index, segment in enumerate(self.segments):
            length = segment["end"] - segment["start"]
            left = self.width() * used / count
            right = self.width() * (used + length) / count
            rect = QRectF(left, 1, max(0, right - left - 1), self.height() - 2)
            painter.fillRect(rect, QColor("#e7ecf6" if index % 2 else "#dde6ff"))
            if index == self.selected:
                painter.setPen(QPen(QColor("#aabfff"), 1))
                painter.drawRect(rect.adjusted(1.5, 1.5, -1.5, -1.5))
            painter.setPen(QColor("#27489b"))
            if rect.width() > 48:
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"片段 {index + 1}")
            used += length
        x = min(self.width() - 1, max(0, self.width() * self.position / count))
        painter.setPen(QPen(QColor("#e45c5c"), 2))
        painter.drawLine(int(x), 0, int(x), self.height())

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        count = total_frames(self.segments)
        if not count:
            return
        position = max(0, min(count - 1, int(event.position().x() * count / max(1, self.width()))))
        offset = 0
        for row, segment in enumerate(self.segments):
            length = segment["end"] - segment["start"]
            if position < offset + length:
                self.seek_requested.emit(row, position)
                break
            offset += length


class AnnotationPreview(QLabel):
    clicked = Signal(float, float)

    def __init__(self, editor, text):
        super().__init__(text)
        self.editor = editor
        self._drag = None
        self._crop_edge = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def image_rect(self):
        pixmap = self.pixmap()
        if pixmap.isNull(): return QRectF()
        w, h = pixmap.width() / pixmap.devicePixelRatio(), pixmap.height() / pixmap.devicePixelRatio()
        return QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)

    def _source_point(self, pos):
        r = self.image_rect()
        if r.isEmpty(): return None
        x = min(1., max(0., (pos.x()-r.left()) / r.width()))
        y = min(1., max(0., (pos.y()-r.top()) / r.height()))
        crop = (0., 0., 1., 1.) if self.editor.crop_mode else self.editor.video_crop
        return QPointF(crop[0]+x*crop[2], crop[1]+y*crop[3])

    def _bounds(self, a):
        e = self.editor; r = self.image_rect()
        b = annotation_bounds(a, e.frame_width, e.frame_height)
        crop = e.video_crop
        return QRectF(r.left() + (b.left()/e.frame_width-crop[0])/crop[2]*r.width(),
                      r.top() + (b.top()/e.frame_height-crop[1])/crop[3]*r.height(),
                      b.width()/e.frame_width/crop[2]*r.width(), b.height()/e.frame_height/crop[3]*r.height())

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.editor.crop_mode and self.editor.pending_crop is not None:
            image = self.image_rect()
            x, y, w, h = self.editor.pending_crop
            rect = QRectF(image.left()+x*image.width(), image.top()+y*image.height(), w*image.width(), h*image.height())
            p.fillRect(QRectF(image.left(), image.top(), image.width(), max(0, rect.top()-image.top())), QColor(0, 0, 0, 150))
            p.fillRect(QRectF(image.left(), rect.bottom(), image.width(), max(0, image.bottom()-rect.bottom())), QColor(0, 0, 0, 150))
            p.fillRect(QRectF(image.left(), rect.top(), max(0, rect.left()-image.left()), rect.height()), QColor(0, 0, 0, 150))
            p.fillRect(QRectF(rect.right(), rect.top(), max(0, image.right()-rect.right()), rect.height()), QColor(0, 0, 0, 150))
            p.setPen(QPen(QColor('black'), 5)); p.drawRect(rect)
            p.setPen(QPen(QColor('#00e89d'), 2)); p.drawRect(rect)
            p.setBrush(QColor('white')); p.setPen(QPen(QColor('#00875b'), 2))
            for center in (QPointF(rect.center().x(), rect.top()), QPointF(rect.center().x(), rect.bottom()),
                           QPointF(rect.left(), rect.center().y()), QPointF(rect.right(), rect.center().y())):
                p.drawRoundedRect(QRectF(center.x()-7, center.y()-7, 14, 14), 3, 3)
        elif not self.editor.crop_mode:
            row = self.editor.annotation_list.currentRow()
            if 0 <= row < len(self.editor.annotations):
                a = self.editor.annotations[row]
                if a['type'] != "stage" and a['start'] <= self.editor.current_frame < a['end']:
                    rect = self._bounds(a)
                    p.setPen(QPen(QColor('white'), 4)); p.drawRect(rect)
                    p.setPen(QPen(QColor('#4269e8'), 2)); p.drawRect(rect)
                    p.setBrush(QColor('#4269e8')); p.drawRect(QRectF(rect.right()-5, rect.bottom()-5, 10, 10))

    def _crop_hit(self, pos):
        r = self.image_rect(); x, y, w, h = self.editor.pending_crop
        left, right = r.left()+x*r.width(), r.left()+(x+w)*r.width()
        top, bottom = r.top()+y*r.height(), r.top()+(y+h)*r.height()
        hits = []
        if top-10 <= pos.y() <= bottom+10:
            if abs(pos.x()-left) <= 10: hits.append(('left', abs(pos.x()-left)))
            if abs(pos.x()-right) <= 10: hits.append(('right', abs(pos.x()-right)))
        if left-10 <= pos.x() <= right+10:
            if abs(pos.y()-top) <= 10: hits.append(('top', abs(pos.y()-top)))
            if abs(pos.y()-bottom) <= 10: hits.append(('bottom', abs(pos.y()-bottom)))
        return min(hits, key=lambda hit: hit[1])[0] if hits else None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton: return
        e = self.editor
        if e.export_worker is not None: return
        if e.crop_mode:
            self._crop_edge = self._crop_hit(event.position())
            if self._crop_edge is None: e._cancel_video_crop()
            return
        if not self.image_rect().contains(event.position()):
            e._deselect_annotation(); return
        self.setFocus()
        for row in reversed(range(len(e.annotations))):
            a = e.annotations[row]
            if a['type'] == "stage" or not a['start'] <= e.current_frame < a['end']: continue
            bounds = self._bounds(a)
            selected = e.annotation_list.currentRow() == row
            if selected and QRectF(bounds.right()-10, bounds.bottom()-10, 20, 20).contains(event.position()):
                mode = 'resize'
            elif bounds.adjusted(-4,-4,4,4).contains(event.position()): mode = 'move'
            else: continue
            e._stop_playback(); e.annotation_list.setCurrentRow(row); e._snapshot_undo()
            self._drag = (mode, row, self._source_point(event.position()), dict(a), bounds)
            self.update(); return
        if e.annotation_list.currentRow() >= 0:
            e._deselect_annotation(); return
        pt = self._source_point(event.position())
        self.clicked.emit(pt.x(), pt.y())

    def mouseMoveEvent(self, event):
        if self.editor.crop_mode:
            if self._crop_edge is None:
                edge = self._crop_hit(event.position())
                self.setCursor(Qt.CursorShape.SizeHorCursor if edge in ('left', 'right') else Qt.CursorShape.SizeVerCursor if edge else Qt.CursorShape.ArrowCursor)
                return
            pt = self._source_point(event.position())
            x, y, w, h = self.editor.pending_crop
            right, bottom = x+w, y+h
            min_w = max(.002, 2 / self.editor.frame_width)
            min_h = max(.002, 2 / self.editor.frame_height)
            if self._crop_edge == 'left': x = min(pt.x(), right-min_w)
            elif self._crop_edge == 'right': right = max(pt.x(), x+min_w)
            elif self._crop_edge == 'top': y = min(pt.y(), bottom-min_h)
            else: bottom = max(pt.y(), y+min_h)
            self.editor.pending_crop = (x, y, right-x, bottom-y)
            self.editor.crop_apply_button.setEnabled(True)
            self.update(); return
        if self._drag is None: return
        mode, row, start, original, bounds = self._drag
        pt = self._source_point(event.position()); a = self.editor.annotations[row]
        if mode == 'move':
            a['x'] = min(1., max(0., original['x'] + pt.x()-start.x()))
            a['y'] = min(1., max(0., original['y'] + pt.y()-start.y()))
        else:
            delta = (event.position().y() - (bounds.bottom())) / max(1., self.image_rect().height())
            a['size'] = min(.3, max(.01, original['size'] + delta * self.editor.video_crop[3]))
        self.editor._select_annotation(row); self.editor._show_frame_at(self.editor.current_frame)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton: return
        if self.editor.crop_mode and self._crop_edge is not None:
            self.mouseMoveEvent(event); self._crop_edge = None; return
        if self._drag is not None:
            self.mouseMoveEvent(event); self._drag = None
            self.editor._refresh_segments_table(select_row=self.editor._selected_row())
            self.editor._on_project_changed()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.editor.crop_mode: self.editor._cancel_video_crop()
            else: self.editor._deselect_annotation()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not self.editor.crop_mode:
            self.editor._delete_annotation(); return
        super().keyPressEvent(event)


class AnnotationStrip(QWidget):
    selected = Signal(int)
    deselected = Signal()
    range_changed = Signal(int, int, int)
    drag_started = Signal()
    drag_finished = Signal()

    def __init__(self):
        super().__init__()
        self.segments, self.annotations, self.selection = [], [], -1
        self._drag = None
        self.setMinimumHeight(36)
        self.setToolTip("点击标注轨选择标注；拖动标注两端调整时间，删除的画面不会显示")

    def set_state(self, segments, annotations, selection):
        self.segments, self.annotations, self.selection = segments, annotations, selection
        self.update()

    def _ranges(self):
        total = total_frames(self.segments)
        if not total:
            return []
        ranges = []
        for index, annotation in enumerate(self.annotations):
            offset = 0
            for segment in self.segments:
                start = max(segment['start'], annotation['start'])
                end = min(segment['end'], annotation['end'])
                if start < end:
                    left = (offset + start - segment['start']) * self.width() / total
                    right = (offset + end - segment['start']) * self.width() / total
                    ranges.append((index, QRectF(left, 5, max(2, right - left), self.height() - 10)))
                offset += segment['end'] - segment['start']
        return ranges

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#f2f5fb'))
        for index, rect in self._ranges():
            painter.fillRect(rect, QColor('#c6d4ff' if index == self.selection else '#dfe6f9'))
            if index == self.selection:
                painter.fillRect(QRectF(rect.left(), rect.top(), 4, rect.height()), QColor('#2455f5'))
                painter.fillRect(QRectF(rect.right()-4, rect.top(), 4, rect.height()), QColor('#2455f5'))
            painter.setPen(QColor('#27489b'))
            if rect.width() > 40:
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.annotations[index]["text"] if self.annotations[index]["type"] == "stage" else f"标注 {index + 1}")

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton: return
        for index, rect in reversed(self._ranges()):
            if rect.adjusted(-5, 0, 5, 0).contains(event.position()):
                self.selected.emit(index)
                annotation = self.annotations[index]
                from timeline import source_to_output
                start = source_to_output(self.segments, annotation['start'])
                end_frame = source_to_output(self.segments, annotation['end']-1)
                total = total_frames(self.segments)
                x = event.position().x()
                if start is not None and abs(x-start*self.width()/total) <= 7: edge = 'start'
                elif end_frame is not None and abs(x-(end_frame+1)*self.width()/total) <= 7: edge = 'end'
                else: return
                self._drag = (index, edge)
                self.drag_started.emit()
                return
        self.deselected.emit()

    def mouseMoveEvent(self, event):
        if self._drag is None: return
        index, edge = self._drag
        total = total_frames(self.segments)
        output = min(total-1, max(0, int(event.position().x() * total / max(1, self.width()))))
        frame = output_to_source(self.segments, output)
        if frame is None: return
        a = self.annotations[index]
        start = min(frame, a['end']-1) if edge == 'start' else a['start']
        end = max(frame+1, a['start']+1) if edge == 'end' else a['end']
        self.range_changed.emit(index, start, end)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self.mouseMoveEvent(event); self._drag = None; self.drag_finished.emit()



def build_editor(editor):
    surface = QWidget()
    surface.setObjectName("main")
    editor.setCentralWidget(surface)
    root = QVBoxLayout(surface)
    root.setContentsMargins(24, 16, 24, 18)
    root.setSpacing(8)

    editor.editor_subtitle = label("分割、删除并修剪画面；口播稿与录制页同步。", "pageSubtitle")
    editor.editor_subtitle.setParent(editor)
    editor.editor_subtitle.hide()
    stats = QHBoxLayout()
    editor.original_stat = label("原始录制时长\n" + editor._format_time(editor.duration), "stat")
    editor.edited_stat = label("剪辑后时长\n—", "stat")
    editor.count_stat = label("当前保留片段\n—", "stat")
    for stat in (editor.original_stat, editor.edited_stat, editor.count_stat):
        stats.addWidget(stat, 1)
    editor.editor_stats = QWidget()
    editor.editor_stats.setLayout(stats)
    editor.editor_stats.hide()
    columns = QHBoxLayout()
    columns.setSpacing(16)
    root.addLayout(columns)
    left_widget = QWidget()
    left_widget.setObjectName("column")
    left = QVBoxLayout(left_widget)
    left.setContentsMargins(0, 0, 0, 0)
    left.setSpacing(14)
    columns.addWidget(left_widget, 7)
    right_widget = QWidget()
    right_widget.setObjectName("column")
    right_widget.setMinimumWidth(200)
    right_widget.setMaximumWidth(390)
    right = QVBoxLayout(right_widget)
    right.setContentsMargins(0, 0, 0, 0)
    right.setSpacing(8)
    tabs = QTabWidget()
    tabs.setObjectName("editorTabs")
    tabs.tabBar().hide()
    tab_strip = QFrame(); tab_strip.setObjectName("editorTabStrip")
    strip_layout = QHBoxLayout(tab_strip); strip_layout.setContentsMargins(3, 3, 3, 3); strip_layout.setSpacing(3)
    editor.tab_buttons = QButtonGroup(editor)
    for index, title in enumerate(("画面", "文字")):
        button = QPushButton(title); button.setObjectName("editorTabChoice"); button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, i=index: tabs.setCurrentIndex(i))
        editor.tab_buttons.addButton(button, index); strip_layout.addWidget(button, 1)
    tabs.currentChanged.connect(lambda index: editor.tab_buttons.button(index).setChecked(True) if editor.tab_buttons.button(index) else None)
    right.addWidget(tab_strip)
    right.addWidget(tabs)
    def add_panel(widget, title):
        panel = QScrollArea()
        panel.setObjectName("panelScroll")
        panel.setWidgetResizable(True)
        panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel.setWidget(widget)
        tabs.addTab(panel, title)
    columns.addWidget(right_widget, 3)

    preview, preview_layout = card("视频预览", "剪后画面")
    left.addWidget(preview, 1)
    editor.preview_label = AnnotationPreview(editor, "正在读取视频…")
    editor.preview_label.clicked.connect(editor._add_annotation_at)
    editor.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    editor.preview_label.setMinimumSize(150, 90)
    editor.preview_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    editor.preview_label.setStyleSheet("background:#20252e; color:#dce5f3; border:0; border-radius:8px;")
    preview_layout.addWidget(editor.preview_label)
    editor.timeline_slider = QSlider(Qt.Orientation.Horizontal)
    editor.timeline_slider.setRange(0, max(0, editor.total_frames - 1))
    editor.timeline_slider.setSingleStep(1)
    editor.timeline_slider.setPageStep(max(1, int(editor.fps * 5)))
    editor.timeline_slider.setTracking(False)
    editor.timeline_slider.sliderReleased.connect(editor._seek_from_slider)
    editor.timeline_slider.sliderMoved.connect(editor._update_time_from_slider)
    preview_layout.addWidget(editor.timeline_slider)
    play = QHBoxLayout()
    editor.jump_start_button = QPushButton("↤")
    editor.jump_start_button.setToolTip("回到开头")
    editor.jump_start_button.clicked.connect(lambda: editor._show_output_frame(0))
    editor.play_button = QPushButton("▶ 播放")
    editor.play_button.clicked.connect(editor._toggle_playback)
    editor.previous_frame_button = QPushButton("上一帧")
    editor.previous_frame_button.clicked.connect(lambda: editor._show_output_frame(editor.timeline_slider.value() - 1))
    editor.next_frame_button = QPushButton("下一帧")
    editor.next_frame_button.clicked.connect(lambda: editor._show_output_frame(editor.timeline_slider.value() + 1))
    for button in (editor.jump_start_button, editor.play_button, editor.previous_frame_button, editor.next_frame_button):
        play.addWidget(button)
    play.addStretch()
    editor.time_label = label("", "hint")
    play.addWidget(editor.time_label)
    preview_layout.addLayout(play)

    timeline, timeline_layout = card("时间线", "点击片段定位播放头")
    left.addWidget(timeline)
    tools = QHBoxLayout()
    editor.split_button = QPushButton("分割")
    editor.split_button.clicked.connect(editor._split_at_current_frame)
    editor.delete_segment_button = QPushButton("删除片段")
    editor.delete_segment_button.clicked.connect(editor._delete_selected_segment)
    editor.trim_start_button = QPushButton("设为起点")
    editor.trim_start_button.clicked.connect(editor._trim_start_at_playhead)
    editor.trim_end_button = QPushButton("设为终点")
    editor.trim_end_button.clicked.connect(editor._trim_end_at_playhead)
    editor.undo_button = QPushButton("撤销")
    editor.undo_button.clicked.connect(editor._undo_edit)
    editor.restore_button = QPushButton("恢复全部")
    editor.restore_button.clicked.connect(editor._restore_all_segments)
    for button in (editor.split_button, editor.delete_segment_button, editor.trim_start_button,
                   editor.trim_end_button, editor.undo_button, editor.restore_button):
        tools.addWidget(button)
    timeline_layout.addLayout(tools)
    editor.timeline_track = TimelineStrip()
    editor.timeline_track.seek_requested.connect(editor._on_timeline_clicked)
    editor.timeline_ruler = label("", "hint")
    timeline_layout.addWidget(editor.timeline_ruler)
    video_row = QHBoxLayout(); video_row.setSpacing(8)
    video_name = label("视频", "hint"); video_name.setFixedWidth(34); video_row.addWidget(video_name)
    video_row.addWidget(editor.timeline_track, 1)
    timeline_layout.addLayout(video_row)
    editor.annotation_hint = label("阶段轨 · 点击选中，拖动两端调整显示时间", "hint")
    editor.annotation_hint.setParent(editor)
    editor.annotation_hint.hide()
    editor.annotation_track = AnnotationStrip()
    stage_row = QHBoxLayout(); stage_row.setSpacing(8)
    stage_name = label("阶段", "hint"); stage_name.setFixedWidth(34); stage_row.addWidget(stage_name)
    stage_row.addWidget(editor.annotation_track, 1)
    timeline_layout.addLayout(stage_row)
    editor.segment_summary = label("", "hint")
    timeline_layout.addWidget(editor.segment_summary)
    editor.segment_table = QTableWidget(0, 4, editor)
    editor.segment_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    editor.segment_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    editor.segment_table.itemSelectionChanged.connect(editor._on_segment_selected)
    editor.segment_table.hide()

    script, script_layout = card("口播稿", "与录制页同步")
    add_panel(script, "口播稿")
    editor.script_input = QPlainTextEdit()
    editor.script_input.setMinimumHeight(110)
    parent = editor.parent()
    if parent is not None and hasattr(parent, "script_input"):
        editor.script_input.setDocument(parent.script_input.document())
    script_layout.addWidget(editor.script_input)
    script_layout.addWidget(label("与录制页共享；创建工程后，稿件及剪辑会自动保存到工程。", "hint"))

    crop_card, crop_layout = card("画面与导出比例", "预览与导出保持一致")
    editor.crop_card = crop_card
    add_panel(crop_card, "裁剪")
    crop_row = QHBoxLayout()
    editor.crop_start_button = QPushButton("调整裁剪")
    editor.crop_start_button.clicked.connect(editor._start_video_crop)
    editor.crop_apply_button = QPushButton("应用裁剪")
    editor.crop_apply_button.setEnabled(False)
    editor.crop_apply_button.clicked.connect(editor._apply_video_crop)
    editor.crop_reset_button = QPushButton("恢复完整画面")
    editor.crop_reset_button.clicked.connect(editor._reset_video_crop)
    crop_row.addWidget(editor.crop_start_button); crop_row.addWidget(editor.crop_apply_button); crop_row.addWidget(editor.crop_reset_button)
    crop_layout.addLayout(crop_row)

    annotations, annotation_layout = card("画面标注", "选择工具后点击画面添加；点击标注可拖动，拖右下角缩放")
    add_panel(annotations, "标注")
    editor.annotation_card = annotations
    editor.annotation_tool = QComboBox()
    for name, kind in (("预览模式", "none"), ("文字说明", "text"), ("箭头指示", "arrow"), ("矩形高亮", "box"), ("说明贴纸", "sticker")):
        editor.annotation_tool.addItem(name, kind)
    editor.annotation_tool.activated.connect(lambda _index: editor.annotation_list.setCurrentRow(-1) if editor.annotation_tool.currentData() != "none" else None)
    annotation_layout.addWidget(editor.annotation_tool)
    editor.annotation_list = QListWidget()
    editor.annotation_list.setMaximumHeight(100)
    editor.annotation_list.currentRowChanged.connect(editor._select_annotation)
    editor.annotation_track.selected.connect(editor._select_annotation_from_track)
    editor.annotation_track.deselected.connect(editor._deselect_annotation)
    editor.annotation_track.drag_started.connect(editor._snapshot_undo)
    editor.annotation_track.drag_finished.connect(lambda: editor._refresh_segments_table(select_row=editor._selected_row()))
    editor.annotation_track.range_changed.connect(editor._change_annotation_range)
    annotation_layout.addWidget(editor.annotation_list)
    form = QFormLayout()
    editor.annotation_text = QLineEdit(); editor.annotation_text.setMaxLength(500); form.addRow("内容", editor.annotation_text)
    editor.annotation_start = QDoubleSpinBox(); editor.annotation_start.setRange(0, editor.duration); editor.annotation_start.setDecimals(2); editor.annotation_start.setSuffix(" 秒"); form.addRow("素材时间", editor.annotation_start)
    editor.annotation_duration = QDoubleSpinBox(); editor.annotation_duration.setRange(.01, max(.01, editor.duration)); editor.annotation_duration.setDecimals(2); editor.annotation_duration.setSuffix(" 秒"); form.addRow("时长", editor.annotation_duration)
    editor.annotation_x = QSpinBox(); editor.annotation_x.setRange(0, 100); editor.annotation_x.setSuffix("%"); form.addRow("横向位置", editor.annotation_x)
    editor.annotation_y = QSpinBox(); editor.annotation_y.setRange(0, 100); editor.annotation_y.setSuffix("%"); form.addRow("纵向位置", editor.annotation_y)
    editor.annotation_size = QSpinBox(); editor.annotation_size.setRange(1, 30); editor.annotation_size.setSuffix("%"); form.addRow("大小", editor.annotation_size)
    editor.annotation_color = QComboBox()
    for name, color in (("蓝色", "#4269e8"), ("红色", "#e45c5c"), ("黄色", "#f4b942"), ("白色", "#ffffff"), ("黑色", "#222222")):
        editor.annotation_color.addItem(name, color)
    form.addRow("颜色", editor.annotation_color)
    editor.annotation_details = QWidget()
    detail_layout = QVBoxLayout(editor.annotation_details)
    detail_layout.setContentsMargins(0, 0, 0, 0)
    detail_layout.setSpacing(8)
    detail_layout.addLayout(form)
    annotation_layout.addWidget(editor.annotation_details)
    editor.annotation_list.currentRowChanged.connect(lambda row: editor.annotation_details.setVisible(row >= 0))
    editor.annotation_details.hide()
    editor.annotation_apply = QPushButton("应用修改")
    editor.annotation_apply.clicked.connect(editor._apply_annotation)
    detail_layout.addWidget(editor.annotation_apply)
    editor.annotation_delete = QPushButton("删除标注")
    editor.annotation_delete.clicked.connect(editor._delete_annotation)
    detail_layout.addWidget(editor.annotation_delete)
    layers = QHBoxLayout()
    editor.annotation_lower = QPushButton("下移图层")
    editor.annotation_lower.clicked.connect(lambda: editor._move_annotation(-1))
    editor.annotation_raise = QPushButton("上移图层")
    editor.annotation_raise.clicked.connect(lambda: editor._move_annotation(1))
    layers.addWidget(editor.annotation_lower); layers.addWidget(editor.annotation_raise)
    detail_layout.addLayout(layers)
    annotation_layout.addWidget(label("标注跟随原素材时间，剪掉的画面不会显示标注。", "hint"))
    info, info_layout = card("当前素材")
    add_panel(info, "工程")
    editor.source_name_label = label(editor.source.name, "noteBox")
    editor.source_name_label.setToolTip(editor.source_path)
    info_layout.addWidget(editor.source_name_label)
    if parent is not None and hasattr(parent, "choose_video_to_edit"):
        open_other = QPushButton("打开其他视频…")
        open_other.clicked.connect(parent.choose_video_to_edit)
        info_layout.addWidget(open_other)
    editor.project_state_label = label("尚未创建工程；剪辑暂存于内存。", "hint")
    info_layout.addWidget(editor.project_state_label)
    save_project = QPushButton("保存工程…")
    save_project.clicked.connect(editor.save_current_project)
    info_layout.addWidget(save_project)
    editor.export_progress = QProgressBar()
    editor.export_progress.setRange(0, 100)
    editor.export_progress.setValue(0)
    info_layout.addWidget(editor.export_progress)
    editor.export_button = QPushButton("导出剪辑 MP4")
    editor.export_button.setObjectName("primaryButton")
    editor.export_button.clicked.connect(editor._choose_export_path)
    info_layout.addWidget(editor.export_button)
    handoff_button = QPushButton("进入导出与交付 →")
    if editor.workbench is not None and hasattr(editor.workbench, "show_export_page"):
        handoff_button.clicked.connect(editor.workbench.show_export_page)
    else:
        handoff_button.setEnabled(False)
    info_layout.addWidget(handoff_button)
    editor.editor_status = label("准备就绪", "hint")
    info_layout.addWidget(editor.editor_status)
    # Keep the legacy widgets alive for existing projects while presenting only the new workflow.
    tabs.removeTab(3)
    tabs.removeTab(2)
    tabs.removeTab(0)
    tabs.setTabText(0, "画面")
    action_bar = QHBoxLayout()
    save_top = QPushButton("保存工程")
    save_top.clicked.connect(editor.save_current_project)
    action_bar.addWidget(save_top)
    open_top = QPushButton("打开素材")
    if editor.workbench is not None:
        open_top.clicked.connect(editor.workbench.choose_video_to_edit)
    else:
        open_top.setEnabled(False)
    action_bar.addWidget(open_top)
    enter_export = QPushButton("进入导出 →")
    enter_export.setObjectName("primaryButton")
    if editor.workbench is not None:
        enter_export.clicked.connect(editor.workbench.show_export_page)
    else:
        enter_export.setEnabled(False)
    action_bar.addWidget(enter_export)
    right.insertLayout(0, action_bar)

    crop_layout.insertWidget(1, label("画面比例", "fieldLabel"))
    editor.ratio_combo = QComboBox()
    for title, data in (("原始比例", None), ("16:9", [16, 9]), ("9:16", [9, 16]),
                        ("1:1", [1, 1]), ("3:4", [3, 4]), ("4:3", [4, 3]), ("自定义", "custom")):
        editor.ratio_combo.addItem(title, data)
    editor.ratio_combo.hide()
    editor.ratio_buttons = QButtonGroup(editor)
    editor.ratio_buttons.setExclusive(True)
    ratio_grid_widget = QWidget()
    ratio_grid = QGridLayout(ratio_grid_widget)
    ratio_grid.setContentsMargins(0, 0, 0, 0); ratio_grid.setSpacing(6)
    for index, (title, combo_index) in enumerate((("16:9", 1), ("9:16", 2), ("1:1", 3),
                                                   ("3:4", 4), ("4:3", 5), ("自定义", 6), ("原始比例", 0))):
        choice = QPushButton(title); choice.setCheckable(True); choice.setObjectName("segmentChoice")
        editor.ratio_buttons.addButton(choice, combo_index)
        choice.clicked.connect(lambda _checked=False, i=combo_index: editor.ratio_combo.setCurrentIndex(i))
        ratio_grid.addWidget(choice, index // 3, index % 3)
    crop_layout.insertWidget(2, ratio_grid_widget)
    editor.ratio_custom = QWidget()
    custom_layout = QHBoxLayout(editor.ratio_custom); custom_layout.setContentsMargins(0, 0, 0, 0)
    editor.ratio_width = QSpinBox(); editor.ratio_width.setRange(1, 100); editor.ratio_width.setValue(16)
    editor.ratio_height = QSpinBox(); editor.ratio_height.setRange(1, 100); editor.ratio_height.setValue(9)
    custom_layout.addWidget(label("宽", "hint")); custom_layout.addWidget(editor.ratio_width)
    custom_layout.addWidget(label("高", "hint")); custom_layout.addWidget(editor.ratio_height)
    ratio_apply = QPushButton("应用比例")
    ratio_apply.clicked.connect(lambda: editor._set_presentation(ratio=[editor.ratio_width.value(), editor.ratio_height.value()]))
    custom_layout.addWidget(ratio_apply); crop_layout.insertWidget(3, editor.ratio_custom)
    editor.fit_combo = QComboBox()
    editor.fit_combo.addItem("完整显示 · 留边", "contain")
    editor.fit_combo.addItem("填满画面 · 裁切", "cover")
    fit_row = QHBoxLayout()
    fit_row.addWidget(label("画面适配", "hint"))
    fit_row.addWidget(editor.fit_combo, 1)
    crop_layout.insertLayout(4, fit_row)
    crop_layout.addWidget(label("预览和导出使用相同的比例与适配方式。", "hint"))
    crop_layout.addStretch()
    def change_ratio(_index):
        value = editor.ratio_combo.currentData()
        editor.ratio_custom.setVisible(value == "custom")
        button = editor.ratio_buttons.button(editor.ratio_combo.currentIndex())
        if button is not None: button.setChecked(True)
        if value != "custom": editor._set_presentation(ratio=value)
    editor.ratio_combo.currentIndexChanged.connect(change_ratio)
    editor.fit_combo.currentIndexChanged.connect(lambda _: editor._set_presentation(fit=editor.fit_combo.currentData()))

    stage_page, stage_layout = card("文字样式", "统一应用于所有阶段")
    editor.annotation_card = stage_page
    colors = QHBoxLayout()
    editor.background_button = QPushButton("背景")
    editor.foreground_button = QPushButton("文字")
    def choose_color(key):
        color = QColorDialog.getColor(QColor(editor.presentation[key]), editor, "选择颜色")
        if color.isValid():
            editor._set_presentation(**{key: color.name()})
            editor._sync_presentation_controls()
    editor.background_button.clicked.connect(lambda: choose_color("background"))
    editor.foreground_button.clicked.connect(lambda: choose_color("foreground"))
    colors.addWidget(label("背景", "hint")); colors.addWidget(editor.background_button, 1)
    colors.addWidget(label("文字", "hint")); colors.addWidget(editor.foreground_button, 1)
    stage_layout.addLayout(colors)
    stage_layout.addWidget(label("文字对齐", "fieldLabel"))
    editor.align_combo = QComboBox()
    for title, value in (("居中", "center"), ("左对齐", "left"), ("右对齐", "right")):
        editor.align_combo.addItem(title, value)
    def change_alignment(index):
        button = editor.align_buttons.button(index)
        if button is not None: button.setChecked(True)
        editor._set_presentation(align=editor.align_combo.currentData())
    editor.align_combo.currentIndexChanged.connect(change_alignment)
    editor.align_combo.hide()
    editor.align_buttons = QButtonGroup(editor)
    align_row = QHBoxLayout(); align_row.setSpacing(5)
    for title, value in (("左对齐", "left"), ("居中", "center"), ("右对齐", "right")):
        choice = QPushButton(title); choice.setCheckable(True); choice.setObjectName("segmentChoice")
        choice.clicked.connect(lambda _checked=False, v=value: editor.align_combo.setCurrentIndex(editor.align_combo.findData(v)))
        editor.align_buttons.addButton(choice, editor.align_combo.findData(value)); align_row.addWidget(choice, 1)
    stage_layout.addLayout(align_row)
    stage_layout.addWidget(label("当前阶段文字", "fieldLabel"))
    editor.stage_text = QLineEdit(); editor.stage_text.setMaxLength(500)
    editor.stage_text.setPlaceholderText("选择阶段后编辑文字")
    editor.stage_text.editingFinished.connect(editor._update_stage_text)
    editor.stage_text.setEnabled(False); stage_layout.addWidget(editor.stage_text)
    stage_layout.addWidget(label("在时间线拖动阶段两端，调整显示时间。", "hint"))
    stage_layout.addStretch()
    stage_actions = QHBoxLayout()
    editor.stage_delete = QPushButton("删除阶段"); editor.stage_delete.setEnabled(False)
    editor.stage_delete.clicked.connect(editor._delete_annotation)
    stage_add = QPushButton("＋ 添加阶段"); editor.stage_add = stage_add; stage_add.setObjectName("primaryButton")
    stage_add.clicked.connect(editor._add_stage)
    stage_actions.addWidget(editor.stage_delete); stage_actions.addWidget(stage_add)
    stage_layout.addLayout(stage_actions)
    add_panel(stage_page, "文字")
    editor._sync_presentation_controls()
    tabs.setCurrentIndex(1)
    editor.tab_buttons.button(1).setChecked(True)
    editor.setStyleSheet(STYLE + """
    QTabWidget#editorTabs::pane { border: 0; background: transparent; }
    QFrame#editorTabStrip { background: #edf0f4; border-radius: 8px; }
    QPushButton#editorTabChoice { background: transparent; color: #6b7280; border: 0; min-height: 26px; }
    QPushButton#editorTabChoice:checked { background: #ffffff; color: #2455f5; font-weight: 700; }
    QLineEdit { background: #f7f8fa; border: 1px solid #eef0f3; border-radius: 6px; padding: 9px; color: #171a20; }
    QTabWidget#editorTabs QTabBar::tab { background: #e9edf2; color: #6b7280; padding: 9px 18px; border: 0; border-radius: 6px; }
    QTabWidget#editorTabs QTabBar::tab:selected { background: white; color: #2455f5; font-weight: 700; }
    QPushButton#segmentChoice { background: #f3f5f8; color: #171a20; border: 0; min-height: 28px; }
    QPushButton#segmentChoice:checked { background: #eef2ff; color: #2455f5; font-weight: 700; }
    """)
    editor.editor_stats.hide()
    editor.editor_subtitle.hide()
    editor.annotation_hint.hide()
    editor.segment_summary.setVisible(editor.height() >= 680)
