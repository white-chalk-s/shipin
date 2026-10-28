"""Shared output framing and fixed stage titles for preview and export."""
import re
import cv2
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from annotations import crop_frame, render_annotations

DEFAULT = {"ratio": None, "fit": "contain", "background": "#18212b", "foreground": "#ffffff", "align": "center"}

def validate_options(value):
    if value is None:
        return dict(DEFAULT)
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError("画面或文字样式格式无效。")
    result = {**DEFAULT, **value}
    ratio = result["ratio"]
    if ratio is not None:
        if not isinstance(ratio, (list, tuple)) or len(ratio) != 2 or any(type(x) is not int or not 1 <= x <= 100 for x in ratio) or not .1 <= ratio[0] / ratio[1] <= 10:
            raise ValueError("画面比例无效，请输入 1 至 100 的宽高，且比例不超过 10:1。")
        result["ratio"] = list(ratio)
    if result["fit"] not in ("contain", "cover") or result["align"] not in ("left", "center", "right"):
        raise ValueError("画面适配或文字对齐无效。")
    for key in ("background", "foreground"):
        if not isinstance(result[key], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", result[key]):
            raise ValueError("文字颜色格式无效。")
    return result

def frame_size(width, height, options):
    options = validate_options(options)
    if not options["ratio"]:
        return width - width % 2, height - height % 2
    ratio = options["ratio"][0] / options["ratio"][1]
    if options["fit"] == "contain":
        if width / height > ratio: out_w, out_h = width, round(width / ratio)
        else: out_w, out_h = round(height * ratio), height
    else:
        if width / height > ratio: out_w, out_h = round(height * ratio), height
        else: out_w, out_h = width, round(width / ratio)
    if max(out_w, out_h) > 8192:
        raise ValueError("目标画面过大，请选择更接近素材的比例。")
    return max(2, out_w - out_w % 2), max(2, out_h - out_h % 2)

def render_frame(frame, frame_index, annotations, crop, options):
    options = validate_options(options)
    legacy = [a for a in annotations if a["type"] != "stage"]
    image = crop_frame(render_annotations(frame, frame_index, legacy), crop)
    h, w = image.shape[:2]
    ow, oh = frame_size(w, h, options)
    if options["fit"] == "contain":
        canvas = np.zeros((oh, ow, 3), dtype=np.uint8)
        x, y = (ow - w) // 2, (oh - h) // 2
        if x < 0 or y < 0:
            image = cv2.resize(image, (min(w, ow), min(h, oh)))
            h, w = image.shape[:2]; x, y = (ow-w)//2, (oh-h)//2
        canvas[y:y+h, x:x+w] = image
    else:
        x, y = max(0, (w-ow)//2), max(0, (h-oh)//2)
        canvas = image[y:y+oh, x:x+ow].copy()
        if canvas.shape[:2] != (oh, ow): canvas = cv2.resize(canvas, (ow, oh))
    active = next((a for a in reversed(annotations) if a["type"] == "stage" and a["start"] <= frame_index < a["end"]), None)
    if not active or not active["text"]:
        return canvas
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    q = QImage(rgb.data, ow, oh, rgb.strides[0], QImage.Format.Format_RGB888).copy()
    painter = QPainter(q); painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    height = max(24, round(oh * .1)); background = QColor(options["background"]); background.setAlpha(230)
    painter.fillRect(QRectF(0, 0, ow, height), background)
    font = QFont("Microsoft YaHei"); font.setPixelSize(max(10, round(oh * .045))); font.setBold(True); painter.setFont(font)
    painter.setPen(QColor(options["foreground"]))
    align = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight}[options["align"]]
    painter.drawText(QRectF(ow*.04, 0, ow*.92, height), align | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, active["text"])
    painter.end(); pixels=np.frombuffer(q.bits(),np.uint8).reshape((oh,q.bytesPerLine()))[:,:ow*3].reshape(oh,ow,3).copy()
    return cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
