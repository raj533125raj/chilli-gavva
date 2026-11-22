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


def _cover_image(path: Path, norm: Dict[str, Any], preview: int = 0) -> None:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for fitness planner images")
    w, h = 1200, 800
    bg = "#f7fbf9"
    ink = _col("ink")
    accent = _col("accent")
    soft = _col("soft")
    line = _col("line")
    im = Image.new("RGB", (w, h), color=bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([24, 24, w - 24, h - 24], outline=accent, width=3)
    draw.rectangle([24, 24, w - 24, 72], fill=accent)
    draw.text((48, 38), "CHILLI GAVVA · FITNESS PLANNER", fill="#ffffff", font=_font(20, True))
    draw.text((60, 120), norm["title"][:42], fill=ink, font=_font(42, True))
    draw.text((60, 178), f"{norm['weeks']}-Week Program · A4 + US Letter", fill=accent, font=_font(22))
    labels = ["Cover", "12-Week Program", "Workout Log", "Progress Review"]
    kind = preview % len(labels)

    if kind == 0:
        draw.text((60, 240), "GOALS & PROGRAM GRID", fill=accent, font=_font(16, True))
        for i in range(12):
            col = i % 6
            row = i // 6
            x = 60 + col * 175
            y = 280 + row * 140
            draw.rectangle([x, y, x + 155, y + 110], outline=line, fill="#ffffff")
            draw.rectangle([x, y, x + 155, y + 28], fill=soft)
            draw.text((x + 10, y + 6), f"Week {i + 1}", fill=accent, font=_font(12, True))
            for ln in range(3):
                draw.line([x + 10, y + 44 + ln * 18, x + 145, y + 44 + ln * 18], fill=line, width=1)
    elif kind == 1:
        draw.text((60, 240), "WEEKLY WORKOUT LOG", fill=accent, font=_font(16, True))
        headers = ["Exercise", "Sets", "Reps", "Weight", "Notes"]
        widths = [320, 90, 90, 110, 270]
        x0, y0 = 60, 280
        for c, label in enumerate(headers):
            x = x0 + sum(widths[:c])
            draw.rectangle([x, y0, x + widths[c], y0 + 34], outline=line, fill=soft)
            draw.text((x + 8, y0 + 8), label, fill=accent, font=_font(12, True))
        for r in range(7):
            y = y0 + 34 + r * 48
            for c in range(len(headers)):
                x = x0 + sum(widths[:c])
                draw.rectangle([x, y, x + widths[c], y + 48], outline=line, fill="#ffffff")
    elif kind == 2:
        draw.text((60, 240), "CARDIO & MEASUREMENTS", fill=accent, font=_font(16, True))
        draw.rounded_rectangle([60, 280, 560, 620], radius=12, fill="#ffffff", outline=line, width=2)
        draw.text((84, 300), "Cardio Log", fill=accent, font=_font(15, True))
        for i, h in enumerate(["Date", "Activity", "Minutes", "Distance"]):
            draw.text((84 + i * 115, 336), h, fill=ink, font=_font(11, True))
        for r in range(6):
            y = 360 + r * 36
            draw.line([84, y, 530, y], fill=line, width=1)
        draw.rounded_rectangle([600, 280, 1130, 620], radius=12, fill="#ffffff", outline=line, width=2)
        draw.text((624, 300), "Body Measurements", fill=accent, font=_font(15, True))
        for i, h in enumerate(["Date", "Chest", "Waist", "Hips", "Weight"]):
            draw.text((624 + i * 98, 336), h, fill=ink, font=_font(11, True))
        for r in range(6):
            y = 360 + r * 36
            draw.line([624, y, 1100, y], fill=line, width=1)
    else:
        draw.text((60, 240), "PROGRESS REVIEW", fill=accent, font=_font(16, True))
        draw.rounded_rectangle([60, 280, 760, 620], radius=12, fill="#ffffff", outline=line, width=2)
        for i, label in enumerate(["What improved this period?", "What felt hard?", "Next focus"]):
            y = 310 + i * 90
            draw.text((84, y), label, fill=accent, font=_font(14, True))
            draw.line([84, y + 36, 720, y + 36], fill=line, width=1)
            draw.line([84, y + 62, 720, y + 62], fill=line, width=1)
        draw.rounded_rectangle([800, 280, 1130, 620], radius=12, fill=soft, outline=line, width=2)
        draw.text((824, 304), "MEAL & WATER", fill=accent, font=_font(14, True))
        for i, item in enumerate(["Breakfast plan", "Lunch plan", "Dinner plan", "Water glasses"]):
            y = 348 + i * 56
            draw.text((824, y), item, fill=ink, font=_font(12))
            draw.line([824, y + 28, 1100, y + 28], fill=line, width=1)

    draw.text((60, 700), "Goals · 12-week grid · Workout log · Cardio · Measurements · Meal & water · Review", fill=_col("muted"), font=_font(15))
    draw.text((880, 720), labels[kind], fill=accent, font=_font(14, True))
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
    _heading(pdf, f"Weekly Workout Log — Page {page_no}", "Record exercises, sets, reps, and weight.")
    pdf.text(pdf_design.MARGIN, pdf.h - 114, "Week: ______  Day: __________________  Focus: ________________________", 9, color=_col("muted"))
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 136
    bottom = 80
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


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    size = PAPER_SIZES[paper]
    pdf = pdf_design.Pdf(size, footer=f"{norm['title']} · {norm['weeks']}-Week Plan", style=_STYLE["name"])
    pdf.new_page(); _title_page(pdf, norm)
    pdf.new_page(); _goals_page(pdf, norm)
    pdf.new_page(); _program_grid_page(pdf, norm)
    for i in range(1, 5):
        pdf.new_page(); _workout_log_page(pdf, norm, i)
    pdf.new_page(); _cardio_log_page(pdf, norm)
    pdf.new_page(); _measurements_page(pdf, norm)
    pdf.new_page(); _meal_water_page(pdf, norm)
    pdf.new_page(); _progress_review_page(pdf, norm)
    pdf.new_page(); _weekly_checkin_page(pdf, norm)
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
        "- 4 weekly workout log pages (sets, reps, weight)",
        "- Cardio log page",
        "- Body measurements page",
        "- Meal and water tracker page",
        "- Progress review page",
        "- cover.png and three preview images",
        "",
        "HOW TO PRINT",
        "1. Open the PDF for your paper size.",
        "2. Print at 100% scale (Actual Size).",
        "3. Print extra workout log pages as needed.",
        "4. Use the progress review page every few weeks.",
        "",
        "PAGE COUNT",
        "- 12 pages per PDF (A4 and US Letter included)",
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
        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "weeks": norm["weeks"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": 12,
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
