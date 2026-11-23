"""Printable kids chore and reward chart pack."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None
    HAS_PILLOW = False

import pdf_design

FORMAT = "kids_chore_reward"
KEYWORDS = [
    "chore chart", "reward chart", "kids routine", "kids planner",
    "reading log for kids", "kids chore chart",
]

DEFAULT_TITLE = "Kids Chore & Reward Chart Pack"
PAPER_SIZES = {"a4": "A4", "letter": "Letter"}

_COLORS = {
    "ink": "#3e3557", "accent": "#7059a6", "muted": "#718096",
    "line": "#d7cde8", "soft": "#f5f0fb", "warm": "#e09b67",
}


def normalize_spec(spec: Any) -> Dict[str, Any]:
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")
    title = " ".join(str(spec.get("title") or DEFAULT_TITLE).split())
    child_name = " ".join(str(spec.get("child_name") or "My Child").split())
    return {"title": title or DEFAULT_TITLE, "child_name": child_name or "My Child"}


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
        raise RuntimeError("Pillow is required for kids chart images")
    w, h = 1200, 800
    im = Image.new("RGB", (w, h), "#fffaf5")
    draw = ImageDraw.Draw(im)
    accent = "#7059a6"
    draw.rounded_rectangle([24, 24, w - 24, h - 24], radius=24, outline=accent, width=5)
    if preview == 0:
        draw.rounded_rectangle([24, 24, w - 24, 92], radius=20, fill=accent)
        draw.text((48, 48), "CHILLI GAVVA · KIDS CHART PACK", fill="#ffffff", font=_font(20, True))
        draw.text((60, 130), norm["title"][:42], fill="#3e3557", font=_font(38, True))
        draw.text((60, 184), norm["child_name"][:30], fill=accent, font=_font(22))
        draw.text((60, 230), "24 printable pages · A4 + US Letter", fill="#718096", font=_font(18, True))
        layouts = [
            ("CHORE CHART", ["Chore", "Mon", "Tue", "Wed"]),
            ("READING LOG", ["Date", "Book", "Pages"]),
            ("ROUTINE CARD", ["1. Wake up", "2. Get dressed", "3. Pack"]),
        ]
        for i, (label, lines) in enumerate(layouts):
            x = 60 + i * 360
            draw.rounded_rectangle([x, 290, x + 320, 680], radius=16, fill="#ffffff", outline="#d7cde8", width=3)
            draw.rectangle([x + 18, 308, x + 302, 360], fill="#f5f0fb")
            draw.text((x + 32, 326), label, fill=accent, font=_font(17, True))
            for r, line in enumerate(lines):
                y = 390 + r * 70
                draw.text((x + 30, y), line, fill="#3e3557", font=_font(15, True))
                draw.line((x + 30, y + 42, x + 290, y + 42), fill="#d7cde8", width=2)
        draw.text((60, 724), "Real charts, logs and cut-ready routine cards", fill=accent, font=_font(16, True))
    elif preview == 1:
        draw.rectangle([24, 24, w - 24, 100], fill=accent)
        draw.text((55, 48), "WEEKLY CHORE CHART · REAL PAGE", fill="#ffffff", font=_font(24, True))
        headers = ["Chore", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        widths = [240, 120, 120, 120, 120, 120, 120, 120]
        left, top = 55, 150
        x = left
        for i, header in enumerate(headers):
            draw.rectangle([x, top, x + widths[i], top + 55], fill="#f5f0fb", outline="#d7cde8")
            draw.text((x + 10, top + 18), header, fill=accent, font=_font(13, True))
            x += widths[i]
        for r in range(9):
            y = top + 55 + r * 58
            x = left
            for width in widths:
                draw.rectangle([x, y, x + width, y + 58], outline="#d7cde8")
                x += width
    elif preview == 2:
        draw.rectangle([24, 24, w - 24, 100], fill=accent)
        draw.text((55, 48), "READING LOG · REAL PAGE", fill="#ffffff", font=_font(24, True))
        headers = ["Date", "Book", "Pages", "Minutes", "Favorite part"]
        widths = [180, 300, 150, 150, 300]
        left, top = 55, 150
        x = left
        for i, header in enumerate(headers):
            draw.rectangle([x, top, x + widths[i], top + 55], fill="#f5f0fb", outline="#d7cde8")
            draw.text((x + 12, top + 18), header, fill=accent, font=_font(14, True))
            x += widths[i]
        for r in range(9):
            y = top + 55 + r * 58
            x = left
            for width in widths:
                draw.rectangle([x, y, x + width, y + 58], outline="#d7cde8")
                x += width
    else:
        draw.rectangle([24, 24, w - 24, 100], fill=accent)
        draw.text((55, 48), "MORNING ROUTINE CARDS · REAL PAGE", fill="#ffffff", font=_font(24, True))
        items = ["Wake up", "Get dressed", "Eat breakfast", "Pack bag", "Brush teeth", "Clean up"]
        for i, item in enumerate(items):
            col, row = i % 2, i // 2
            x, y = 55 + col * 560, 150 + row * 190
            draw.rounded_rectangle([x, y, x + 520, y + 150], radius=14, fill="#fffaf5", outline="#d7cde8", width=3)
            draw.text((x + 24, y + 24), f"{i + 1:02d}", fill="#e09b67", font=_font(20, True))
            draw.text((x + 90, y + 30), item, fill="#3e3557", font=_font(22, True))
            draw.line((x + 24, y + 105, x + 490, y + 105), fill="#d7cde8", width=2)
            draw.text((x + 24, y + 112), "Done:  ____________", fill="#718096", font=_font(14))
    im.save(str(path), format="PNG", optimize=True)


def _heading(pdf: Any, title: str, subtitle: str = "") -> None:
    pdf.text(pdf_design.MARGIN, pdf.h - 62, title, 19, bold=True, color=_COLORS["ink"])
    if subtitle:
        pdf.text(pdf_design.MARGIN, pdf.h - 84, subtitle, 9.5, color=_COLORS["muted"])
    pdf.line(pdf_design.MARGIN, pdf.h - 96, pdf.w - pdf_design.MARGIN, pdf.h - 96, color=_COLORS["line"])


def _table(pdf: Any, title: str, subtitle: str, headers: List[str], rows: int = 16) -> None:
    _heading(pdf, title, subtitle)
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 125
    col_w = (right - left) / len(headers)
    row_h = (top - 28 - 80) / rows
    for i, header in enumerate(headers):
        x = left + i * col_w
        pdf.fill(x, top - 28, col_w, 28, _COLORS["soft"])
        pdf.text_center(x + col_w / 2, top - 19, header[:16], 8, bold=True, color=_COLORS["accent"])
        pdf.line(x, top - 28, x, top - 28 - rows * row_h, color=_COLORS["line"], width=0.5)
    pdf.line(right, top - 28, right, top - 28 - rows * row_h, color=_COLORS["line"], width=0.5)
    for r in range(rows):
        y = top - 28 - (r + 1) * row_h
        pdf.line(left, y, right, y, color=_COLORS["line"], width=0.4)


def _cover(pdf: Any, norm: Dict[str, Any]) -> None:
    pdf.fill(pdf_design.MARGIN, pdf.h - 150, pdf.w - 2 * pdf_design.MARGIN, 90, _COLORS["soft"])
    lines, size = pdf_design.fit_text(norm["title"], 30, pdf.w - 2 * pdf_design.MARGIN - 40,
                                      bold=True, max_lines=3, min_size=18)
    y = pdf.h - 170
    for line in lines:
        pdf.text_center(pdf.w / 2, y, line, size, bold=True, color=_COLORS["ink"])
        y -= size * 1.25
    pdf.text_center(pdf.w / 2, y - 10, norm["child_name"], 16, color=_COLORS["accent"])
    pdf.text_center(pdf.w / 2, 170, "Chores · Reward chart · Reading · Routines · Homework · Screen time", 9,
                    color=_COLORS["muted"])


def _routine_cards(pdf: Any, title: str) -> None:
    _heading(pdf, title, "Cut along the dashed guide and pin the card where it is used.")
    left = pdf_design.MARGIN
    top = pdf.h - 125
    card_w = (pdf.w - 2 * pdf_design.MARGIN - 24) / 2
    card_h = 92
    items = ("Wake up", "Get dressed", "Eat breakfast", "Pack bag", "Finish homework",
             "Clean up", "Brush teeth", "Bedtime")
    for i in range(8):
        col = i % 2
        row = i // 2
        x = left + col * (card_w + 24)
        y = top - (row + 1) * card_h - row * 38
        pdf.rect(x, y, card_w, card_h - 12, color=_COLORS["line"], width=1.0)
        pdf.text(x + 14, y + card_h - 38, f"{i + 1:02d}  {items[i]}", 12, bold=True, color=_COLORS["ink"])
        pdf.text(x + 14, y + 20, "Done:  ____________", 9, color=_COLORS["muted"])


def _certificate(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Certificate Page", "For a kind act, a finished goal, or steady effort.")
    pdf.rect(70, 150, pdf.w - 140, pdf.h - 260, color=_COLORS["line"], width=2)
    pdf.text_center(pdf.w / 2, pdf.h - 230, "CERTIFICATE", 28, bold=True, color=_COLORS["accent"])
    pdf.text_center(pdf.w / 2, pdf.h - 300, "This certificate is for", 12, color=_COLORS["muted"])
    pdf.line(pdf.w / 2 - 150, pdf.h - 340, pdf.w / 2 + 150, pdf.h - 340, color=_COLORS["line"])
    pdf.text_center(pdf.w / 2, pdf.h - 370, norm["child_name"], 22, bold=True, color=_COLORS["ink"])
    pdf.text_center(pdf.w / 2, pdf.h - 430, "for", 12, color=_COLORS["muted"])
    pdf.line(pdf.w / 2 - 180, pdf.h - 470, pdf.w / 2 + 180, pdf.h - 470, color=_COLORS["line"])
    pdf.text_center(pdf.w / 2, 300, "Signed: ____________________", 12, color=_COLORS["ink"])


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    pdf = pdf_design.Pdf(PAPER_SIZES[paper], footer=f"{norm['title']} · {PAPER_SIZES[paper]} · for personal use", style="A")
    pdf.new_page()
    _cover(pdf, norm)
    pdf.new_page()
    _heading(pdf, "Start & Goal", "Choose one small goal and celebrate steady effort.")
    y = pdf.h - 130
    for label in ("Child name", "This week", "My goal", "My reward choice"):
        pdf.text(pdf_design.MARGIN, y, label, 11, bold=True, color=_COLORS["ink"])
        pdf.line(pdf_design.MARGIN + 150, y - 2, pdf.w - pdf_design.MARGIN, y - 2, color=_COLORS["line"])
        y -= 72
    pdf.text(pdf_design.MARGIN, y, "How I will get started", 11, bold=True, color=_COLORS["ink"])
    for _ in range(8):
        y -= 24
        pdf.line(pdf_design.MARGIN, y, pdf.w - pdf_design.MARGIN, y, color=_COLORS["line"], width=0.4)
    for index in range(1, 5):
        pdf.new_page()
        _table(pdf, f"Weekly Chore Chart {index}", "Mark a box after each chore is finished.", ["Chore", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], 10)
    for index in range(1, 4):
        pdf.new_page()
        _table(pdf, f"Reward Chart {index}", "Use stars or stickers for each completed goal.", ["Goal", "Star 1", "Star 2", "Star 3", "Star 4", "Star 5"], 12)
    for index in range(1, 5):
        pdf.new_page()
        _table(pdf, f"Reading Log {index}", "Write the book title and record the pages or minutes.", ["Date", "Book", "Pages", "Minutes", "Favorite part"], 14)
    for index in range(1, 5):
        pdf.new_page()
        title = "Morning Routine Cards" if index <= 2 else "Evening Routine Cards"
        _routine_cards(pdf, f"{title} {index}")
    for index in range(1, 3):
        pdf.new_page()
        _table(pdf, f"Homework Tracker {index}", "Write one task and its check-off status.", ["Date", "Subject", "Task", "Done", "Next step"], 14)
    for index in range(1, 3):
        pdf.new_page()
        _table(pdf, f"Screen-Time Log {index}", "Keep an honest record of screen time.", ["Day", "Activity", "Start", "Finish", "Minutes"], 14)
    pdf.new_page()
    _heading(pdf, "Weekly Reflection", "Notice effort, helping, and one goal for next week.")
    y = pdf.h - 130
    prompts = ("A chore I finished", "A kind thing I did", "Something I learned", "One goal for next week")
    for prompt in prompts:
        pdf.text(pdf_design.MARGIN, y, prompt, 11, bold=True, color=_COLORS["ink"])
        for _ in range(4):
            y -= 24
            pdf.line(pdf_design.MARGIN, y, pdf.w - pdf_design.MARGIN, y, color=_COLORS["line"], width=0.4)
        y -= 42
    pdf.new_page()
    _table(pdf, "Kindness & Helping Log", "Record a helpful choice from the week.", ["Day", "Helping act", "Who was helped", "How felt"], 14)
    pdf.new_page()
    _certificate(pdf, norm)
    pdf.save(str(path), title=norm["title"])


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"{norm['title']}",
        "=" * 52,
        "",
        "WHAT YOU GET",
        "- A4 PDF and US Letter PDF",
        "- 24 printable pages",
        "- Weekly chore chart and reward chart",
        "- Reading log and morning/evening routine cards",
        "- Homework and screen-time trackers",
        "- Weekly reflection, kindness log, and certificate",
        "- Cover.png and three preview images",
        "",
        "HOW TO PRINT",
        "1. Open the PDF matching your paper.",
        "2. Print at 100% scale (Actual Size).",
        "3. Use pens, stickers, or stars to mark progress.",
        "4. Reprint pages for a new week.",
        "",
        "LICENSE",
        "Personal use only. Do not resell or redistribute the files.",
    ]) + "\n"


def _license(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"LICENSE - {norm['title']}",
        "",
        "This printable pack is for personal use only.",
        "You may print copies for yourself and your household.",
        "You may not resell, redistribute, share, or claim the files as your own.",
        "",
    ])


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for kids chart images")
    norm = normalize_spec(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="kids_build_"))
    names: List[str] = []
    try:
        for paper in PAPER_SIZES:
            name = f"kids_chore_reward_{paper}.pdf"
            _build_pdf(temp_dir / name, paper, norm)
            names.append(name)
        names.extend(["cover.png", "preview_1.png", "preview_2.png", "preview_3.png"])
        _cover_image(temp_dir / "cover.png", norm, 0)
        for index in range(1, 4):
            _cover_image(temp_dir / f"preview_{index}.png", norm, index)
        names.extend(["README.txt", "LICENSE.txt"])
        (temp_dir / "README.txt").write_text(_readme(norm), encoding="utf-8")
        (temp_dir / "LICENSE.txt").write_text(_license(norm), encoding="utf-8")
        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "child_name": norm["child_name"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": 24,
            "files": sorted(names + ["manifest.json"]),
        }
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        names.append("manifest.json")
        out_path.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(temp_dir / name, out_path / name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return sorted(names)
