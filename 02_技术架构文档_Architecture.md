# 视频录制工作台｜技术架构与数据协议（规格 V1.3）

> 文档状态：方案设计草案 · 2026-10-28  
> 主工程运行平台：Windows 10/11 x64；离线优先；单机桌面软件。  
> 本文中“建议”“待实现”均指未来开发目标，**不代表现有 Python 源码已经实现**。

## 1. 核心架构决策（ADR）

| 决策 | 选择 | 原因/边界 |
|---|---|---|
| ADR-001 GUI | **PySide6 / Qt Widgets**，原生桌面布局 | 复用现有程序，不强行把浏览器 HTML 改为录屏内核；HTML 用作视觉与交互参考 |
| ADR-002 录屏 | Windows 系统屏幕采集；短期保留 `mss`，采集能力抽象成接口 | 现有代码为 MSS；未来性能不足可换 DXGI/Desktop Duplication 或 Windows Graphics Capture |
| ADR-003 鼠标事件 | `pynput` 全局监听 + Qt Signal/线程安全事件队列 | 全局 C/V/F9；事件消费者由录制线程负责，避免从监听线程修改 UI/视频缓冲 |
| ADR-004 编辑 | 非破坏性剪辑清单（EDL）+ 标注元数据 | 原始素材不可覆盖、可撤销、可重开、可再导出 |
| ADR-005 时间模型 | **统一整数毫秒/内部单调时间戳**，导出时换算为帧索引 | 避免浮点累计误差和录制固定帧数造成时长漂移 |
| ADR-006 视频编码 | 目标 H.264 MP4，实际编码器优先采用经过验证的 FFmpeg 发布方案 | 当前代码的 OpenCV `mp4v` 兼容性不足；最终编码器依赖许可证审查及能力探测 |
| ADR-007 数据 | JSON 文件工程 + UTF-8 TXT 口播 + 无声 MP4 | 对下游工具语言和运行环境无侵入 |
| ADR-008 用户安装 | **PyInstaller onedir + Inno Setup 单文件 Setup.exe** | 保证用户一键安装；可卸载；利于升级；不会要求 Python |
| ADR-009 发布 | 首版**手动下载安装新版本覆盖升级** | 不实现后台自动更新及联网依赖 |
| ADR-010 本工具边界 | **不做** TTS/BGM/字幕/配音同步及视频整体变速 | 与下游视频生产工具职责隔离 |

### 1.1 为什么不是 PyInstaller `--onefile` 直接交付？

`--onefile` 是**单可执行程序打包方式**，不等于有开始菜单、卸载、升级策略的完整安装程序。建议先 `--onedir` 产出含依赖的应用目录，再由 Inno Setup 将其制作成**一个对用户分发的安装器文件**。这样用户只看到一个 `视频录制工作台_Setup_1.0.0.exe`，内部仍可包含 DLL、Qt 插件、视频编码器和其他资源。

参考：[PyInstaller 打包方式](https://pyinstaller.org/en/stable/operating-mode.html)、[Inno Setup 命令行编译](https://documentation.help/Inno-Setup/topic_compilercmdline.htm)。

## 2. 组件结构图

```text
┌─────────────────── Windows 本地 UI（PySide6）───────────────────┐
│ 主窗口：录制工作台 │ 剪辑与标注 │ 导出交付 │ 历史项目             │
│ 悬浮窗 A：录制控制（计时、暂停、停止、丢弃、快捷键）            │
│ 悬浮窗 B：口播提词（独立移动、缩放、滚动与显示/隐藏）          │
└─────────────────────────────┬──────────────────────────────────┘
                              │ Qt 信号 / 命令
┌─────────────────────────────▼──────────────────────────────────┐
│                         应用服务层                              │
│ ProjectService  RecordingService  EditorService  ExportService  │
└──────────┬─────────────────┬────────────────────┬───────────────┘
           │                 │                    │
┌──────────▼────────┐ ┌──────▼───────────┐ ┌──────▼───────────────┐
│ Recording Engine  │ │ Editing Engine   │ │ Export Pipeline      │
│ DisplayDetector   │ │ EDL segments     │ │ Timeline renderer    │
│ CaptureBackend    │ │ Trim / Split     │ │ Encode H.264 MP4     │
│ HotkeyController  │ │ Delete / Undo    │ │ ffprobe verification │
│ EffectRenderer    │ │ Overlay track    │ │ handoff.json         │
│ TimestampClock    │ │ TimelineMapping  │ │ temp / atomic commit │
└──────────┬────────┘ └────────┬─────────┘ └─────────┬────────────┘
           │                   │                     │
           └───────────────────┼─────────────────────┘
                               ▼
                   项目目录 / JSON / MP4 / TXT
                               │
                               ▼
                     下游工具本地导入交付目录
```

**进程模型：** 单进程 Qt 主程序；捕获、编码/导出在独立工作线程或受控 FFmpeg 子进程；禁止在 Qt GUI 主线程做屏幕抓取、整视频解码或大文件写盘。

## 3. 代码模块与职责边界

建议从 3060 行左右的单文件程序逐步拆分，初期保留旧代码适配层，避免一次重写。

```text
video-capture-workbench/
├── pyproject.toml                 # 元信息与工具配置（或保留 requirements）
├── requirements.txt               # 开发期依赖；发布使用锁定版本
├── requirements-lock.txt          # 发布构建锁定的确切版本
├── src/
│   └── vcw/
│       ├── __main__.py
│       ├── app.py                 # QApplication、依赖装配、生命周期
│       ├── domain/
│       │   ├── models.py         # Project, Clip, Annotation 等类型
│       │   ├── timeline.py       # EDL、裁剪、时间映射（纯逻辑）
│       │   └── schema.py         # JSON Schema 与版本迁移
│       ├── capture/
│       │   ├── backends.py       # CaptureBackend 抽象
│       │   ├── mss_backend.py    # 现有 MSS 适配
│       │   ├── monitors.py       # DPI 与坐标变换、区域预设
│       │   ├── hotkeys.py        # C、V、F9 全局输入监听
│       │   ├── effects.py        # 高亮与放大渲染
│       │   └── recording.py      # 录制状态机、定时、写入队列
│       ├── editor/
│       │   ├── player.py         # 剪后片段预览
│       │   ├── annotations.py    # 文字/箭头/方框/贴纸
│       │   └── commands.py       # Split/Delete/Trim/Undo 命令
│       ├── export/
│       │   ├── renderer.py       # 视频与标注合成
│       │   ├── encoder.py        # FFmpeg 编码、日志和错误处理
│       │   ├── verify.py         # ffprobe 校验音轨、codec、时长
│       │   └── handoff.py        # video + script + JSON 清单
│       ├── storage/
│       │   ├── projects.py       # 工程保存、打开、迁移、自动保存
│       │   └── settings.py       # 配置、最近项目、默认目录
│       ├── ui/
│       │   ├── main_window.py
│       │   ├── recording_page.py
│       │   ├── floating_controls.py    # 悬浮窗 A（独立 QDialog/QWidget）
│       │   ├── teleprompter.py        # 悬浮窗 B（独立 QDialog/QWidget）
│       │   ├── floating_window_manager.py # 仅管理生命周期与显隐，不绑定坐标
│       │   ├── editor_page.py
│       │   ├── export_page.py
│       │   └── history_page.py
│       └── utils/
│           ├── paths.py
│           └── logging.py
├── assets/                         # 图标、非 Python 资源
├── vendor/                         # 已核查许可证的可再分发编码器
├── packaging/
│   ├── VideoCaptureWorkbench.spec
│   ├── VideoCaptureWorkbench.iss
│   └── build_release.ps1
├── tests/
│   ├── unit/
│   ├── integration/
│   └── gui/
└── docs/
    ├── PRD.md
    ├── ARCHITECTURE.md
    └── DEVELOPMENT.md
```

**兼容说明：** 这是**目标结构**，并非源程序已经具备这些文件。建议最先抽出 `timeline.py`、`hotkeys.py`、`projects.py`，随后替换录制写入和编码导出。

## 4. 录制数据链路

```text
显示器选择／窗口选择／框选
      ↓ 物理像素区域 & DPI 变换
帧采集（MSS）+ monotonic 时间戳
      ↓
录制事件队列（C/V/F9 + 当前鼠标物理坐标）
      ↓
叠加渲染（光标、C 高亮、V 局部缩放）
      ↓
恒定输出尺寸、帧调度/补帧或丢帧
      ↓
视频编码子进程（H.264 MP4，音轨 = 0）
      ↓
录制临时文件 → 停止完成校验 → raw.mp4
```

### 4.1 坐标模型

- `monitor_rect`: Windows 虚拟桌面的**物理像素**矩形 `(left, top, width, height)`；支持第二屏负坐标。
- `capture_rect`: 实际采集区域；其像素坐标相对虚拟桌面，而不是 Qt 逻辑像素。
- `cursor_global`: 物理桌面坐标；`cursor_local = cursor_global - capture_rect.origin`。
- 画面贴纸位置使用相对坐标 `x_norm, y_norm ∈ [0,1]`，独立于导出尺寸。
- 更换录制窗口/屏幕后，输出尺寸必须保持**本次录制开始确定的固定宽高**；不同宽高比例应使用明确的 Fit/Pad 规则或分段录制，避免不受控拉伸。

### 4.2 时间与 FPS

当前源码按循环写入固定 FPS 帧序列，机器卡顿时可能造成**成品时长短于实际录制时间**。目标方案：

1. `time.perf_counter_ns()` 作为录制单调时钟；开始/暂停/恢复/停止明确维护有效录制时长。
2. 计算每个目标输出帧的时间点（30 FPS：`n * 1e9 / 30` 纳秒），从最新采样帧选择、必要时重复最后有效帧，确保音频虽不存在但视频时长与真实操作时间一致。
3. 不论实际抓屏帧率是否短暂下降，视频输出为合法且连续的 CFR；同时记录实际采集帧率和补/丢帧数量用于诊断。
4. GUI 预览最多约 10 FPS，避免和正式录制争用 CPU。
5. 录制结束由 `ffprobe` 检查实际帧数/时长；**绝不只依据 `FPS = 30` 常量来判断时间正确**。

### 4.3 特效状态

```text
C 按下（忽略重复 / Ctrl、Alt、Win 修饰键）
 └─ 新建 HighlightEvent(position, start_monotonic_ns, hold_ms=1500)

V 按下（忽略重复 / Ctrl、Alt、Win 修饰键）
 └─ 新建 ZoomEvent(position, start_monotonic_ns, zoom=2.0,
                  in_ms=350, hold_ms=2000, out_ms=350)
```

- 监听线程只生成事件；录制线程对当前时间求当前效果，不直接修改互相共享的视频帧。
- V 期间按 V：替换放大事件并以新鼠标位置起算，保证行为确定；C 和 V 可以叠加。
- 暂停状态不触发新特效；恢复后按恢复后新时间继续录制。
- 当前 V1 **效果直接烘焙进原始录屏**，不是可编辑的非破坏性缩放轨。后续如需回撤单次放大，可升级为原帧 + 特效事件记录。
- **快捷键监听绝不能吞掉/修改目标应用的 C/V 输入**，且 `Ctrl+C`、`Ctrl+V` 不触发。

### 4.4 窗口/屏幕可捕获性

MSS/Windows 捕获技术对受保护内容、某些 UAC/管理员窗口、部分 GPU 硬件叠加场景存在局限。必须在实际 Windows 机器验证：锁屏、桌面切换、DPI 混用、窗口最小化、显示器热插拔、多屏缩放。自动跟随鼠标跨屏属于高级功能，**不启用为默认模式**。

## 5. 剪辑与标注的时间模型

### 5.1 非破坏性 EDL

- 原始视频始终保留 `raw.mp4`。
- 工程保存有序的保留片段，每段为原素材的 `[source_in_ms, source_out_ms)` 半开区间。
- 导出时按照当前片段顺序拼接成单一视频；修改列表不修改原文件。
- **输出时间线时间**通过片段时长求前缀和映射，`output_duration_ms = Σ(source_out_ms - source_in_ms)`。

例：原视频 124 秒，保留 `[0,45000)` + `[54000,124000)`，剪后时长 = 115000 ms。位于原始时间 60000 ms 的事件，映射到输出时间 51000 ms。

### 5.2 视频预览必须走剪后时间线

当前代码 `VideoEditorWindow._on_play_tick()` 按 `current_frame + 1` 线性推进原片，未按保留片段列表映射。目标架构：

```text
用户拖动输出时间线 51 秒
    → find_clip_at_output_time(51000)
    → 原素材区间 [54000, 124000)，片段内偏移 6000
    → source_time_ms = 60000
    → 解码原素材对应帧，叠加当前有效标注
```

分割、删除后必须立即刷新时间线和导出预估时长；播放时只播放保留片段。

### 5.3 标注时间锚点

采用“依附原素材时间”的方式存储：

```text
annotation.source_start_ms
annotation.source_end_ms
annotation.position = { x_norm, y_norm }
```

导出时，取 `annotation.source_interval ∩ each_kept_clip.source_interval`，把交集映射到输出时间线；空交集不渲染，被裁断的部分截断。UI 应给出“受剪辑影响的标注已调整”提示。未来加入多源素材时需增加 `source_id`。

标注类型：`text | arrow | rectangle | sticker`。V1 文字绘制必须完整支持中文字体；透明度、边框和字号按比例渲染，坐标可随输出分辨率缩放。

## 6. 工程文件规范（内部）

统一 JSON UTF-8，`schema_version` 必填，以下为建议的 **V1.0 可实现最小字段**：

```json
{
  "schema_version": "1.0.0",
  "project_id": "62a93835-574c-43e4-b7a5-ef434e804052",
  "name": "软件操作演示_001",
  "created_at": "2026-10-28T09:00:00+08:00",
  "updated_at": "2026-10-28T09:30:00+08:00",
  "source": {
    "file": "media/raw.mp4",
    "width": 1920,
    "height": 1080,
    "fps_num": 30,
    "fps_den": 1,
    "duration_ms": 124000,
    "has_audio": false
  },
  "timeline": {
    "clips": [
      {"id": "clip-001", "source_in_ms": 0, "source_out_ms": 45000},
      {"id": "clip-002", "source_in_ms": 54000, "source_out_ms": 124000}
    ],
    "annotations": [
      {
        "id": "note-001",
        "type": "text",
        "source_start_ms": 10000,
        "source_end_ms": 13000,
        "position": {"x_norm": 0.68, "y_norm": 0.38},
        "text": "点击设置按钮",
        "style": {"font_size_px": 32, "color": "#FFFFFF", "opacity": 1.0}
      }
    ]
  },
  "narration": {"file": "script.txt", "encoding": "utf-8"},
  "export": {"target_fps_num": 30, "target_fps_den": 1, "audio": false}
}
```

**相对路径规则：** 所有 `file` 字段相对于项目根目录；不允许绝对路径、`..` 越界路径；导入旧工程时验证资源是否存在并提供定位修复。时间单位一律 `ms`，不要混用帧号、毫秒、秒等字段名称。

**项目建议布局：**

```text
%USERPROFILE%\Videos\视频录制工作台\软件操作演示_001\
├── project.json
├── script.txt
├── media\
│   └── raw.mp4
├── autosave\
│   └── project.autosave.json
└── exports\
    └── 软件操作演示_001_20261028_093000\
        ├── video.mp4
        ├── script.txt
        └── handoff.json
```

**用户设置：** `%APPDATA%\VideoCaptureWorkbench\settings.json`（或 `QSettings`），**运行日志与缓存：** `%LOCALAPPDATA%\VideoCaptureWorkbench\`；不可写入安装目录。

### 6.1 项目自动保存与恢复

- 用户有修改后采用短延时防抖写入、关闭时强制 flush；先写 `*.tmp`，成功后 `os.replace` 原子替换。
- 工程版本升级需要迁移脚本 `schema_vN_to_vN+1`；未知新版本工程不得以旧模式覆盖保存。
- 缺失源素材时提示重新定位，绝不自动猜测替代素材。

## 7. 下游交付协议（本地目录 V1）

默认交付结构：

```text
output/<project_name>_<timestamp>/
├── video.mp4
├── script.txt
└── handoff.json
```

`handoff.json` 示例（**交付产物的真实数据须由编码完成后的检测结果填入**）：

```json
{
  "schema_version": "1.0.0",
  "producer": {"name": "VideoCaptureWorkbench", "version": "1.0.0"},
  "project_id": "62a93835-574c-43e4-b7a5-ef434e804052",
  "project_name": "软件操作演示_001",
  "video": {
    "file": "video.mp4",
    "format": "mp4",
    "video_codec": "h264",
    "width": 1920,
    "height": 1080,
    "fps_num": 30,
    "fps_den": 1,
    "duration_ms": 115000,
    "has_audio": false,
    "playback_speed": 1.0
  },
  "narration": {
    "file": "script.txt",
    "encoding": "utf-8",
    "type": "plain_text",
    "tts_generated": false
  },
  "postproduction": {
    "duration_alignment_owner": "downstream",
    "speed_formula": "video_duration_seconds / tts_duration_seconds",
    "suggested_auto_speed_min": 0.8,
    "suggested_auto_speed_max": 1.25
  }
}
```

**协议限制：** JSON 不携带音频/字幕数据；`playback_speed=1.0` 表示本工具**没有变速**。下游必须自己生成 TTS 并重新计算速度；不要求下游实际使用推荐范围（只是提示）。不要把文件中的 115000 ms 视为固定数字，应以实际 `ffprobe` 读数为准。

**校验规则：** 相对路径不越界；交付三个文件均存在；`script.txt` UTF-8 可解码且非空（允许 UI 手动勾选空稿导出？首版默认阻止空稿交付）；`video` 可解码、时长正数、无音轨；文件写入成功后才能创建 `handoff.json` 完成标志。若有部分输出，自动标记为未完成并可重试。

**后续可扩展：** 只有在下游明确支持 `--import-package <directory>` 或 API 后，才增加“导出并发送”按钮自动调起；V1 默认采用**交付目录 + 在下游工具导入**，不会虚构外部程序接口。

## 8. 编码和导出

### 8.1 选择与回退

**目标：** H.264 / MP4、`yuv420p`、CFR 30 FPS、无音轨。`mp4v` 可用作开发期回退/内部测试，但不作为稳定下游兼容性的默认交付格式。

优先通过随应用提供的、已核实授权和解码兼容性的 FFmpeg 可执行文件编码；启动时运行能力检查并**记录实际可用编码器**。`libx264` 并非每份 FFmpeg 构建都具备，不能在安装包中假设一定可用，也不能忽略其 GPL 合规影响。必要时选用已验证的 Windows Media Foundation/H.264 编码途径，但同样需实机确认硬件/系统支持。

第三方库许可证与视频编解码器专利/分发要求需独立审查，不能仅凭“能打包运行”判定可商用分发。

### 8.2 导出工作流

1. 验证工程清单、标注、源文件以及磁盘可用空间。
2. 使用 EDL 构建输出帧生成器，按顺序解码保留区间并渲染标注。
3. 向编码器写入固定大小视频帧，不包含任何音频输入。
4. 输出临时路径 `video.__exporting__.mp4`；正常结束后使用 `ffprobe` 验证格式、尺寸、duration、音轨。
5. `os.replace` 提交为 `video.mp4`（同一卷内原子替换）；接着写 `script.txt.tmp` 并提交；最后原子写 `handoff.json`。
6. 向 UI 返回成功状态和目录；失败时清理本次临时文件但不删除工程/原始视频。

**超长录制：** 优先流式解码/处理，禁止一次把所有视频帧加载进 RAM；发布版应支持用户取消导出并安全释放子进程。

**速度限制：** 整体变速是下游工具的职责，本导出器始终设置/记录正常速度。

## 9. Windows 安装/目录架构

```text
开发机（Windows x64）
  Python + 锁定依赖 + PyInstaller + Inno Setup + 许可合规的第三方编码器
      ↓ build_release.ps1
  dist/VideoCaptureWorkbench/         # onedir 可独立启动的目录
      ↓ ISCC.exe
  release/视频录制工作台_Setup_1.0.0.exe
      ↓ 用户双击安装
  %LOCALAPPDATA%/Programs/VideoCaptureWorkbench/
      ├── VideoCaptureWorkbench.exe
      ├── _internal/...
      └── tools/...（如果打包）
```

- 默认使用 **per-user 安装**（Inno Setup `PrivilegesRequired=lowest`），不强制管理员权限。
- 注册卸载项、开始菜单、可选桌面快捷方式；版本号和 AppId/UpgradeCode 稳定。
- 卸载默认**不删除** `%USERPROFILE%\Videos\视频录制工作台\` 中的用户项目。
- 升级应先关闭进程，特别是正在录制时不能强制丢掉文件；新版本同 AppId 覆盖安装。
- 内置 Qt platform plugins、编解码器可执行文件、中文字体资源或与系统字体的确定性回退策略。
- 首版不自动联机更新；公开分发建议代码签名，未签名可能触发 SmartScreen 提示。

详见《03_开发实施文档_Development.md》的可执行构建命令及 Inno Setup 配置示例。

## 10. 安全、隐私与可观测性

- 录屏数据仅保存在用户明确选择的本地目录；不上传云端。
- 主程序不启用通用 HTTP 服务、不监听外部端口、不需要登录。
- 导入 `handoff.json/project.json` 时验证 schema 和相对路径；防止路径穿越。
- 仅通过用户选择/明确配置的编码器路径运行外部进程；`subprocess` 参数数组调用，避免 `shell=True` 拼接用户输入。
- 日志默认记时间、源尺寸、编码器版本、帧率、错误码；**不记录用户口播全文、截屏内容或按键文本**。
- 针对系统高权限窗口/密码字段等敏感内容给出录制风险提示；不提供安全限制绕过功能。
- 导出交付 JSON 不包含用户机器用户名、绝对磁盘路径或硬件设备唯一标识。

## 11. 性能与测试策略

| 层级 | 重点 |
|---|---|
| 纯单元测试 | 时间线映射、分割/删除/首尾剪辑、标注交集、项目 schema、快捷键状态规则 |
| 组件测试 | 采集帧队列与时长同步、编码器异常、ffprobe 判定、原子文件提交 |
| GUI 自动/手工 | Qt 主窗口、悬浮窗位置、文字编辑、快捷键开关、项目重开 |
| 实机兼容 | Win10/11 x64；单/双屏；DPI 100%/125%/150%；不同刷新率；中文目录 |
| 安装验证 | 无 Python/pip 的干净机器；安装/覆盖升级/卸载/保留项目/运行离线 |
| 视频交付 | 5 分钟录制输出时长误差 ≤0.5 秒；剪辑时长误差不大于 1 输出帧，或标出编码容器尾帧精度误差 |

**已知未知：** 上传的旧源码仅见语法检查/README 声明，尚未提供双屏实测、长时间稳定性、安装器实测和编码兼容性结果。以上均为待完成测试，而非既有成绩。

## 12. 关键风险与应对

| 风险 | 技术应对 |
|---|---|
| 全局单键 C/V 与应用文本输入冲突 | 可见启停开关、忽略修饰键组合、可扩展热键，不劫持目标输入 |
| MSS 高分辨率抓取/编码速度不足 | 实测 profiling；采集/编码线程解耦、目标帧调度；必要时替换捕获后端 |
| 现有剪辑预览与实际导出不同 | 以 EDL/时间映射为唯一来源；预览与导出复用同一段落生成逻辑 |
| 自动跟随窗口导致跳画面 | 默认关闭高级跟随模式，固定区域为主 |
| 中文贴纸在不同电脑显示差异 | 字体回退策略、渲染前自检字体，发布包字体许可证审查 |
| FFmpeg / x264 分发许可证差异 | 锁定选定二进制来源、构建参数和许可证文件；发版前法务确认 |
| 用户将便携 EXE 理解成安装包 | 统一产出 `Setup.exe`，完整测试快捷方式和卸载入口 |

## 13. 相关资料（官方）

- [PyInstaller：Operating Mode](https://pyinstaller.org/en/stable/operating-mode.html)
- [PyInstaller：Using Spec Files](https://pyinstaller.org/en/stable/spec-files.html)
- [Inno Setup：ISCC 命令行](https://documentation.help/Inno-Setup/topic_compilercmdline.htm)
- [Inno Setup：PrivilegesRequired](https://documentation.help/Inno-Setup/topic_setup_privilegesrequired.htm)
- [FFmpeg：Legal/License](https://ffmpeg.org/legal.html)
- [FFprobe：音视频属性检测](https://www.ffmpeg.org/ffprobe.html)

> 本文的第三方工具选择与许可证说明是工程建议，不构成法律意见；发行前仍需针对具体下载/编译的二进制核验。


---

## 14. V1.3 技术补充：双独立悬浮窗与提词框（2026-10-28）

> **覆盖旧决策：**此前要求把提词框紧贴录制控制窗 `C/V/F9` 栏左侧、并在控制窗移动时随之移动的 V1.2 强制停靠逻辑，现已作废。**以本节为准**。

### 14.1 窗口架构

- `RecordingControlWindow`（悬浮窗 A）负责录制状态、计时、C/V/F9 说明和录制控制；`TeleprompterWindow`（悬浮窗 B）负责口播稿只读显示与阅读操作。
- 两者分别创建为独立顶层 `QWidget` 或 `QDialog`，可设 `Qt.WindowStaysOnTopHint` 与合适的 `Qt.Tool` 标志；**不要将 B 的坐标绑定到 A 的 `moveEvent` / `resizeEvent`**，也不使用固定的左右相连容器。
- `FloatingWindowManager` 只统一协调 `show/hide/close`、录制生命周期和安全恢复；A 的 `toggle_prompter` 信号仅改变 B 的可见性，**不改变 B 的保存位置**。
- A 和 B 的窗口几何分别保存为 `control_geometry`、`prompter_geometry`；可用 Qt `saveGeometry()/restoreGeometry()` 配合屏幕可用区域校验，处理 DPI、跨屏、分辨率改变与显示器拔插。B 的尺寸独立持久化，A 可限制最小尺寸以防功能按钮被遮挡。
- 默认定位：A 位于主屏右下角可用工作区内；B 初次启动时摆在 A 左侧有间距处，若不够空间则摆在 A 上方或其他可见区域。**这是首次打开默认值，不是动态停靠关系。** 以后优先恢复各自最后位置。
- B 关闭按钮仅隐藏；A 关闭触发明确的录制停止确认或安全保存流程。录制停止时统一隐藏两个窗口、恢复主界面。

### 14.2 数据与交互

1. 项目的 `script` / `script.txt` 为唯一稿件来源；录制页和剪辑页编辑同一模型，B 只读呈现，不生成配音。
2. B 支持独立拖动、尺寸调整、字号增减、人工翻稿/滚动、回到开头、隐藏/显示；自动滚动可选且默认关闭，暂停录制时自动滚动同步暂停。
3. C/V 是全局录制效果快捷键，不截断目标应用实际输入。鼠标操作 A/B 时，不应将其控件点击错误写成录屏效果事件。
4. 两窗口彼此不跟随。可选「贴边吸附」仅在用户拖动时提供非强制吸附，默认可不实现。

### 14.3 捕获排除与失败策略（强制）

- MSS 抓取显示器像素时，可能捕获悬浮窗。`SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` 可以作为 Windows 候选措施，但**不能单凭调用返回成功就认定与 MSS/当前驱动组合兼容**。
- 按录制模式分别验证：单屏整屏、单屏指定窗口、指定区域、双屏固定显示器、不同 DPI。通过实录文件查看两窗都未进入视频；不以应用自身实时预览替代成品验证。
- 无法可靠排除时：优先提示把两个悬浮窗移到未录制显示器或捕获区域外；必要时使用经过实测可满足排除要求的捕获后端；无安全方案时阻止该组合开始录制。**不能默默让口播稿入镜。**

### 14.4 建议接口（示意）

```python
class FloatingWindowManager:
    def show_recording_controls(self): ...
    def show_teleprompter(self): ...
    def hide_teleprompter(self): ...
    def toggle_teleprompter(self): ...
    def save_window_geometries(self): ...
    def restore_window_geometries(self): ...
    def close_recording_overlays(self): ...
```

*注意：管理器只管理显隐和状态；不得在 A 移动时主动把 B 重新放到 A 的旁边。*
