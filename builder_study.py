"""builder_study.py — Exam and study planner printable pack for Indian students.

FORMAT = 'study_planner'
KEYWORDS = ['study planner', 'exam planner', 'revision timetable', 'chapter tracker']

Builds a deterministic A4 + US Letter printable pack with an editable subject
list (10th, Inter, EAMCET, JEE, NEET style sets), a revision timetable,
chapter tracker, mock-test log, pomodoro sheet, and exam-day checklist.
Cover and three preview images use Pillow only. No stock images, no currency
amounts, no network.
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

FORMAT = "study_planner"
KEYWORDS = [
    "study planner", "exam planner", "revision timetable", "chapter tracker",
    "mock test log", "pomodoro sheet", "exam day checklist",
]

DEFAULT_TITLE = "Exam & Study Planner"
DEFAULT_EXAM = "jee"
SUBJECT_SETS: Dict[str, List[str]] = {
    "class_10": ["Maths", "Science", "Social", "English", "Telugu", "Hindi"],
    "inter": ["Maths A", "Maths B", "Physics", "Chemistry", "English", "Sanskrit"],
    "eamcet": ["Maths A", "Maths B", "Physics", "Chemistry", "Botany", "Zoology"],
    "jee": ["Physics", "Chemistry", "Maths"],
    "neet": ["Physics", "Chemistry", "Biology"],
}
EXAM_ALIASES = {
    "10th": "class_10", "class10": "class_10", "class_10": "class_10", "ssc": "class_10",
    "inter": "inter", "intermediate": "inter", "mpc": "inter",
    "eamcet": "eamcet", "emcet": "eamcet",
    "jee": "jee", "iit": "jee", "jee_main": "jee", "jee_advanced": "jee",
    "neet": "neet", "medical": "neet",
}
WEEK_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SLOTS = ["Morning", "Afternoon", "Evening"]
DEFAULT_CHECKLIST = [
    "Hall ticket / admit card", "Photo ID", "Two black pens", "Two pencils",
    "Eraser and sharpener", "Geometry box", "Water bottle", "Watch (no smart watch)",
    "Exam centre address", "Travel plan and backup", "Light snack", "Calm breathing reminder",
]
PAPER_SIZES = {"a4": "A4", "letter": "Letter"}


def _clean_text(value: Any, fallback: str = "") -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def _clean_list(value: Any, fallback: List[str], limit: int) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return list(fallback)[:limit]
    out: List[str] = []
    for item in value:
        text = _clean_text(item)
        if text and text not in out:
            out.append(text)
        if len(out) == limit:
            break
    return out or list(fallback)[:limit]


def normalize_spec(spec: Any) -> Dict[str, Any]:
    """Validate the spec and return a deterministic normalized dictionary."""
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")

    title = _clean_text(spec.get("title"), DEFAULT_TITLE)
    raw_exam = _clean_text(spec.get("exam") or spec.get("course"), DEFAULT_EXAM).lower().replace(" ", "_")
    exam = EXAM_ALIASES.get(raw_exam, raw_exam if raw_exam in SUBJECT_SETS else DEFAULT_EXAM)

    raw_subjects = spec.get("subjects")
    if isinstance(raw_subjects, (list, tuple)) and raw_subjects:
        subjects = _clean_list(raw_subjects, SUBJECT_SETS[exam], 12)
    else:
        subjects = list(SUBJECT_SETS[exam])

    try:
        chapters = int(spec.get("chapters_per_subject", 12))
    except (TypeError, ValueError):
        raise ValueError("chapters_per_subject must be an integer")
    if not 6 <= chapters <= 20:
        raise ValueError("chapters_per_subject must be between 6 and 20")

    try:
        cycles = int(spec.get("pomodoro_cycles", 8))
    except (TypeError, ValueError):
        raise ValueError("pomodoro_cycles must be an integer")
    if not 4 <= cycles <= 12:
        raise ValueError("pomodoro_cycles must be between 4 and 12")

    checklist = _clean_list(spec.get("checklist"), DEFAULT_CHECKLIST, 16)
    exam_date = _clean_text(spec.get("exam_date"), "")
    return {
        "title": title,
        "exam": exam,
        "exam_label": exam.replace("_", " ").title(),
        "subjects": subjects,
        "chapters_per_subject": chapters,
        "pomodoro_cycles": cycles,
        "checklist": checklist,
        "exam_date": exam_date,
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
        raise RuntimeError("Pillow is required for study planner images")
    w, h = 1200, 800
    bg = "#f4f7fb"
    ink = "#1c2d42"
    accent = "#2f5d8a"
    warm = "#c9853b"
    im = Image.new("RGB", (w, h), color=bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([26, 26, w - 26, h - 26], outline=accent, width=3)
    draw.rectangle([26, 26, w - 26, 76], fill=accent)
    draw.text((50, 40), "CHILLI GAVVA · EXAM & STUDY PLANNER", fill="#ffffff", font=_font(19, True))
    draw.text((60, 126), norm["title"][:40], fill=ink, font=_font(40, True))
    draw.text((60, 182), f"{norm['exam_label']} set · {len(norm['subjects'])} subjects · A4 + US Letter", fill=accent, font=_font(19))

    draw.rounded_rectangle([60, 250, 690, 640], radius=12, fill="#ffffff", outline="#cad8e6", width=2)
    draw.text((82, 270), "REVISION TIMETABLE", fill=accent, font=_font(16, True))
    for i, day in enumerate(WEEK_DAYS):
        draw.text((82, 308 + i * 42), day, fill=ink, font=_font(12, True))
        for slot in range(3):
            draw.rectangle([160 + slot * 160, 302 + i * 42, 300 + slot * 160, 334 + i * 42], outline="#cad8e6")
    draw.text((82, 606), "Morning · Afternoon · Evening", fill="#5f6b73", font=_font(12))

    draw.rounded_rectangle([740, 250, 1140, 640], radius=12, fill="#ffffff", outline="#cad8e6", width=2)
    draw.text((764, 270), "SUBJECTS", fill=warm, font=_font(16, True))
    for i, subject in enumerate(norm["subjects"][:8]):
        draw.text((764, 310 + i * 34), f"• {subject}", fill=ink, font=_font(14))
    draw.text((764, 590), "Chapter tracker · Mock tests · Pomodoro", fill="#5f6b73", font=_font(11))
    draw.text((60, 690), "Editable subjects · Revision timetable · Chapter tracker · Mock-test log · Exam-day checklist", fill="#5f6b73", font=_font(14))
    draw.text((900, 720), ["Cover", "Timetable", "Mock Tests", "Checklist"][preview % 4], fill=accent, font=_font(14, True))
    im.save(str(path), format="PNG", optimize=True)


def _heading(pdf: Any, text: str, subtitle: str = "") -> None:
    pdf.text(pdf_design.MARGIN, pdf.h - 62, text, 20, bold=True, color="#1c2d42")
    if subtitle:
        pdf.text(pdf_design.MARGIN, pdf.h - 84, subtitle, 10, color="#5f6b73")
    pdf.line(pdf_design.MARGIN, pdf.h - 96, pdf.w - pdf_design.MARGIN, pdf.h - 96, color="#cad8e6")


def _title_page(pdf: Any, norm: Dict[str, Any]) -> None:
    lines, size = pdf_design.fit_text(norm["title"], 30, pdf.w - 2 * pdf_design.MARGIN, bold=True, max_lines=3, min_size=16)
    y = pdf.h / 2 + 95
    for line in lines:
        pdf.text_center(pdf.w / 2, y, line, size, bold=True, color="#1c2d42")
        y -= size * 1.25
    pdf.text_center(pdf.w / 2, y - 8, f"{norm['exam_label']} preparation set", 17, color="#2f5d8a")
    if norm["exam_date"]:
        pdf.text_center(pdf.w / 2, y - 34, f"Exam date: {norm['exam_date']}", 11, color="#c9853b")
    pdf.text_center(pdf.w / 2, y - 60, "Revision timetable · Chapter tracker · Mock tests · Pomodoro · Checklist", 10, color="#5f6b73")
    pdf.line(pdf.w / 2 - 90, y - 88, pdf.w / 2 + 90, y - 88, color="#2f5d8a", width=1.2)


def _timetable_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Revision Timetable", "Write the subject and chapter you will revise in each slot.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    label_w = 52
    col_w = (right - left - label_w) / len(SLOTS)
    row_h = min(58.0, (top - 90) / len(WEEK_DAYS))
    pdf.text(left, top + 8, "Day", 8, bold=True, color="#5f6b73")
    for i, slot in enumerate(SLOTS):
        pdf.text_center(left + label_w + i * col_w + col_w / 2, top + 8, slot, 8, bold=True, color="#5f6b73")
    for r, day in enumerate(WEEK_DAYS):
        y = top - (r + 1) * row_h
        pdf.text(left + 2, y + row_h / 2 - 4, day, 9, bold=True, color="#1c2d42")
        for c in range(len(SLOTS)):
            pdf.rect(left + label_w + c * col_w, y, col_w, row_h, color="#cad8e6", width=0.5)
    pdf.text(left, 66, "Rule: one focused block at a time. Tick the box when the block is finished.", 9, color="#5f6b73")


def _chapter_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Chapter Tracker", "Colour a box after each chapter is revised and tested.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 135
    label_w = 92
    chapters = norm["chapters_per_subject"]
    cell_w = (right - left - label_w) / chapters
    row_h = min(46.0, (top - 100) / max(1, len(norm["subjects"])))
    for day in range(1, chapters + 1):
        pdf.text_center(left + label_w + (day - 1) * cell_w + cell_w / 2, top + 10, str(day), 6.5, color="#5f6b73")
    for r, subject in enumerate(norm["subjects"]):
        y = top - (r + 1) * row_h
        pdf.text(left + 2, y + row_h / 2 - 4, subject[:18], 8.5, color="#1c2d42")
        for chapter in range(chapters):
            pdf.rect(left + label_w + chapter * cell_w, y, cell_w, row_h, color="#cad8e6", width=0.5)
    pdf.text(left, 66, "Mark each chapter: revised, practised, and confident.", 9, color="#5f6b73")


def _mock_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Mock-Test Log", "Record every mock test and the one fix for next time.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    headers = ["Date", "Test", "Marks", "Weak areas", "Next action"]
    widths = [0.13, 0.22, 0.10, 0.27, 0.28]
    x = left
    for header, width in zip(headers, widths):
        pdf.text(x + 2, top + 8, header, 8, bold=True, color="#5f6b73")
        x += (right - left) * width
    row_h = min(34.0, (top - 90) / 12)
    for r in range(12):
        y = top - (r + 1) * row_h
        x = left
        for width in widths:
            pdf.rect(x, y, (right - left) * width, row_h, color="#cad8e6", width=0.4)
            x += (right - left) * width


def _pomodoro_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Pomodoro Sheet", "Focus 25 minutes · break 5 minutes · tick one cycle.")
    left = pdf_design.MARGIN
    top = pdf.h - 135
    gap = 14
    cols = 2
    rows = (norm["pomodoro_cycles"] + cols - 1) // cols
    box_w = (pdf.w - 2 * left - gap) / cols
    box_h = min(92.0, (top - 180 - (rows - 1) * gap) / rows)
    for index in range(norm["pomodoro_cycles"]):
        col = index % cols
        row = index // cols
        x = left + col * (box_w + gap)
        y = top - (row + 1) * box_h - row * gap
        pdf.rect(x, y, box_w, box_h, color="#cad8e6", width=0.7)
        pdf.text(x + 10, y + box_h - 18, f"Cycle {index + 1:02d}", 10, bold=True, color="#2f5d8a")
        pdf.text(x + 10, y + box_h - 34, "Focus: 25 min", 8, color="#1c2d42")
        pdf.text(x + 10, y + box_h - 48, "Break: 5 min", 8, color="#1c2d42")
        pdf.rect(x + box_w - 26, y + 10, 12, 12, color="#9fb8b5", width=0.6)
    pdf.text(left, 120, "Notes", 10, bold=True, color="#2f5d8a")
    for line in range(3):
        pdf.line(left, 100 - line * 18, pdf.w - left, 100 - line * 18, color="#d7dfe1")


def _checklist_page(pdf: Any, norm: Dict[str, Any]) -> None:
    _heading(pdf, "Exam-Day Checklist", "Pack the night before and sleep on time.")
    left = pdf_design.MARGIN
    top = pdf.h - 135
    col_w = (pdf.w - 2 * left) / 2
    row_h = min(30.0, (top - 100) / max(1, (len(norm["checklist"]) + 1) // 2))
    for index, item in enumerate(norm["checklist"]):
        col = index % 2
        row = index // 2
        x = left + col * col_w
        y = top - (row + 1) * row_h
        pdf.rect(x + 4, y + row_h / 2 - 5, 10, 10, color="#9fb8b5", width=0.7)
        pdf.text(x + 22, y + row_h / 2 - 4, item[:52], 9, color="#1c2d42")
    pdf.text(left, 66, "Reach the centre early. Read every instruction twice. Breathe.", 9, color="#5f6b73")


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    size = PAPER_SIZES[paper]
    pdf = pdf_design.Pdf(size, footer=f"{norm['title']} · {norm['exam_label']} Study Planner", style="A")
    for page_fn in (_title_page, _timetable_page, _chapter_page, _mock_page, _pomodoro_page, _checklist_page):
        pdf.new_page()
        page_fn(pdf, norm)
    pdf.save(str(path), title=norm["title"])


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"{norm['title']} — {norm['exam_label']} Set",
        "=" * 52,
        "",
        "WHAT IS INSIDE",
        "- A4 PDF and US Letter PDF",
        f"- Editable subject list: {', '.join(norm['subjects'])}",
        "- Revision timetable for seven days and three study slots",
        f"- Chapter tracker with {norm['chapters_per_subject']} chapters per subject",
        "- Mock-test log and pomodoro sheet",
        "- Exam-day checklist",
        "- cover.png and three preview images",
        "",
        "HOW TO PRINT",
        "1. Open the PDF for your paper size.",
        "2. Print at 100% scale (Actual Size).",
        "3. Reprint the timetable and tracker each week.",
        "4. Keep the checklist page in your bag the night before.",
        "",
        "TIPS",
        "- Revise in short focused blocks and take real breaks.",
        "- Update the mock-test log on the same day.",
        "- Track chapters honestly so revision stays realistic.",
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
        "All layouts and text are generated by the Chilli Gavva robot.",
        "",
    ])


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    """Build the exam and study planner pack and return created file names."""
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for the study planner builder")
    norm = normalize_spec(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="study_build_"))
    names: List[str] = []
    try:
        for paper in PAPER_SIZES:
            name = f"study_planner_{paper}.pdf"
            _build_pdf(temp_dir / name, paper, norm)
            names.append(name)

        names.append("cover.png")
        _cover_image(temp_dir / "cover.png", norm, preview=0)
        for index in range(1, 4):
            name = f"preview_{index}.png"
            _cover_image(temp_dir / name, norm, preview=index)
            names.append(name)

        names.extend(["README.txt", "LICENSE.txt"])
        (temp_dir / "README.txt").write_text(_readme(norm), encoding="utf-8")
        (temp_dir / "LICENSE.txt").write_text(_license(norm), encoding="utf-8")

        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "exam": norm["exam"],
            "exam_label": norm["exam_label"],
            "subjects": norm["subjects"],
            "chapters_per_subject": norm["chapters_per_subject"],
            "pomodoro_cycles": norm["pomodoro_cycles"],
            "exam_date": norm["exam_date"],
            "checklist": norm["checklist"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": 6,
            "files": sorted(names + ["manifest.json"]),
        }
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
