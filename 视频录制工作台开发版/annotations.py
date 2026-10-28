"""Source-time annotations and a shared Qt renderer for preview and export."""
import math
import re
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QPolygonF
import cv2
import numpy as np

KINDS = {'text', 'arrow', 'box', 'sticker', 'stage'}
FIELDS = {'id', 'type', 'text', 'start', 'end', 'x', 'y', 'size', 'color'}


def validate_annotations(items, frame_count):
    if not isinstance(items, list):
        raise ValueError('标注数据无效。')
    seen = set()
    for a in items:
        if not isinstance(a, dict) or set(a) != FIELDS:
            raise ValueError('标注格式不受支持，已阻止打开以免丢失数据。')
        if not isinstance(a['id'], str) or not a['id'] or a['id'] in seen or a['type'] not in KINDS:
            raise ValueError('标注类型或编号无效。')
        seen.add(a['id'])
        if not isinstance(a['text'], str) or len(a['text']) > 500:
            raise ValueError('标注文字无效。')
        if type(a['start']) is not int or type(a['end']) is not int or not 0 <= a['start'] < a['end'] <= frame_count:
            raise ValueError('标注时间无效。')
        if any(not isinstance(a[k], (int, float)) or isinstance(a[k], bool) or not math.isfinite(a[k]) for k in ('x', 'y', 'size')):
            raise ValueError('标注位置或字号无效。')
        if not (0 <= a['x'] <= 1 and 0 <= a['y'] <= 1 and 0.01 <= a['size'] <= 0.3):
            raise ValueError('标注位置或字号超出范围。')
        if not isinstance(a['color'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', a['color']):
            raise ValueError('标注颜色无效。')
    return items


def render_annotations(frame, frame_index, annotations):
    active = [a for a in annotations if a['start'] <= frame_index < a['end']]
    if not active:
        return frame
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    image = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    for a in active:
        x, y = a['x'] * w, a['y'] * h
        unit = max(8, a['size'] * h)
        color = QColor(a['color'])
        painter.setPen(QPen(color, max(2, unit * .12)))
        if a['type'] == 'box':
            painter.drawRect(QRectF(x, y, unit * 4, unit * 2))
        elif a['type'] == 'arrow':
            painter.drawLine(QPointF(x, y), QPointF(x + unit * 2, y))
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([QPointF(x + unit * 2.5, y), QPointF(x + unit * 1.8, y - unit * .45), QPointF(x + unit * 1.8, y + unit * .45)]))
        else:
            font = QFont('Microsoft YaHei'); font.setPixelSize(max(8, int(unit))); font.setBold(True)
            painter.setFont(font)
            if a['type'] == 'sticker':
                bounds = painter.fontMetrics().boundingRect(a['text'])
                rect = QRectF(x, y, max(unit * 2, bounds.width() + unit), unit * 1.6)
                painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(color); painter.drawRoundedRect(rect, unit * .25, unit * .25)
                painter.setPen(QColor('white')); painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, a['text'])
            else:
                painter.drawText(QPointF(x, y + unit), a['text'])
    painter.end()
    ptr = image.bits()
    result = np.frombuffer(ptr, np.uint8).reshape((h, image.bytesPerLine()))[:, :w * 3].reshape(h, w, 3).copy()
    return cv2.cvtColor(result, cv2.COLOR_RGB2BGR)


def annotation_bounds(a, width, height):
    x, y = a['x'] * width, a['y'] * height
    unit = max(8, a['size'] * height)
    if a['type'] == 'box':
        return QRectF(x, y, unit * 4, unit * 2)
    if a['type'] == 'arrow':
        return QRectF(x, y - unit * .45, unit * 2.5, unit * .9)
    font = QFont('Microsoft YaHei'); font.setPixelSize(max(8, int(unit))); font.setBold(True)
    metrics = QFontMetricsF(font)
    if a['type'] == 'sticker':
        return QRectF(x, y, max(unit * 2, metrics.boundingRect(a['text']).width() + unit), unit * 1.6)
    return QRectF(x, y, max(unit, metrics.horizontalAdvance(a['text'])), max(unit * 1.3, metrics.height()))


def validate_crop(crop):
    if crop is None: return (0.0, 0.0, 1.0, 1.0)
    if not isinstance(crop, (tuple, list)) or len(crop) != 4 or any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) for v in crop):
        raise ValueError('画面裁剪数据无效。')
    x, y, w, h = crop
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1.000001 or y+h > 1.000001:
        raise ValueError('画面裁剪范围无效。')
    return tuple(crop)


def crop_frame(frame, crop):
    x, y, w, h = validate_crop(crop)
    height, width = frame.shape[:2]
    left = min(width-2, max(0, round(x*width)))
    top = min(height-2, max(0, round(y*height)))
    right = min(width, max(left+2, round((x+w)*width)))
    bottom = min(height, max(top+2, round((y+h)*height)))
    # MP4 encoders require even output dimensions.
    right -= (right-left) % 2
    bottom -= (bottom-top) % 2
    return frame[top:bottom, left:right].copy()
