"""report_charts.py — deterministic chart maths and Pillow chart images.

The weekly PDF report embeds these PNG-quality raster charts as PDF images.
Every function is pure or deterministic for the same input: fixed palette,
fixed margins, no clock, no random numbers and no image metadata.

Pillow is the only third-party dependency. It is imported defensively so the
rest of the robot (and the test suite on a machine without Pillow) still works;
chart functions raise ``ChartUnavailable`` when Pillow is missing.
"""

from __future__ import annotations

import io
import math
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
    PIL_ERROR = None
except Exception as exc:  # pragma: no cover - exercised only without Pillow
    Image = ImageDraw = ImageFont = None
    PIL_AVAILABLE = False
    PIL_ERROR = exc

BASE_DIR = Path(__file__).resolve().parent
FONT_REGULAR = BASE_DIR / "fonts" / "Inter-Regular.ttf"
FONT_SEMIBOLD = BASE_DIR / "fonts" / "Inter-SemiBold.ttf"
FONT_BOLD = BASE_DIR / "fonts" / "Inter-Bold.ttf"

PALETTE = (
    "#2f7d78", "#c4657a", "#d99b4e", "#5b7db1",
    "#7a9e7e", "#8d6a9f", "#b5838d", "#4d908e",
)
INK = "#1f2933"
MUTED = "#5f6b73"
GRID = "#d7dee2"
BACKGROUND = "#ffffff"
NO_DATA = "No data yet"

_FONT_CACHE = {}


class ChartUnavailable(RuntimeError):
    """Raised when Pillow is not installed."""


def _require_pillow():
    if not PIL_AVAILABLE:
        raise ChartUnavailable("Pillow is required for charts") from PIL_ERROR


def _font(size=14, bold=False):
    _require_pillow()
    key = (int(size), bool(bold))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    path = FONT_BOLD if bold else FONT_REGULAR
    try:
        font = ImageFont.truetype(str(path), int(size))
    except Exception:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _short(text, limit=12):
    text = str(text).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _safe_values(values):
    out = []
    for value in values or []:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        if math.isnan(number) or math.isinf(number) or number < 0:
            number = 0.0
        out.append(number)
    return out


def nice_max(value, minimum=1):
    """A rounded axis maximum at or above value (1, 2, 2.5, 5 or 10 times a power of ten)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if math.isnan(value) or math.isinf(value) or value < 0:
        value = 0.0
    value = max(value, float(minimum))
    if value <= 10:
        return int(math.ceil(value))
    magnitude = 10 ** int(math.floor(math.log10(value)))
    for step in (1, 2, 2.5, 5, 10):
        candidate = step * magnitude
        if value <= candidate:
            return int(candidate) if float(candidate).is_integer() else round(candidate, 2)
    return int(math.ceil(value))


def bar_heights(values, height, top=None):
    """Pixel heights for bars, bounded by height. All-zero input gives all-zero heights."""
    values = _safe_values(values)
    if height <= 0:
        return [0 for _ in values]
    axis = top if top is not None else nice_max(max(values) if values else 0)
    axis = max(float(axis), 1.0)
    return [int(round(min(max(v, 0.0), axis) / axis * height)) for v in values]


def line_points(values, width, height, pad_x=0, pad_y=0, top=None):
    """(x, y) points in image coordinates for a line chart, y=0 at the top."""
    values = _safe_values(values)
    if not values:
        return []
    width = max(float(width), 1.0)
    height = max(float(height), 1.0)
    pad_x = max(float(pad_x), 0.0)
    pad_y = max(float(pad_y), 0.0)
    plot_w = max(width - 2 * pad_x, 1.0)
    plot_h = max(height - 2 * pad_y, 1.0)
    axis = float(top if top is not None else nice_max(max(values)))
    axis = max(axis, 1.0)
    count = len(values)
    if count == 1:
        xs = [pad_x + plot_w / 2]
    else:
        xs = [pad_x + plot_w * i / (count - 1) for i in range(count)]
    ys = [pad_y + plot_h - min(max(v, 0.0), axis) / axis * plot_h for v in values]
    return [(round(x, 3), round(y, 3)) for x, y in zip(xs, ys)]


def donut_segments(values):
    """[(start_angle, end_angle, value, colour_index)] clockwise from twelve o'clock."""
    values = _safe_values(values)
    total = sum(values)
    if total <= 0:
        return []
    segments = []
    cursor = -90.0
    for index, value in enumerate(values):
        if value <= 0:
            continue
        sweep = 360.0 * value / total
        segments.append((round(cursor, 4), round(cursor + sweep, 4), value, index))
        cursor += sweep
    return segments


def chart_bytes(image):
    """PNG bytes for a Pillow image, without volatile metadata."""
    _require_pillow()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def save_png(image, path):
    """Write a chart PNG and return its path."""
    _require_pillow()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(chart_bytes(image))
    return path


def _canvas(width, height, title):
    _require_pillow()
    image = Image.new("RGB", (int(width), int(height)), BACKGROUND)
    draw = ImageDraw.Draw(image)
    if title:
        draw.text((24, 18), str(title), fill=INK, font=_font(20, True))
    return image, draw


def _grid(draw, left, top, right, bottom, axis):
    steps = 4
    for index in range(steps + 1):
        value = axis * index / steps
        y = bottom - (bottom - top) * index / steps
        draw.line((left, y, right, y), fill=GRID, width=1)
        draw.text((left - 10, y - 7), f"{value:g}", fill=MUTED, font=_font(11), anchor="ra")


def bar_chart(labels, values, title="", width=1000, height=420):
    """A vertical bar chart image (bars, value labels and x labels)."""
    _require_pillow()
    labels = [str(label) for label in (labels or [])]
    values = _safe_values(values)
    if not labels:
        labels, values = [NO_DATA], [0.0]
    if len(values) < len(labels):
        values = values + [0.0] * (len(labels) - len(values))
    labels, values = labels[:len(values)], values[:len(labels)]

    image, draw = _canvas(width, height, title)
    left, right, top, bottom = 82, width - 28, 78, height - 72
    axis = nice_max(max(values) if values else 0)
    _grid(draw, left, top, right, bottom, axis)

    count = len(values)
    slot = (right - left) / max(count, 1)
    bar_w = max(8, min(58, int(slot * 0.58)))
    if not any(values):
        draw.text(((left + right) / 2, (top + bottom) / 2), NO_DATA,
                  fill=MUTED, font=_font(18), anchor="mm")
        return image

    for index, (label, value) in enumerate(zip(labels, values)):
        height_px = int(round(min(value, axis) / axis * (bottom - top)))
        x_mid = left + slot * (index + 0.5)
        x0, x1 = x_mid - bar_w / 2, x_mid + bar_w / 2
        y0 = bottom - height_px
        colour = PALETTE[index % len(PALETTE)]
        draw.rectangle((x0, y0, x1, bottom), fill=colour)
        draw.text((x_mid, y0 - 8), f"{value:g}", fill=INK, font=_font(12, True), anchor="ms")
        draw.text((x_mid, bottom + 8), _short(label, 16), fill=MUTED, font=_font(11), anchor="mt")
    return image


def line_chart(labels, values, title="", width=1000, height=400):
    """A line chart with dots, a light grid and x labels."""
    _require_pillow()
    labels = [str(label) for label in (labels or [])]
    values = _safe_values(values)
    if not values:
        labels, values = [NO_DATA], [0.0]
    labels = labels[:len(values)]

    image, draw = _canvas(width, height, title)
    left, right, top, bottom = 82, width - 28, 78, height - 72
    axis = nice_max(max(values) if values else 0)
    _grid(draw, left, top, right, bottom, axis)
    points = line_points(values, right - left, bottom - top, top=axis)
    points = [(left + x, top + y) for x, y in points]

    if not any(values):
        draw.text(((left + right) / 2, (top + bottom) / 2), NO_DATA,
                  fill=MUTED, font=_font(18), anchor="mm")
        return image

    if len(points) > 1:
        draw.line(points, fill=PALETTE[0], width=4, joint="curve")
    for index, (x, y) in enumerate(points):
        radius = 5
        draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                     fill=PALETTE[index % len(PALETTE)], outline=INK)
    for index, (x, _y) in enumerate(points):
        if index < len(labels):
            draw.text((x, bottom + 8), _short(labels[index], 10), fill=MUTED,
                      font=_font(11), anchor="mt")
    return image


def donut_chart(labels, values, title="", width=860, height=400):
    """A donut chart with a centre total and a colour legend."""
    _require_pillow()
    labels = [str(label) for label in (labels or [])]
    values = _safe_values(values)
    if len(values) < len(labels):
        values = values + [0.0] * (len(labels) - len(values))
    labels, values = labels[:len(values)], values[:len(labels)]

    image, draw = _canvas(width, height, title)
    cx, cy, outer = 230, 225, 130
    inner = 70
    total = sum(values)
    if total <= 0:
        draw.ellipse((cx - outer, cy - outer, cx + outer, cy + outer), outline=GRID, width=36)
        draw.text((cx, cy), NO_DATA, fill=MUTED, font=_font(16), anchor="mm")
    else:
        for start, end, value, index in donut_segments(values):
            draw.arc((cx - outer, cy - outer, cx + outer, cy + outer),
                     start=start, end=end, fill=PALETTE[index % len(PALETTE)], width=36)
        draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill=BACKGROUND)
        draw.text((cx, cy - 8), f"{total:g}", fill=INK, font=_font(28, True), anchor="mm")
        draw.text((cx, cy + 24), "total", fill=MUTED, font=_font(12), anchor="mm")

    legend_x = 430
    legend_y = 92
    for index, (label, value) in enumerate(zip(labels, values)):
        y = legend_y + index * 34
        draw.rectangle((legend_x, y, legend_x + 16, y + 16),
                       fill=PALETTE[index % len(PALETTE)])
        draw.text((legend_x + 26, y + 8), f"{_short(label, 22)}  {value:g}",
                  fill=INK, font=_font(13), anchor="lm")
    return image
