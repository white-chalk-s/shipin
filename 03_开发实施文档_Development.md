# 视频录制工作台｜开发、测试、打包与一键安装实施文档（规格 V1.3）

> 文档状态：开发执行指南 · 2026-10-28  
> 用途：开发人员可按本文逐项落地 Windows 本地应用及 `Setup.exe` 安装器。  
> **重要：本文是开发与发布方案，不代表 Windows 可安装程序已经构建完成。** 当前环境未运行 Windows 实机测试，也未生成真实安装包。

## 1. 交付定义与工程目标

最终建议交付给**终端用户**：

```text
release/
└── 视频录制工作台_Setup_1.0.0.exe     # 双击可安装的软件安装器
```

最终建议交付给**开发/维护人员**：

```text
source/
├── src/                                  # PySide6 工程源码
├── tests/                                # 自动化测试
├── assets/                               # 图标/UI 资源
├── vendor/                               # 经许可核验的编码器资源
├── packaging/
│   ├── VideoCaptureWorkbench.spec        # PyInstaller 配置（开发后期固定）
│   ├── VideoCaptureWorkbench.iss         # Inno Setup 安装脚本
│   └── build_release.ps1                 # 一键构建发布包
├── requirements.txt
├── requirements-lock.txt
└── docs/
    ├── PRD.md
    ├── ARCHITECTURE.md
    └── DEVELOPMENT.md
```

**验收分界：** 开发者执行一条发布脚本生成安装包；用户**不需要**执行脚本，用户只需双击安装包、按向导完成安装并打开程序。

## 2. 开发环境与版本管理

### 2.1 推荐环境

| 项目 | 推荐基线 | 说明 |
|---|---|---|
| 构建操作系统 | Windows 11 x64 | 必须在 Windows 构建 Windows EXE；同时测试 Win10 x64 |
| Python | 3.11 x64 | 与当前 README 支持范围对齐；3.12 可作为补充验证 |
| 桌面 UI | PySide6（沿用现有） | 避免无必要地改用 Electron/Tauri |
| 采集 | `mss` | 沿用现有，实现兼容抽象后可替换 |
| 键盘鼠标监听 | `pynput` | 实现 C/V/F9 全局键事件；不拦截按键 |
| 视频处理 | OpenCV / NumPy + 验证过的编码器 | OpenCV 供读帧/图像变换；正式导出使用兼容的 H.264 方案 |
| 安装器 | PyInstaller + Inno Setup 6 | PyInstaller onedir → Inno Setup exe |
| 测试 | pytest + 手工 Windows GUI 测试 | Qt GUI 依赖真实显示器的功能须做实机测试 |

现有上传的 `requirements(2).txt` 包含 `PySide6>=6.7, mss>=9.0, opencv-python>=4.10, numpy>=1.26, pynput>=1.7.7`。这些只是**最低版本约束**，不构成可重复发布的冻结依赖。发布前要在 Windows 构建机锁定所有依赖确切版本。

### 2.2 初始化开发环境（PowerShell，Windows）

```powershell
# 在项目根目录执行。首先将上传文件整理为标准工程文件名。
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller pytest
.\.venv\Scripts\python.exe -m pip check

# 在一次完整测试通过后冻结发布版本，不应在每次发布前自动升级到最新版本。
.\.venv\Scripts\python.exe -m pip freeze > requirements-lock.txt
```

日常构建使用 `requirements-lock.txt`（发布时需将 `pyinstaller`、测试工具的版本管理纳入 lock 或 build requirements）；开发期可维护较宽松的 `requirements.txt`。

### 2.3 旧脚本整理

现有文件名在上传目录中带有 `(2)`：

```text
region_recorder(2).py   → region_recorder.py
requirements(2).txt    → requirements.txt
build_windows(2).bat   → build_windows_legacy.bat
run_windows(2).bat     → run_windows_legacy.bat
```

旧 `build_windows_legacy.bat` 引用 `requirements.txt` 和 `region_recorder.py`，同时使用 `--onefile`，只能作为历史参考；**不要把它直接当作用户安装包生产流水线**。

## 3. 开发阶段与任务清单

建议按“先可靠录制 → 可靠剪辑 → 工程/交付 → 安装发布”顺序实施，**不是按 UI 页面顺序一次重写**。

### 阶段 D1：工程整理与可运行基线

- [ ] 创建模块目录、统一文件名和入口点 `python -m vcw`。
- [ ] 旧程序在 Windows 虚拟环境可启动；保留一次能回滚的基线提交。
- [ ] 将 UI、捕获线程、视频处理、文件路径分别抽出，原功能先不变。
- [ ] 引入 `logging` 与统一异常汇报；日志写 `%LOCALAPPDATA%\VideoCaptureWorkbench\logs`。
- [ ] 增加单实例运行控制或明确允许多实例的行为；若禁止多实例，第二次启动聚焦原窗口。

**D1 完成条件：** 重构后旧功能仍可演示，并能执行至少一段有效录制/停止/导出。

### 阶段 D2：录制与快捷键

- [ ] 从原有 `mouse.Listener(on_click=...)` 触发缩放机制移除“左键放大”依赖。
- [ ] 建立 `HotkeyController`，监听 `c` / `v` / `F9` 的按下事件，忽略 key-repeat。
- [ ] 修饰键状态跟踪：按住 `Ctrl`/`Alt`/`Win` 时 `C/V` 不产生特效；F9 不受录屏窗口焦点影响。
- [ ] 悬浮控制窗 A 加入“启用 C/V 效果”切换，支持录制过程中更改；并提供“显示/隐藏提词框 B”入口。
- [ ] 实现 C 当前位置高亮（默认 1.5 秒）与 V 当前位置 2× 放大（进出各约 0.35 秒、停留 2 秒）。
- [ ] 采集时使用 `perf_counter_ns()` 记录时间；录制线程实现目标 30 FPS 帧调度，消除简单写帧造成的显著时长漂移。
- [ ] 固定显示器与窗口/区域捕获的 DPI 坐标统一；默认固定屏幕，不默认跨屏跟随。
- [ ] 录制中“暂停/继续/停止并保存/停止并丢弃”的资源清理与失败回收。
- [ ] **两个**悬浮窗的捕获排除均需实机验证；无法安全排除时，提示移出录制区域、切换验证过的捕获方式或禁止不安全录制组合。

**D2 完成条件：** 录制中按 C/V 有正确效果；`Ctrl+C` 与 `Ctrl+V` 不误触发；5 分钟录制时长达到 NFR 测试目标；即使异常退出，也能按规定报告临时素材状态。

### 阶段 D3：轻剪辑与预览

- [ ] 片段模型采用毫秒整数时间区间，编辑只修改 EDL，不覆盖原视频。
- [ ] 增加首尾修剪，保留分割、删除、撤销、恢复；剪后输出时长统一计算。
- [ ] **优先修复预览播放**：原源码播放按原视频帧顺序前进，删除片段后仍可能播放已删除内容；改为输出时间 → 原视频时间映射。
- [ ] 播放头、片段高亮、进度条显示及输出时长保持同步。
- [ ] 实现标注轨：文字、箭头、矩形框、简单贴纸。
- [ ] 标注属性：位置、颜色、字号、开始/结束时间；删除片段时标注自动裁切/删除。
- [ ] 预览和导出共用标注渲染核心；字体与透明效果实测一致。
- [ ] 支持 Ctrl+Z 撤销；可扩展 Ctrl+S 保存项目、空格播放暂停（键盘焦点在口播输入框时应优先文本编辑）。

**D3 完成条件：** 分割删除后预览真正跳过已删除片段，导出成品与预览关键帧一致；中文标注可显示。

### 阶段 D4：口播、工程与交付

- [ ] 口播稿编辑区支持粘贴、导入 TXT、编辑和字数统计。
- [ ] 录制/剪辑/口播写入 `project.json` + `script.txt`，支持自动保存和重开工程。
- [ ] 工程为 JSON 版本化 schema；路径使用项目内相对路径，不依赖原电脑绝对目录。
- [ ] 默认新项目目录位于用户的 Videos 中，用户可手动选择自定义目录。
- [ ] 导出为 `video.mp4`、`script.txt`、`handoff.json`；文件写盘/校验成功后再标记完成。
- [ ] Handoff 的视频时长、编解码器、尺寸由导出的实际文件读取，不由 UI 假定。
- [ ] 导出页面明确提示“正常速度；口播时长匹配在下游处理”。
- [ ] 历史项目展示最近工程与最近导出目录；失败/删除后的路径失效可修复或移出列表。
- [ ] 下游联调时以“导入交付目录”作为最小闭环；若没有外部协议，不实现虚假的一键调用。

**D4 完成条件：** 安装程序可以离线新建项目→录制→剪辑→写稿→保存→重开→导出；下游可解析三个文件。

### 阶段 D5：编码、性能与安装发布

- [ ] 确定并审核随包 FFmpeg/其他视频编码器的版本、来源、编译参数、许可证与再分发策略。
- [ ] 验证发布版 H.264 MP4/无音轨/固定 FPS，并以 `ffprobe` 读取真实媒体信息。
- [ ] 编码/导出在工作线程或子进程中运行，导出时 GUI 不冻结。
- [ ] PyInstaller 使用 **onedir** 构建；检查 Qt 平台插件与外部二进制路径。
- [ ] Inno Setup 生成 `Setup.exe`，配置开始菜单、可选桌面快捷方式、卸载入口、升级覆盖。
- [ ] 安装后无 Python、无网络环境下完整走一遍“录制→导出”。
- [ ] 未签名版本测试 Windows 安全提示；公开分发前考虑代码签名。
- [ ] 完成第三方许可证清单、版本信息、发布包 SHA-256、人工发布记录。

**D5 完成条件：** 干净 Windows 10/11 机器上，用户双击安装器，不运行任何命令即可完成安装、运行和卸载，升级不会丢失用户工程。

## 4. 必须先修复的现有代码问题

| 位置 | 现状 | 修改方向 |
|---|---|---|
| `RecorderWorker._on_mouse_click()` | 左键触发放大 | 废止作为放大触发器；改成 C/V 全局按键事件 |
| `RecorderWorker._zoom_state_at()` | 一次放大固定事件型状态，倍率固定约 2× | 封装独立的 ZoomEffect，参数可配置，能按 V 重新触发 |
| `MainWindow._on_global_key()` | 只对 F9 有监听 | 增加 C/V，并处理修饰键、key-repeat、暂停状态 |
| `RecorderWorker.run()` | `mss` 抓图→OpenCV `mp4v` 写帧；循环固定写 FPS | 分离抓帧与编码；用真实时间戳调度，正式导出验证 H.264 |
| `VideoEditorWindow._on_play_tick()` | 原视频线性前进 | 统一走剪后 EDL 映射，严格预览保留片段 |
| `VideoExportWorker` | 仅合并保留帧并用 `mp4v` 重编码 | 标注渲染、H.264 可用性检测、临时文件、可取消、媒体校验 |
| `VideoEditorWindow` | 仅列表片段，没有标注轨和口播区 | 新增属性编辑、工程自动保存、交付入口 |
| 旧 BAT | `--onefile` 直接生成 EXE | 开发阶段可用，发布改 `--onedir` + Inno Setup |

**重构原则：** 同一源文件可以分批迁移，先保留已有行为的测试样例，再把新事件/时间线实现接入。避免同时大幅修改 GUI、视频核心、安装器，导致无法定位故障。

## 5. 编码/导出实现要点

### 5.1 录屏输出

1. 确定固定分辨率；采集缓冲与编码队列有上限，防止积压耗尽内存。
2. 采集图像、叠加光标和快捷键特效；所有效果依据单调时间戳求值。
3. 以恒定目标 FPS 将画面编码为临时视频；录制完成后关闭编码器、探测媒体再提交。
4. 发生磁盘满/编码器异常时停止采集并显式报错，不能继续显示“正在正常录制”。

### 5.2 剪辑导出

- 视频源使用帧索引/时间映射流式解码；对保留片段逐一编码输出，不要求内存加载整片。
- 原始媒体默认音轨为空。若导入外部带音轨的视频，V1 明确按产品边界**忽略输入音频并输出无声视频**；避免意外携带系统声。
- 统一输出 30 FPS、正方像素、H.264 MP4、`yuv420p`，支持合理的压缩质量设置；字幕和 TTS 不输出。
- 最终 `duration_ms` 用 `ffprobe` 探测，不能仅计算 `总帧数/30` 并假设容器毫无尾帧偏差。

可用于实机验收的检测命令：

```powershell
# ffprobe.exe 应来自开发环境或已审核的发布资源
ffprobe -v error -show_entries format=duration -of json "video.mp4"
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,r_frame_rate -of json "video.mp4"
```

确保输出中有一个视频流、零个音频流；codec_name 应为 `h264`，时长与剪辑预期一致。

### 5.3 许可证特别说明

- `PyInstaller` 收集依赖不代表第三方视频编码库及其二进制文件已满足再分发条款。
- FFmpeg 可能是 LGPL 或因选择可选 GPL 组件变成 GPL 构建；`libx264` 相关构建需特别核查。
- 发版时附第三方许可证、来源、构建信息；商业分发请进行对应许可证/专利风险审查。
- 参考官方说明：<https://ffmpeg.org/legal.html>。

## 6. 构建工程：从源码到 onedir

### 6.1 开发版快速验证（尚未迁移工程入口时）

旧代码统一命名为 `region_recorder.py` 以后，可以先用下面命令确认 PyInstaller 可启动；该命令只是**可执行目录测试**：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm --clean --windowed --onedir `
  --name VideoCaptureWorkbench `
  --collect-all mss `
  --collect-all pynput `
  --collect-all cv2 `
  region_recorder.py

.\dist\VideoCaptureWorkbench\VideoCaptureWorkbench.exe
```

**说明：** 当工程迁移完成后，构建入口从 `region_recorder.py` 改为 `src/vcw/__main__.py` 或提供稳定的 `main.py`；是否需要额外 `--collect-*` 必须依据 Windows 打包日志逐项确认。

### 6.2 正式打包命令（工程入口迁移后）

```powershell
# 打包发生在 Windows x64 开发/CI 机器上
.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm --clean --windowed --onedir `
  --name VideoCaptureWorkbench `
  --collect-all mss `
  --collect-all pynput `
  --collect-all cv2 `
  --paths src `
  src\vcw\__main__.py
```

上述示例未包含 FFmpeg。正式发布必须追加已经通过许可证核验的编码器文件，或采用其他已验证编码途径；不可为了通过打包而默默回退到未经下游验证的 `mp4v` 格式。

可在选择随包 `vendor\ffmpeg\ffmpeg.exe` 与 `vendor\ffmpeg\ffprobe.exe` 时采用下面的附加参数（示意，**需要先准备对应合法二进制**）：

```powershell
  --add-binary "vendor\ffmpeg\ffmpeg.exe;tools" `
  --add-binary "vendor\ffmpeg\ffprobe.exe;tools" `
```

运行时从打包资源目录定位内置工具，而不是依赖终端用户的 PATH：

```python
from pathlib import Path
import sys


def bundled_tool(name: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # PyInstaller onedir 资源目录
    else:
        base = Path(__file__).resolve().parents[3] / "vendor" / "ffmpeg"
    path = base / "tools" / name if getattr(sys, "frozen", False) else base / name
    if not path.is_file():
        raise FileNotFoundError(f"Required bundled tool missing: {path}")
    return path
```

上面开发路径仅适用于约定的 `src/vcw/...` 结构；可以通过配置/路径单元测试固化，不能当作通用项目根目录探测器。

### 6.3 为什么选 onedir + 安装器

| 方案 | 对终端用户 | 维护判断 |
|---|---|---|
| 仅 `--onefile` | 一个便携 EXE，可运行但缺标准安装/卸载体验 | 不满足当前“一键安装”目标 |
| 仅 `--onedir` | 一个包含大量 DLL 的文件夹，需要手动拷贝 | 开发调试好，用户交付差 |
| **`--onedir` + Inno Setup** | **一个 Setup.exe，安装后为标准本地程序** | **推荐**，便于更新、资源部署、卸载 |

参考 [PyInstaller 官方文档](https://pyinstaller.org/en/stable/operating-mode.html)。

## 7. Inno Setup 安装脚本（可直接用作模板）

创建文件：`packaging/VideoCaptureWorkbench.iss`。

> 前置条件：`dist\VideoCaptureWorkbench\VideoCaptureWorkbench.exe` 已生成。此脚本是安装打包**模板**，需要在 Windows 上使用实际项目编译和测试，并替换产品发行信息。

```ini
#define MyAppName "视频录制工作台"
#define MyAppVersion "1.0.0"
#define MyAppExeName "VideoCaptureWorkbench.exe"

[Setup]
AppId={{C04D74CE-DC60-409B-AB93-7A2D3478D891}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=VideoCaptureWorkbench
DefaultDirName={localappdata}\Programs\VideoCaptureWorkbench
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=视频录制工作台_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "..\dist\VideoCaptureWorkbench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动视频录制工作台"; Flags: nowait postinstall skipifsilent
```

**关键参数解释：**

- `PrivilegesRequired=lowest`：默认按用户安装，不请求提权；适合单人本地工具。<https://documentation.help/Inno-Setup/topic_setup_privilegesrequired.htm>
- `DefaultDirName={localappdata}\Programs\...`：安装到用户目录，**不是**录屏项目目录。
- `AppId`：必须在后续版本保持稳定；不要每次版本升级重新随机生成。
- `[Files]`：复制整个 PyInstaller onedir 文件夹，包括内部依赖。
- `[Icons]`：创建开始菜单快捷方式，桌面图标可选。
- 同一 `AppId` 覆盖安装时须验证“不覆盖原项目、用户设置保留、旧进程先退出”。
- **不要**编写卸载时递归清理用户 Videos/项目目录的规则；卸载默认保留用户视频与稿件。
- `CloseApplications=yes` 只负责协助关闭程序，应用本身必须实现正在录制/未保存时安全退出流程，升级安装需要手工测试。
- `chinesesimp` 语言资源在所用 Inno Setup 版本中需要实机检查；若编译器缺少该语言文件，应安装正确的语言资源或先删除 `[Languages]` 段以生成英语界面测试包。

### 7.1 编译安装包

安装 Inno Setup 6 后，使用命令行编译器 `ISCC.exe`：

```powershell
# 如果 ISCC 已配置在 PATH 中：
ISCC.exe .\packaging\VideoCaptureWorkbench.iss

# 成功后预期输出：
# .\release\视频录制工作台_Setup_1.0.0.exe
```

若提示找不到 `ISCC.exe`，使用实际安装路径（常见位置：`C:\Program Files (x86)\Inno Setup 6\ISCC.exe`），不要求终端用户安装 Inno Setup。

### 7.2 发布构建脚本（建议结构）

建议新增 `packaging/build_release.ps1`，确保**开发者一键构建、用户一键安装**。下面是最小流水线逻辑；CI 可再加上版本与代码签名步骤。

```powershell
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (!(Test-Path $python)) { throw "缺少构建虚拟环境，请先创建 .venv" }

# 项目入口约定已迁移为 src/vcw/__main__.py
& $python -m pytest tests
if ($LASTEXITCODE -ne 0) { throw "测试未通过" }

& $python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name VideoCaptureWorkbench --paths src `
    --collect-all mss --collect-all pynput --collect-all cv2 `
    src\vcw\__main__.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

$exe = "dist\VideoCaptureWorkbench\VideoCaptureWorkbench.exe"
if (!(Test-Path $exe)) { throw "未生成主程序：$exe" }

$iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if ($null -eq $iscc) { throw "未发现 Inno Setup ISCC.exe，请配置 PATH" }
& $iscc.Source "packaging\VideoCaptureWorkbench.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 编译失败" }

Get-FileHash "release\视频录制工作台_Setup_1.0.0.exe" -Algorithm SHA256
Write-Host "安装包构建完成。请继续在干净 Windows 机器进行安装验收。"
```

> 此处命令是**模板**：正式 CI 应改为执行锁定依赖安装，并在构建时加入必需的 FFmpeg/资源文件以及发布前编码器能力检查；否则不能把仅通过打包的程序认定为完整 V1。

## 8. 首次安装、升级、卸载的预期行为

### 首次安装

1. 用户双击 `Setup.exe`。
2. 安装向导选择目标路径，默认不弹管理员 UAC。
3. 安装程序将 Python 运行时、PySide6、Qt 插件、已审查的媒体工具一并复制。
4. 创建开始菜单项，可选桌面快捷方式。
5. 安装后直接从开始菜单运行；首次打开自动创建配置和项目根目录。
6. 用户无需另装 Python、pip、OpenCV、PySide6 或 FFmpeg。

### 覆盖升级

1. 用户退出录制和编辑程序（有未保存内容必须提示）。
2. 运行新 `Setup.exe`，保留同一个 `AppId` 和用户配置目录。
3. 安装完成启动新版本；旧工程通过 `schema_version` 检查/迁移打开。
4. 旧项目、原视频、稿件、导出目录全部保留。

### 卸载

1. Windows 设置 → 已安装的应用 → 视频录制工作台 → 卸载。
2. 删除安装目录、开始菜单项、卸载注册信息。
3. 默认保留用户视频、稿件、工程；若未来提供“清除个人数据”，必须单独明确询问。

## 9. 单元测试建议与关键断言

### 9.1 时间线

```python
# 示意性用例，不表示当前工程已存在这些函数
clips = [(0, 45000), (54000, 124000)]
assert total_output_ms(clips) == 115000
assert source_time_for_output(clips, 51000) == 60000
assert visible_annotation_intervals(
    clips,
    annotation_start_ms=44000,
    annotation_end_ms=55000
) == [(44000, 45000), (54000, 55000)]
```

**提示：** `visible_annotation_intervals` 示例返回的是原素材时间交集，最终导出渲染前还需要映射为输出时间。该断言用于测试裁剪逻辑而非输出位置。

### 9.2 快捷键

- `C keydown`（录制且开关打开）→ 触发一次 highlight；长按重复事件不得触发多次。
- `V keydown` → 生成当前位置的 zoom event；中途第二次 V 应替换当前放大事件。
- `Ctrl+C` / `Ctrl+V` / Alt+V / Win+V → 不触发；原应用仍可收到按键。
- 非录制/暂停/开关关闭状态的 C/V → 不触发。
- 快捷键监听初始化失败要在 UI 提示，不得静默假装可用。

### 9.3 工程与媒体

- `project.json` 保存/打开后保持片段顺序、标注时间和口播内容。
- 删除片段后标注被正确裁剪/隐藏；预览画面与最终导出对应时间一致。
- 从本地相对路径恢复移动到另一个文件夹的完整项目。
- 空磁盘/只读目录/导出中断时**不覆盖源文件**，失败可重试。
- `ffprobe` 验证 MP4 为 H.264、无音轨、时长正确。

## 10. Windows 实机测试矩阵

| 编号 | 测试维度 | 验收动作 |
|---|---|---|
| W01 | Win11 x64，单屏 1080p | 录制 5 分钟，验证画面与时长 |
| W02 | Win10 x64，单屏 1080p | 启动/采集/编码/剪辑/导出 |
| W03 | 双屏 + 不同分辨率 | 固定第二屏录制；跨屏移动鼠标不跳画面 |
| W04 | DPI 100%、125%、150% | 框选区域、光标高亮位置、贴纸坐标准确 |
| W05 | 屏幕左侧为负坐标 | 正确定位到目标屏幕且不出现黑边偏移 |
| W06 | 输入中文/英文、Ctrl+C/V | 单键与组合键行为符合设计 |
| W07 | 录制中暂停/继续/丢弃 | 计时、编码资源、文件管理正常 |
| W08 | 在导出时关闭界面 | 明确取消/等待行为，无损坏原始媒体 |
| W09 | 有中文、空格、较长路径 | 工程、剪辑、导出、安装路径均可用 |
| W10 | 无 Python 的干净系统 | 只装 Setup.exe 即运行全部功能 |
| W11 | 覆盖升级旧版本 | 工程可读、自动保存与用户目录保留 |
| W12 | 卸载重装 | 用户项目仍存在，重装后可以重新载入 |
| W13 | 高分辨率/CPU 压力 | 确定 FPS 与帧补偿策略，不闪退/静默错时长 |
| W14 | 浏览器、办公软件、加速渲染窗口 | 记录不支持场景和可重复的失败信息 |

### 10.1 质量门禁

| 门禁 | 通过要求 |
|---|---|
| Q1 语法/单元测试 | 全绿；时间线和 schema 逻辑具覆盖测试 |
| Q2 用户链路 | 新建→录制→分割删除→标注→输入稿件→保存重开→交付成功 |
| Q3 数据保护 | 原始录屏不覆盖；异常导出不留下冒充完整成品的 `handoff.json` |
| Q4 时长 | 基准 5 分钟录制的时长偏差 ≤0.5 秒；剪辑目标误差 ≤一帧或记录实际容器边界误差 |
| Q5 安装 | 干净 Win10/Win11 无 Python 安装成功、可卸载、可升级 |
| Q6 兼容性 | 下游实际解码 `video.mp4`，并读入 script.txt 与 handoff.json |
| Q7 许可证 | 内置依赖、编码器版权/许可清单检查完成，分发方式与许可证一致 |

## 11. 发布前检查清单

- [ ] `__version__`、安装器 `AppVersion`、工程 schema 版本区分管理并在发行记录中登记。
- [ ] 源代码依赖冻结且 `pip check` 通过，无未经审查的构建源。
- [ ] Windows 实机录制/录制区域/快捷键/剪辑/标注/工程恢复完整走通。
- [ ] 预览与最终成品关键片段一致；导出 H.264 兼容下游。
- [ ] 随包编码器可执行文件存在并由程序自行定位，离线运行不依赖用户 PATH。
- [ ] Inno Setup 编译成功；真实 `Setup.exe` 安装、运行、升级、卸载验收通过。
- [ ] 安装目录与用户工程目录明确分开；卸载不误删视频、口播稿。
- [ ] 发布包文件名含版本；生成 SHA-256 和更改记录。
- [ ] 如果公开分发，完成代码签名策略、SmartScreen 提示评估及第三方许可审查。
- [ ] 提供最简用户帮助：快捷键 C/V/F9、如何关闭特效、停止/丢弃、如何导出与导入下游。

## 12. 给开发者的最小任务指令（可直接复制）

> 基于现有 PySide6 `region_recorder.py` 代码改造为 Windows 10/11 x64 本地应用“视频录制工作台”。不要开发 TTS、字幕、BGM、封面或整段变速。保留多屏、区域框选、暂停/停止。取消左键自动放大，改为录制中 C 键当前位置高亮、V 键当前位置放大；忽略按键长按重复和 Ctrl/Alt/Win 修饰组合，提供快捷键总开关。剪辑采用非破坏性 EDL，支持首尾修剪、分割、删除、撤销及真正的剪后时间线预览；添加文字、箭头、矩形框和贴纸。提供录制前口播稿 TXT 编辑和录制中独立的口播提词悬浮窗；提词框可移动/缩放/滚动，与录制控制悬浮窗位置互不绑定，两个窗口都应在最终视频中被安全排除。提供本地工程保存/恢复。导出无声、正常速度、下游兼容的 H.264 MP4，以及 UTF-8 TXT 和 handoff.json。使用 PyInstaller onedir 构建，再用 Inno Setup 生成单一 Setup.exe 安装器；终端用户不需要 Python。严格依次完成自动化测试和 Windows 实机安装/升级/卸载验收，不得把 HTML 原型或未测试的便携 EXE 称为完成的安装应用。

## 13. 参考文档

- [产品需求文档](./01_产品需求文档_PRD.md)
- [技术架构与数据协议](./02_技术架构文档_Architecture.md)
- [PyInstaller 官方：spec files](https://pyinstaller.org/en/stable/spec-files.html)
- [Inno Setup 官方：ISCC 命令行](https://documentation.help/Inno-Setup/topic_compilercmdline.htm)
- [FFmpeg 官方：许可证说明](https://ffmpeg.org/legal.html)


---

## 14. V1.3 增量开发任务：双独立悬浮窗（P0，2026-10-28）

> **设计变更：** 取消 V1.2 的“提词框贴 C/V/F9 栏并自动跟随控制窗”。现在采用两个互不锁定位置的独立悬浮窗。旧版 HTML 原型仅作为视觉参考，不作为窗口跟随行为的验收依据。

### D3.5 录前稿件及双悬浮窗

- [ ] 录制工作台增加口播稿输入：粘贴、导入 UTF-8 TXT、清空、字数统计、草稿自动保存。
- [ ] 主窗口、剪辑页、工程存盘和导出的 `script.txt` 复用同一 `ScriptDocument` 数据模型。
- [ ] 现有 `RecordingControlWindow` 改为悬浮窗 A；计时、C/V/F9 提示、暂停、停止、丢弃以及提词框显隐入口保留。
- [ ] 新建 `TeleprompterWindow`（悬浮窗 B）；只读文本、窗口独立拖动与缩放、字号 +/−、滚动/上一段/下一段/回到开头、隐藏/恢复。
- [ ] **禁止**在 A 的 `moveEvent/resizeEvent` 中强制重算 B 坐标；分别存储 A 与 B 的位置、B 的尺寸。拖动 A，B 保持原位；拖动 B，A 保持原位。
- [ ] 首次默认 A 在右下角、B 在 A 左侧附近；以后恢复各自最后保存位置。显示器拔插、DPI/分辨率变化时将离屏悬浮窗移回可视区域。
- [ ] 关闭 B 的 `×` 仅隐藏 B，不影响录制；从 A 单击“显示/隐藏提词框”可恢复。录制结束，安全关闭两窗并打开剪辑页。
- [ ] 自动滚动作为 P1；默认关闭，用户手动滚动优先；暂停录制时滚动也暂停。
- [ ] 全局 C/V 不劫持文本编辑与系统快捷键；B 为只读；操作悬浮窗本身不应触发错误的 C/V 特效。
- [ ] **发布阻断：**单屏整屏、单屏窗口、指定区域、双屏固定录制，均验证 A/B 不出现在最终 MP4。不能通过时必须采用安全降级或更换捕获方案，不能忽略。

### D3.6 GUI 验收与回归

- [ ] A、B 是两个独立可操作顶层窗口，而不是单个窗口的左右分栏。
- [ ] 拖动 A 不影响 B 的位置；拖动 B 不影响 A 的位置（包含两窗分处不同屏幕的情况）。
- [ ] B 可单独改变宽高；恢复软件后字号、滚动位置和窗口尺寸按约定保留。
- [ ] B 可隐藏后经 A 恢复；整个过程中录制计时持续、视频无损坏。
- [ ] 多 DPI、双屏拔插或分辨率变化后，两个悬浮窗均可见、可拖动、功能按钮可点击。
- [ ] 录制中调整 B 阅读设置不影响 C/V、F9 录制行为；主窗口录前稿件与 B、剪辑页、导出 TXT 一致。
- [ ] 对每种声明支持的录制方式，在成品中检查开头/中段/末尾及悬浮窗覆盖位置，证实双窗不入镜。

**新的端到端路径：**录制前输入稿件 → 点击开始 → A 和 B 分别出现 → 用户自由布置两个悬浮窗 → 通过 B 阅读口播并通过 A 控制录制 → 停止进入剪辑 → 校对稿件 → 导出 TXT + MP4 + JSON。
