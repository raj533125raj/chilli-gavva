"""product_builder.py — Product Builder agent: turns APPROVED niche drafts into ready-to-sell files.

For each approved draft from a niche agent ("[Budget Sheets Agent] ..." etc.) that has no product
yet, the large model writes a JSON product spec, and this module builds the real files:

  Budget Sheets / Freelancer Trackers -> .xlsx workbook (Instructions tab, data tabs, formulas)
  ADHD Planners                       -> printable PDF in A4 and US Letter
  Notion Templates                    -> Notion import .zip (Markdown + CSV databases) + import guide
  Resume Bundle                       -> resume .docx + cover letter .docx + job tracker .xlsx

Every product also gets listing.md (title, description, 5 tags, USD price, buyer, platform),
README.md (instructions), cover_image_idea.txt, LICENSE.txt (personal use only) and listing images
(cover.png, preview-1..3.png, pinterest.png, made by mockups.py). Output: exported_content/products/<id>-<name>/.

The product files use only the Python standard library (plain Office Open XML / PDF; planner pages are
drawn by pdf_design.py in the owner-approved Style A / Style B). The listing images need two free tools:
Pillow and poppler (see mockups.py). Nothing is uploaded, published or sold: a human uploads the files.

Usage:
    python3 product_builder.py            # build up to 2 new products
    python3 product_builder.py --max 1
    python3 product_builder.py --list     # show what was built
"""
import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
import zipfile
from xml.sax.saxutils import escape

import guardrails

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_DIR = os.path.join(BASE_DIR, "exported_content", "products")
MAX_TRIES = 3
MAX_BUILDS_PER_RUN = 2

NICHES = {
    "Budget Sheets Agent": {"kind": "sheets", "price": 7, "platform": "Gumroad",
                            "buyer": "adults who want a simple monthly money plan",
                            "tags": ["budget spreadsheet", "google sheets budget", "money tracker",
                                     "monthly budget", "budget template"]},
    "Freelancer Trackers Agent": {"kind": "sheets", "price": 14, "platform": "Gumroad",
                                  "buyer": "freelancers and small online shop owners",
                                  "tags": ["freelancer tracker", "invoice tracker", "small business sheet",
                                           "profit calculator", "google sheets template"]},
    "ADHD Planners Agent": {"kind": "planner", "price": 8, "platform": "Gumroad",
                            "buyer": "neurodivergent adults who like simple printable planners",
                            "tags": ["adhd planner", "printable planner", "daily planner",
                                     "routine planner", "focus planner"]},
    "Notion Templates Agent": {"kind": "notion", "price": 15, "platform": "Gumroad",
                               "buyer": "students, freelancers and creators who use Notion",
                               "tags": ["notion template", "notion planner", "notion dashboard",
                                        "productivity template", "notion system"]},
    "2027 Planners Agent": {"kind": "dated_planner", "price": 9, "platform": "Gumroad",
                            "buyer": "one clear buyer, for example teachers or small business owners",
                            "tags": ["2027 planner", "printable planner", "monthly planner", "2027 calendar",
                                     "dated planner"]},
    "Resume Bundle Agent": {"kind": "resume", "price": 12, "platform": "Gumroad",
                            "buyer": "job seekers who want a clean ATS-friendly resume",
                            "tags": ["ats resume", "resume template", "cover letter template",
                                     "job tracker", "word resume"]},
}
# Claims a product must never make; checked on the listing title, description and instructions.
RISKY_PATTERNS = [r"\bguaranteed\b", r"\bget hired\b", r"\bland (?:a|your) (?:dream )?job\b", r"\bjob offers?\b",
                  r"\bcures?\b", r"\bheals?\b", r"\btreats? adhd\b", r"\bdiagnos[ie]s\b", r"\bpassive income\b",
                  r"\bget rich\b", r"\bearn \$", r"\brisk[- ]free\b", r"\bfish", r"\bshrimp", r"\baquaculture\b"]


# ---- bookkeeping --------------------------------------------------------------------
def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS product_builds (
        approval_id INTEGER PRIMARY KEY, status TEXT, tries INTEGER DEFAULT 0,
        folder TEXT, note TEXT, ts TEXT)""")
    return conn


def _record(db_path, approval_id, status, folder="", note="", add_try=False):
    conn = connect(db_path)
    row = conn.execute("SELECT tries FROM product_builds WHERE approval_id = ?", (approval_id,)).fetchone()
    tries = (row[0] if row else 0) + (1 if add_try else 0)
    conn.execute("INSERT OR REPLACE INTO product_builds (approval_id, status, tries, folder, note, ts) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (approval_id, status, tries, folder, note[:300], datetime.datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return tries


def builds(db_path=None):
    conn = connect(db_path)
    rows = conn.execute("SELECT approval_id, status, tries, folder, note FROM product_builds "
                        "ORDER BY approval_id").fetchall()
    conn.close()
    return [dict(zip(("approval_id", "status", "tries", "folder", "note"), r)) for r in rows]


def niche_of(title):
    """Return the niche agent name from a draft title like 'Draft: [Budget Sheets Agent] ...'."""
    m = re.search(r"\[([^\]]+ Agent)\]", title or "")
    return m.group(1) if m and m.group(1) in NICHES else None


def _todo(db_path=None, out_dir=None):
    """Approved drafts that still need a product, oldest first."""
    out_dir = out_dir or PRODUCTS_DIR
    conn = connect(db_path)
    rows = conn.execute("SELECT a.id, a.title, a.payload FROM approvals a "
                        "LEFT JOIN product_builds b ON b.approval_id = a.id "
                        "WHERE a.status = 'approved' AND a.kind = 'public_draft' "
                        "AND (b.status IS NULL OR b.status = 'retry' "
                        "     OR (b.status = 'failed' AND b.note = ?)) ORDER BY a.id", (AI_DOWN,)).fetchall()
    conn.close()
    existing = set(os.listdir(out_dir)) if os.path.isdir(out_dir) else set()
    out = []
    for aid, title, payload in rows:
        folder = next((d for d in existing if d.startswith(f"{aid}-")), None)
        if folder:  # files already on disk (e.g. DB restored from an older copy): never rebuild
            _record(db_path, aid, "built", folder, "found existing folder")
            continue
        out.append({"id": aid, "title": title or "", "payload": payload or ""})
    return out


# ---- spec from the large model ---------------------------------------------------------
_SHEET_EXAMPLE = {"name": "Income Log", "columns": [
    {"name": "Date", "type": "date"}, {"name": "Client", "type": "text"},
    {"name": "Amount", "type": "money"}, {"name": "Status", "type": "text", "options": ["Paid", "Unpaid"]},
    {"name": "Due Date", "type": "date"}],
    "rows": [["2026-01-05", "Sample Client", 450, "Unpaid", "2026-01-20"]],
    "overdue": {"due": "Due Date", "status": "Status", "done": "Paid"},
    "balance": {"in": "Amount", "out": ""}}
EXAMPLES = {
    "dated_planner": {"pages": [{"title": "Class Roster", "subtitle": "One line per student",
                                 "sections": [{"heading": "Students", "type": "lines", "count": 12}]}]},
    "sheets": {"sheets": [_SHEET_EXAMPLE]},
    "planner": {"daily_page": True,
                "pages": [{"title": "Energy-Based Daily Plan", "subtitle": "Plan by energy, not by the clock",
                           "sections": [{"heading": "Energy check", "type": "checklist",
                                         "items": ["High", "Medium", "Low"]},
                                        {"heading": "Top 3 tasks", "type": "lines", "count": 3},
                                        {"heading": "Time blocks", "type": "schedule",
                                         "slots": ["Morning", "Midday", "Afternoon", "Evening"]}]}]},
    "notion": {"pages": [{"title": "Start Here", "markdown": "# Start Here\n\nHow to use this template..."}],
               "databases": [{"name": "Clients", "columns": ["Name", "Status", "Next Follow-up", "Notes"],
                              "rows": [["Sample Client", "Lead", "2026-01-10", "Sent intro email"]]}]},
    "resume": {"resume": {"sections": [{"heading": "Professional Summary", "bullets": ["[Two lines about you]"]},
                                       {"heading": "Experience",
                                        "bullets": ["[Job Title], [Company], [Dates]",
                                                    "[Result you achieved, with a number]"]}]},
               "cover_letter": {"paragraphs": ["Dear [Hiring Manager Name],", "[Why this role]",
                                               "Kind regards,", "[Your Name]"]},
               "tracker": _SHEET_EXAMPLE},
}
KIND_RULES = {
    "sheets": "Give 2-4 data sheets. Column types: text, number, money, date. Use 'overdue' only when the sheet "
              "has a due-date and a status column, and 'balance' only for a money in/out ledger "
              "(use \"\" when a side is missing). 3-6 sample rows per sheet, dates as YYYY-MM-DD.",
    "planner": "Give 3-6 printable pages. Section types: checklist (items), lines (count 1-12), "
               "schedule (slots), notes (count of lines). Set daily_page to true for a daily or time-blindness "
               "planner: code then adds a fixed 'Today' page (time anchors, top 3, now/next/later, waiting for, "
               "wins, guess vs real time, 15-minute focus blocks, tomorrow's first step, brain dump). Code also "
               "adds a cover and a 'How to use' page to every planner.",
    "notion": "Give 2-4 pages in Markdown and 1-3 databases with 3-6 sample rows each.",
    "dated_planner": ("The calendar part is FIXED and made by code: a cover, a 2027 year-at-a-glance page and 12 "
                      "monthly calendar pages (January to December 2027, weeks start on Sunday, each with the boxes "
                      "This month's focus, Key dates and Notes), plus a cover and a How to use page. You only "
                      "choose 1-4 extra planner pages for ONE clear buyer "
                      "(checklist, lines, schedule or notes sections). Printable PDF in A4 and US Letter. "
                      "The listing may ONLY promise these things. Never promise weekly or daily dated pages, "
                      "hyperlinks, fillable fields, stickers, a digital/tablet version, dashboards or formulas."),
    "resume": "Use [placeholders] for personal details. Single column, standard headings, no tables. "
              "The tracker is a job application sheet with an overdue follow-up flag.",
}


def spec_prompt(niche, draft):
    kind = NICHES[niche]["kind"]
    example = {"name": "Short Product Name", "short_name": "short-product-name",
               "listing": {"title": "Listing title", "description": "2-4 short paragraphs",
                           "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"], "price_usd": NICHES[niche]["price"],
                           "buyer": NICHES[niche]["buyer"], "platform": NICHES[niche]["platform"]},
               "instructions": ["Step 1 ...", "Step 2 ..."],
               "cover_image_idea": "One sentence describing the cover image",
               **EXAMPLES[kind]}
    return ("You are the Product Builder for a small digital-product shop selling to global buyers in USD.\n"
            "Turn the APPROVED draft below into a complete product spec.\n"
            f"Product type: {kind}. {KIND_RULES[kind]}\n"
            "Rules: plain honest English; no income, health, medical or job promises; no made-up statistics.\n"
            "Reply with ONLY one JSON object shaped like this example (replace every value):\n"
            + json.dumps(example, indent=1) + "\n\nApproved draft:\n" + draft[:5000])


def _clean(value, limit=200):
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def parse_spec(text, niche):
    """Read and normalise the model's JSON spec. Returns (spec, None) or (None, reason)."""
    if not isinstance(text, str):
        return None, "no reply from the model"
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, "reply has no JSON object"
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None, "reply is not valid JSON"
    if not isinstance(data, dict) or not isinstance(data.get("listing"), dict):
        return None, "spec has no listing"
    info, kind = NICHES[niche], NICHES[niche]["kind"]
    listing = data["listing"]
    title = _clean(listing.get("title"), 140) or _clean(data.get("name"), 140)
    description = str(listing.get("description") or "").strip()[:3000]
    if not title or not description:
        return None, "listing needs a title and a description"
    tags = [_clean(t, 40) for t in (listing.get("tags") or []) if _clean(t, 40)]
    tags = (tags + [t for t in info["tags"] if t not in tags])[:5]
    try:
        price = round(float(str(listing.get("price_usd", info["price"])).replace("$", "")), 2)
    except ValueError:
        price = info["price"]
    if not 1 <= price <= 99:
        price = info["price"]
    spec = {"name": _clean(data.get("name") or title, 80), "kind": kind, "niche": niche,
            "short_name": _slug(data.get("short_name") or data.get("name") or title),
            "listing": {"title": title, "description": description, "tags": tags, "price_usd": price,
                        "buyer": _clean(listing.get("buyer"), 160) or info["buyer"],
                        "platform": info["platform"]},   # always Gumroad (the store we use)
            "instructions": [_clean(s, 300) for s in (data.get("instructions") or []) if _clean(s)][:12],
            "cover_image_idea": _clean(data.get("cover_image_idea"), 400)}
    checker = {"sheets": _norm_sheets, "planner": _norm_pages, "notion": _norm_notion, "resume": _norm_resume,
               "dated_planner": _norm_dated}[kind]
    reason = checker(data, spec)
    if reason:
        return None, reason
    risky = _risky_text(" ".join([title, description] + spec["instructions"]))
    if risky:
        return None, f"risky claim in listing: {risky}"
    return spec, None


def _risky_text(text):
    lower = text.lower()
    for pattern in RISKY_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            return m.group(0)
    return None


def _norm_sheet(raw):
    if not isinstance(raw, dict):
        return None
    cols = []
    for c in (raw.get("columns") or [])[:12]:
        c = c if isinstance(c, dict) else {"name": c}
        name = _clean(c.get("name"), 40)
        if name and name not in [x["name"] for x in cols]:
            ctype = c.get("type") if c.get("type") in ("text", "number", "money", "date") else "text"
            opts = [_clean(o, 40) for o in (c.get("options") or []) if _clean(o, 40)][:10]
            cols.append({"name": name, "type": ctype, "options": opts})
    if not cols:
        return None
    rows = [r for r in (raw.get("rows") or []) if isinstance(r, list)][:30]
    names = [c["name"] for c in cols]
    overdue = raw.get("overdue") if isinstance(raw.get("overdue"), dict) else {}
    balance = raw.get("balance") if isinstance(raw.get("balance"), dict) else {}
    sheet = {"name": _sheet_name(raw.get("name") or "Sheet"), "columns": cols, "rows": rows}
    if overdue.get("due") in names and overdue.get("status") in names:
        sheet["overdue"] = {"due": overdue["due"], "status": overdue["status"],
                            "done": _clean(overdue.get("done") or "Done", 40)}
    if balance.get("in") in names or balance.get("out") in names:
        sheet["balance"] = {"in": balance.get("in") if balance.get("in") in names else "",
                            "out": balance.get("out") if balance.get("out") in names else ""}
    return sheet


def _norm_sheets(data, spec):
    sheets = [s for s in (_norm_sheet(r) for r in (data.get("sheets") or [])[:5]) if s]
    if not sheets:
        return "spec has no usable sheets"
    seen = set()
    for s in sheets:  # sheet names must be unique in a workbook
        base, n = s["name"], 2
        while s["name"].lower() in seen | {"instructions", "dashboard"}:
            s["name"] = _sheet_name(f"{base[:27]} {n}")
            n += 1
        seen.add(s["name"].lower())
    spec["sheets"] = sheets
    return None


def _norm_pages(data, spec):
    pages = []
    for p in (data.get("pages") or [])[:12]:
        if not isinstance(p, dict) or not _clean(p.get("title")):
            continue
        sections = []
        for s in (p.get("sections") or [])[:8]:
            if not isinstance(s, dict):
                continue
            stype = s.get("type") if s.get("type") in ("checklist", "lines", "schedule", "notes") else "lines"
            items = [_clean(i, 90) for i in (s.get("items") or s.get("slots") or []) if _clean(i, 90)][:14]
            try:
                count = max(1, min(12, int(s.get("count") or 3)))
            except (TypeError, ValueError):
                count = 3
            sections.append({"heading": _clean(s.get("heading"), 80), "type": stype, "items": items, "count": count})
        if sections:
            pages.append({"title": _clean(p["title"], 80), "subtitle": _clean(p.get("subtitle"), 120),
                          "sections": sections})
    if not pages:
        return "spec has no usable planner pages"
    spec["pages"] = pages
    if spec.get("kind") == "planner":
        flag = data.get("daily_page")
        if flag is None:   # the model forgot: a daily or time-blindness planner gets the Today page
            flag = bool(re.search(r"\bdaily\b|time[- ]blind", spec["listing"]["title"].lower()))
        spec["daily_page"] = bool(flag)
    return None


def _norm_dated(data, spec):
    import pdf_design
    spec["monthly_sections"] = list(pdf_design.MONTH_BOXES)   # fixed by the design, the same every month
    spec["year"] = PLANNER_YEAR
    if data.get("pages"):
        if _norm_pages(data, spec):
            spec["pages"] = []
        spec["pages"] = spec.get("pages", [])[:4]
    else:
        spec["pages"] = []
    return None


def _norm_notion(data, spec):
    pages = [{"title": _clean(p.get("title"), 80), "markdown": str(p.get("markdown") or "")[:6000]}
             for p in (data.get("pages") or [])[:6] if isinstance(p, dict) and _clean(p.get("title"))]
    dbs = []
    for d in (data.get("databases") or [])[:4]:
        if not isinstance(d, dict):
            continue
        cols = [_clean(c.get("name") if isinstance(c, dict) else c, 40) for c in (d.get("columns") or [])][:12]
        cols = [c for c in cols if c]
        if _clean(d.get("name")) and cols:
            rows = [[_clean(v, 200) for v in r][:len(cols)] for r in (d.get("rows") or []) if isinstance(r, list)][:30]
            dbs.append({"name": _clean(d["name"], 60), "columns": cols, "rows": rows})
    if not dbs and not pages:
        return "spec has no Notion pages or databases"
    spec["pages"], spec["databases"] = pages, dbs
    return None


def _norm_resume(data, spec):
    resume = data.get("resume") if isinstance(data.get("resume"), dict) else {}
    sections = [{"heading": _clean(s.get("heading"), 60),
                 "bullets": [_clean(b, 300) for b in (s.get("bullets") or []) if _clean(b)][:10]}
                for s in (resume.get("sections") or [])[:10] if isinstance(s, dict) and _clean(s.get("heading"))]
    letter = data.get("cover_letter") if isinstance(data.get("cover_letter"), dict) else {}
    paragraphs = [str(p).strip()[:1200] for p in (letter.get("paragraphs") or []) if str(p).strip()][:10]
    if not sections or not paragraphs:
        return "spec needs resume sections and cover letter paragraphs"
    tracker = _norm_sheet(data.get("tracker")) or _norm_sheet(_DEFAULT_TRACKER)
    tracker["name"] = "Job Applications"
    spec["resume"], spec["cover_letter"], spec["tracker"] = sections, paragraphs, tracker
    return None


_DEFAULT_TRACKER = {"name": "Job Applications", "columns": [
    {"name": "Company", "type": "text"}, {"name": "Role", "type": "text"},
    {"name": "Date Applied", "type": "date"},
    {"name": "Status", "type": "text", "options": ["Applied", "Interview", "Offer", "Closed"]},
    {"name": "Follow-up Date", "type": "date"}, {"name": "Notes", "type": "text"}],
    "rows": [["Sample Company", "Sample Role", "2026-01-05", "Applied", "2026-01-12", "Sent thank-you note"]],
    "overdue": {"due": "Follow-up Date", "status": "Status", "done": "Closed"}}


def _slug(text, maxlen=40):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:maxlen].strip("-") or "product"


def _sheet_name(text):
    return re.sub(r"[\[\]:*?/\\']", "", _clean(text, 31)).strip() or "Sheet"


# ---- XLSX (Office Open XML, standard library only) -----------------------------------------
_XLSX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00"/><numFmt numFmtId="165" formatCode="yyyy-mm-dd"/></numFmts>
<fonts count="4"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font><font><b/><sz val="14"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2F5597"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="9">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="164" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>
<xf numFmtId="2" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""
S_HEADER, S_MONEY, S_DATE, S_WRAP, S_TITLE, S_BOLD, S_TOTAL_MONEY, S_TOTAL_NUM = 1, 2, 3, 4, 5, 6, 7, 8


def _col(n):
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _excel_date(value):
    try:
        d = datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    return (d - datetime.date(1899, 12, 30)).days


def _quote_sheet(name):
    return "'" + name.replace("'", "''") + "'"


class Workbook:
    """Tiny .xlsx writer: inline strings, numbers, formulas, styles, frozen header, dropdowns."""

    def __init__(self):
        self.sheets = []

    def add_sheet(self, name, widths=None, freeze=True):
        sheet = {"name": _sheet_name(name), "cells": {}, "widths": widths or [], "freeze": freeze,
                 "validations": []}
        self.sheets.append(sheet)
        return sheet

    @staticmethod
    def put(sheet, row, col, value, style=0, formula=None):
        sheet["cells"][(row, col)] = (value, style, formula)

    def _sheet_xml(self, sheet):
        rows = {}
        for (r, c), cell in sheet["cells"].items():
            rows.setdefault(r, []).append((c, cell))
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
               '<sheetViews><sheetView workbookViewId="0">']
        if sheet["freeze"]:
            out.append('<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                       '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>')
        out.append('</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>')
        if sheet["widths"]:
            out.append("<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                                          for i, w in enumerate(sheet["widths"])) + "</cols>")
        out.append("<sheetData>")
        for r in sorted(rows):
            out.append(f'<row r="{r}">')
            for c, (value, style, formula) in sorted(rows[r]):
                ref, s = f"{_col(c)}{r}", f' s="{style}"' if style else ""
                if formula:
                    out.append(f'<c r="{ref}"{s}><f>{escape(formula)}</f></c>')
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    out.append(f'<c r="{ref}"{s}><v>{value}</v></c>')
                elif value not in (None, ""):
                    out.append(f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">'
                               f'{escape(str(value))}</t></is></c>')
            out.append("</row>")
        out.append("</sheetData>")
        if sheet["validations"]:
            out.append(f'<dataValidations count="{len(sheet["validations"])}">')
            for sqref, options in sheet["validations"]:
                listing = escape('"' + ",".join(o.replace(",", " ") for o in options)[:250] + '"')
                out.append(f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="{sqref}">'
                           f'<formula1>{listing}</formula1></dataValidation>')
            out.append("</dataValidations>")
        out.append('<pageMargins left="0.5" right="0.5" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
                   "</worksheet>")
        return "".join(out)

    def save(self, path):
        n = len(self.sheets)
        types = "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/'
                        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(n))
        files = {
            "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + types + "</Types>",
            "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
            "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(f'<sheet name="{escape(s["name"])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                      for i, s in enumerate(self.sheets))
            + '</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>',
            "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/'
                      f'2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(n))
            + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/styles" Target="styles.xml"/></Relationships>',
            "xl/styles.xml": _XLSX_STYLES,
        }
        for i, s in enumerate(self.sheets):
            files[f"xl/worksheets/sheet{i + 1}.xml"] = self._sheet_xml(s)
        _write_zip(path, files)


def _write_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


def _instructions_sheet(wb, spec, extra=()):
    sheet = wb.add_sheet("Instructions", widths=[100], freeze=False)
    lines = [(spec["listing"]["title"], S_TITLE), ("", 0)]
    lines += [(f"{i}. {step}", S_WRAP) for i, step in enumerate(spec["instructions"] or
              ["Make a copy of this file (Google Sheets: File > Make a copy; Excel: Save As).",
               "Delete the sample rows and type your own.",
               "Cells with formulas update by themselves, so you do not need to edit them."], 1)]
    lines += [("", 0)] + [(e, S_WRAP) for e in extra]
    lines += [("", 0), ("Blue header rows stay visible while you scroll. Totals sit under each table.", S_WRAP),
              ("This template helps you organise your own numbers. It is not financial, tax or legal advice.", S_WRAP)]
    for r, (text, style) in enumerate(lines, 1):
        Workbook.put(sheet, r, 0, text, style)


def _data_sheet(wb, sheet_spec):
    """One table: header, sample rows, computed columns, then a TOTAL row. Returns total cell refs."""
    cols = list(sheet_spec["columns"])
    names = [c["name"] for c in cols]
    extra = []
    if sheet_spec.get("balance"):
        extra.append("Balance")
    if sheet_spec.get("overdue"):
        extra.append("Overdue?")
    widths = [max(12, min(40, len(n) + 6)) for n in names] + [14] * len(extra)
    sheet = wb.add_sheet(sheet_spec["name"], widths=widths)
    for c, name in enumerate(names + extra):
        Workbook.put(sheet, 1, c, name, S_HEADER)
    rows = sheet_spec["rows"] or [[""] * len(cols)]
    first, last = 2, len(rows) + 1
    for i, row in enumerate(rows):
        r = first + i
        for c, col in enumerate(cols):
            value = row[c] if c < len(row) else ""
            if col["type"] in ("money", "number"):
                try:
                    value = float(str(value).replace("$", "").replace(",", "")) if value != "" else ""
                except ValueError:
                    value = ""
                Workbook.put(sheet, r, c, value, S_MONEY if col["type"] == "money" else 0)
            elif col["type"] == "date":
                serial = _excel_date(value)
                Workbook.put(sheet, r, c, serial if serial is not None else _clean(value, 40),
                             S_DATE if serial is not None else 0)
            else:
                Workbook.put(sheet, r, c, _clean(value, 300))
        c = len(cols)
        if sheet_spec.get("balance"):
            b = sheet_spec["balance"]
            add = f"N({_col(names.index(b['in']))}{r})" if b["in"] else "0"
            sub = f"N({_col(names.index(b['out']))}{r})" if b["out"] else "0"
            prev = f"N({_col(c)}{r - 1})+" if r > first else ""
            Workbook.put(sheet, r, c, None, S_MONEY, f"{prev}{add}-{sub}")
            c += 1
        if sheet_spec.get("overdue"):
            o = sheet_spec["overdue"]
            due, status = f"{_col(names.index(o['due']))}{r}", f"{_col(names.index(o['status']))}{r}"
            done = o["done"].replace('"', "")
            Workbook.put(sheet, r, c, None, 0,
                         f'IF(AND(ISNUMBER({due}),{status}<>"{done}",{due}<TODAY()),"OVERDUE","")')
    for c, col in enumerate(cols):
        if col["options"]:
            sheet["validations"].append((f"{_col(c)}{first}:{_col(c)}{last + 200}", col["options"]))
    total_row, totals = last + 2, {}
    Workbook.put(sheet, total_row, 0, "TOTAL", S_BOLD)
    for c, col in enumerate(cols):
        if col["type"] in ("money", "number") and c > 0:
            Workbook.put(sheet, total_row, c, None, S_TOTAL_MONEY if col["type"] == "money" else S_TOTAL_NUM,
                         f"SUM({_col(c)}{first}:{_col(c)}{last})")
            totals[col["name"]] = (f"{_quote_sheet(sheet['name'])}!{_col(c)}{total_row}", col["type"])
    if sheet_spec.get("overdue"):
        oc = len(cols) + (1 if sheet_spec.get("balance") else 0)
        Workbook.put(sheet, total_row + 1, 0, "Overdue rows", S_BOLD)
        Workbook.put(sheet, total_row + 1, 1, None, S_BOLD, f'COUNTIF({_col(oc)}{first}:{_col(oc)}{last},"OVERDUE")')
        totals["Overdue rows"] = (f"{_quote_sheet(sheet['name'])}!B{total_row + 1}", "number")
    return totals


def build_workbook(path, spec, sheet_specs, extra_instructions=()):
    wb = Workbook()
    _instructions_sheet(wb, spec, extra_instructions)
    dashboard = wb.add_sheet("Dashboard", widths=[34, 18, 18])
    all_totals = [(s["name"], _data_sheet(wb, s)) for s in sheet_specs]
    Workbook.put(dashboard, 1, 0, "Summary", S_HEADER)
    Workbook.put(dashboard, 1, 1, "Value", S_HEADER)
    r = 2
    for name, totals in all_totals:
        for label, (ref, ctype) in totals.items():
            Workbook.put(dashboard, r, 0, f"{name}: {label}")
            Workbook.put(dashboard, r, 1, None, S_MONEY if ctype == "money" else 0, ref)
            r += 1
    if r == 2:
        Workbook.put(dashboard, 2, 0, "Add number or money columns to see totals here.")
    wb.save(path)


# ---- DOCX (Office Open XML, standard library only) ------------------------------------------
def build_docx(path, blocks, title=""):
    """blocks: list of (style, text) with style in Title, Heading1, Normal, Bullet."""
    paras = []
    for style, text in blocks:
        text = str(text)
        ppr = "" if style == "Normal" else f'<w:pPr><w:pStyle w:val="{"ListParagraph" if style == "Bullet" else style}"/></w:pPr>'
        if style == "Bullet":
            text = "• " + text
        paras.append(f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')
    body = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + "".join(paras)
            + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1080" w:right="1080" w:bottom="1080" '
            'w:left="1080" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>')

    def style(sid, name, size, bold=False, before=0, after=120, indent=0):
        return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
                f'<w:qFormat/><w:pPr><w:spacing w:before="{before}" w:after="{after}"/>'
                + (f'<w:ind w:left="{indent}"/>' if indent else "") + '</w:pPr><w:rPr>'
                + ("<w:b/>" if bold else "") + f'<w:sz w:val="{size}"/></w:rPr></w:style>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
              '<w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr>'
              '</w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
              + style("Title", "Title", 36, True, 0, 60) + style("Heading1", "heading 1", 26, True, 240, 80)
              + style("ListParagraph", "List Paragraph", 22, False, 0, 60, 360) + "</w:styles>")
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/'
        'vnd.openxmlformats-package.core-properties+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
        'core-properties" Target="docProps/core.xml"/></Relationships>',
        "word/_rels/document.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'styles" Target="styles.xml"/></Relationships>',
        "docProps/core.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + escape(title) + "</dc:title></cp:coreProperties>",
        "word/document.xml": body,
        "word/styles.xml": styles,
    }
    _write_zip(path, files)


# ---- PDF planners: the page design lives in pdf_design.py (owner-approved mockups, 2026-10-16) ----------
from pdf_design import (BOTTOM, PAGE_SIZES, PLANNER_YEAR, STYLES, TODAY_PAGE, WEEKDAYS_SUN, Pdf,  # noqa: F401
                        _pdf_text, _winansi, build_dated_planner_pdf, build_planner_pdf, month_cells, month_weeks,
                        style_for, text_width, wrap_text)


# ---- product folders -------------------------------------------------------------------
def _listing_md(spec):
    l = spec["listing"]
    return "\n".join([
        f"# {l['title']}", "",
        f"**Price:** {l['price_usd']:.2f} USD  ", f"**Buyer:** {l['buyer']}  ", f"**Platform:** {l['platform']}", "",
        "## Description", "", l["description"], "",
        "## Tags (5)", "", *[f"- {t}" for t in l["tags"]], "",
        "## Cover image idea", "", spec["cover_image_idea"] or "A clean flat-lay of the product on a laptop screen.",
        "", "---", f"Built by the Product Builder from approved draft #{spec['approval_id']}. "
        "Check the files, then upload them to the store yourself.", ""])


def _readme_md(spec, files, how_to):
    steps = spec["instructions"] or how_to
    return "\n".join([
        f"# {spec['name']}", "", "## What's inside", "", *[f"- `{f}`" for f in files], "",
        "## How to use", "", *[f"{i}. {s}" for i, s in enumerate(steps, 1)], "",
        "## Notes", "",
        "- This is a planning and organising tool for personal use. It does not give financial, medical, "
        "legal or career advice and does not promise any result.",
        "- Sample rows and placeholder text are examples: replace them with your own.", ""])


def _build_sheets(folder, spec):
    name = f"{spec['short_name']}.xlsx"
    build_workbook(os.path.join(folder, name), spec, spec["sheets"],
                   ["Works in Microsoft Excel, Google Sheets (File > Import) and Apple Numbers."])
    return [name], ["Open the .xlsx in Excel, or upload it to Google Drive and open it with Google Sheets.",
                    "Read the Instructions tab, then replace the sample rows with your own."]


def _build_planner(folder, spec):
    files = []
    for size in ("A4", "Letter"):
        name = f"{spec['short_name']}-{size}.pdf"
        build_planner_pdf(os.path.join(folder, name), spec, size)
        files.append(name)
    return files, ["Print the A4 or US Letter PDF at 100% scale (or use it in a PDF annotation app).",
                   "Print as many copies as you need for personal use."]


def _build_notion(folder, spec):
    name = f"{spec['short_name']}-notion-import.zip"
    files = {}
    for p in spec["pages"] or [{"title": spec["name"], "markdown": f"# {spec['name']}"}]:
        md = p["markdown"] if p["markdown"].lstrip().startswith("#") else f"# {p['title']}\n\n{p['markdown']}"
        files[f"{_slug(p['title'])}.md"] = md
    for d in spec["databases"]:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(d["columns"])
        w.writerows(d["rows"])
        files[f"{_slug(d['name'])}.csv"] = buf.getvalue()
    _write_zip(os.path.join(folder, name), files)
    guide = "\n".join([
        f"# How to import {spec['name']} into Notion", "",
        "1. Open Notion on a computer and go to the page where you want the template.",
        "2. Click **Import** in the left sidebar (or Settings > Import).",
        f"3. Choose **Text & Markdown** or **CSV**, then select the files from `{name}` "
        "(unzip it first if your browser does not open .zip files).",
        "4. Each .csv file becomes a Notion database and each .md file becomes a page.",
        "5. Change property types if you like (for example Status to a Select, dates to Date).", ""])
    with open(os.path.join(folder, "HOW_TO_IMPORT.md"), "w", encoding="utf-8") as f:
        f.write(guide)
    return [name, "HOW_TO_IMPORT.md"], ["Follow HOW_TO_IMPORT.md to bring the pages and databases into Notion."]


def _build_resume(folder, spec):
    base = spec["short_name"]
    resume = [("Title", "[Your Name]"), ("Normal", "[City, Country] | [Email] | [Phone] | [LinkedIn or portfolio]")]
    for s in spec["resume"]:
        resume.append(("Heading1", s["heading"]))
        resume += [("Bullet", b) for b in s["bullets"]]
    build_docx(os.path.join(folder, f"{base}-resume.docx"), resume, "Resume template")
    letter = [("Normal", "[Your Name] | [Email] | [Phone]"), ("Normal", "[Date]")]
    letter += [("Normal", p) for p in spec["cover_letter"]]
    build_docx(os.path.join(folder, f"{base}-cover-letter.docx"), letter, "Cover letter template")
    tracker = f"{base}-job-tracker.xlsx"
    build_workbook(os.path.join(folder, tracker), spec, [spec["tracker"]],
                   ["The Overdue? column shows OVERDUE when a follow-up date has passed and the status is not Closed."])
    return [f"{base}-resume.docx", f"{base}-cover-letter.docx", tracker], [
        "Open the .docx files in Word or Google Docs and replace every [placeholder].",
        "Keep the single-column layout and standard headings so applicant tracking systems read it well.",
        "Log each application in the job tracker and check the Overdue? column for follow-ups."]


LICENSE_TEXT = "Personal use only. Do not resell or share the files.\n"

def _build_dated_planner(folder, spec):
    files = []
    for size in ("A4", "Letter"):
        name = f"{spec['short_name']}-{size}.pdf"
        build_dated_planner_pdf(os.path.join(folder, name), spec, size)
        files.append(name)
    return files, ["Print the A4 or US Letter PDF at 100% scale (or use it in a PDF annotation app).",
                   "Every month of 2027 has its own calendar page; weeks start on Sunday."]


BUILDERS = {"sheets": _build_sheets, "planner": _build_planner, "notion": _build_notion, "resume": _build_resume,
            "dated_planner": _build_dated_planner}


def build_product(spec, approval_id, out_dir=None, folder_name=None):
    """Write all files for one spec. Returns the folder name."""
    out_dir = out_dir or PRODUCTS_DIR
    spec = dict(spec, approval_id=approval_id)
    folder_name = folder_name or f"{approval_id}-{spec['short_name']}"
    folder = os.path.join(out_dir, folder_name)
    os.makedirs(folder, exist_ok=True)
    files, how_to = BUILDERS[spec["kind"]](folder, spec)
    with open(os.path.join(folder, "listing.md"), "w", encoding="utf-8") as f:
        f.write(_listing_md(spec))
    with open(os.path.join(folder, "cover_image_idea.txt"), "w", encoding="utf-8") as f:
        f.write((spec["cover_image_idea"] or "A clean flat-lay of the product on a laptop screen.") + "\n")
    with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as f:
        f.write(LICENSE_TEXT)
    extra = ["listing.md", "cover_image_idea.txt", "LICENSE.txt"]
    try:  # listing images; a drawing problem never blocks the product files
        import mockups
        extra += mockups.write_mockups(folder, spec)
    except Exception as exc:
        print(f"Product Builder: listing images skipped for #{approval_id}: {type(exc).__name__}", file=sys.stderr)
    with open(os.path.join(folder, "README.md"), "w", encoding="utf-8") as f:
        f.write(_readme_md(spec, files + extra, how_to))
    return folder_name


# ---- listing images for products built before images existed ---------------------------
_PDF_OP = re.compile(r"BT /(F1|F2) ([\d.]+) Tf [\d.]+ ([\d.]+) Td \((.*)\) Tj ET$")
_CODE_PAGES = {"How to use", "Today"}


def _pdf_unescape(s):
    return s.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")


def planner_pages_from_pdf(path):
    """Read page titles, headings and items back from a planner PDF written by build_planner_pdf."""
    with open(path, "rb") as f:
        data = f.read().decode("latin-1")
    pages = []
    streams = re.findall(r"stream\n(.*?)\nendstream", data, re.S)
    if "/ChilliStyle (" in data:      # the new design: page 1 is the cover, code-made pages are skipped
        streams = streams[1:]
    for stream in streams:
        page, section, boxed = None, None, False
        for op in stream.splitlines():
            if op.endswith(" re S 0 G"):
                boxed = True
                continue
            m = _PDF_OP.match(op)
            if not m:
                continue
            font, size = m.group(1), float(m.group(2))
            text = _pdf_unescape(m.group(4)).encode("latin-1", "replace").decode("cp1252", "replace")
            if font == "F2" and size >= 18:
                if page is not None and not page["sections"] and not page["subtitle"]:
                    page["title"] += " " + text.replace(" (cont.)", "")     # a title on two lines
                else:
                    page = {"title": text.replace(" (cont.)", ""), "subtitle": "", "sections": []}
                    pages.append(page)
            elif page is None or size <= 8:
                continue   # footer
            elif font == "F2" and size >= 10.5:
                section = {"heading": text, "type": "lines", "items": [], "count": 3}
                page["sections"].append(section)
            elif section is not None and (boxed or font == "F2"):
                section["type"] = "checklist" if boxed else "schedule"
                section["items"].append(text)
            elif size in (10, 10.5) and not page["sections"]:
                page["subtitle"] = text
            boxed = False
    merged = []
    pages = [p for p in pages if not ("/ChilliStyle (" in data and p["title"] in _CODE_PAGES)]
    for p in pages:   # "(cont.)" pages belong to the page before
        if merged and merged[-1]["title"] == p["title"]:
            merged[-1]["sections"] += p["sections"]
        else:
            merged.append(p)
    return merged


def spec_from_folder(folder):
    """A small spec (name, kind, first pages) rebuilt from an existing product folder."""
    names = sorted(os.listdir(folder))
    name = os.path.basename(folder).split("-", 1)[-1].replace("-", " ").title()
    try:
        with open(os.path.join(folder, "README.md"), encoding="utf-8") as f:
            first = f.readline().strip()
        if first.startswith("# "):
            name = first[2:].strip()
    except OSError:
        pass
    pdfs = [n for n in names if n.endswith(".pdf")]
    if pdfs:
        pdf = next((n for n in pdfs if n.endswith("-A4.pdf")), pdfs[0])
        return {"kind": "planner", "name": name, "pages": planner_pages_from_pdf(os.path.join(folder, pdf))}
    if any(n.endswith("-resume.docx") for n in names):
        return {"kind": "resume", "name": name, "resume": [], "cover_letter": []}
    if any(n.endswith("-notion-import.zip") for n in names):
        return {"kind": "notion", "name": name, "pages": [], "databases": []}
    return {"kind": "sheets", "name": name, "sheets": []}


def add_missing_images(out_dir=None):
    """Give every built product folder without listing images its images and LICENSE.txt.

    Only new files are added; the product files themselves are never changed. Returns the folders done.
    """
    out_dir = out_dir or PRODUCTS_DIR
    done = []
    if not os.path.isdir(out_dir):
        return done
    import mockups
    for entry in sorted(os.listdir(out_dir)):
        folder = os.path.join(out_dir, entry)
        if not os.path.isdir(folder) or os.path.exists(os.path.join(folder, "cover.png")):
            continue
        if not os.path.exists(os.path.join(folder, "listing.md")):
            continue   # not a finished product folder
        try:
            mockups.write_mockups(folder, spec_from_folder(folder))
        except Exception as exc:
            print(f"Product Builder: images for {entry} skipped: {type(exc).__name__}", file=sys.stderr)
            continue
        if not os.path.exists(os.path.join(folder, "LICENSE.txt")):
            with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as f:
                f.write(LICENSE_TEXT)
        done.append(entry)
    return done


# Owner decisions: products that must not be sold, with the reason (the files are kept).
OWNER_HOLDS = {4: "NOT FOR SALE - files do not match listing",
               6: "NOT FOR SALE - files do not match listing"}   # decided 2026-10-16; Notion niche paused


def apply_owner_holds(db_path=None, out_dir=None):
    """Mark held products NOT FOR SALE in the build table and at the top of their README. Never deletes."""
    out_dir = out_dir or PRODUCTS_DIR
    done = []
    conn = connect(db_path)
    for approval_id, reason in OWNER_HOLDS.items():
        row = conn.execute("SELECT folder, status FROM product_builds WHERE approval_id = ?", (approval_id,)).fetchone()
        if not row or not row[0]:
            continue
        if row[1] != "not_for_sale":
            conn.execute("UPDATE product_builds SET status = 'not_for_sale', note = ? WHERE approval_id = ?",
                         (reason, approval_id))
            done.append(approval_id)
        readme = os.path.join(out_dir, row[0], "README.md")
        if os.path.exists(readme):
            with open(readme, encoding="utf-8") as f:
                body = f.read()
            if not body.startswith(f"> **{reason}**"):
                with open(readme, "w", encoding="utf-8") as f:
                    f.write(f"> **{reason}**\n\n" + body)
    conn.commit()
    conn.close()
    return done


def truth_check_folder(approval_id, folder_name, db_path=None, out_dir=None, notify_fn=None):
    """Run the Truth Checker on a built product: write TRUTH_CHECK.md, set the status to 'built' (ready)
    or 'needs_fix', and send ONE Telegram line per verdict change. Returns the result."""
    import truth_check
    out_dir = out_dir or PRODUCTS_DIR
    folder = os.path.join(out_dir, folder_name)
    result = truth_check.check(folder)
    name = spec_from_folder(folder)["name"]
    with open(os.path.join(folder, "TRUTH_CHECK.md"), "w", encoding="utf-8") as f:
        f.write(truth_check.report_md(name, result))
    conn = connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS truth_checks (approval_id INTEGER PRIMARY KEY, verdict TEXT, "
                 "score INTEGER, notified TEXT)")
    old = conn.execute("SELECT notified FROM truth_checks WHERE approval_id = ?", (approval_id,)).fetchone()
    status = "built" if result["verdict"] == "ready" else "needs_fix"
    held = approval_id in OWNER_HOLDS
    if not held:
        conn.execute("UPDATE product_builds SET status = ?, note = ? WHERE approval_id = ?",
                     (status, ("ready" if status == "built" else "needs fix: " + "; ".join(result["problems"][:2]))[:300],
                      approval_id))
    sent = old[0] if old else None
    if sent != result["verdict"]:
        try:
            if notify_fn is None:
                import notify
                notify_fn = notify.send
            if notify_fn(truth_check.telegram_text(approval_id, name, result)):
                sent = result["verdict"]
        except Exception:
            pass
    conn.execute("INSERT OR REPLACE INTO truth_checks (approval_id, verdict, score, notified) VALUES (?, ?, ?, ?)",
                 (approval_id, result["verdict"], result["score"], sent))
    conn.commit()
    conn.close()
    return result


def check_unchecked(db_path=None, out_dir=None, notify_fn=None):
    """Truth-check every built product that has no TRUTH_CHECK.md yet (products built before the checker)."""
    out_dir = out_dir or PRODUCTS_DIR
    done = []
    for b in builds(db_path):
        if b["folder"] and b["status"] in ("built", "needs_fix", "not_for_sale") and \
                os.path.isdir(os.path.join(out_dir, b["folder"])) and \
                not os.path.exists(os.path.join(out_dir, b["folder"], "TRUTH_CHECK.md")):
            done.append((b["approval_id"], truth_check_folder(b["approval_id"], b["folder"], db_path, out_dir,
                                                              notify_fn)["verdict"]))
    return done


REBUILD_DIR = os.path.join(BASE_DIR, "specs", "rebuild")


def rebuild_versions(db_path=None, out_dir=None, spec_dir=None, notify_fn=None):
    """Build NEW versions of products from the hand-checked specs in specs/rebuild/<id>-<version>.json.

    The new version gets its own folder (<id>-<version>-<name>); the old folder is kept, never deleted.
    Each new version is built once, truth-checked, and becomes the product's current folder. Held products
    (OWNER_HOLDS) are never rebuilt here. Returns [(approval_id, folder, verdict)]."""
    out_dir, spec_dir = out_dir or PRODUCTS_DIR, spec_dir or REBUILD_DIR
    done = []
    if not os.path.isdir(spec_dir):
        return done
    for name in sorted(os.listdir(spec_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(spec_dir, name), encoding="utf-8") as f:
            spec = json.load(f)
        approval_id, version = int(spec["approval_id"]), spec["version"]
        if approval_id in OWNER_HOLDS:
            continue
        folder = f"{approval_id}-{version}-{spec['short_name']}"
        if os.path.isdir(os.path.join(out_dir, folder)):
            continue
        old = next((b["folder"] for b in builds(db_path) if b["approval_id"] == approval_id), "") or ""
        build_product(spec, approval_id, out_dir, folder_name=folder)
        _record(db_path, approval_id, "built", folder, f"{version}; older version kept in {old or '-'}")
        verdict = truth_check_folder(approval_id, folder, db_path, out_dir, notify_fn)["verdict"]
        done.append((approval_id, folder, verdict))
    return done


def write_index(db_path=None, out_dir=None):
    out_dir = out_dir or PRODUCTS_DIR
    os.makedirs(out_dir, exist_ok=True)
    lines = ["# Products", "",
             "Built from approved drafts and checked by the Truth Checker against the real files. Nothing "
             "here is for sale until the owner decides; the owner uploads to Gumroad by hand. Nothing is "
             "published automatically.", "",
             "| Draft | Folder | Status |", "|---|---|---|"]
    current = set()
    for b in builds(db_path):
        if b["status"] in ("built", "needs_fix", "not_for_sale") and b["folder"]:
            current.add(b["folder"])
            label = {"built": "ready (truth check passed) - the owner decides if it goes on sale",
                     "needs_fix": "needs fix - see TRUTH_CHECK.md",
                     "not_for_sale": f"**{OWNER_HOLDS.get(b['approval_id'], 'NOT FOR SALE')}**"}[b["status"]]
            lines.append(f"| #{b['approval_id']} | [{b['folder']}]({b['folder']}/) | {label} |")
        elif b["status"] in ("retry", "failed"):
            lines.append(f"| #{b['approval_id']} | - | {b['status']} after {b['tries']} try(s): {b['note'][:80]} |")
    older = [d for d in sorted(os.listdir(out_dir)) if os.path.isdir(os.path.join(out_dir, d)) and d not in current
             and re.match(r"\d+-", d)]
    if older:
        lines += ["", "## Older versions (kept, not for sale)", "", "| Draft | Folder |", "|---|---|"]
        lines += [f"| #{d.split('-', 1)[0]} | [{d}]({d}/) |" for d in older]
    with open(os.path.join(out_dir, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


AI_DOWN = "no reply from the model"


def build_pending(db_path=None, llm=None, max_builds=MAX_BUILDS_PER_RUN, out_dir=None):
    """Build products for approved niche drafts. At most max_builds model calls per run."""
    out_dir = out_dir or PRODUCTS_DIR
    results, attempts = [], 0
    for item in _todo(db_path, out_dir):
        niche = niche_of(item["title"])
        if not niche:
            _record(db_path, item["id"], "skipped", note="not from a niche agent")
            results.append({"id": item["id"], "status": "skipped"})
            continue
        if attempts >= max_builds:
            break
        attempts += 1
        import workforce  # lazy: keeps the LLM router out of plain file-building code
        llm = llm or workforce.default_llm(db_path)
        try:
            spec, reason = parse_spec(workforce._ask({"llm": llm}, spec_prompt(niche, item["payload"]),
                                                     size="large"), niche)
        except Exception as exc:
            spec, reason = None, f"model error: {exc}"
        if spec is None and reason == AI_DOWN:
            # No AI answered: not the draft's fault, so it does not use up a try. Retry on a later run.
            _record(db_path, item["id"], "retry", note=reason)
            results.append({"id": item["id"], "status": "retry", "reason": reason})
            break
        if spec is None:
            tries = _record(db_path, item["id"], "retry", note=reason, add_try=True)
            if tries >= MAX_TRIES:
                _record(db_path, item["id"], "failed", note=reason)
            results.append({"id": item["id"], "status": "failed" if tries >= MAX_TRIES else "retry",
                            "reason": reason})
            continue
        folder = build_product(spec, item["id"], out_dir)
        _record(db_path, item["id"], "built", folder, f"{spec['kind']} for {niche}", add_try=True)
        verdict = truth_check_folder(item["id"], folder, db_path, out_dir)["verdict"]
        results.append({"id": item["id"], "status": "built" if verdict == "ready" else "needs_fix",
                        "folder": folder})
    if results:
        write_index(db_path, out_dir)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chilli Gavva Product Builder")
    parser.add_argument("--max", type=int, default=MAX_BUILDS_PER_RUN, help="max products to build this run")
    parser.add_argument("--list", action="store_true", help="show built products")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    if args.list:
        for b in builds(args.db):
            print(f"#{b['approval_id']:<5} {b['status']:<8} tries={b['tries']} {b['folder'] or b['note']}")
        return
    if guardrails.is_killed():
        print("Product Builder: paused (kill switch or /pause), nothing built.")
        return
    import rest_window
    if rest_window.in_rest_window():
        print("Product Builder: daily rest window, nothing built.")
        return
    results = build_pending(db_path=args.db, max_builds=args.max)
    rebuilt = rebuild_versions(args.db)
    for approval_id, folder, verdict in rebuilt:
        print(f"Product Builder: new version of #{approval_id} -> {folder} ({verdict})")
    for entry in add_missing_images():
        print(f"Product Builder: added listing images and LICENSE.txt to {entry}")
    held = apply_owner_holds(args.db)
    checked = check_unchecked(args.db)
    for approval_id, verdict in checked:
        print(f"Product Builder: truth check #{approval_id} -> {verdict}")
    if results or held or checked or rebuilt:
        write_index(args.db)
    if not results:
        print("Product Builder: no new approved niche drafts.")
    for r in results:
        print(f"Product Builder: draft #{r['id']} -> {r['status']} {r.get('folder') or r.get('reason') or ''}")


if __name__ == "__main__":
    main(sys.argv[1:])
