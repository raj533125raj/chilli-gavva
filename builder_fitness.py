"""builder_fitness.py — Workout & Fitness Planner printable pack.

FORMAT = 'fitness_planner'
KEYWORDS = ['workout planner', 'fitness planner', 'workout log',
            'fitness routine', 'gym planner', 'exercise tracker']

Builds a deterministic A4 + US Letter printable pack for fitness tracking:
goals page, 12-week program grid, weekly workout log (sets/reps/weight),
cardio log, body measurements, meal & water tracker, progress review.
Cover and three preview images are drawn with Pillow only.
No stock images, no currency amounts, no network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PILLOW = False

import pdf_design

_DEFAULT_COLOURS = {
    'ink': '#1f2933', 'accent': '#1f7a5a', 'muted': '#5f6b73',
    'line': '#c9d3d6', 'box': '#94b8aa', 'soft': '#edf7f2', 'warm': '#1f7a5a',
}
_COLOURS = dict(_DEFAULT_COLOURS)
_STYLE = {"name": "A"}


def _col(role: str) -> str:
    return _COLOURS[role]


def _use_theme(spec: Any) -> str:
    _COLOURS.clear()
    _COLOURS.update(_DEFAULT_COLOURS)
    _STYLE["name"] = "A"
    try:
        import pdf_themes
        pdf_themes.register_themes_in_pdf_design()
        theme = pdf_themes.get_theme_for_spec(dict(spec or {}))
    except Exception:
        return _STYLE["name"]
    tok = theme["tokens"]
    _COLOURS.update(
        ink=tok["ink"], accent=tok["accent"], muted=tok["muted"], line=tok["line"],
        box=tok.get("border", tok["line"]),
        soft=tok.get("weekend") or tok.get("surface_soft", "#f4f4f4"),
        warm=tok.get("accent_light", tok["accent"]),
    )
    _STYLE["name"] = theme["slug"]
    return _STYLE["name"]


FORMAT = "fitness_planner"
KEYWORDS = [
    "workout planner", "fitness planner", "workout log",
    "fitness routine", "gym planner", "exercise tracker",
]

DEFAULT_TITLE = "Workout & Fitness Planner"
DEFAULT_WEEKS = 12
DEFAULT_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
PAPER_SIZES = {"a4": "A4", "letter": "Letter"}
WORKOUT_LOG_PAGES = 12
COVER_SIZE = (1600, 1067)
PREVIEW_SIZE = (1600, 1067)


def _clean_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    return title or DEFAULT_TITLE


def normalize_spec(spec: Any) -> Dict[str, Any]:
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")
    title = _clean_title(spec.get("title", DEFAULT_TITLE))
    try:
        weeks = int(spec.get("weeks", DEFAULT_WEEKS))
    except (TypeError, ValueError):
        raise ValueError("weeks must be an integer")
    if not 4 <= weeks <= 52:
        raise ValueError("weeks must be between 4 and 52")
    name = " ".join(str(spec.get("name", "")).split())[:60]
    return {
        "title": title,
        "weeks": weeks,
        "name": name,
    }


def _font(size: int, bold: bool = False):
    try:
        import mockups
        face = mockups.font("bold" if bold else "regular", size)
        if face is not None:
            return face
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_mini(draw, x, y, w, h, title, accent, soft, line, ink):
    draw.rectangle([x, y, x + w, y + h], fill="#ffffff", outline=line, width=2)
    draw.rectangle([x, y, x + w, y + 24], fill=accent)
    draw.text((x + 8, y + 5), title, fill="#ffffff", font=_font(12, True))
    for i in range(6):
        ly = y + 40 + i * ((h - 48) / 6)
        draw.line([x + 10, ly, x + w - 10, ly], fill=line, width=1)
        if i == 0:
            draw.rectangle([x + 10, y + 28, x + w - 10, ly], fill=soft, outline=line)


def _cover_image(path: Path, norm: Dict[str, Any], preview: int = 0) -> None:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for fitness planner images")
    labels = ["Cover", "12-Week Program", "Workout Log", "Progress Review"]
    kind = preview % len(labels)
    w, h = COVER_SIZE if kind == 0 else PREVIEW_SIZE
    bg = "#f7fbf9"
    ink = _col("ink")
    accent = _col("accent")
    soft = _col("soft")
    line = _col("line")
    im = Image.new("RGB", (w, h), color=bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([20, 20, w - 20, h - 20], outline=accent, width=4)
    draw.rectangle([20, 20, w - 20, 84], fill=accent)
    draw.text((44, 36), "CHILLI GAVVA · FITNESS PLANNER", fill="#ffffff", font=_font(24, True))
    draw.text((48, 110), norm["title"][:48], fill=ink, font=_font(46, True))
    draw.text((48, 170), f"{norm['weeks']}-Week Program · 24+ pages · A4 + US Letter", fill=accent, font=_font(24, True))
    draw.text((48, 206), "For anyone who wants a simple printable fitness routine", fill=_col("muted"), font=_font(18))
    if kind == 0:
        draw.rounded_rectangle([48, 260, w - 48, h - 110], radius=18, fill="#ffffff", outline=line, width=2)
        draw.text((72, 280), "INSIDE THE PACK", fill=accent, font=_font(20, True))
        _draw_mini(draw, 72, 330, 430, 420, "12-Week Grid", accent, soft, line, ink)
        _draw_mini(draw, 540, 330, 430, 420, "Workout Log", accent, soft, line, ink)
        _draw_mini(draw, 1008, 330, 430, 420, "Progress Review", accent, soft, line, ink)
        draw.text((72, 780), "Goals · 12-week grid · 12 workout logs · Cardio · Measurements · Meal & water · Review", fill=ink, font=_font(18))
    elif kind == 1:
        draw.text((48, 250), "12-WEEK PROGRAM GRID", fill=accent, font=_font(22, True))
        for i in range(12):
            col = i % 4
            row = i // 4
            x = 48 + col * 370
            y = 300 + row * 210
            draw.rectangle([x, y, x + 340, y + 180], outline=line, fill="#ffffff")
            draw.rectangle([x, y, x + 340, y + 34], fill=soft)
            draw.text((x + 12, y + 8), f"Week {i + 1}", fill=accent, font=_font(14, True))
            for ln in range(4):
                draw.line([x + 12, y + 56 + ln * 28, x + 328, y + 56 + ln * 28], fill=line, width=1)
    elif kind == 2:
        draw.text((48, 250), "WEEKLY WORKOUT LOG", fill=accent, font=_font(22, True))
        headers = ["Exercise", "Sets", "Reps", "Weight", "Notes"]
        widths = [500, 140, 140, 180, 540]
        x0, y0 = 48, 296
        for c, label in enumerate(headers):
            x = x0 + sum(widths[:c])
            draw.rectangle([x, y0, x + widths[c], y0 + 44], outline=line, fill=soft)
            draw.text((x + 10, y0 + 10), label, fill=accent, font=_font(16, True))
        for r in range(10):
            y = y0 + 44 + r * 58
            for c in range(len(headers)):
                x = x0 + sum(widths[:c])
                draw.rectangle([x, y, x + widths[c], y + 58], outline=line, fill="#ffffff")
    else:
        draw.text((48, 250), "PROGRESS REVIEW", fill=accent, font=_font(22, True))
        draw.rounded_rectangle([48, 296, 980, h - 110], radius=16, fill="#ffffff", outline=line, width=2)
        for i, label in enumerate(["What improved?", "What felt hard?", "Measurements / energy", "Next 4-week focus"]):
            y = 336 + i * 132
            draw.text((76, y), label, fill=accent, font=_font(18, True))
            for ln in range(2):
                draw.line([76, y + 42 + ln * 34, 940, y + 42 + ln * 34], fill=line, width=1)
        draw.rounded_rectangle([1020, 296, w - 48, h - 110], radius=16, fill=soft, outline=line, width=2)
        draw.text((1048, 326), "MEAL & WATER", fill=accent, font=_font(18, True))
        for i, item in enumerate(["Breakfast plan", "Lunch plan", "Dinner plan", "Snack / water", "Sleep / recovery"]):
            y = 386 + i * 86
            draw.text((1048, y), item, fill=ink, font=_font(16))
            draw.line([1048, y + 34, w - 90, y + 34], fill=line, width=1)
    draw.text((48, h - 72), "Goals · 12-week grid · 12 workout logs · Exercise library · Cardio · Measurements · Review", fill=_col("muted"), font=_font(16))
    draw.text((w - 260, h - 72), labels[kind], fill=accent, font=_font(18, True))
    im.save(str(path), format="PNG", optimize=True)


def _heading(pdf: Any, text: str, subtitle: str = "") -> None:
    pdf.text(pdf_design.MARGIN, pdf.h - 62, text, 20, bold=True, color=_col("ink"))
    if subtitle:
        pdf.text(pdf_design.MARGIN, pdf.h - 84, subtitle, 10, color=_col("muted"))
    pdf.line(pdf_design.MARGIN, pdf.h - 96, pdf.w - pdf_design.MARGIN, pdf.h - 96, color=_col("line"))


def _title_page(pdf: Any, norm: Dict[str, Any]) -> None:
    lines, size = pdf_design.fit_text(norm["title"], 30, pdf.w - 2 * pdf_design.MARGIN, bold=True, max_lines=3, min_size=16)
    y = pdf.h * 0.66
    for line in lines:
        pdf.text_center(pdf.w / 2, y, line, size, bold=True, color=_col("ink"))
        y -= size * 1.25
    pdf.text_center(pdf.w / 2, y - 18, f"{norm['weeks']}-Week Fitness Program", 18, color=_col("accent"))
    pdf.text_center(pdf.w / 2, y - 48, "Goals · 12-week grid · Workout log · Cardio · Measurements · Meal & water · Review", 10, color=_col("muted"))
    pdf.line(pdf.w / 2 - 90, y - 76, pdf.w / 2 + 90, y - 76, color=_col("accent"), width=1.2)
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = y - 110
    bottom = 86
    pdf.rect(left, bottom, right - left, top - bottom, color=_col("line"), width=0.8)
    pdf.text(left + 14, top - 22, "Start here", 12, bold=True, color=_col("accent"))
    fields = ["Name", "Start date", "Main goal", "Training days per week"]
    fy = top - 50
    gap = (top - bottom - 90) / len(fields)
    for label in fields:
        pdf.text(left + 14, fy, label, 9, color=_col("ink"))
        pdf.line(left + 150, fy - 2, right - 14, fy - 2, color=_col("line"), width=0.6)
        fy -= max(22.0, gap)
    pdf.text(left + 14, bottom + 20, "Print at 100% scale (Actual Size). Reprint workout log pages as needed.", 8.5, color=_col("muted"))


def _goals_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Fitness Goals", "Set clear goals and track why they matter.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    y = pdf.h - 130
    blocks = [
        ("My main goal", 2), ("Why this goal matters", 3),
        ("Strength goals", 3), ("Cardio goals", 3),
        ("Healthy habits to build", 4), ("Reward when I complete this plan", 2),
    ]
    for label, lines in blocks:
        pdf.text(left, y, label, 9, bold=True, color=_col("accent"))
        y -= 14
        for _ in range(lines):
            pdf.line(left, y, right, y, color=_col("line"), width=0.6)
            y -= 20
        y -= 6
        if y < 90:
            break


def _program_grid_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, f"{norm['weeks']}-Week Program Grid", "Plan the focus for each week.")
    left = pdf_design.MARGIN
    top = pdf.h - 128
    gap = 10
    cols = 4
    rows = 3
    col_w = (pdf.w - 2 * pdf_design.MARGIN - (cols - 1) * gap) / cols
    row_h = (top - 90 - (rows - 1) * gap) / rows
    for i in range(norm["weeks"]):
        c = i % cols
        r = i // cols
        x = left + c * (col_w + gap)
        y = top - (r + 1) * row_h - r * gap
        pdf.rect(x, y, col_w, row_h, color=_col("line"), width=0.7)
        pdf.fill(x, y + row_h - 18, col_w, 18, _col("soft"))
        pdf.text(x + 6, y + row_h - 13, f"Week {i + 1}", 9, bold=True, color=_col("accent"))
        for ln in range(4):
            ly = y + row_h - 34 - ln * 20
            if ly > y + 8:
                pdf.line(x + 6, ly, x + col_w - 6, ly, color=_col("line"), width=0.5)


def _workout_log_page(pdf: Any, norm: Dict[str, Any], page_no: int) -> None:
    _heading(pdf, f"Weekly Workout Log — Week {page_no}", "Record exercises, sets, reps, and weight. One page per week.")
    pdf.text(pdf_design.MARGIN, pdf.h - 114, f"Week: {page_no}    Day / date: __________________  Focus: ________________________", 9, color=_col("muted"))
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 136
    bottom = 90
    headers = ["Exercise", "Sets", "Reps", "Weight", "Notes"]
    widths = [180, 55, 55, 70, right - left - 360]
    row_h = (top - bottom - 22) / 14
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(14):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w
    pdf.text(left, bottom - 10, "Warm-up / cool-down / notes:", 8, bold=True, color=_col("accent"))
    for i in range(2):
        ly = bottom - 24 - i * 14
        if ly > 58:
            pdf.line(left + 140, ly, right, ly, color=_col("line"), width=0.5)


def _cardio_log_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Cardio Log", "Track cardio sessions and how they felt.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Date", "Activity", "Minutes", "Distance / Steps", "Intensity", "Notes"]
    widths = [60, 100, 60, 90, 70, right - left - 380]
    row_h = (top - bottom - 22) / 16
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(16):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w


def _measurements_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Body Measurements", "Record measurements over time. Use any unit you prefer.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Date", "Chest", "Waist", "Hips", "Arm", "Thigh", "Weight", "Notes"]
    widths = [60, 55, 55, 55, 55, 55, 55, right - left - 390]
    row_h = (top - bottom - 22) / 16
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(16):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w


def _meal_water_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Meal & Water Tracker", "Plan simple meals and track daily water.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    mid = left + (right - left) * 0.62
    top = pdf.h - 128
    bottom = 80
    # meal table
    pdf.text(left, top + 2, "Meal plan", 9, bold=True, color=_col("accent"))
    headers = ["Day", "Breakfast", "Lunch", "Dinner", "Snack"]
    widths = [50, 80, 80, 80, mid - left - 290]
    row_h = (top - bottom - 20) / 7
    pdf.fill(left, top - 20, mid - left - 8, 20, _col("soft"))
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 14, htxt, 7, bold=True, color=_col("accent"))
        x += w
    for r, day in enumerate(DEFAULT_DAYS):
        y = top - 20 - (r + 1) * row_h
        pdf.rect(left, y, mid - left - 8, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w
        pdf.text(left + 3, y + row_h / 2 - 3, day[:3], 7, color=_col("ink"))
    # water tracker
    wx = mid + 8
    pdf.text(wx, top + 2, "Water tracker", 9, bold=True, color=_col("accent"))
    pdf.text(wx, top - 16, "Shade one box per glass.", 7.5, color=_col("muted"))
    for r, day in enumerate(DEFAULT_DAYS):
        y = top - 40 - r * 46
        pdf.text(wx, y, day[:3], 7.5, bold=True, color=_col("ink"))
        for g in range(8):
            bx = wx + 30 + g * 20
            pdf.rect(bx, y - 4, 14, 14, color=_col("line"), width=0.6)


def _progress_review_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Progress Review", "Look back, learn, and plan the next step.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    y = pdf.h - 130
    blocks = [
        ("Wins this period", 3), ("What was challenging", 3),
        ("Habits that helped most", 3), ("Changes for next period", 3),
        ("Next 4-week focus", 2),
    ]
    for label, lines in blocks:
        pdf.text(left, y, label, 9, bold=True, color=_col("accent"))
        y -= 14
        for _ in range(lines):
            pdf.line(left, y, right, y, color=_col("line"), width=0.6)
            y -= 20
        y -= 6
        if y < 90:
            break


def _weekly_checkin_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Weekly Check-In", "A quick weekly review to stay on track.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Week", "Workouts done", "Best session", "Energy (1-5)", "Next week focus"]
    widths = [50, 80, right - left - 50 - 80 - 70 - 140, 70, 140]
    row_h = (top - bottom - 22) / 12
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for htxt, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, htxt, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(12):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w
        pdf.text(left + 8, y + row_h / 2 - 3, str(r + 1), 8, color=_col("ink"))


def _exercise_library_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Exercise Library", "Simple exercise ideas to mix into your weekly plan.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 76
    groups = [
        ("Upper body", ["Push-ups / wall push-ups", "Dumbbell row", "Shoulder press", "Band pull-apart", "Triceps dips"]),
        ("Lower body", ["Squats / chair squats", "Lunges", "Glute bridge", "Calf raises", "Step-ups"]),
        ("Core", ["Plank", "Dead bug", "Bird dog", "Side plank", "Glute bridge hold"]),
        ("Cardio / mobility", ["Brisk walk", "Cycling / jump rope", "Light stretch", "Hip mobility", "Easy cooldown"]),
    ]
    col_w = (right - left - 12) / 2
    row_h = (top - bottom - 12) / 2
    for idx, (name, items) in enumerate(groups):
        c = idx % 2
        r = idx // 2
        x = left + c * (col_w + 12)
        y = top - (r + 1) * row_h - r * 12
        pdf.rect(x, y, col_w, row_h, color=_col("line"), width=0.8)
        pdf.fill(x, y + row_h - 22, col_w, 22, _col("soft"))
        pdf.text(x + 8, y + row_h - 15, name, 10, bold=True, color=_col("accent"))
        for i, item in enumerate(items):
            iy = y + row_h - 40 - i * 22
            pdf.rect(x + 10, iy - 2, 10, 10, color=_col("line"), width=0.6)
            pdf.text(x + 26, iy, item, 8.5, color=_col("ink"))
            pdf.line(x + 26, iy - 6, x + col_w - 10, iy - 6, color=_col("line"), width=0.4)


def _progress_chart_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Progress Charts", "Track simple trends over time. Use any unit you prefer.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    gap = 18
    chart_h = (top - 84 - 2 * gap) / 3
    charts = ["Weight / measurement trend", "Workout sessions completed per week", "Energy or mood trend (1-5)"]
    for i, label in enumerate(charts):
        y = top - (i + 1) * chart_h - i * gap
        pdf.text(left, y + chart_h - 12, label, 9, bold=True, color=_col("accent"))
        box_y = y
        box_h = chart_h - 20
        pdf.rect(left, box_y, right - left, box_h, color=_col("line"), width=0.8)
        # grid
        for gx in range(13):
            x = left + gx * (right - left) / 12
            pdf.line(x, box_y, x, box_y + box_h, color=_col("line"), width=0.3)
            if gx > 0:
                pdf.text(x - 3, box_y - 10, str(gx), 6, color=_col("muted"))
        for gy in range(6):
            ly = box_y + gy * box_h / 5
            pdf.line(left, ly, right, ly, color=_col("line"), width=0.3)
        pdf.text(left, box_y - 10, "Week:", 6, color=_col("muted"))


def _monthly_summary_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Monthly Summary", "Review your month at a glance.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    y = pdf.h - 128
    fields = [("Month / dates", 1), ("Top wins", 3), ("Challenges", 3), ("Habits to keep", 3), ("Focus for next month", 3)]
    for label, lines in fields:
        pdf.text(left, y, label, 9, bold=True, color=_col("accent"))
        y -= 14
        for _ in range(lines):
            pdf.line(left, y, right, y, color=_col("line"), width=0.6)
            y -= 20
        y -= 6
        if y < 90:
            break


def _stretch_mobility_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Stretch & Mobility Log", "Track warm-up, stretching, and recovery sessions.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 80
    headers = ["Date", "Warm-up", "Stretch / mobility", "Duration", "How it felt", "Notes"]
    widths = [60, 110, 130, 60, 80, right - left - 440]
    row_h = (top - bottom - 22) / 16
    pdf.fill(left, top - 22, right - left, 22, _col("soft"))
    pdf.rect(left, top - 22, right - left, 22, color=_col("line"), width=0.7)
    x = left
    for h, w in zip(headers, widths):
        pdf.text(x + 3, top - 15, h, 7.5, bold=True, color=_col("accent"))
        x += w
    for r in range(16):
        y = top - 22 - (r + 1) * row_h
        pdf.rect(left, y, right - left, row_h, color=_col("line"), width=0.5)
        x = left
        for w in widths:
            pdf.line(x, y, x, y + row_h, color=_col("line"), width=0.4)
            x += w


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    size = PAPER_SIZES[paper]
    pdf = pdf_design.Pdf(size, footer=f"{norm['title']} · {norm['weeks']}-Week Plan", style=_STYLE["name"])
    pdf.new_page(); _title_page(pdf, norm)
    pdf.new_page(); _goals_page(pdf, norm)
    pdf.new_page(); _program_grid_page(pdf, norm)
    for i in range(1, WORKOUT_LOG_PAGES + 1):
        pdf.new_page(); _workout_log_page(pdf, norm, i)
    pdf.new_page(); _exercise_library_page(pdf, norm)
    pdf.new_page(); _cardio_log_page(pdf, norm)
    pdf.new_page(); _measurements_page(pdf, norm)
    pdf.new_page(); _meal_water_page(pdf, norm)
    pdf.new_page(); _progress_chart_page(pdf, norm)
    pdf.new_page(); _progress_review_page(pdf, norm)
    pdf.new_page(); _weekly_checkin_page(pdf, norm)
    pdf.new_page(); _stretch_mobility_page(pdf, norm)
    pdf.new_page(); _monthly_summary_page(pdf, norm)
    pdf.save(str(path), title=norm["title"])


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"{norm['title']} — {norm['weeks']}-Week Plan",
        "=" * 52,
        "",
        "WHAT IS INSIDE",
        "- A4 PDF and US Letter PDF",
        "- Goals page",
        "- 12-week program grid",
        f"- {WORKOUT_LOG_PAGES} weekly workout log pages (sets, reps, weight)",
        "- Exercise library page",
        "- Cardio log page",
        "- Body measurements page",
        "- Meal and water tracker page",
        "- Progress charts page",
        "- Progress review page",
        "- Weekly check-in page",
        "- cover.png and three preview images",
        "",
        "HOW TO PRINT",
        "1. Open the PDF for your paper size.",
        "2. Print at 100% scale (Actual Size).",
        "3. Print extra workout log pages as needed.",
        "4. Use the progress review page every few weeks.",
        "",
        "PAGE COUNT",
        f"- {3 + WORKOUT_LOG_PAGES + 9} pages per PDF (A4 and US Letter included)",
        "",
        "LICENSE",
        "Personal use only. Do not resell or redistribute the files.",
    ]) + "\n"


def _license(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"LICENSE — {norm['title']}",
        "",
        "This printable pack is for personal use only.",
        "You may print copies for yourself and your household.",
        "You may not resell, redistribute, share, or claim the files as your own.",
        "All layouts and text are generated for the Chilli Gavva product library.",
        "",
    ])


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for the fitness planner builder")
    norm = normalize_spec(spec)
    _use_theme(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="fitness_build_"))
    names: List[str] = []
    try:
        for paper in PAPER_SIZES:
            name = f"fitness_planner_{paper}.pdf"
            _build_pdf(temp_dir / name, paper, norm)
            names.append(name)
        cover_name = "cover.png"
        _cover_image(temp_dir / cover_name, norm, preview=0)
        names.append(cover_name)
        for index in range(1, 4):
            name = f"preview_{index}.png"
            _cover_image(temp_dir / name, norm, preview=index)
            names.append(name)
        names.extend(["README.txt", "LICENSE.txt"])
        _write_text(temp_dir / "README.txt", _readme(norm))
        _write_text(temp_dir / "LICENSE.txt", _license(norm))
        page_count = 3 + WORKOUT_LOG_PAGES + 9
        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "weeks": norm["weeks"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": page_count,
            "workout_log_pages": WORKOUT_LOG_PAGES,
            "files": sorted(names + ["manifest.json"]),
        }
        _write_text(temp_dir / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        names.append("manifest.json")
        out_path.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(temp_dir / name, out_path / name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return sorted(names)


def _content_digest(norm: Dict[str, Any]) -> str:
    payload = json.dumps(norm, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
