"""Prototype-inspired Qt workbench; recording logic remains in MainWindow."""
from PySide6.QtCore import Qt
from pathlib import Path
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
                               QScrollArea, QButtonGroup, QRadioButton, QCheckBox, QComboBox,
                               QDoubleSpinBox, QPlainTextEdit, QStackedWidget, QSizePolicy)


class PreviewLabel(QLabel):
    def __init__(self):
        super().__init__("选择显示器后，点击“刷新预览”查看当前画面")
        self._image = None
        self.setObjectName("preview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumSize(140, 120)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def set_image(self, image):
        self._image = QPixmap.fromImage(image)
        self._fit()

    def _fit(self):
        if self._image is not None:
            self.setPixmap(self._image.scaled(self.contentsRect().size(), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class PathLabel(QLabel):
    def setText(self, text):
        value = str(text)
        self.setToolTip(value)
        super().setText(Path(value).name or value)


def label(text, name=""):
    result = QLabel(text)
    if name:
        result.setObjectName(name)
    result.setWordWrap(True)
    return result


def card(title, subtitle=""):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 18)
    layout.setSpacing(10)
    heading = QHBoxLayout()
    title_label = label(title, "cardTitle")
    title_label.setWordWrap(False)
    heading.addWidget(title_label)
    if subtitle:
        heading.addStretch()
        heading.addWidget(label(subtitle, "hint"))
    layout.addLayout(heading)
    return frame, layout


def section_rule():
    line = QFrame()
    line.setObjectName("sectionRule")
    line.setFixedHeight(1)
    return line


def build_workbench(owner):
    shell = QWidget()
    shell.setObjectName("shell")
    root = QVBoxLayout(shell)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    sidebar = QFrame()
    owner.workbench_sidebar = sidebar
    sidebar.setObjectName("sidebar")
    sidebar.setFixedHeight(62)
    nav = QHBoxLayout(sidebar)
    nav.setContentsMargins(24, 8, 24, 8)
    nav.setSpacing(8)
    owner.brand_label = label("▣  视频录制工作台", "brand")
    owner.brand_subtitle = label("CAPTURE STUDIO", "brandSub")
    owner.nav_caption = label("工作流程", "navCaption")
    nav.addWidget(owner.brand_label)
    owner.brand_subtitle.hide()
    owner.nav_caption.hide()
    nav.addSpacing(28)
    active = QPushButton("录制工作台")
    active.setObjectName("activeNav")
    owner.record_nav_button = active
    active.clicked.connect(owner.show_record_page)
    nav.addWidget(active)
    owner.edit_video_button = QPushButton("剪辑与标注")
    owner.edit_video_button.clicked.connect(owner.show_editor_page)
    nav.addWidget(owner.edit_video_button)
    owner.export_nav_button = QPushButton("导出与交付")
    owner.export_nav_button.clicked.connect(owner.show_export_page)
    nav.addWidget(owner.export_nav_button)
    owner.unavailable_nav = []
    owner.history_nav_button = QPushButton("历史项目")
    owner.history_nav_button.clicked.connect(owner.show_history_page)
    nav.addWidget(owner.history_nav_button)
    owner.open_project_button = QPushButton("打开工程…")
    owner.open_project_button.clicked.connect(owner.choose_project)
    nav.addStretch()
    nav.addWidget(owner.open_project_button)
    owner.sidebar_foot = label("离线工作台\n无声录制 · 本地保存", "sidebarFoot")
    owner.sidebar_foot.hide()
    root.addWidget(sidebar)

    owner.main_scroll = QWidget()
    owner.main_scroll.setObjectName("main")
    main = owner.main_scroll
    owner.page_stack = QStackedWidget()
    owner.page_stack.addWidget(owner.main_scroll)
    from history_ui import HistoryPage
    owner.history_page = HistoryPage(owner)
    owner.page_stack.addWidget(owner.history_page)
    from export_ui import ExportPage
    owner.export_page = ExportPage(owner)
    owner.page_stack.addWidget(owner.export_page)
    root.addWidget(owner.page_stack, 1)
    owner.setCentralWidget(shell)
    outer = QVBoxLayout(main)
    outer.setContentsMargins(24, 16, 24, 18)
    outer.setSpacing(10)
    columns = QHBoxLayout()
    columns.setSpacing(16)
    outer.addLayout(columns, 1)
    target, target_layout = card("录制设置")
    target.setMinimumWidth(230)
    target.setObjectName("recordSettings")
    target_layout.setContentsMargins(14, 12, 14, 12)
    target_layout.setSpacing(5)
    columns.addWidget(target, 3)
    target_layout.addWidget(label("显示器", "fieldLabel"))
    screens = QHBoxLayout()
    owner.monitor_combo = QComboBox()
    owner.monitor_combo.currentIndexChanged.connect(owner._on_monitor_selected)
    screens.addWidget(owner.monitor_combo, 1)
    owner.monitor_refresh_button = QPushButton("刷新")
    owner.monitor_refresh_button.clicked.connect(owner._refresh_monitor_choices)
    screens.addWidget(owner.monitor_refresh_button)
    target_layout.addLayout(screens)
    target_layout.addWidget(section_rule())
    target_layout.addWidget(label("录制范围", "fieldLabel"))
    regions = QHBoxLayout()
    regions.setSpacing(6)
    owner.region_mode = QButtonGroup(owner)
    owner.full_region = QRadioButton("全屏")
    owner.window_region = QRadioButton("窗口")
    owner.custom_region = QRadioButton("框选")
    for index, widget in enumerate((owner.full_region, owner.custom_region, owner.window_region)):
        owner.region_mode.addButton(widget, index)
    for widget in (owner.full_region, owner.window_region, owner.custom_region):
        widget.setObjectName("rangeChoice")
        regions.addWidget(widget, 1)
        widget.toggled.connect(lambda _checked: owner._on_range_mode_changed())
    target_layout.addLayout(regions)
    owner.window_controls = QWidget()
    window_row = QHBoxLayout(owner.window_controls)
    window_row.setContentsMargins(0, 0, 0, 0)
    owner.window_target_label = label("尚未选择窗口", "hint")
    window_row.addWidget(owner.window_target_label, 1)
    owner.select_window_button = QPushButton("选择窗口…")
    owner.select_window_button.clicked.connect(owner.choose_record_window)
    window_row.addWidget(owner.select_window_button)
    target_layout.addWidget(owner.window_controls)
    owner.preset_controls = QWidget()
    preset = QVBoxLayout(owner.preset_controls)
    preset.setContentsMargins(0, 0, 0, 0)
    preset.setSpacing(6)
    owner.region_preset_combo = QComboBox()
    owner.region_preset_combo.currentIndexChanged.connect(owner._on_region_preset_changed)
    preset.addWidget(owner.region_preset_combo)
    preset_buttons = QHBoxLayout()
    owner.save_region_preset_button = QPushButton("框选并保存")
    owner.save_region_preset_button.clicked.connect(owner.start_custom_region_selection)
    owner.delete_region_preset_button = QPushButton("删除预设")
    owner.delete_region_preset_button.clicked.connect(owner.delete_region_preset)
    preset_buttons.addWidget(owner.save_region_preset_button)
    preset_buttons.addWidget(owner.delete_region_preset_button)
    preset.addLayout(preset_buttons)
    target_layout.addWidget(owner.preset_controls)
    owner.preset_hint_label = label("", "hint")
    owner.preset_hint_label.hide()
    target_layout.addWidget(section_rule())
    target_layout.addWidget(label("鼠标与特效", "fieldLabel"))
    owner.shortcut_checkbox = QCheckBox("启用 C / V 快捷键")
    owner.shortcut_checkbox.setChecked(owner.settings.value("effects_enabled", True, type=bool))
    owner.shortcut_checkbox.toggled.connect(lambda value: owner.settings.setValue("effects_enabled", value))
    target_layout.addWidget(owner.shortcut_checkbox)
    owner.shortcut_checkbox.setToolTip("输入文字时建议关闭；快捷键不会拦截目标程序收到的按键。")
    cursor_row = QHBoxLayout()
    cursor_label = label("鼠标样式", "hint")
    cursor_label.setFixedWidth(62)
    cursor_row.addWidget(cursor_label)
    owner.cursor_style_combo = QComboBox()
    owner.cursor_style_combo.addItems(["箭头", "手型", "十字准星", "高亮圆圈", "实心圆点", "不显示"])
    cursor_value = str(owner.settings.value("cursor_style", "箭头"))
    if owner.cursor_style_combo.findText(cursor_value) >= 0:
        owner.cursor_style_combo.setCurrentText(cursor_value)
    owner.cursor_style_combo.currentTextChanged.connect(lambda value: owner.settings.setValue("cursor_style", value))
    cursor_row.addWidget(owner.cursor_style_combo, 1)
    target_layout.addLayout(cursor_row)
    zoom_row = QHBoxLayout()
    zoom_label = label("放大", "hint")
    zoom_label.setWordWrap(False)
    zoom_label.setFixedWidth(38)
    zoom_row.addWidget(zoom_label)
    owner.zoom_factor_combo = QComboBox()
    for value in (1.5, 2.0, 2.5, 3.0):
        owner.zoom_factor_combo.addItem(f"{value:g}×", value)
    try:
        zoom_index = owner.zoom_factor_combo.findData(float(owner.settings.value("zoom_factor", 2.0)))
    except (TypeError, ValueError):
        zoom_index = -1
    owner.zoom_factor_combo.setCurrentIndex(zoom_index if zoom_index >= 0 else 1)
    owner.zoom_factor_combo.currentIndexChanged.connect(lambda _: owner.settings.setValue("zoom_factor", owner.zoom_factor_combo.currentData()))
    zoom_row.addWidget(owner.zoom_factor_combo, 1)
    owner.zoom_hold_spin = QDoubleSpinBox()
    owner.zoom_hold_spin.setMinimumWidth(90)
    owner.zoom_hold_spin.setRange(0.5, 30.0)
    owner.zoom_hold_spin.setSingleStep(0.5)
    owner.zoom_hold_spin.setSuffix(" 秒")
    try:
        owner.zoom_hold_spin.setValue(float(owner.settings.value("zoom_hold_seconds", 2.0)))
    except (TypeError, ValueError):
        owner.zoom_hold_spin.setValue(2.0)
    owner.zoom_hold_spin.valueChanged.connect(lambda value: owner.settings.setValue("zoom_hold_seconds", value))
    zoom_row.addWidget(owner.zoom_hold_spin, 1)
    target_layout.addLayout(zoom_row)
    highlight_row = QHBoxLayout()
    highlight_label = label("高亮样式", "hint")
    highlight_label.setFixedWidth(62)
    highlight_row.addWidget(highlight_label)
    owner.highlight_style_combo = QComboBox()
    owner.highlight_style_combo.addItems(["黄色圆环", "蓝色圆环", "红色圆环", "黄色光圈"])
    highlight_value = str(owner.settings.value("highlight_style", "黄色圆环"))
    if owner.highlight_style_combo.findText(highlight_value) >= 0:
        owner.highlight_style_combo.setCurrentText(highlight_value)
    owner.highlight_style_combo.currentTextChanged.connect(lambda value: owner.settings.setValue("highlight_style", value))
    highlight_row.addWidget(owner.highlight_style_combo, 1)
    target_layout.addLayout(highlight_row)
    target_layout.addWidget(section_rule())
    target_layout.addWidget(label("保存位置", "fieldLabel"))
    location = QHBoxLayout()
    owner.path_label = PathLabel()
    owner.path_label.setText(str(owner.save_dir))
    location.addWidget(owner.path_label, 1)
    owner.change_path_button = QPushButton("更改")
    owner.change_path_button.clicked.connect(owner.choose_save_dir)
    location.addWidget(owner.change_path_button)
    target_layout.addLayout(location)
    target_layout.addStretch()

    center = QWidget()
    center_layout = QVBoxLayout(center)
    center_layout.setContentsMargins(0, 0, 0, 0)
    center_layout.setSpacing(10)
    columns.addWidget(center, 6)
    preview, preview_layout = card("录制预览")
    center_layout.addWidget(preview, 1)
    owner.preview_caption = label("当前选中的显示器", "hint")
    preview_layout.addWidget(owner.preview_caption)
    owner.preview_label = PreviewLabel()
    preview_layout.addWidget(owner.preview_label, 1)
    preview_actions = QHBoxLayout()
    owner.preview_resolution = label("画面尺寸：—", "hint")
    preview_actions.addWidget(owner.preview_resolution, 1)
    preview_actions.addWidget(label("30 FPS · 无声", "hint"))
    refresh = QPushButton("刷新预览")
    refresh.clicked.connect(owner.refresh_record_preview)
    preview_actions.addWidget(refresh)
    preview_layout.addLayout(preview_actions)
    owner.start_button = QPushButton("●   开始录制")
    owner.start_button.setObjectName("primaryButton")
    owner.start_button.setMinimumHeight(44)
    owner.start_button.clicked.connect(owner.start_recording_by_options)
    center_layout.addWidget(owner.start_button)
    owner.status_label = label("状态：准备就绪", "statusText")
    center_layout.addWidget(owner.status_label)

    script, script_layout = card("口播稿", "草稿自动保存")
    script.setMinimumWidth(230)
    columns.addWidget(script, 3)
    owner.script_input = QPlainTextEdit()
    owner.script_input.setPlaceholderText("输入或粘贴口播稿，录制时可在提词窗查看。")
    owner.script_input.setMinimumHeight(100)
    owner.script_input.setPlainText(owner.script_document.text)
    owner.script_input.textChanged.connect(owner._on_script_changed)
    script_layout.addWidget(owner.script_input, 1)
    owner.script_count = label(f"{len(owner.script_document.text)} 字", "hint")
    script_layout.addWidget(owner.script_count)
    script_actions = QHBoxLayout()
    owner.import_script_button = QPushButton("导入 TXT")
    owner.import_script_button.clicked.connect(owner._import_script)
    script_actions.addWidget(owner.import_script_button)
    owner.preview_prompt_button = QPushButton("预览提词")
    owner.preview_prompt_button.clicked.connect(owner._preview_prompter)
    script_actions.addWidget(owner.preview_prompt_button)
    owner.clear_script_button = QPushButton("清空")
    owner.clear_script_button.clicked.connect(owner.script_input.clear)
    script_actions.addWidget(owner.clear_script_button)
    script_layout.addLayout(script_actions)
    owner.full_region.setChecked(True)
    owner._on_range_mode_changed()
    shell.setStyleSheet(STYLE)
    owner._refresh_monitor_choices()
    owner._refresh_region_preset_combo(str(owner.settings.value("selected_region_preset", "")))


STYLE = """
QWidget#shell { background: #f4f6fa; color: #1d2638; font-family: 'Microsoft YaHei UI'; font-size: 12px; }
QWidget#main, QWidget#column { background: #f4f6fa; }
QScrollArea#mainScroll { border: 0; background: #f4f6fa; }
QFrame#sidebar { background: #172033; }
QFrame#sidebar QLabel { color: #aab9d3; background: transparent; }
QFrame#sidebar QLabel#brand { color: white; font-size: 15px; font-weight: 700; }
QFrame#sidebar QLabel#brandSub, QFrame#sidebar QLabel#navCaption { color: #8393af; font-size: 10px; }
QFrame#sidebar QLabel#sidebarFoot { color: #a1b0ca; background: #202d44; padding: 14px; border: 1px solid #354158; border-radius: 10px; line-height: 1.5; }
QFrame#sidebar QPushButton { background: transparent; color: #bdc9df; border: 0; text-align: left; padding: 13px 11px; border-radius: 9px; }
QFrame#sidebar QPushButton:hover { background: #24334d; color: white; }
QFrame#sidebar QPushButton#activeNav { background: #3353a2; color: white; font-weight: 700; }
QFrame#sidebar QPushButton:disabled { color: #74829a; }
QLabel#pageTitle { font-size: 24px; font-weight: 700; color: #1d2638; }
QLabel#pageSubtitle, QLabel#hint { color: #748098; font-size: 11px; }
QLabel#statusPill { background: #edf9f3; color: #25875f; border-radius: 7px; padding: 8px 12px; }
QLabel#step, QLabel#activeStep { background: white; border: 1px solid #e5e9f1; border-radius: 9px; padding: 12px; color: #8490a5; }
QLabel#activeStep { color: #3155c8; border-color: #b2c3ff; background: #f9fbff; font-weight: 700; }
QFrame#card { background: white; border: 1px solid #e5e9f1; border-radius: 14px; }
QFrame#card QLabel { background: transparent; border: 0; }
QFrame#card QLabel#cardTitle { color: #1d2638; font-size: 15px; font-weight: 700; }
QFrame#card QLabel#fieldLabel { color: #34425b; font-weight: 700; margin-top: 3px; }
QFrame#card QLabel#noteBox { background: #f7f9fd; border: 1px solid #e5e9f1; border-radius: 7px; padding: 9px; color: #52647e; }
QFrame#card QLabel#effectHint { background: #f1f5ff; border: 1px solid #dce6ff; border-radius: 8px; padding: 12px; color: #3658c4; }
QFrame#card QLabel#statusText { color: #25875f; }
QFrame#card QLabel#preview { background: #172238; border: 8px solid #28354c; border-radius: 10px; color: #d4dff0; }
QLabel#stat { background: white; border: 1px solid #e5e9f1; border-radius: 10px; padding: 13px; color: #50617c; }
QLabel#infoBox { background: #eef5ff; border: 1px solid #dce8ff; border-radius: 10px; padding: 14px; color: #50709e; }
QPushButton { background: white; border: 1px solid #dfe5ef; border-radius: 7px; color: #50607c; padding: 8px 10px; }
QPushButton:hover { background: #f2f5fc; }
QPushButton:disabled { color: #a5adba; background: #f3f5f8; }
QPushButton#primaryButton { background: #4269e8; color: white; border: 0; font-size: 14px; font-weight: 700; }
QPushButton#primaryButton:hover { background: #3157d3; }
QComboBox, QDoubleSpinBox, QPlainTextEdit { background: white; border: 1px solid #dfe5ef; border-radius: 7px; padding: 7px; color: #32425d; }
QCheckBox, QRadioButton { color: #45536c; spacing: 6px; }
"""

STYLE += """
QWidget#shell, QWidget#main, QWidget#column { background: #f2f4f7; color: #171a20; }
QFrame#sidebar { background: #ffffff; border-bottom: 1px solid #e4e7ec; }
QFrame#sidebar QLabel#brand { color: #171a20; font-size: 15px; }
QFrame#sidebar QPushButton { background: transparent; color: #687180; border: 0; padding: 9px 12px; text-align: center; }
QFrame#sidebar QPushButton:hover { background: #f2f4f7; color: #171a20; }
QFrame#sidebar QPushButton#activeNav { background: #eef2ff; color: #2455f5; }
QScrollArea#panelScroll { border: 0; background: transparent; }
QFrame#recordSettings { background: #ffffff; border: 1px solid #e9ebef; border-radius: 12px; }
QFrame#recordSettings QLabel { background: transparent; border: 0; }
QFrame#recordSettings QLabel#cardTitle { color: #171a20; font-size: 15px; font-weight: 700; }
QFrame#recordSettings QLabel#fieldLabel { color: #171a20; font-weight: 700; margin-top: 2px; }
QFrame#recordSettings QFrame#sectionRule { background: #e9edf2; border: 0; }
QFrame#recordSettings QComboBox, QFrame#recordSettings QDoubleSpinBox { background: #f4f6f8; border: 1px solid #e8ebf0; border-radius: 7px; padding: 6px 8px; color: #171a20; }
QFrame#recordSettings QRadioButton#rangeChoice { background: #f4f6f8; border: 1px solid #edf0f4; border-radius: 7px; padding: 8px 5px; color: #596474; }
QFrame#recordSettings QRadioButton#rangeChoice:checked { background: #eef2ff; border: 1px solid #cbd7ff; color: #2455f5; font-weight: 700; }
QFrame#recordSettings QRadioButton#rangeChoice::indicator { width: 0px; height: 0px; border: none; background: transparent; image: none; }
QFrame#recordSettings QCheckBox { color: #303946; }

QLabel#pageTitle { color: #171a20; }
QFrame#card { background: #ffffff; border: 1px solid #e9ebef; border-radius: 12px; }
QFrame#card QLabel#preview { background: #20252e; border: 0; color: #c5cfdd; }
QPushButton#primaryButton { background: #2455f5; color: white; }
QPushButton#primaryButton:hover { background: #1744d5; }
QPlainTextEdit { background: #f7f8fa; }
QPushButton { padding: 6px 8px; }
"""
