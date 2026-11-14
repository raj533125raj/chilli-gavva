"""report_pdf.py — the owner's weekly PDF report with real charts.

The report is built from the robot's own SQLite state (drafts, product builds,
delivery states, task runs, provider calls and the post plan). Every query has a
safe fallback, so a brand-new or partly-migrated database still produces a full
4–6 page report of counts. Money amounts are never read or written.

Charts are drawn by report_charts.py (Pillow) and embedded as raster images in a
small self-contained PDF writer; pdf_design.py is used only for text metrics and
page sizes. The output is deterministic for a fixed clock and fixed input.

Public helpers:
  collect_weekly_data(db_path=None, now=None, plan_path=None) -> dict
  render_weekly_report(data, out_path, now=None) -> Path
  build_weekly_report(db_path=None, now=None, out_dir=None, plan_path=None) -> dict
"""

from __future__ import annotations

import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata
import zlib

import pdf_design
import report_charts

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PLAN = BASE_DIR / "exported_content" / "post_plan.json"
PAGE_W, PAGE_H = pdf_design.PAGE_SIZES["A4"]
MARGIN = pdf_design.MARGIN
ACCENT = pdf_design.STYLES["A"]["accent"]
ACCENT_SOFT = pdf_design.STYLES["A"]["bar"]
INK = pdf_design.STYLES["A"]["ink"]
MUTED = pdf_design.STYLES["A"]["muted"]
LINE = pdf_design.STYLES["A"]["line"]
PAPER = "#f7faf9"

MONEY = re.compile(
    r"(?:(?:USD|INR|EUR|GBP|AUD|CAD|Rs\.?|\$|₹|€|£)\s*\d[\d,.]*"
    r"|\d[\d,.]*\s*(?:USD|INR|EUR|GBP|AUD|CAD|Rs\.?|dollars?|rupees?|euros?|pounds?))",
    re.IGNORECASE,
)

SECRET = re.compile(
    r"(?ix)"
    r"(?:\bsk-[a-z0-9_-]{12,}\b)"
    r"|(?:\bbearer\s+[a-z0-9._~+/-]{10,}=*)"
    r"|(?:\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret[_-]?key|token|secret)\s*[:=]\s*[^\s,;]{8,})"
    r"|(?:\b[0-9a-f]{32,}\b)"
    r"|(?:\beyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\b)"
)

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _utc(now=None):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if isinstance(now, datetime.datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    if isinstance(now, (int, float)):
        return datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    if isinstance(now, datetime.date):
        return datetime.datetime(now.year, now.month, now.day, tzinfo=datetime.timezone.utc)
    raise TypeError("now must be a datetime, date or timestamp")


def _safe_pdf_chars(text):
    """Keep WinAnsi characters and replace anything else with a visible ASCII fallback."""
    out = []
    for char in text:
        try:
            char.encode("cp1252")
            out.append(char)
        except UnicodeEncodeError:
            plain = unicodedata.normalize("NFKD", char).encode("ascii", "ignore").decode()
            out.append(plain or "?")
    return "".join(out)


def _scrub(text):
    text = str(text or "").replace("\x00", " ").strip()
    text = MONEY.sub("[amount]", text)
    text = EMAIL.sub("[email]", text)
    text = SECRET.sub("[secret]", text)
    return _safe_pdf_chars(text)


def _count(conn, sql, args=()):
    try:
        row = conn.execute(sql, args).fetchone()
        return int(row[0] or 0) if row else 0
    except sqlite3.Error:
        return 0


def _count_table(conn, table, sql, args=()):
    if not _table_exists(conn, table):
        return 0
    return _count(conn, sql, args)


def _rows(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _table_exists(conn, name):
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


def _week_bounds(now):
    now = _utc(now)
    start = now.date() - datetime.timedelta(days=now.weekday())
    end = start + datetime.timedelta(days=6)
    end_exclusive = end + datetime.timedelta(days=1)
    iso_year, iso_week, _ = start.isocalendar()
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_date": start,
        "end_date": end,
        "start_iso": start.isoformat(),
        "end_iso": end.isoformat(),
        "end_exclusive_iso": end_exclusive.isoformat(),
        "iso_year": iso_year,
        "iso_week": iso_week,
        "week_key": f"{iso_year}-W{iso_week:02d}",
        "label": f"Week {iso_week:02d} · {start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}",
    }


def _load_plan_with_warning(plan_path):
    path = Path(plan_path) if plan_path else DEFAULT_PLAN
    if not path.is_file():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return [], f"Post plan file chadavaledu ({path.name}); posts 0 ga chupistunnam."
    if not isinstance(data, list):
        return [], f"Post plan file list format lo ledu ({path.name}); posts 0 ga chupistunnam."
    return data, None


def _load_plan(plan_path):
    return _load_plan_with_warning(plan_path)[0]


def _posts(plan, start_iso, end_iso):
    planned = done = 0
    for item in plan:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")
        if start_iso <= day <= end_iso:
            planned += 1
            if str(item.get("status") or "").lower() == "done":
                done += 1
    return planned, done


def _providers(conn, start_day, end_day):
    stats = {}
    for provider, ok, fail in _rows(
            conn,
            "SELECT provider, SUM(ok), SUM(fail) FROM provider_log "
            "WHERE day >= ? AND day <= ? GROUP BY provider ORDER BY provider",
            (start_day, end_day)):
        name = _scrub(provider or "unknown")[:40]
        ok, fail = int(ok or 0), int(fail or 0)
        stats[name] = {"ok": ok, "fail": fail, "total": ok + fail}
    return stats


def _runs_per_day(conn, bounds):
    counts = {}
    if _table_exists(conn, "tasks"):
        for day, count in _rows(
                conn,
                "SELECT SUBSTR(COALESCE(finished_ts, ts), 1, 10) AS day, COUNT(*) "
                "FROM tasks WHERE COALESCE(finished_ts, ts) >= ? AND COALESCE(finished_ts, ts) < ? "
                "GROUP BY day ORDER BY day",
                (bounds["start_iso"], bounds["end_exclusive_iso"])):
            if day:
                counts[str(day)] = int(count)
    days = []
    values = []
    for offset in range(7):
        day = (bounds["start_date"] + datetime.timedelta(days=offset)).isoformat()
        days.append(WEEKDAYS[offset])
        values.append(counts.get(day, 0))
    return days, values


def _failures_by_step(conn, bounds):
    if not _table_exists(conn, "tasks"):
        return []
    rows = _rows(
        conn,
        "SELECT kind, COUNT(*) FROM tasks WHERE status = 'failed' "
        "AND COALESCE(finished_ts, ts) >= ? AND COALESCE(finished_ts, ts) < ? "
        "GROUP BY kind ORDER BY COUNT(*) DESC, kind",
        (bounds["start_iso"], bounds["end_exclusive_iso"]))
    return [(_scrub(kind or "unknown")[:32], int(count)) for kind, count in rows]


def _wins(data):
    wins = []
    if data["drafts_approved"]:
        wins.append(f"{data['drafts_approved']} draft(s) owner approve chesaru.")
    if data["packs_built"]:
        wins.append(f"{data['packs_built']} pack(s) build ayyayi.")
    if data["posts_done"]:
        wins.append(f"{data['posts_done']} post(s) done ga mark ayyayi.")
    if data["runs_ok"] and not data["runs_failed"]:
        wins.append(f"{data['runs_ok']} robot run(s) OK; failures ledu.")
    if data["ready"]:
        wins.append(f"{data['ready']} pack(s) READY state lo unnayi.")
    if not wins:
        wins.append("Ee week ki inka data ledu; robot ready ga undi.")
    return wins[:3]


def _problems(data):
    problems = []
    if data["runs_failed"]:
        problems.append({
            "problem": f"{data['runs_failed']} robot run(s) fail ayyayi.",
            "fix": "Task log chusi reason fix cheyandi, taruvata retry cheyandi.",
        })
    if data["drafts_pending"]:
        problems.append({
            "problem": f"{data['drafts_pending']} draft(s) owner approval kosam wait lo unnayi.",
            "fix": "Telegram lo /list chusi approve leda reject cheyandi.",
        })
    if data["needs_fix"]:
        problems.append({
            "problem": f"{data['needs_fix']} pack(s) NEEDS FIX lo unnayi.",
            "fix": "Pack files and listing check chesi build retry cheyandi.",
        })
    provider_fail = sum(item["fail"] for item in data["providers"].values())
    if provider_fail:
        problems.append({
            "problem": f"AI providers lo {provider_fail} failed call(s) unnayi.",
            "fix": "Provider keys and daily limits check cheyandi.",
        })
    remaining_posts = data["posts_planned"] - data["posts_done"]
    if remaining_posts > 0:
        problems.append({
            "problem": f"{remaining_posts} planned post(s) inka done kaaledu.",
            "fix": "Post plan chusi /posted command tho done mark cheyandi.",
        })
    if not problems:
        problems.append({
            "problem": "Pedda problem emi ledu.",
            "fix": "Same schedule continue cheyandi; owner review final step.",
        })
    return problems[:3]


def _next_plan(data):
    plan = []
    if data["drafts_pending"]:
        plan.append(f"Pending {data['drafts_pending']} draft(s) review chesi decision pettandi.")
    if data["needs_fix"]:
        plan.append(f"NEEDS FIX {data['needs_fix']} pack(s) repair cheyandi.")
    if data["runs_failed"]:
        plan.append("Failed runs ki reason fix chesi retry schedule pettandi.")
    if data["posts_planned"] - data["posts_done"] > 0:
        plan.append(f"Remaining {data['posts_planned'] - data['posts_done']} post(s) publish chesi done mark cheyandi.")
    if not data["providers"]:
        plan.append("AI provider calls inka ledu; next week cycle run cheyandi.")
    plan.append("Daily cycle same time lo continue cheyandi; auto-publish emi undadu.")
    return plan[:6]


def _ranked_wins_text(wins):
    return [f"Win {index + 1}: {_scrub(text)}" for index, text in enumerate(wins)]


def _problems_text(problems):
    lines = []
    for index, item in enumerate(problems):
        lines.append(f"Problem {index + 1}: {_scrub(item['problem'])}")
        lines.append(f"Fix {index + 1}: {_scrub(item['fix'])}")
    return lines


def collect_weekly_data(db_path=None, now=None, plan_path=None, products_dir=None):
    """Counts for the week containing ``now``. Missing tables become zero, never an error."""
    bounds = _week_bounds(now)
    conn = sqlite3.connect(str(db_path) if db_path else ":memory:")
    try:
        start, end = bounds["start_iso"], bounds["end_exclusive_iso"]
        drafts_made = _count(conn, "SELECT COUNT(*) FROM approvals WHERE ts >= ? AND ts < ?", (start, end))
        drafts_approved = _count(
            conn, "SELECT COUNT(*) FROM approvals WHERE status = 'approved' AND decided_ts >= ? AND decided_ts < ?",
            (start, end))
        drafts_rejected = _count(
            conn, "SELECT COUNT(*) FROM approvals WHERE status = 'rejected' AND decided_ts >= ? AND decided_ts < ?",
            (start, end))
        drafts_pending = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'pending'")
        packs_built = _count_table(
            conn, "product_builds",
            "SELECT COUNT(*) FROM product_builds WHERE status = 'built' AND ts >= ? AND ts < ?",
            (start, end))
        ready = _count_table(conn, "product_deliveries", "SELECT COUNT(*) FROM product_deliveries WHERE status = 'ready'")
        needs_fix = _count_table(
            conn, "product_builds",
            "SELECT COUNT(*) FROM product_builds WHERE status IN ('failed', 'retry', 'needs_fix')")
        needs_fix += _count_table(
            conn, "product_deliveries", "SELECT COUNT(*) FROM product_deliveries WHERE status = 'retry'")
        runs_ok = _count(
            conn,
            "SELECT COUNT(*) FROM tasks WHERE status = 'done' "
            "AND COALESCE(finished_ts, ts) >= ? AND COALESCE(finished_ts, ts) < ?", (start, end))
        runs_failed = _count(
            conn,
            "SELECT COUNT(*) FROM tasks WHERE status = 'failed' "
            "AND COALESCE(finished_ts, ts) >= ? AND COALESCE(finished_ts, ts) < ?", (start, end))
        providers = _providers(conn, bounds["start_iso"], bounds["end_iso"])
        run_days, run_values = _runs_per_day(conn, bounds)
        failures = _failures_by_step(conn, bounds)
    finally:
        conn.close()

    plan, plan_warning = _load_plan_with_warning(plan_path)
    planned, done = _posts(plan, bounds["start_iso"], bounds["end_iso"])
    data = {
        "week": bounds["label"],
        "week_key": f"{bounds['iso_year']}-W{bounds['iso_week']:02d}",
        "start": bounds["start_iso"],
        "end": bounds["end_iso"],
        "drafts_made": drafts_made,
        "drafts_approved": drafts_approved,
        "drafts_rejected": drafts_rejected,
        "drafts_pending": drafts_pending,
        "packs_built": packs_built,
        "ready": ready,
        "needs_fix": needs_fix,
        "posts_planned": planned,
        "posts_done": done,
        "runs_ok": runs_ok,
        "runs_failed": runs_failed,
        "run_days": run_days,
        "run_values": run_values,
        "providers": providers,
        "provider_total": sum(item["total"] for item in providers.values()),
        "failures_by_step": failures,
        "plan_warning": plan_warning,
    }
    data["wins"] = _wins(data)
    problems = _problems(data)
    if plan_warning:
        problems.insert(0, {
            "problem": plan_warning,
            "fix": "exported_content/post_plan.json check chesi malli save cheyandi.",
        })
    data["problems"] = problems[:3]
    data["next_plan"] = _next_plan(data)
    data["generated_for"] = "Owner"
    return data


class _ReportPdf:
    """Small PDF writer with Helvetica text and Flate-compressed RGB images."""

    def __init__(self, width=PAGE_W, height=PAGE_H):
        self.w = float(width)
        self.h = float(height)
        self.pages = []
        self.images = []

    def new_page(self):
        self.pages.append([])

    def _ops(self):
        return self.pages[-1]

    def fill(self, x, y, w, h, color):
        self._ops().append(f"{pdf_design._c(color)} {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f 0 g")

    def rect(self, x, y, w, h, color=None, width=0.6):
        stroke = pdf_design._c(color, True) if color else "0.7 G"
        self._ops().append(f"{stroke} {width:g} w {x:.1f} {y:.1f} {w:.1f} {h:.1f} re S 0 G")

    def line(self, x1, y1, x2, y2, color=None, width=0.7):
        stroke = pdf_design._c(color, True) if color else "0.7 G"
        self._ops().append(f"{stroke} {width:g} w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S 0 G")

    def text(self, x, y, text, size=11, bold=False, color=None):
        ops = []
        if color:
            ops.append(pdf_design._c(color))
        ops.append(f"BT /{'F2' if bold else 'F1'} {size:g} Tf {x:.1f} {y:.1f} Td "
                   f"({pdf_design._pdf_text(text)}) Tj ET")
        if color:
            ops.append("0 g")
        self._ops().append(" ".join(ops))

    def text_right(self, x_right, y, text, size=11, bold=False, color=None):
        self.text(x_right - pdf_design.text_width(str(text), size, bold), y, text, size, bold, color)

    def text_center(self, x_mid, y, text, size=11, bold=False, color=None):
        self.text(x_mid - pdf_design.text_width(str(text), size, bold) / 2, y, text, size, bold, color)

    def wrapped(self, x, top, text, size=11, max_width=400, bold=False, color=None, leading=1.35):
        """Draw wrapped text from top downward; returns the y below the last line."""
        y = top
        for line in pdf_design.wrap_text(_scrub(text), size, max_width, bold):
            self.text(x, y, line, size, bold, color)
            y -= size * leading
        return y

    def image(self, image, x, bottom, w, h):
        rgb = image.convert("RGB")
        self.images.append({"width": rgb.width, "height": rgb.height, "rgb": rgb.tobytes()})
        name = f"Im{len(self.images) - 1}"
        self._ops().append(
            f"q {w:.1f} 0 0 {h:.1f} {x:.1f} {bottom:.1f} cm /{name} Do Q")

    def _footer(self, page_number, total):
        self.line(MARGIN, 44, self.w - MARGIN, 44, color=LINE, width=0.6)
        self.text(MARGIN, 30, "Chilli Gavva · owner weekly report · counts only", 8, color=MUTED)
        self.text_right(self.w - MARGIN, 30, f"Page {page_number} of {total}", 8, color=MUTED)

    def _footer_ops(self, page_number, total):
        original = self.pages
        self.pages = [[]]
        try:
            self._footer(page_number, total)
            return self.pages[0]
        finally:
            self.pages = original

    def save(self, path, title="Weekly report"):
        saved_pages = self.pages
        if not saved_pages:
            raise ValueError("cannot save a report PDF with no pages")
        total = len(saved_pages)
        for number, ops in enumerate(saved_pages, 1):
            ops.extend(self._footer_ops(number, total))
        self.pages = saved_pages
        objects = [None, None,
                   "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                   "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
        image_ids = []
        for image in self.images:
            data = zlib.compress(image["rgb"], 6)
            objects.append(
                b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
                b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n"
                % (image["width"], image["height"], len(data)) + data + b"\nendstream")
            image_ids.append(len(objects))
        xobject = " ".join(f"/Im{i} {obj_id} 0 R" for i, obj_id in enumerate(image_ids))
        resources = "<< /Font << /F1 3 0 R /F2 4 0 R >>"
        if xobject:
            resources += f" /XObject << {xobject} >>"
        resources += " >>"

        kids = []
        for ops in self.pages:
            stream = "\n".join(ops).encode("latin-1")
            content_id = len(objects) + 1
            objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
            page_id = len(objects) + 1
            objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.w:g} {self.h:g}] "
                           f"/Resources {resources} /Contents {content_id} 0 R >>")
            kids.append(f"{page_id} 0 R")

        objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
        objects[0] = "<< /Type /Catalog /Pages 2 0 R >>"
        info_id = len(objects) + 1
        objects.append(f"<< /Title ({pdf_design._pdf_text(_scrub(title))}) "
                       f"/Author (Chilli Gavva) /Creator (Chilli Gavva robot) /Producer (report_pdf.py) >>")
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for index, obj in enumerate(objects, 1):
            offsets.append(len(out))
            data = obj if isinstance(obj, bytes) else obj.encode("latin-1")
            out += f"{index} 0 obj\n".encode() + data + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(bytes(out))


def _fmt(value):
    return f"{int(value):,}"


def _kpi(pdf, x, y, w, h, label, value, colour=ACCENT):
    pdf.fill(x, y, w, h, PAPER)
    pdf.rect(x, y, w, h, LINE, 0.6)
    pdf.text(x + 12, y + h - 26, _fmt(value), 22, True, colour)
    pdf.text(x + 12, y + 12, label, 9, False, MUTED)


def _cover(pdf, data):
    pdf.new_page()
    pdf.fill(0, PAGE_H - 250, PAGE_W, 250, ACCENT)
    pdf.text(MARGIN, PAGE_H - 92, "Weekly Report", 34, True, "#ffffff")
    pdf.text(MARGIN, PAGE_H - 125, data["week"], 15, False, "#d3e8e6")
    pdf.text(MARGIN, PAGE_H - 170, "Chilli Gavva robot · owner copy", 12, False, "#ffffff")
    pdf.text(MARGIN, PAGE_H - 190, "Counts only. No money amounts. Nothing auto-published.", 10, False,
             "#d3e8e6")

    pdf.text(MARGIN, PAGE_H - 300, "The week at a glance", 18, True, INK)
    boxes = [
        ("Drafts made", data["drafts_made"]),
        ("Packs built", data["packs_built"]),
        ("Runs OK", data["runs_ok"]),
        ("READY", data["ready"]),
    ]
    width = (PAGE_W - 2 * MARGIN - 3 * 12) / 4
    y = PAGE_H - 430
    for index, (label, value) in enumerate(boxes):
        _kpi(pdf, MARGIN + index * (width + 12), y, width, 84, label, value)

    pdf.text(MARGIN, y - 54, "What this file is", 15, True, INK)
    intro = ("This report is built from the robot's own database and post plan. "
             "It shows what happened this week, what needs a fix, and the plan for next week. "
             "The owner still approves every draft and uploads every product by hand.")
    y = pdf.wrapped(MARGIN, y - 78, intro, 11, PAGE_W - 2 * MARGIN, color=INK)

    pdf.fill(MARGIN, y - 56, PAGE_W - 2 * MARGIN, 48, ACCENT_SOFT)
    pdf.text(MARGIN + 16, y - 34, "Owner decision: review approval queue and NEEDS FIX packs first.", 11,
             True, ACCENT)

    pdf.text(MARGIN, 82, f"Week key: {data['week_key']} · generated for {data['generated_for']}", 9,
             False, MUTED)


def _numbers(pdf, data):
    pdf.new_page()
    pdf.text(MARGIN, PAGE_H - 80, "This week in numbers", 25, True, INK)
    pdf.text(MARGIN, PAGE_H - 103, data["week"], 11, False, MUTED)

    items = [
        ("Drafts made", data["drafts_made"]),
        ("Approved", data["drafts_approved"]),
        ("Rejected", data["drafts_rejected"]),
        ("Packs built", data["packs_built"]),
        ("READY", data["ready"]),
        ("NEEDS FIX", data["needs_fix"]),
        ("Posts planned", data["posts_planned"]),
        ("Posts done", data["posts_done"]),
    ]
    gap = 10
    width = (PAGE_W - 2 * MARGIN - 3 * gap) / 4
    top = PAGE_H - 152
    for index, (label, value) in enumerate(items):
        x = MARGIN + (index % 4) * (width + gap)
        y = top - (index // 4) * 78
        _kpi(pdf, x, y - 64, width, 64, label, value, ACCENT if index % 3 else INK)

    chart_labels = ["Drafts", "Approved", "Rejected", "Packs", "Ready", "Fix", "Posts", "Done"]
    chart_values = [data["drafts_made"], data["drafts_approved"], data["drafts_rejected"],
                    data["packs_built"], data["ready"], data["needs_fix"],
                    data["posts_planned"], data["posts_done"]]
    chart = report_charts.bar_chart(chart_labels, chart_values, title="Activity counts")
    pdf.image(chart, MARGIN, 150, PAGE_W - 2 * MARGIN, 300)
    pdf.text(MARGIN, 120, "Every value is a count of rows or real files. No revenue or price is shown.",
             9, False, MUTED)


def _trends(pdf, data):
    pdf.new_page()
    pdf.text(MARGIN, PAGE_H - 80, "Trends and decisions", 25, True, INK)
    pdf.text(MARGIN, PAGE_H - 103, "Robot runs per day and the owner's approval split", 11, False, MUTED)

    line = report_charts.line_chart(data["run_days"], data["run_values"], title="Robot runs per day")
    pdf.image(line, MARGIN, PAGE_H - 470, PAGE_W - 2 * MARGIN, 250)

    approved = data["drafts_approved"]
    rejected = data["drafts_rejected"]
    pending = data["drafts_pending"]
    donut = report_charts.donut_chart(
        ["Approved", "Rejected", "Pending"], [approved, rejected, pending],
        title="Draft decisions", width=860, height=400)
    pdf.image(donut, MARGIN, PAGE_H - 720, PAGE_W - 2 * MARGIN, 190)

    y = PAGE_H - 755
    pdf.text(MARGIN, y, f"Approved this week: {_fmt(approved)}   Rejected: {_fmt(rejected)}   "
                        f"Pending now: {_fmt(pending)}", 11, True, INK)
    pdf.text(MARGIN, y - 22, f"Runs this week: {_fmt(data['runs_ok'])} OK, "
                             f"{_fmt(data['runs_failed'])} failed.", 11, False, MUTED)


def _providers_page(pdf, data):
    pdf.new_page()
    pdf.text(MARGIN, PAGE_H - 80, "AI calls and failures", 25, True, INK)
    pdf.text(MARGIN, PAGE_H - 103, "Calls per provider (counts only) and failed task steps", 11, False, MUTED)

    providers = sorted(data["providers"].items())
    labels = [name for name, _ in providers] or ["No data yet"]
    values = [item["total"] for _, item in providers] or [0]
    chart = report_charts.bar_chart([_scrub(x)[:14] for x in labels], values,
                                    title="AI calls per provider")
    pdf.image(chart, MARGIN, PAGE_H - 470, PAGE_W - 2 * MARGIN, 250)

    y = PAGE_H - 510
    pdf.text(MARGIN, y, "Provider detail", 15, True, INK)
    y -= 24
    if providers:
        for name, item in providers:
            ok = item["ok"]
            fail = item["fail"]
            status = "UP" if ok >= fail else "DOWN"
            pdf.text(MARGIN, y, f"{_scrub(name)[:28]} · ok {_fmt(ok)} · fail {_fmt(fail)} · {status}",
                     10, False, INK)
            y -= 17
    else:
        pdf.text(MARGIN, y, "No provider calls recorded for this week.", 10, False, MUTED)
        y -= 17

    y -= 12
    pdf.text(MARGIN, y, "Failed steps", 15, True, INK)
    y -= 24
    if data["failures_by_step"]:
        for step, count in data["failures_by_step"][:8]:
            pdf.text(MARGIN, y, f"{_scrub(step)[:40]} · {_fmt(count)} failed", 10, False, INK)
            y -= 17
    else:
        pdf.text(MARGIN, y, "No failed task steps this week.", 10, False, MUTED)
        y -= 17
    pdf.text(MARGIN, 82, "Provider names are shown as labels; no keys, tokens or account details are included.",
             9, False, MUTED)


def _actions(pdf, data):
    pdf.new_page()
    pdf.text(MARGIN, PAGE_H - 80, "Wins and problems", 25, True, INK)
    pdf.text(MARGIN, PAGE_H - 103, "Top 3 wins and top 3 problems with a fix for each", 11, False, MUTED)

    y = PAGE_H - 150
    pdf.text(MARGIN, y, "Top 3 wins", 16, True, ACCENT)
    y -= 26
    for line in _ranked_wins_text(data["wins"]):
        y = pdf.wrapped(MARGIN, y, line, 11, PAGE_W - 2 * MARGIN, color=INK) - 10

    y -= 12
    pdf.line(MARGIN, y + 8, PAGE_W - MARGIN, y + 8, color=LINE)
    y -= 8
    pdf.text(MARGIN, y, "Top 3 problems and fixes", 16, True, "#c4657a")
    y -= 28
    for line in _problems_text(data["problems"]):
        y = pdf.wrapped(MARGIN, y, line, 11, PAGE_W - 2 * MARGIN, color=INK) - 6

    y -= 8
    pdf.fill(MARGIN, y - 54, PAGE_W - 2 * MARGIN, 46, ACCENT_SOFT)
    pdf.text(MARGIN + 14, y - 32, "Fix the first problem on this page before adding new work.", 11,
             True, ACCENT)


def _plan(pdf, data):
    pdf.new_page()
    pdf.text(MARGIN, PAGE_H - 80, "Next week plan", 25, True, INK)
    pdf.text(MARGIN, PAGE_H - 103, "Small, safe steps in Tenglish for the owner", 11, False, MUTED)

    y = PAGE_H - 160
    for index, line in enumerate(data["next_plan"], 1):
        pdf.fill(MARGIN, y - 7, 18, 18, ACCENT)
        pdf.text_center(MARGIN + 9, y, str(index), 10, True, "#ffffff")
        y = pdf.wrapped(MARGIN + 30, y, line, 12, PAGE_W - 2 * MARGIN - 30, color=INK) - 18

    y -= 10
    pdf.line(MARGIN, y + 10, PAGE_W - MARGIN, y + 10, color=LINE)
    y -= 20
    pdf.text(MARGIN, y, "Owner checklist", 15, True, INK)
    y -= 26
    checklist = [
        "Approval queue lo unna drafts review cheyandi.",
        "NEEDS FIX packs files check chesi retry cheyandi.",
        "Posts publish chesi done mark cheyandi.",
        "Nothing publishes, sells or emails by itself.",
    ]
    for item in checklist:
        pdf.text(MARGIN, y, f"- {_scrub(item)}", 11, False, INK)
        y -= 20

    y -= 20
    pdf.fill(MARGIN, 116, PAGE_W - 2 * MARGIN, 58, PAPER)
    pdf.rect(MARGIN, 116, PAGE_W - 2 * MARGIN, 58, LINE)
    pdf.text(MARGIN + 16, 146, f"Next report: the Sunday routine can call report_pdf.build_weekly_report().",
             10, False, MUTED)
    pdf.text(MARGIN + 16, 128, f"Week key: {data['week_key']} · owner decides every upload.", 10,
             False, MUTED)


def render_weekly_report(data, out_path, now=None):
    """Render 6 pages from a collected data dict; returns the written path."""
    pdf = _ReportPdf()
    _cover(pdf, data)
    _numbers(pdf, data)
    _trends(pdf, data)
    _providers_page(pdf, data)
    _actions(pdf, data)
    _plan(pdf, data)
    pdf.save(out_path, title=f"Weekly report {data['week_key']}")
    return Path(out_path)


def report_filename(now=None):
    bounds = _week_bounds(now)
    return f"weekly-report-{bounds['iso_year']}-W{bounds['iso_week']:02d}.pdf"


def build_weekly_report(db_path=None, now=None, out_dir=None, plan_path=None):
    """Collect data and write the weekly PDF. Safe for the Sunday routine to call."""
    data = collect_weekly_data(db_path=db_path, now=now, plan_path=plan_path)
    directory = Path(out_dir) if out_dir else Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / report_filename(now)
    render_weekly_report(data, path, now=now)
    return {"path": str(path), "filename": path.name, "data": data, "pages": 6}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Build the owner's weekly PDF report.")
    parser.add_argument("--db", default=None, help="SQLite database path")
    parser.add_argument("--out-dir", default=None, help="output folder (default system temp)")
    parser.add_argument("--plan", default=None, help="post_plan.json path")
    args = parser.parse_args(argv)
    result = build_weekly_report(db_path=args.db, out_dir=args.out_dir, plan_path=args.plan)
    print(result["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
