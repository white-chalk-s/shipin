"""Create a validated, silent H.264 handoff without modifying source media."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from annotations import validate_annotations, crop_frame
from presentation import render_frame, validate_options


def _run_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _executable(name):
    # Development may use PATH. A release must bundle audited binaries.
    bundled = Path(__file__).resolve().parent / "vendor" / "ffmpeg" / (name + (".exe" if os.name == "nt" else ""))
    return str(bundled) if bundled.is_file() else shutil.which(name)


class HandoffCancelled(Exception):
    pass


class ExportControl:
    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._process = None

    def cancel(self):
        with self._lock:
            self._cancelled = True
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.terminate()
                except OSError:
                    pass

    def check(self):
        with self._lock:
            if self._cancelled:
                raise HandoffCancelled()

    def bind(self, process):
        with self._lock:
            self._process = process
            if self._cancelled:
                try:
                    process.terminate()
                except OSError:
                    pass

    def unbind(self, process):
        with self._lock:
            if self._process is process:
                self._process = None

    def commit(self, staging, final):
        with self._lock:
            if self._cancelled:
                raise HandoffCancelled()
            os.rename(staging, final)


def _probe_video(ffprobe, video, control):
    process = subprocess.Popen([ffprobe, '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(video)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_run_options())
    control.bind(process)
    try:
        stdout, stderr = process.communicate()
        control.check()
        if process.returncode:
            raise RuntimeError("视频校验失败：" + stderr.decode('utf-8', errors='replace')[-2000:])
        return json.loads(stdout.decode('utf-8'))
    finally:
        control.unbind(process)


def export_handoff(parent, source, segments, fps, script, project_id, project_name, annotations=None, crop=None, presentation=None, progress=lambda value: None, control=None):
    control = control or ExportControl()
    control.check()
    if not script.strip():
        raise ValueError("口播稿为空，请先填写稿件。")
    if not segments or fps <= 0:
        raise ValueError("没有可交付的视频片段。")
    ffmpeg, ffprobe = _executable("ffmpeg"), _executable("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("找不到 FFmpeg 或 ffprobe，请安装后重试；正式发布版需要内置已审核的编码器。")
    parent = Path(parent)
    if not parent.is_dir():
        raise ValueError("交付目录不可用。")
    safe_name = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in project_name).strip(" .")[:50] or "录屏"
    final = parent / f"{safe_name}_交付_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    staging = Path(tempfile.mkdtemp(prefix=".vcw-export-", dir=parent))
    cap = None
    process = None
    try:
        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            raise RuntimeError("无法打开原视频。")
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        validate_annotations(annotations or [], count)
        if width < 1 or height < 1 or any(not isinstance(s.get('start'), int) or not isinstance(s.get('end'), int) or s['start'] < 0 or s['end'] > count or s['end'] <= s['start'] for s in segments):
            raise ValueError("视频尺寸或剪辑片段无效。")
        options = validate_options(presentation)
        output_shape = render_frame(np.zeros((height, width, 3), dtype=np.uint8), 0, [], crop, options).shape
        output_height, output_width = output_shape[:2]
        total = sum(s['end'] - s['start'] for s in segments)
        video = staging / "video.mp4"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{output_width}x{output_height}", "-r", str(fps), "-i", "pipe:0", "-an", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(video)]
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, **_run_options())
            control.bind(process)
            written = 0
            try:
                for segment in segments:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, segment['start'])
                    for index in range(segment['start'], segment['end']):
                        control.check()
                        ok, frame = cap.read()
                        if not ok or frame is None or frame.shape[:2] != (height, width):
                            raise RuntimeError(f"读取原视频第 {index} 帧失败。")
                        process.stdin.write(render_frame(frame, index, annotations or [], crop, options).tobytes())
                        written += 1
                        if written % 5 == 0 or written == total:
                            progress(int(written * 85 / total))
                process.stdin.close()
                process.stdin = None
                code = process.wait()
            except BaseException as exc:
                if process.stdin:
                    try: process.stdin.close()
                    except OSError: pass
                    process.stdin = None
                if process.poll() is None:
                    process.terminate()
                process.wait()
                control.check()
                if isinstance(exc, BrokenPipeError):
                    errors.seek(0)
                    detail = errors.read().decode('utf-8', errors='replace')[-2000:]
                    raise RuntimeError("H.264 编码器提前退出：" + (detail or "请检查编码器和磁盘空间。")) from exc
                raise
            finally:
                control.unbind(process)
            control.check()
            if code:
                errors.seek(0)
                raise RuntimeError("H.264 编码失败：" + errors.read().decode('utf-8', errors='replace')[-2000:])
        process = None
        result = _probe_video(ffprobe, video, control)
        streams = result.get('streams', [])
        videos = [s for s in streams if s.get('codec_type') == 'video']
        if len(videos) != 1 or any(s.get('codec_type') == 'audio' for s in streams):
            raise RuntimeError("交付视频的流结构不符合无声视频要求。")
        video_stream = videos[0]
        rate = Fraction(video_stream['r_frame_rate'])
        duration = float(video_stream.get('duration') or result['format']['duration'])
        frames = int(video_stream.get('nb_read_frames') or 0)
        if video_stream.get('codec_name') != 'h264' or rate != 30 or duration <= 0 or frames <= 0 or abs(duration - total / fps) > max(1 / 30, 1 / fps) + 0.005:
            raise RuntimeError("交付视频编码、帧率或时长校验失败。")
        (staging / 'script.txt').write_text(script, encoding='utf-8', newline='')
        handoff = {'schema_version': '1.0.0', 'producer': {'name': 'VideoCaptureWorkbench', 'version': '1.0.0'}, 'project_id': project_id, 'project_name': project_name,
                   'video': {'file': 'video.mp4', 'format': 'mp4', 'video_codec': video_stream['codec_name'], 'width': int(video_stream['width']), 'height': int(video_stream['height']), 'fps_num': rate.numerator, 'fps_den': rate.denominator, 'duration_ms': round(duration * 1000), 'has_audio': False, 'playback_speed': 1.0},
                   'narration': {'file': 'script.txt', 'encoding': 'utf-8', 'type': 'plain_text', 'tts_generated': False},
                   'postproduction': {'duration_alignment_owner': 'downstream', 'speed_formula': 'video_duration_seconds / tts_duration_seconds', 'suggested_auto_speed_min': 0.8, 'suggested_auto_speed_max': 1.25}}
        (staging / 'handoff.json').write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='')
        control.commit(staging, final)
        progress(100)
        return final
    finally:
        if cap is not None:
            cap.release()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if staging.exists():
            shutil.rmtree(staging)


class HandoffWorker(QThread):
    progress_changed = Signal(int)
    succeeded = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, *args):
        super().__init__()
        self.args = args
        self.control = ExportControl()

    def cancel(self):
        self.control.cancel()

    def run(self):
        try:
            path = export_handoff(*self.args, progress=self.progress_changed.emit, control=self.control)
            self.succeeded.emit(str(path))
        except HandoffCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
