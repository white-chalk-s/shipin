"""Versioned local projects with atomic manifests and recoverable script snapshots."""
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from fractions import Fraction
from pathlib import Path, PurePosixPath, PureWindowsPath
from annotations import FIELDS, validate_annotations, validate_crop
from presentation import validate_options


SCHEMA_VERSION = "1.0.0"


def _atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".saving-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _local_path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("工程资源路径无效。")
    posix, windows = PurePosixPath(relative), PureWindowsPath(relative)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise ValueError("工程资源路径不能指向工程目录外。")
    root = Path(root).resolve()
    path = (root / Path(*posix.parts)).resolve()
    if not path.is_relative_to(root):
        raise ValueError("工程资源路径不能指向工程目录外。")
    return path


def _clips_from_frames(segments, fps):
    clips = []
    for segment in segments:
        start = round(segment["start"] * 1000 / fps)
        end = round(segment["end"] * 1000 / fps)
        if end <= start:
            raise ValueError("片段过短，无法以毫秒准确保存。")
        clips.append({"id": str(uuid.uuid4()), "source_in_ms": start, "source_out_ms": end})
    return clips


def _commit(root, metadata, segments, script, fps, annotations=None, crop=None, presentation=None):
    root = Path(root)
    text_bytes = script.encode("utf-8")
    digest = hashlib.sha256(text_bytes).hexdigest()
    snapshot = root / "autosave" / "scripts" / f"{digest}.txt"
    if not snapshot.exists():
        _atomic_bytes(snapshot, text_bytes)
    updated = dict(metadata)
    updated["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    annotations = annotations or []
    validate_annotations(annotations, int(metadata["source"]["frame_count"]))
    stored_annotations = []
    for item in annotations:
        stored = dict(item)
        stored["source_in_ms"] = round(stored.pop("start") * 1000 / fps)
        stored["source_out_ms"] = round(stored.pop("end") * 1000 / fps)
        if stored["source_in_ms"] >= stored["source_out_ms"]:
            raise ValueError("标注时长过短，无法按毫秒保存。")
        stored_annotations.append(stored)
    updated["timeline"] = {**metadata.get("timeline", {}), "clips": _clips_from_frames(segments, fps), "annotations": stored_annotations, "crop": list(validate_crop(crop))}
    updated["presentation"] = validate_options(presentation if presentation is not None else metadata.get("presentation"))
    updated["narration"] = {**metadata.get("narration", {}), "file": "script.txt", "encoding": "utf-8", "sha256": digest}
    _atomic_bytes(root / "script.txt", text_bytes)
    _atomic_bytes(root / "project.json", (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return updated


def create_project(parent, source, width, height, fps, frame_count, segments, script, annotations=None, crop=None, presentation=None):
    parent, source = Path(parent), Path(source).resolve()
    if not parent.is_dir() or not source.is_file():
        raise ValueError("项目目录或原始视频不可用。")
    if not fps or fps <= 0:
        raise ValueError("原始视频帧率无效。")
    stem = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in source.stem).strip(" .")[:50] or "录屏"
    name = f"{stem}_项目_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    final = parent / name
    staging = Path(tempfile.mkdtemp(prefix=".vcw-project-", dir=parent))
    try:
        media = staging / "media" / ("raw" + (source.suffix.lower() or ".mp4"))
        media.parent.mkdir()
        before = source.stat()
        shutil.copyfile(source, media)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or media.stat().st_size != before.st_size:
            raise RuntimeError("复制期间原视频发生变化，工程未创建。")
        rate = Fraction(float(fps)).limit_denominator(100000)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        metadata = {"schema_version": SCHEMA_VERSION, "project_id": str(uuid.uuid4()), "name": stem,
                    "created_at": now, "updated_at": now,
                    "source": {"file": media.relative_to(staging).as_posix(), "width": width, "height": height,
                               "fps_num": rate.numerator, "fps_den": rate.denominator,
                               "duration_ms": round(frame_count * 1000 / fps), "frame_count": frame_count,
                               "has_audio": None},
                    "timeline": {"clips": [], "annotations": []},
                    "export": {"target_fps_num": 30, "target_fps_den": 1, "audio": False}}
        _commit(staging, metadata, segments, script, fps, annotations, crop, presentation)
        os.rename(staging, final)
        return final / "project.json"
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def read_project(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("不支持的工程版本，未修改工程文件。")
    root = path.parent
    source = _local_path(root, data["source"]["file"])
    if not source.is_file():
        raise FileNotFoundError(f"工程原始素材不存在：{source}")
    narration = data["narration"]
    script_path = _local_path(root, narration["file"])
    digest = narration.get("sha256")
    text_bytes = script_path.read_bytes() if script_path.is_file() else b""
    recovered = False
    if digest and hashlib.sha256(text_bytes).hexdigest() != digest:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("口播稿校验值无效。")
        backup = _local_path(root, f"autosave/scripts/{digest}.txt")
        if not backup.is_file():
            raise ValueError("口播稿与工程不一致，且没有可恢复的快照。")
        text_bytes = backup.read_bytes()
        if hashlib.sha256(text_bytes).hexdigest() != digest:
            raise ValueError("口播稿快照校验失败。")
        recovered = True
    elif not script_path.is_file():
        raise FileNotFoundError(f"工程口播稿不存在：{script_path}")
    script = text_bytes.decode("utf-8-sig")
    source_info = data["source"]
    num, den = int(source_info["fps_num"]), int(source_info["fps_den"])
    if num <= 0 or den <= 0:
        raise ValueError("工程帧率无效。")
    fps = num / den
    frame_count = int(source_info.get("frame_count", round(source_info["duration_ms"] * fps / 1000)))
    if frame_count <= 0:
        raise ValueError("工程素材帧数无效。")
    timeline = data["timeline"]
    annotations = []
    stored_annotations = timeline.get("annotations", [])
    if not isinstance(stored_annotations, list):
        raise ValueError("工程标注格式无效。")
    for item in stored_annotations:
        if not isinstance(item, dict) or set(item) != (FIELDS - {"start", "end"}) | {"source_in_ms", "source_out_ms"}:
            raise ValueError("该工程包含不支持的标注格式，已阻止打开以免丢失数据。")
        if type(item["source_in_ms"]) is not int or type(item["source_out_ms"]) is not int:
            raise ValueError("标注时间格式无效。")
        annotation = dict(item)
        annotation["start"] = round(annotation.pop("source_in_ms") * fps / 1000)
        annotation["end"] = round(annotation.pop("source_out_ms") * fps / 1000)
        annotations.append(annotation)
    validate_annotations(annotations, frame_count)
    segments = []
    last_end = 0
    for clip in timeline["clips"]:
        start = round(int(clip["source_in_ms"]) * fps / 1000)
        end = round(int(clip["source_out_ms"]) * fps / 1000)
        if start < last_end or end <= start or end > frame_count:
            raise ValueError("工程片段范围无效。")
        segments.append({"start": start, "end": end})
        last_end = end
    return {"path": path, "metadata": data, "source": source, "script": script,
            "segments": segments, "annotations": annotations, "crop": validate_crop(timeline.get("crop")), "presentation": validate_options(data.get("presentation")), "fps": fps, "frame_count": frame_count, "recovered": recovered}


def save_project(path, segments, script, fps, annotations=None, crop=None, presentation=None):
    current = read_project(path)
    if abs(current["fps"] - fps) > 0.01:
        raise ValueError("工程与视频帧率不一致，无法保存。")
    _commit(current["path"].parent, current["metadata"], segments, script, fps, current["annotations"] if annotations is None else annotations, current["crop"] if crop is None else crop, current["presentation"] if presentation is None else presentation)
