"""product_builder.py — Product Builder agent: turns APPROVED niche drafts into ready-to-sell files.

For each approved draft from a niche agent ("[Budget Sheets Agent] ..." etc.) that has no product
yet, the large model writes a JSON product spec, and this module builds the real files:

  Budget Sheets / Freelancer Trackers -> .xlsx workbook (Instructions tab, data tabs, formulas)
  ADHD Planners                       -> printable PDF in A4 and US Letter
  Notion Templates                    -> Notion import .zip (Markdown + CSV databases) + import guide
  Resume Bundle                       -> resume .docx + cover letter .docx + job tracker .xlsx

Every product also gets listing.md (title, description, 5 tags, real files, buyer, platform),
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
import secret_guard
import argparse
import copy
import csv
import datetime
import io
import json
import os
import re
import sys
import zipfile
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

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
                 (approval_id, status, tries, folder, secret_guard.scrub_text(note)[:300], datetime.datetime.now().isoformat()))
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



def niche_for_draft(title, payload=""):
    """Resolve old agent titles and registered product formats through the same builder path."""
    niche = niche_of(title)
    if niche:
        return niche
    import format_registry
    kind = format_registry.classify(f"{title}\n{payload}")
    if not kind:
        return None
    for name, meta in NICHES.items():
        if meta['kind'] == kind:
            return name
    name = kind.replace('_', ' ').title() + " Agent"
    base = dict(NICHES['ADHD Planners Agent'])
    base.update(kind=kind, buyer="people organizing this task", tags=[kind.replace('_', ' '), "printable", "organizer"])
    NICHES[name] = base
    return name

def _todo(db_path=None, out_dir=None):
    """Scheduled image-tool probe: every approved, unfinished delivery is work.

    This includes legacy builds and needs-fix packs. A folder on disk is never
    proof of a complete product, and probing work must not mark anything built.
    """
    import product_pipeline
    return product_pipeline.pending(db_path)


def _core_todo(db_path=None, out_dir=None):
    """Core file-builder queue; the scheduled CLI uses the delivery pipeline."""
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
    "dated_planner": {"calendar": "jan-dec", "pages": [{"title": "Class Roster", "subtitle": "One line per student",
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
    "sheets": "Give 2-4 data sheets, up to 40 columns per sheet (a habit plus 31 days fits). Column types: text, number, money, date. Use 'overdue' only when the sheet "
              "has a due-date and a status column, and 'balance' only for a money in/out ledger "
              "(use \"\" when a side is missing). 3-6 sample rows per sheet, dates as YYYY-MM-DD. Set dashboard to true only when the requested product includes a Dashboard tab.",
    "planner": "Give 3-6 printable pages. Section types: checklist (items), lines (count 1-12), "
               "schedule (slots), notes (count of lines), table (columns/count), checkboxes (count), "
               "grid (columns or days 7/31, count of habits), blocks (note), numbered/bullets (items), "
               "index (items as [label, page] pairs), nnl (count). Set daily_page to true for a daily or time-blindness "
               "planner: code then adds a fixed 'Today' page (time anchors, top 3, now/next/later, waiting for, "
               "wins, guess vs real time, 15-minute focus blocks, tomorrow's first step, brain dump). Code also "
               "adds a cover and a 'How to use' page to every planner. For dated daily/weekly pages or a year "
               "overview, set dated_pages to an object with boolean daily, weekly, year_overview flags and optional "
               "start/end ISO dates within the planner year (year defaults to 2027). Dates are generated by code.",
    "notion": "Give 2-4 pages in Markdown and 1-3 databases with 3-6 sample rows each.",
    "dated_planner": ("The calendar part is FIXED and made by code: a cover, a How to use page, a year-at-a-glance "
                      "page and 12 monthly calendar pages, each with the boxes This month's focus, Key dates and "
                      "Notes. Set \"calendar\" to \"jan-dec\" (January-December 2027, weeks start on Sunday), "
                      "\"us-academic\" (August 2027 - July 2028, Sunday start) or \"uk-academic\" (September 2027 - "
                      "August 2028, Monday start). You only choose 1-4 extra planner pages for ONE clear buyer "
                      "(checklist, lines, schedule or notes sections). Printable PDF in A4 and US Letter. "
                      "For dated daily or weekly pages set dated_pages.daily / dated_pages.weekly to true; optional "
                      "dated_pages.start/end ISO dates select an inclusive range within this calendar. Code generates "
                      "the dates. Never promise weekly or daily dated pages without enabling the corresponding flags. "
                      "Never promise hyperlinks, fillable fields, stickers, a digital/tablet version, "
                      "dashboards or formulas."),
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
               **EXAMPLES.get(kind, {"kind": kind, "title": "Product title"})}
    return ("You are the Product Builder for a small digital-product shop selling to global buyers in USD.\n"
            "Turn the APPROVED draft below into a complete product spec.\n"
            f"Product type: {kind}. {KIND_RULES.get(kind, 'Use the registered builder controls; describe only files that builder can create.')}\n"
            "Rules: plain honest English; no income, health, medical or job promises; no made-up statistics.\n"
            "Reply with ONLY one JSON object shaped like this example (replace every value):\n"
            + json.dumps(example, indent=1) + "\n\nApproved draft:\n" + draft[:5000])


def rewrite_spec_prompt(niche, draft, reason, previous):
    """The one automatic retry: tell the model exactly why its spec could not be used."""
    return ("Your previous product spec could not be used. Problem: " + str(reason)[:200] + ".\n"
            "Rewrite it. Reply with ONLY one valid JSON object: no text before or after it, no code fences, "
            "no comments, no trailing commas, double quotes only. Keep it short enough to finish.\n"
            "Start of your previous reply (for reference):\n" + str(previous or "")[:1500] + "\n\n"
            + spec_prompt(niche, draft))


def request_spec(ask, niche, draft):
    """Ask the large model for a spec; one automatic 'rewrite spec' retry when it cannot be parsed.

    ask(prompt) -> reply text. Returns (spec, None) or (None, reason). A missing reply (AI down or
    call cap) is returned as AI_DOWN at once: that is not the draft's fault and must not use a retry.
    """
    reply = ask(spec_prompt(niche, draft))
    spec, reason = parse_spec(reply, niche, draft)
    if spec is not None or reason == AI_DOWN:
        return spec, reason
    retry = ask(rewrite_spec_prompt(niche, draft, reason, reply))
    spec, second = parse_spec(retry, niche, draft)
    if spec is not None:
        return spec, None
    if second == AI_DOWN:
        return None, f"{reason} (rewrite retry got no AI reply)"
    return None, f"{second} (after one rewrite retry)"


def _clean(value, limit=200):
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


# ---- tolerant JSON reading (CCZ-1) ---------------------------------------------------------
# Free models often wrap the spec in prose or ```json fences, leave trailing commas, write Python
# True/None, use curly quotes, add // comments or stop mid-object at the token limit. These helpers
# only REPAIR THE SYNTAX of the model's own answer; the content checks in parse_spec stay strict.
_FENCE_RX = re.compile(r"```[ \t]*(?:json|JSON|javascript|js)?[ \t]*\n?(.*?)(?:```|\Z)", re.S)
_SMART = {"“": '"', "”": '"', "‘": "'", "’": "'"}
_PY_WORDS = {"True": "true", "False": "false", "None": "null"}


def _json_spans(text):
    """Every top-level {...} span (string-aware). An unclosed last object is returned to its end."""
    spans, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append(text[start:i + 1])
    if depth and start is not None:
        spans.append(text[start:])
    return spans


def _repair_json(src):
    """String-aware syntax repair: comments, trailing commas, Python words, unclosed brackets."""
    out, stack, i, in_str, esc, n = [], [], 0, False, False, len(src)
    while i < n:
        ch = src[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ch == "\n":
                ch = "\\n"
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif src.startswith("//", i) or ch == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            while out and out[-1] in " \t\r\n,":
                out.pop()
            if stack:
                stack.pop()
        elif ch.isalpha():
            m = re.match(r"[A-Za-z_]+", src[i:])
            word = m.group(0)
            out.append(_PY_WORDS.get(word, word))
            i += len(word)
            continue
        out.append(ch)
        i += 1
    if in_str:          # cut off inside a string at the token limit
        out.append('"')
    text = "".join(out).rstrip()
    if stack:           # cut off mid-object: drop a dangling key / comma, then close what is open
        text = re.sub(r',\s*"[^"]*"\s*:?\s*$', "", text)
        text = re.sub(r'[,:]\s*$', "", text)
        text = re.sub(r'\{\s*"[^"]*"\s*$', "{", text)
        text += "".join(reversed(stack))
    return text


def extract_json_object(text):
    """Best JSON object in a model reply. Returns (dict, None) or (None, reason)."""
    if not isinstance(text, str) or not text.strip():
        return None, AI_DOWN
    for k, v in _SMART.items():
        text = text.replace(k, v)
    blocks = [b for b in _FENCE_RX.findall(text) if "{" in b] + [text]
    found = []
    for block in blocks:
        for span in _json_spans(block):
            for attempt in (span, _repair_json(span)):
                try:
                    value = json.loads(attempt)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    found.append(value)
                    break
    if not found:
        return None, ("reply has no JSON object" if "{" not in text else "reply is not valid JSON")
    for value in list(found):   # unwrap {"spec": {...}} / {"product": {...}}
        inner = [v for v in value.values() if isinstance(v, dict) and "listing" in v]
        if "listing" not in value and len(inner) == 1:
            found.append(inner[0])
    found.sort(key=lambda d: ("listing" in d, len(json.dumps(d))), reverse=True)
    return found[0], None


_DRAFT_LABEL_RX = r"(?im)^\s*\**\s*{label}\s*\**\s*:\s*(.*?)(?=^\s*\**\s*(?:PRODUCT NAME|BUYER|PRICE|PLATFORM|WHAT'S INSIDE|HOW TO USE|LISTING TITLE|LISTING DESCRIPTION|SEARCH TAGS)\s*\**\s*:|\Z)"


def draft_listing(draft):
    """The approved draft's own LISTING TITLE / DESCRIPTION / SEARCH TAGS (safe defaults for a spec)."""
    def section(label):
        m = re.search(_DRAFT_LABEL_RX.format(label=label), draft or "", re.S)
        return m.group(1).strip() if m else ""
    title = clean_draft_title(section("LISTING TITLE") or section("PRODUCT NAME"))
    tags = [_clean(t.strip(" #-*"), 40) for t in re.split(r"[,\n]", section("SEARCH TAGS")) if t.strip(" #-*")]
    return {"title": _clean(title, 140), "description": section("LISTING DESCRIPTION")[:3000],
            "tags": tags[:5], "name": _clean(clean_draft_title(section("PRODUCT NAME")), 80)}


def _as_list(value):
    """A model may send one string where a list belongs: one line per item then."""
    if isinstance(value, str):
        return [line.strip(" -*\t") for line in value.splitlines() if line.strip(" -*\t")]
    return value if isinstance(value, list) else []


def clean_draft_title(title):
    """'Draft: [Budget Sheets Agent] Google Sheets X (v4, llm) (rewritten)' -> 'Google Sheets X'."""
    title = re.sub(r"(?i)^\s*draft\s*:\s*", "", str(title or ""))
    title = re.sub(r"\[[^\]]*\]", "", title)
    title = re.sub(r"\((?:v\d+[^)]*|rewritten|llm|fallback)\)", "", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip(" -:|")


def parse_spec(text, niche, draft=""):
    """Read and normalise the model's JSON spec. Returns (spec, None) or (None, reason).

    The JSON may be wrapped in prose/fences or slightly broken (repaired by extract_json_object).
    Listing title / description / tags / name the model left out are filled from the approved
    draft's own sections. The product structure (sheets, pages, sections) is still checked strictly.
    """
    data, reason = extract_json_object(text)
    if data is None:
        return None, reason
    fallback = draft_listing(draft) if draft else {}
    listing = data.get("listing") if isinstance(data.get("listing"), dict) else {}
    for key in ("title", "description", "tags"):
        if not listing.get(key) and data.get(key):
            listing[key] = data[key]
        if not listing.get(key) and fallback.get(key):
            listing[key] = fallback[key]
    if not listing.get("title") and not data.get("name") and fallback.get("name"):
        data["name"] = fallback["name"]
    if not listing:
        return None, "spec has no listing"
    data["listing"] = listing
    info, kind = NICHES[niche], NICHES[niche]["kind"]
    title = _clean(listing.get("title"), 140) or _clean(data.get("name"), 140)
    description = str(listing.get("description") or "").strip()[:3000]
    if not title or not description:
        return None, "listing needs a title and a description"
    raw_tags = listing.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.split(",")
    tags = [_clean(t, 40) for t in raw_tags if _clean(t, 40)] if isinstance(raw_tags, list) else []
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
            "instructions": [_clean(s, 300) for s in _as_list(data.get("instructions")) if _clean(s)][:12],
            "cover_image_idea": _clean(data.get("cover_image_idea"), 400)}
    checker = {"sheets": _norm_sheets, "planner": _norm_pages, "notion": _norm_notion, "resume": _norm_resume,
               "dated_planner": _norm_dated}.get(kind)
    if checker is None:
        import format_registry
        if not callable(format_registry.get_builder(kind)):
            return None, "registered builder is unavailable"
        spec.update({key: value for key, value in data.items() if key not in spec})
        spec["title"] = spec["name"]
    reason = checker(data, spec) if checker else None
    if not reason and kind in ("planner", "dated_planner") and data.get("sheets"):
        reason = _norm_sheets(data, spec)
    if not reason and kind == "sheets" and data.get("pages"):
        reason = _norm_pages(data, spec)
    if reason:
        return None, reason
    # Sanitize listing text and instructions to only promise features present in built files
    spec["listing"]["title"] = _sanitize_text(spec["listing"]["title"], spec)
    spec["listing"]["description"] = _sanitize_text(spec["listing"]["description"], spec)
    spec["instructions"] = [_sanitize_text(inst, spec) for inst in spec["instructions"]]
    risky = _risky_text(" ".join([spec["listing"]["title"], spec["listing"]["description"]] + spec["instructions"]))
    if risky:
        return None, f"risky claim in listing: {risky}"
    return spec, None


_NOT_NAMES = {"the", "this", "each", "a", "an", "your", "one", "every", "new", "all", "any", "both", "extra",
              "blank", "printable", "simple", "single", "notion", "pdf", "letter", "a4", "gumroad", "start",
              "home", "first", "last", "next", "main", "two", "three", "four", "five", "six"}
_NAMED_RX = re.compile(r"\b((?:[A-Z][\w'’&-]*\s){0,3}[A-Z][\w'’&-]*)\s+(database|page|sheet|tab|tracker|section)s?\b")


def _get_spec_names(spec):
    kind = spec.get("kind")
    names = []
    if kind in ("planner", "dated_planner"):
        names += ["Cover", "How to use"]
        if spec.get("daily_page"):
            names.append("Today")
        if kind == "dated_planner" or (spec.get("dated_pages") or {}).get("year_overview"):
            names += ["Year at a glance", "2027 at a glance", "At a glance", "Year overview"]
        if kind == "dated_planner":
            import calendar
            for m in calendar.month_name[1:]:
                names.append(m)
        for p in spec.get("pages") or []:
            if isinstance(p, dict) and p.get("title"):
                names.append(p["title"])
                for s in p.get("sections") or []:
                    if isinstance(s, dict) and s.get("heading"):
                        names.append(s["heading"])
    elif kind == "sheets":
        names += ["Instructions"] + (["Dashboard"] if spec.get("dashboard") is True else [])
        for s in spec.get("sheets") or []:
            if isinstance(s, dict) and s.get("name"):
                names.append(s["name"])
    elif kind == "notion":
        for p in spec.get("pages") or []:
            if isinstance(p, dict):
                if p.get("title"):
                    names.append(p["title"])
                if p.get("markdown"):
                    for line in p["markdown"].splitlines():
                        if line.startswith("#"):
                            names.append(line.lstrip("# ").strip())
        for d in spec.get("databases") or []:
            if isinstance(d, dict) and d.get("name"):
                names.append(d["name"])
    elif kind == "resume":
        names += ["Resume", "Cover Letter", "Job Applications", "Instructions"] + (["Dashboard"] if spec.get("dashboard") is True else [])
        for s in (spec.get("resume") or []):
            if isinstance(s, dict) and s.get("heading"):
                names.append(s["heading"])
    return [n.lower() for n in names if n]


def _sanitize_text(text, spec, facts=None):
    if not text:
        return ""
    kind = spec.get("kind")
    spec_names = _get_spec_names(spec)
    if facts:
        import truth_check
        fact_names = truth_check._names(facts)
        all_names = list(set(spec_names + fact_names))
    else:
        all_names = spec_names

    if facts:
        actual_count = facts["pages"] if facts["kind"] == "pdf" else (len(facts["sheets"]) or len(facts["md"]) + len(facts["csv"]))
        has_formulas = facts.get("formulas", 0) > 0
        has_validations = facts.get("validations", 0) > 0
        has_charts = facts.get("charts", 0) > 0
    else:
        if kind == "dated_planner":
            actual_count = 15 + len(spec.get("pages") or [])
        elif kind == "planner":
            actual_count = len(spec.get("pages") or []) + 2 + (1 if spec.get("daily_page") else 0)
        elif kind == "sheets":
            actual_count = len(spec.get("sheets") or []) + 1 + (1 if spec.get("dashboard") is True else 0)
        elif kind == "notion":
            actual_count = len(spec.get("pages") or []) + len(spec.get("databases") or [])
        else:
            actual_count = 3
        if kind in ("planner", "dated_planner") and spec.get("dated_pages"):
            from datetime import date, timedelta
            from pdf_design import dated_page_options
            opts = dated_page_options(spec)
            start, end = date.fromisoformat(opts["start"]), date.fromisoformat(opts["end"])
            week = start - timedelta(days=(start.weekday() - opts["first_weekday"]) % 7)
            actual_count += ((end - start).days + 1) if opts["daily"] else 0
            actual_count += ((end - week).days // 7 + 1) if opts["weekly"] else 0
            actual_count += int(opts["year_overview"] and kind != "dated_planner")
        has_formulas = (kind in ("sheets", "resume"))
        has_validations = (kind in ("sheets", "resume"))
        has_charts = False

    # 1. Unproven features
    if not has_charts:
        text = re.sub(r"\bcharts?\b", "summaries", text, flags=re.I)
    text = re.sub(r"\bgraphs?\b", "visuals", text, flags=re.I)
    text = re.sub(r"\bprogress bars?\b", "progress indicators", text, flags=re.I)

    text = re.sub(r"\bbuttons?\b", "links", text, flags=re.I)
    text = re.sub(r"\bone[- ]click\b", "easy", text, flags=re.I)
    text = re.sub(r"\bsingle click\b", "easy access", text, flags=re.I)

    text = re.sub(r"\bduplicate\b", "use", text, flags=re.I)

    text = re.sub(r"\bfillable\b", "printable", text, flags=re.I)
    text = re.sub(r"\beditable pdf\b", "printable PDF", text, flags=re.I)
    text = re.sub(r"\btype (?:on|into) the pdf\b", "write on the printed PDF", text, flags=re.I)
    text = re.sub(r"\b(?:hyperlinked|hyperlinks?|clickable)\b", "printable", text, flags=re.I)

    text = re.sub(r"\bstickers?\b", "labels", text, flags=re.I)
    text = re.sub(r"\bgoodnotes\b|\bnotability\b|\btablet version\b", "printable version", text, flags=re.I)
    text = re.sub(r"\bdigital planner\b", "printable planner", text, flags=re.I)

    text = re.sub(r"\bcalendar views?\b", "calendar layout", text, flags=re.I)
    text = re.sub(r"\bkanban\b|\btimeline views?|\bgallery views?|\bboard views?\b", "view", text, flags=re.I)

    dashboard_proven = truth_check._proven("dashboard", facts)[0] if facts else any("dashboard" in n for n in all_names)
    if not dashboard_proven:
        text = re.sub(r"\bdashboards?\b", "overview", text, flags=re.I)

    if kind not in ("sheets", "resume"):
        text = re.sub(r"\blinked databases?\b", "databases", text, flags=re.I)
        text = re.sub(r"\blinked to\b", "connected to", text, flags=re.I)
        text = re.sub(r"\brelational\b|\brelations?\b|\broll-?ups?\b", "connected", text, flags=re.I)

    if not has_formulas:
        text = re.sub(r"\bformulas?\b", "calculations", text, flags=re.I)
        text = re.sub(r"\bauto(?:matic(?:ally)?)?[- ]calculat\w*\b", "calculated", text, flags=re.I)
        text = re.sub(r"\bcalculates? (?:automatically|instantly)\b", "calculates", text, flags=re.I)
        text = re.sub(r"\binstant(?:ly)? (?:gpa|totals?|calculation)\b", "summary totals", text, flags=re.I)
        text = re.sub(r"\bupdat\w* automatically\b", "updates", text, flags=re.I)
        text = re.sub(r"\brecalculat\w*\b", "calculates", text, flags=re.I)

    if kind != "dated_planner" and not any(w in " ".join(all_names) for w in ("year overview", "at a glance")):
        text = re.sub(r"\bat a glance\b", "overview", text, flags=re.I)
        text = re.sub(r"\byear overview\b", "overview", text, flags=re.I)

    if kind != "dated_planner":
        text = re.sub(r"\b(?:12|twelve) monthly\b", "monthly", text, flags=re.I)
    dated = spec.get("dated_pages") or {}
    if kind != "dated_planner" and not dated.get("weekly"):
        text = re.sub(r"(?<!undated )\bweekly spreads?\b", "undated weekly spreads", text, flags=re.I)
        text = re.sub(r"(?<!undated )\b(?:weekly dated|dated weekly|weekly) pages?\b", "undated weekly pages", text, flags=re.I)
    if kind != "dated_planner" and not dated.get("daily"):
        text = re.sub(r"\b(?:daily dated|dated daily) pages?\b", "undated daily pages", text, flags=re.I)

    if not has_validations:
        text = re.sub(r"\bdrop-?downs?\b", "options", text, flags=re.I)
        text = re.sub(r"\bselect (?:field|option|menu)s?\b", "options", text, flags=re.I)

    # 2. Counts
    num_words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
                 "fifteen": 15, "sixteen": 16}

    is_pdf = bool(facts.get("pdf_sizes")) if facts is not None else kind in ("planner", "dated_planner")

    def repl_count(m):
        raw_num, unit = m.group(1), m.group(2)
        n = num_words.get(raw_num.lower(), int(raw_num) if raw_num.isdigit() else None)
        modifiers = re.findall(r"[a-z0-9-]+", unit.lower())[:-1]
        total_modifiers = {"printable", "pdf", "a4", "us", "letter", "and", "or", "in", "total"}
        if (not unit.lower().endswith(("page", "pages")) or not is_pdf or n == actual_count
                or any(word not in total_modifiers for word in modifiers)):
            return m.group(0)
        replacement = str(actual_count)
        if not raw_num.isdigit() and actual_count in num_words.values():
            replacement = next(word for word, value in num_words.items() if value == actual_count)
            if raw_num.isupper():
                replacement = replacement.upper()
            elif raw_num[0].isupper():
                replacement = replacement.capitalize()
        if actual_count == 1:
            unit = re.sub(r"pages$", "page", unit, flags=re.I)
        elif unit.lower().endswith("page"):
            unit += "s"
        return f"{replacement} {unit}"

    count_rx = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen)\s+((?:[a-z0-9-]+,?\s){0,5}?(?:pages?|sheets|tabs|worksheets|databases))\b", re.I)
    text = count_rx.sub(repl_count, text)
    count_spans = [m.span() for m in count_rx.finditer(text)]

    # 3. Named parts
    def repl_named(m):
        full = m.group(0)
        if any(start <= m.start() and m.end() <= end for start, end in count_spans):
            return full
        prefix = m.group(1)
        suffix = m.group(2)
        words = prefix.split()
        while words and words[0].lower() in _NOT_NAMES:
            words = words[1:]
        if not words:
            return full
        part = f"{' '.join(words)} {suffix}".strip().lower()
        part_words = [w for w in re.findall(r"[a-z]+", part) if w not in ("database", "page", "sheet", "tab", "tracker", "section", "the", "and")]
        if part_words and any(all(w in n for w in part_words) for n in all_names):
            return full
        return prefix.lower() + " " + suffix

    text = _NAMED_RX.sub(repl_named, text)
    return text


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
    for c in (raw.get("columns") or [])[:40]:
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
    spec["dashboard"] = data.get("dashboard") is True
    seen = set()
    for s in sheets:  # sheet names must be unique in a workbook
        base, n = s["name"], 2
        while s["name"].lower() in seen | {"instructions"} | ({"dashboard"} if spec["dashboard"] else set()):
            s["name"] = _sheet_name(f"{base[:27]} {n}")
            n += 1
        seen.add(s["name"].lower())
    spec["sheets"] = sheets
    return None


def _norm_dated_pages(data, spec):
    import pdf_design
    raw = data.get("dated_pages", {})
    if not isinstance(raw, dict):
        return "dated_pages must be an object"
    if any(key in raw and not isinstance(raw[key], bool) for key in ("daily", "weekly", "year_overview")):
        return "dated_pages flags must be booleans"
    requested = pdf_design.dated_requests(" ".join(
        [spec["listing"].get("title", ""), spec["listing"].get("description", "")] + spec.get("instructions", [])))
    options = dict(raw)
    for key, promised in requested.items():
        if promised:
            options[key] = True
    if not options:
        return None
    if spec.get("kind") != "dated_planner":
        spec["year"] = data.get("year", PLANNER_YEAR)
        spec["calendar"] = data.get("calendar") if data.get("calendar") in pdf_design.CALENDARS else "jan-dec"
    spec["dated_pages"] = options
    try:
        spec["dated_pages"] = pdf_design.dated_page_options(spec)
    except ValueError as exc:
        return str(exc)
    return None


def _norm_pages(data, spec):
    reason = _norm_dated_pages(data, spec)
    if reason:
        return reason
    pages = []
    for p in (data.get("pages") or [])[:12]:
        if not isinstance(p, dict) or not _clean(p.get("title")):
            continue
        sections = []
        for s in (p.get("sections") or [])[:8]:
            if not isinstance(s, dict):
                continue
            stype = s.get("type") if s.get("type") in ("checklist", "lines", "schedule", "notes", "table", "checkboxes",
                                                       "grid", "blocks", "numbered", "index", "bullets", "nnl") else "lines"
            raw_items = s.get("items") or s.get("slots") or []
            if stype == "index":
                items = [[_clean(i[0], 90), _clean(i[1], 24)] for i in raw_items
                         if isinstance(i, (list, tuple)) and len(i) == 2][:14]
            else:
                items = [_clean(i, 90) for i in raw_items if _clean(i, 90)][:14]
            try:
                count = max(1, min(12, int(s.get("count") or 3)))
            except (TypeError, ValueError):
                count = 3
            section = {"heading": _clean(s.get("heading"), 80), "type": stype, "items": items, "count": count}
            if stype in ("table", "grid"):
                section["columns"] = [_clean(c.get("name") if isinstance(c, dict) else c, 60)
                                      for c in (s.get("columns") or [])[:40]]
                section["columns"] = [c for c in section["columns"] if c]
            if stype == "grid":
                try:
                    section["days"] = max(1, min(31, int(s.get("days") or 7)))
                except (TypeError, ValueError):
                    section["days"] = 7
            if s.get("column") in (1, 2):
                section["column"] = s["column"]
            if s.get("note"):
                section["note"] = _clean(s["note"], 300)
            sections.append(section)
        if sections:
            pages.append({"title": _clean(p["title"], 80), "subtitle": _clean(p.get("subtitle"), 120),
                          "sections": sections, "columns": 2 if p.get("columns") == 2 else 1})
    if not pages and not any((spec.get("dated_pages") or {}).get(key) for key in ("daily", "weekly", "year_overview")):
        return "spec has no usable planner pages"
    spec["pages"] = pages
    if spec.get("kind") == "planner":
        flag = data.get("daily_page")
        if flag is None:   # the model forgot: a daily or time-blindness planner gets the Today page
            flag = not (spec.get("dated_pages") or {}).get("daily") and bool(
                re.search(r"\bdaily\b|time[- ]blind", spec["listing"]["title"].lower()))
        spec["daily_page"] = bool(flag)
    return None


def _norm_dated(data, spec):
    import pdf_design
    spec["monthly_sections"] = list(pdf_design.MONTH_BOXES)   # fixed by the design, the same every month
    spec["year"] = PLANNER_YEAR
    spec["calendar"] = data.get("calendar") if data.get("calendar") in pdf_design.CALENDARS else "jan-dec"
    reason = _norm_dated_pages(data, spec)
    if reason:
        return reason
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
    spec["dashboard"] = data.get("dashboard") is True
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
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2F5597"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD9D9D9"/><bgColor indexed="64"/></patternFill></fill></fills>
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


def _xlsx_styles():
    """A grey-filled twin for each base style, retaining money/date/font formats."""
    root = ET.fromstring(_XLSX_STYLES)
    xfs = root.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}cellXfs")
    for base in list(xfs):
        formula = ET.fromstring(ET.tostring(base))
        formula.set("fillId", "3")
        formula.set("applyFill", "1")
        xfs.append(formula)
    xfs.set("count", str(len(xfs)))
    return ET.tostring(root, encoding="unicode")


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
        sheet["cells"][(row, col)] = (value, style + 9 if formula and style < 9 else style, formula)

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
            "xl/styles.xml": _xlsx_styles(),
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
              ["Make a copy of this Excel file with Save As.",
               "Delete the sample rows and type your own.",
               "Cells with formulas update by themselves, so you do not need to edit them."], 1)]
    lines += [("", 0)] + [(e, S_WRAP) for e in extra]
    lines += [("", 0), ("Grey cells contain formulas. Do not type over them; formula cells are not locked.", S_WRAP),
              ("Blue header rows stay visible while you scroll. Totals sit under each table.", S_WRAP),
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
    direction = next((c for c, col in enumerate(cols)
                      if {str(option).strip().lower() for option in col["options"]} >= {"in", "out"}), None)
    Workbook.put(sheet, total_row, 0, "TOTAL IN" if direction is not None else "TOTAL", S_BOLD)
    if direction is not None:
        Workbook.put(sheet, total_row + 1, 0, "TOTAL OUT", S_BOLD)
    for c, col in enumerate(cols):
        if col["type"] in ("money", "number") and c > 0:
            style = S_TOTAL_MONEY if col["type"] == "money" else S_TOTAL_NUM
            if direction is None:
                Workbook.put(sheet, total_row, c, None, style, f"SUM({_col(c)}{first}:{_col(c)}{last})")
                totals[col["name"]] = (f"{_quote_sheet(sheet['name'])}!{_col(c)}{total_row}", col["type"])
            else:
                selector = f"${_col(direction)}${first}:${_col(direction)}${last}"
                for offset, label in enumerate(("In", "Out")):
                    Workbook.put(sheet, total_row + offset, c, None, style,
                                 f'SUMIF({selector},"{label}",{_col(c)}{first}:{_col(c)}{last})')
                    totals[f"{col['name']} {label}"] = (f"{_quote_sheet(sheet['name'])}!{_col(c)}{total_row + offset}", col["type"])
    summary_end = total_row + (1 if direction is not None else 0)
    if sheet_spec.get("overdue"):
        oc = len(cols) + (1 if sheet_spec.get("balance") else 0)
        Workbook.put(sheet, summary_end + 1, 0, "Overdue rows", S_BOLD)
        Workbook.put(sheet, summary_end + 1, 1, None, S_BOLD, f'COUNTIF({_col(oc)}{first}:{_col(oc)}{last},"OVERDUE")')
        totals["Overdue rows"] = (f"{_quote_sheet(sheet['name'])}!B{summary_end + 1}", "number")
    return totals


def build_workbook(path, spec, sheet_specs, extra_instructions=()):
    wb = Workbook()
    _instructions_sheet(wb, spec, extra_instructions)
    dashboard = wb.add_sheet("Dashboard", widths=[34, 18, 18]) if spec.get("dashboard") is True else None
    all_totals = [(s["name"], _data_sheet(wb, s)) for s in sheet_specs]
    if dashboard is not None:
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
def _delivery_text(text, spec, facts):
    """Final buyer text uses built evidence; leave the general spec sanitizer compatible."""
    text = _sanitize_text(text, spec, facts=facts)
    if not facts.get("pdf_sizes") and spec.get("kind") != "notion":
        text = re.sub(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen)\s+"
                      r"(?:(?:printable|pdf|a4|us|letter|and|or|in|total)\s+)*pages?\b",
                      "the included files", text, flags=re.I)
    for label, pattern in (("A4", r"\bA4\b"), ("Letter", r"(?<!cover )(?<!cover-)\b(?:US\s+)?Letter\b(?!\s+to\b)")):
        if label not in facts.get("pdf_sizes", []):
            text = re.sub(pattern, "the supplied format", text, flags=re.I)
    return text


def _verified_pdf(path):
    """(page count, "A4" | "Letter" | other PAGE_SIZES label | None) read from the PDF itself.

    The size is only given when EVERY page has a MediaBox and all of them are the same known size."""
    with open(path, "rb") as stream:
        data = stream.read()
    count = len(re.findall(rb"/Type\s*/Page\b", data))
    boxes = re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data)
    paper, known_boxes = set(), 0
    for w, h in boxes:
        dimensions = sorted((float(w), float(h)))
        label = next((label for label, dims in PAGE_SIZES.items()
                      if all(abs(a - b) < 1 for a, b in zip(dimensions, sorted(dims)))), None)
        if label:
            paper.add(label)
            known_boxes += 1
    # The robot writes a MediaBox on each page. Do not infer missing boxes.
    verified = paper if count and known_boxes == len(boxes) == count and len(paper) == 1 else set()
    return count, next(iter(verified), None)


# Store / listing pictures made for the owner, not files the buyer downloads
LISTING_IMAGE_PREFIXES = ("cover", "preview", "thumbnail", "pinterest", "mockup", "pin-", "pin_")


def _png_size(path):
    """(width, height) read from the PNG header itself, or None."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _delivery_facts(folder):
    """Inspect robot-built deliverables; filenames and spec promises are not PDF evidence."""
    import truth_check
    facts = truth_check.facts(folder)
    contents, sizes, counts = [], set(), []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        ext = os.path.splitext(name)[1].lower()
        if not os.path.isfile(path):
            continue
        if ext == ".pdf":
            count, size = _verified_pdf(path)
            verified = {size} if size else set()
            sizes.update(verified)
            if "-cover-" not in name:       # CCC-5: a one-page box label is not "every PDF" of the product
                counts.append(count)
            label = "US Letter" if verified == {"Letter"} else next(iter(verified), "paper size unspecified")
            # Counts are per file: A4 and Letter can paginate differently.
            contents.append(f"`{name}` — PDF, page count: {count}, {label}")
        elif ext == ".xlsx":
            with zipfile.ZipFile(path) as workbook:
                root = ET.fromstring(workbook.read("xl/workbook.xml"))
                tabs = [element.get("name", "") for element in root.findall("{*}sheets/{*}sheet")]
            # The existing truth checker treats a resume as one format and
            # cannot prove its optional Dashboard capability. As in the launch
            # listing, advertise a subset of real tabs without that claim.
            listed = [tab for tab in tabs if facts["kind"] != "resume" or tab.casefold() != "dashboard"]
            label = "tabs" if listed == tabs else "tabs include"
            contents.append(f"`{name}` — spreadsheet (.xlsx); {label}: " + ", ".join(listed))
        elif ext == ".html":
            contents.append(f"`{name}` — HTML file (copy into your email program)")
        elif ext == ".png" and not name.startswith(LISTING_IMAGE_PREFIXES):
            size = _png_size(path)
            contents.append(f"`{name}` — PNG image" + (f", {size[0]} x {size[1]} px" if size else ""))
        elif ext in (".docx", ".zip") or name in {"README.md", "README.txt", "README", "LICENSE.txt", "HOW_TO_IMPORT.md"}:
            label = {".docx": "Word document", ".zip": "ZIP archive", ".md": "Markdown", ".txt": "plain text"}.get(ext, "plain text")
            contents.append(f"`{name}` — {label}")
    # CCC-0: the proven facts get their own "Details" section. "What you get" lists only real files,
    # because buyers (and the sell-flow check) read every line there as a file name.
    facts["details"] = _proof_lines(folder, facts, counts)
    facts["contents"] = contents
    facts["pdf_sizes"] = sorted(sizes)
    if counts:
        facts["pages"] = min(counts)
    return facts


# ---- CCB-2: what you get, in words the Truth Checker can verify --------------------------
# Packs got NEEDS FIX "Score 95+ without enough proof" when the listing named almost nothing the
# checker can prove (it counts proven promises). These lines state ONLY facts read from the built
# files - page and sheet counts, tab names, formulas, drop-downs, charts - and each line is written
# only when the Truth Checker's own reading of the files agrees with it.
_GENERIC_WORDS = {"database", "page", "sheet", "tab", "tracker", "section", "the", "and"}


def _plain_tab_name(tab, kind):
    """A tab name that can be written as '<Name> tab' and proven by name, or None."""
    import truth_check
    if not re.fullmatch(r"[A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*){0,3}", tab or ""):
        return None
    low = tab.lower()
    if any(re.search(rx, low) for _claim, rx in truth_check.CLAIMS):
        return None                       # e.g. a "Dashboard" or "Charts" tab is a feature claim of its own
    if kind == "resume" and "dashboard" in low:
        return None
    words = [w for w in low.split() if w not in _GENERIC_WORDS]
    while words and words[0] in truth_check._NOT_NAMES:
        words = words[1:]
    return tab if words else None


def _proof_lines(folder, facts, counts):
    lines = []
    pages = facts.get("pages") or 0                 # the Truth Checker's own page count of the main PDF
    if counts and pages and min(counts) <= pages and min(counts) <= 99:
        n = min(counts)
        lines.append(f"{n} printable pages in every PDF" if len(set(counts)) == 1
                     else f"At least {n} printable pages in every PDF")
    if counts and not set(facts.get("pdf_sizes", [])) & {"A4", "Letter"}:
        for title in facts.get("titles", []):
            if re.fullmatch(r"[A-Z][A-Za-z0-9 &-]{2,55}", title) and not re.search(r"planner|hyperlink|digital", title, re.I):
                title = re.sub(r"\s+\d.*", "", title).strip()
                lines.append(f"{title} page included")
                if len(lines) >= 3:
                    break
    sheets = list(facts.get("sheets") or [])
    if sheets and len(sheets) <= 99:
        named = [name for name in (_plain_tab_name(tab, facts.get("kind")) for tab in sheets) if name]
        line = f"{len(sheets)} sheets in the Excel workbook"
        if named:
            line += ": " + ", ".join(f"{name} tab" for name in named)
        lines.append(line)
    if facts.get("formulas"):
        lines.append("Formulas built in: totals calculate automatically")
    if facts.get("validations"):
        lines.append("Drop-down lists in the workbook")
    if facts.get("charts"):
        lines.append("Charts in the workbook")
    return lines


def _contents_lines(facts=None, file_list=()):
    """Detailed buyer contents when inspected; no invented counts before a build."""
    if facts and "contents" in facts:
        return facts["contents"]
    return [f"`{name}`" for name in file_list
            if os.path.splitext(name)[1].lower() in (".pdf", ".xlsx", ".docx", ".zip")
            or name in ("README.md", "README.txt", "LICENSE.txt", "HOW_TO_IMPORT.md")]


def _listing_md(spec, facts=None):
    l = spec["listing"]
    body = "\n".join([
        f"# {l['title']}", "",
        f"**Buyer:** {l['buyer']}  ", f"**Platform:** {l['platform']}", "",
        "## Description", "", l["description"], "",
        "## What you get", "", *[f"- {item}" for item in _contents_lines(facts)], "",
        *(["## Details", "", *[f"- {item}" for item in facts["details"]], ""]
          if facts and facts.get("details") else []),
        "## How to use", "", *[f"{i}. {step}" for i, step in enumerate(spec.get("instructions", []), 1)], "",
        "## License", "", "Personal use only. Do not resell or share the files.", "",
        "## Tags", "", *[f"- {t}" for t in l["tags"]], "",
        "## Cover image idea", "", spec["cover_image_idea"] or "A clean flat-lay of the product on a laptop screen.",
        "", "---", (spec.get("source_note") or f"Built by the Product Builder from approved draft #{spec['approval_id']}.")
        + " "
        "Check the files, then upload them to the store yourself.",
        *([f"{DESIGN_THEME_LABEL} {spec['design_theme']}."] if spec.get("design_theme") else []), ""])
    # Narrative, tags and file names can contain money too. Refuse rather than
    # silently changing the owner's text or emitting a priced listing.
    from upload_pack import _check_money
    _check_money(body, "generated listing.md")
    return body


def _buyer_listing(spec, facts, files):
    """Write buyer copy using inspected deliverables, preserving the original niche."""
    from upload_pack import _check_money
    from pin_ready import BRAND_CHECK
    listing = spec["listing"]
    _check_money(str(listing), "approved listing")

    def plain(value):
        value = re.sub(r"\bExcel\b", "spreadsheet", str(value), flags=re.I)
        value = re.sub(r"\bNotion\b", "workspace", value, flags=re.I)
        return re.sub(r"\s+", " ", BRAND_CHECK.sub("", value)).strip()

    title = plain(_delivery_text(listing["title"], spec, facts))
    if len(title) < 40:
        title += " - Digital Files and Personal Use Guide"
    if len(title) > 80:
        title = title[:80].rsplit(" ", 1)[0].rstrip(" -")
    listing["title"] = title
    # Put the product topic first; all remaining tags come from the approved spec.
    topic = " ".join(title.split()[:2])[:20].strip()
    tags = [topic] + [plain(_delivery_text(tag, spec, facts)) for tag in listing.get("tags", [])]
    tags += ["digital files", "personal use", "download guide"]
    listing["tags"] = list(dict.fromkeys(tag.casefold() for tag in tags if 0 < len(tag) <= 20))[:10]
    details = [plain(line.split(" — ", 1)[-1].replace("`", "")) for line in facts.get("contents", [])]
    steps = [plain(step) for step in spec.get("instructions", [])]
    if not steps:
        steps = ["Download and extract the archive, then open the included files. Read the guide before using them."]
    spec["instructions"] = steps
    description = plain(_delivery_text(listing["description"], spec, facts))
    evidence = " Files included: " + "; ".join(details[:6]) + ". "
    # Archive size is an actual file size even for formats without paper pages.
    size = sum(os.path.getsize(os.path.join(facts["folder"], name)) for name in files
               if os.path.isfile(os.path.join(facts["folder"], name))) if facts.get("folder") else 0
    if size:
        evidence += f"Download files total {max(1, (size + 1023) // 1024)} KB. "
    instructions = " How to use: " + " ".join(steps) + " Personal use only; do not resell or share the files."
    # Keep original buyer benefits and leave room for concrete evidence and instructions.
    listing["description"] = description[:max(0, 2900-len(evidence)-len(instructions))].rstrip() + evidence + instructions


def _readme_md(spec, files, how_to, facts=None):
    steps = spec["instructions"] or how_to
    return "\n".join([
        "# " + (_delivery_text(spec["name"], spec, facts) if facts is not None else _sanitize_text(spec["name"], spec)), "", "## What's inside", "", *[f"- `{f}`" for f in files], "",
        "## How to use", "", *[f"{i}. {s}" for i, s in enumerate(steps, 1)], "",
        "## Notes", "",
        "- This is a planning and organising tool for personal use. It does not give financial, medical, "
        "legal or career advice and does not promise any result.",
        "- Sample rows and placeholder text are examples: replace them with your own.", ""])


def _build_sheets(folder, spec):
    name = f"{spec['short_name']}.xlsx"
    build_workbook(os.path.join(folder, name), spec, spec["sheets"])
    return [name], ["Open the .xlsx file in Excel.",
                    "Read the Instructions tab, then replace the sample rows with your own."]


def _build_planner(folder, spec):
    files = []
    # Ensure theme from spec or niche default is applied
    import pdf_themes
    pdf_themes.register_themes_in_pdf_design()
    planner_spec = dict(spec)
    theme = pdf_themes.get_theme_for_spec(spec)
    if "style" not in planner_spec:
        planner_spec["style"] = theme["slug"]
    for size in ("A4", "Letter"):
        name = f"{spec['short_name']}-{size}.pdf"
        build_planner_pdf(os.path.join(folder, name), planner_spec, size)
        files.append(name)
    return files, ["Print the A4 or US Letter PDF at 100% scale (or use it in a PDF annotation app).",
                   "Print as many copies as you need for personal use."]


def _build_notion(folder, spec):
    name = f"{spec['short_name']}-workspace-import.zip"
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
    guides = []
    for size in ("A4", "Letter"):
        guide_name = f"{spec['short_name']}-import-guide-{size}.pdf"
        _notion_guide_pdf(os.path.join(folder, guide_name), size, spec, name)
        guides.append(guide_name)
    return [name, "HOW_TO_IMPORT.md"] + guides, [
        "Follow HOW_TO_IMPORT.md (or the printable import guide) to bring the pages and databases into Notion."]


# ---- CCC-10: printable Notion import guide --------------------------------------------------------
# upload_pack.make only stages a pack that has a PDF or Excel file, so a Notion pack (a .zip of .md/.csv)
# could never reach /sell. The guide is a real part of the product: import steps, a printable copy of every
# page and, for every database, its columns with the Notion property type to choose and the sample rows.
# It has at least one page per Notion page and database, so page counts in a listing stay true.

def _guide_wrap(text, size, width, bold=False):
    """Word-wrap by the real Helvetica width AND by characters: deep QA estimates a line as
    characters x size x 0.5, so a line of narrow letters must not pass the 5 mm safe margin by that measure."""
    import pdf_design
    max_chars = int(width / (size * 0.5))
    lines, line = [], ""
    for word in str(text).split():
        trial = f"{line} {word}".strip()
        if line and (pdf_design.text_width(trial, size, bold) > width or len(trial) > max_chars):
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    for i in range(len(lines) - 1):                 # a 1-2 letter word never ends a line
        head, _, last = lines[i].rpartition(" ")
        if head and len(last) <= 2:
            lines[i], lines[i + 1] = head, f"{last} {lines[i + 1]}"
    return lines


def _property_type(column, values):
    low = column.lower()
    sample = [str(v) for v in values if str(v).strip()]
    if any(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in sample) or any(w in low for w in ("date", "due", "follow")):
        return "Date"
    if any(w in low for w in ("status", "stage", "type", "category", "priority")):
        return "Select"
    if any(w in low for w in ("note", "description", "summary")):
        return "Text"
    if sample and all(re.fullmatch(r"-?\d+(?:\.\d+)?", v) for v in sample):
        return "Number"
    return "Title" if column == "" else "Text"


def _notion_guide_pdf(path, size, spec, zip_name):
    import pdf_design
    import pdf_themes
    pdf_themes.register_themes_in_pdf_design()
    theme = pdf_themes.get_theme_for_spec(spec)
    tok = theme["tokens"]
    ink, muted, accent, line_c = tok["ink"], tok["muted"], tok["accent"], tok["line"]
    pdf = Pdf(size, footer=f"{spec['name']} · Notion import guide", style=theme["slug"])
    left, right = pdf_design.MARGIN, pdf.w - pdf_design.MARGIN
    bottom = pdf_design.BOTTOM + 12

    def heading(title, subtitle):
        pdf.text(left, pdf.h - 62, title[:70], 18, bold=True, color=ink)
        pdf.text(left, pdf.h - 82, subtitle[:110], 10, color=muted)
        pdf.line(left, pdf.h - 94, right, pdf.h - 94, color=line_c)
        return pdf.h - 120

    def notes(y):
        if y - 40 < bottom:
            return
        pdf.text(left, y, "Notes", 10, bold=True, color=accent)
        y -= 22
        while y >= bottom:
            pdf.line(left, y, right, y, color=line_c, width=0.5)
            y -= 22

    pages = spec["pages"] or [{"title": spec["name"], "markdown": f"# {spec['name']}"}]
    # Page 1: how to import + what is inside
    pdf.new_page()
    y = heading(f"{spec['name']}: Import Guide", "Bring the pages and databases into Notion in a few minutes.")
    steps = ["Open Notion on a computer and go to the page where you want the template.",
             "Click Import in the left sidebar (or Settings, then Import).",
             f"Choose Text & Markdown for the .md pages and CSV for the .csv databases, then pick the files "
             f"from {zip_name} (unzip it first if your browser does not open .zip files).",
             "Each .csv file becomes a Notion database and each .md file becomes a page.",
             "Set the property types shown on the database pages of this guide (for example Status to Select).",
             "Replace the sample rows with your own; they are examples only."]
    for number, step in enumerate(steps, 1):
        wrapped = _guide_wrap(step, 10, right - left - 22)
        pdf.text(left, y, f"{number}.", 10, bold=True, color=accent)
        for text in wrapped:
            pdf.text(left + 22, y, text, 10, color=ink)
            y -= 14
        y -= 6
    y -= 12
    pdf.text(left, y, "What is inside the import file", 12, bold=True, color=accent)
    y -= 20
    for p in pages:
        pdf.text(left, y, f"Page: {p['title']}"[:60], 10, color=ink)
        pdf.text(left + 260, y, f"{_slug(p['title'])}.md", 9, color=muted)
        y -= 16
    for d in spec["databases"]:
        pdf.text(left, y, f"Database: {d['name']} ({len(d['rows'])} sample rows)"[:60], 10, color=ink)
        pdf.text(left + 260, y, f"{_slug(d['name'])}.csv", 9, color=muted)
        y -= 16
    notes(y - 14)

    # One printable page per Notion page
    for p in pages:
        pdf.new_page()
        y = heading(p["title"], f"Printable copy of {_slug(p['title'])}.md")
        for raw in p["markdown"].splitlines():
            text = raw.strip()
            if not text or text.lstrip("# ").strip() == p["title"]:
                continue
            bold = text.startswith("#")
            text = re.sub(r"^#+\s*|[*_`]", "", text).strip()
            if not text:
                continue
            for part in _guide_wrap(text, 11 if bold else 10, right - left, bold):
                if y < bottom + 20:
                    break
                pdf.text(left, y, part, 11 if bold else 10, bold=bold, color=ink)
                y -= 15
            y -= 8
        notes(y - 10)

    # One page per database: property types, then the sample rows
    for d in spec["databases"]:
        pdf.new_page()
        y = heading(f"Database: {d['name']}", f"{_slug(d['name'])}.csv · {len(d['columns'])} columns")
        pdf.text(left, y, "Column", 9, bold=True, color=muted)
        pdf.text(left + 200, y, "Notion property type", 9, bold=True, color=muted)
        y -= 16
        for i, column in enumerate(d["columns"]):
            kind = "Title" if i == 0 else _property_type(column, [r[i] for r in d["rows"] if i < len(r)])
            pdf.text(left, y, str(column)[:40], 10, color=ink)
            pdf.text(left + 200, y, kind, 10, color=accent)
            pdf.line(left, y - 5, right, y - 5, color=line_c, width=0.4)
            y -= 18
        y -= 16
        pdf.text(left, y, "Sample rows (replace them with your own)", 11, bold=True, color=accent)
        y -= 18
        col_w = (right - left) / max(1, len(d["columns"]))
        for i, column in enumerate(d["columns"]):
            pdf.text(left + i * col_w + 3, y, str(column)[:18], 8, bold=True, color=muted)
        y -= 6
        for row in d["rows"]:
            if y - 18 < bottom:
                break
            for i in range(len(d["columns"])):
                pdf.rect(left + i * col_w, y - 18, col_w, 18, color=line_c, width=0.4)
                value = str(row[i]) if i < len(row) else ""
                pdf.text(left + i * col_w + 3, y - 12, value[:int(col_w / 4.6)], 8, color=ink)
            y -= 18
        notes(y - 20)
    pdf.save(path, title=f"{spec['name']} Notion import guide")


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
    # Ensure theme from spec or niche default is applied
    import pdf_themes
    pdf_themes.register_themes_in_pdf_design()
    planner_spec = dict(spec)
    theme = pdf_themes.get_theme_for_spec(spec)
    if "style" not in planner_spec:
        planner_spec["style"] = theme["slug"]
    for size in ("A4", "Letter"):
        name = f"{spec['short_name']}-{size}.pdf"
        build_dated_planner_pdf(os.path.join(folder, name), planner_spec, size)
        files.append(name)
    import pdf_design
    months, first = pdf_design.planner_months(spec)
    span = (f"{_calendar_name(months[0])} to {_calendar_name(months[-1])}")
    return files, ["Print the A4 or US Letter PDF at 100% scale (or use it in a PDF annotation app).",
                   f"Every month from {span} has its own calendar page; weeks start on "
                   f"{pdf_design.DAY_NAMES[first]}."]


def _calendar_name(year_month):
    import calendar
    return f"{calendar.month_name[year_month[1]]} {year_month[0]}"


BUILDERS = {"sheets": _build_sheets, "planner": _build_planner, "notion": _build_notion, "resume": _build_resume,
            "dated_planner": _build_dated_planner}


def get_builder_dispatch(kind: str):
    """Return the build function for kind, checking registered builders if not built-in."""
    if kind in BUILDERS:
        return BUILDERS[kind]
    try:
        import format_registry
        reg_build = format_registry.get_builder(kind)
        if reg_build:
            def _adapted_builder(folder, spec):
                res = reg_build(spec, folder)
                created_files = res if isinstance(res, (list, tuple)) else []
                if isinstance(res, dict) and isinstance(res.get("files"), (list, tuple)):
                    # builders that return a manifest (some list full paths: keep the file names only)
                    created_files = [os.path.basename(str(name)) for name in res["files"]]
                instructions = spec.get("instructions", [])
                how_to = instructions if isinstance(instructions, list) else [str(instructions)]
                renamed = standard_pdf_names(folder, spec)
                files = [renamed.get(os.path.basename(str(name)), name) for name in created_files]
                return list(files), how_to
            return _adapted_builder
    except Exception:
        pass
    return None


def standard_pdf_names(folder, spec):
    """CCB-2: give a format builder's PDFs the robot's standard name "<short_name>-A4.pdf" /
    "-Letter.pdf" when every page of the PDF really is that size (MediaBox), so the Truth Checker can
    prove the paper size the listing states. Text files in the folder that name the old file are
    updated. A PDF whose size is not proven keeps its name. Returns {old name: new name}."""
    base = re.sub(r"[^a-z0-9-]+", "-", str(spec.get("short_name") or "product").lower()).strip("-") or "product"
    renamed = {}
    pdfs = sorted(n for n in os.listdir(folder) if n.lower().endswith(".pdf"))
    for name in pdfs:
        if re.search(r"-(?:A4|Letter)\.pdf$", name):
            continue
        _count, size = _verified_pdf(os.path.join(folder, name))
        if size not in ("A4", "Letter"):
            continue
        target = f"{base}-{size}.pdf"
        taken = lambda t: os.path.exists(os.path.join(folder, t)) or t in renamed.values()  # noqa: E731
        if taken(target):
            # CCC-5: a second PDF of the same size keeps its own name inside the new one
            # ("cover.pdf" -> "<base>-cover-A4.pdf"). It sorts AFTER the main "<base>-A4.pdf", so the
            # Truth Checker (which reads the first "-A4.pdf") still reads the real product, not a cover.
            stem = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(name)[0].lower()).strip("-")
            stem = re.sub(r"-(?:a4|letter)$", "", stem) or "extra"
            target = f"{base}-{stem}-{size}.pdf"
        number = 2
        while taken(target):
            target = f"{base}-{number}-{size}.pdf"
            number += 1
        os.replace(os.path.join(folder, name), os.path.join(folder, target))
        renamed[name] = target
    if renamed:
        for entry in os.listdir(folder):
            path = os.path.join(folder, entry)
            if not os.path.isfile(path) or not entry.lower().endswith((".txt", ".md")):
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
            except (OSError, UnicodeError):
                continue
            changed = text
            for old, new in renamed.items():
                changed = changed.replace(old, new)
            if changed != text:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(changed)
    return renamed


def remove_empty_dirs(folder):
    """CCC-10: a builder may leave empty sub-folders (the flashcards builder makes marketing/images).
    upload_pack.make refuses a product folder with any sub-folder, so empty ones are removed.
    Folders with files in them are never touched. Returns the removed paths."""
    removed = []
    for root, dirs, _files in os.walk(folder, topdown=False):
        for name in dirs:
            path = os.path.join(root, name)
            if not os.path.islink(path) and not os.listdir(path):
                os.rmdir(path)
                removed.append(path)
    return removed


BUYER_EXTRAS = (".docx", ".html", ".png", ".jpg", ".jpeg", ".zip", ".md", ".txt")


def listed_files(listing_md_text):
    """The file names written in backticks under '## What you get' (the buyer promise)."""
    section = listing_md_text.split("## What you get", 1)[1] if "## What you get" in listing_md_text else ""
    section = re.split(r"\n## ", section, maxsplit=1)[0]
    return re.findall(r"^- `([^`/\\]+)`", section, re.M)


def complete_buyer_zip(product_folder, pack_dir):
    """CCC-10: upload_pack.make zips only PDF, XLSX, README and LICENSE files, so a pack lost the files its
    listing promises in other formats (resume .docx, signature .html, Notion import .zip, art / card PNGs).
    make() has already money-checked EVERY file of the product folder; this adds each file named under
    'What you get' that is missing from product.zip. Only listed files, never listing pictures.
    Returns the added names."""
    listing = os.path.join(product_folder, "listing.md")
    archive_path = os.path.join(pack_dir, "product.zip")
    if not os.path.isfile(listing) or not os.path.isfile(archive_path):
        return []
    with open(listing, encoding="utf-8") as handle:
        wanted = listed_files(handle.read())
    with zipfile.ZipFile(archive_path) as archive:
        present = set(archive.namelist())
    extras = [name for name in wanted
              if name not in present and name.lower().endswith(BUYER_EXTRAS)
              and not name.startswith(LISTING_IMAGE_PREFIXES)
              and os.path.isfile(os.path.join(product_folder, name))
              and not os.path.islink(os.path.join(product_folder, name))]
    if not extras:
        return []
    import shutil
    temp = archive_path + ".tmp"
    shutil.copyfile(archive_path, temp)
    with zipfile.ZipFile(temp, "a", zipfile.ZIP_DEFLATED) as archive:
        for name in extras:
            archive.write(os.path.join(product_folder, name), arcname=name)
    os.replace(temp, archive_path)
    return extras


def build_product(spec, approval_id, out_dir=None, folder_name=None):
    """Write all files for one spec. Returns the folder name."""
    out_dir = out_dir or PRODUCTS_DIR
    spec = dict(copy.deepcopy(spec), approval_id=approval_id)
    folder_name = folder_name or f"{approval_id}-{spec['short_name']}"
    folder = os.path.join(out_dir, folder_name)
    os.makedirs(folder, exist_ok=True)

    builder_fn = get_builder_dispatch(spec["kind"])
    if builder_fn is None:
        raise KeyError(f"No builder registered for kind '{spec['kind']}'")
    files, how_to = builder_fn(folder, spec)
    remove_empty_dirs(folder)
    if spec["kind"] in ("planner", "dated_planner") and spec.get("sheets"):
        sheet_files, sheet_how_to = _build_sheets(folder, spec)
        files += sheet_files
        how_to += sheet_how_to
    elif spec["kind"] == "sheets" and spec.get("pages"):
        pdf_files, pdf_how_to = _build_planner(folder, spec)
        files += pdf_files
        how_to += pdf_how_to

    # Keep the registered format private and stable even when buyer titles are sanitized.
    with open(os.path.join(folder, "build-format.json"), "w", encoding="utf-8") as handle:
        json.dump({"format": spec["kind"], "approval_id": approval_id}, handle)

    # Extract actual facts from built files and sanitize listing text and instructions
    facts = _delivery_facts(folder)
    spec["listing"]["title"] = _delivery_text(spec["listing"]["title"], spec, facts)
    spec["listing"]["description"] = _delivery_text(spec["listing"]["description"], spec, facts)
    spec["instructions"] = [_delivery_text(inst, spec, facts) for inst in spec["instructions"]]

    _buyer_listing(spec, dict(facts, folder=folder), files)

    _listing_md(spec, facts=facts)  # validate before writing listing media
    with open(os.path.join(folder, "cover_image_idea.txt"), "w", encoding="utf-8") as f:
        f.write((spec["cover_image_idea"] or "A clean flat-lay of the product on a laptop screen.") + "\n")
    with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as f:
        f.write(LICENSE_TEXT)
    extra = ["listing.md", "cover_image_idea.txt", "LICENSE.txt"]
    try:  # listing images; a drawing problem never blocks the product files
        theme = listing_theme(folder, spec)
        if theme is not None:
            spec["design_theme"] = theme["name"]       # written into listing.md so later re-draws match
        extra += write_listing_images(folder, spec)    # cover, previews, pin, thumbnail in the pack's theme
    except Exception as exc:
        print(f"Product Builder: listing images skipped for #{approval_id}: {type(exc).__name__}", file=sys.stderr)
    with open(os.path.join(folder, "README.md"), "w", encoding="utf-8") as f:
        f.write(_readme_md(spec, files + extra, how_to, facts=_delivery_facts(folder)))
    with open(os.path.join(folder, "listing.md"), "w", encoding="utf-8") as f:
        f.write(_listing_md(spec, facts=_delivery_facts(folder)))
    return folder_name


# ---- CCB-1: listing images in the pack's own theme ---------------------------------------
# Cover, previews, the Pinterest image and the thumbnail use the SAME pdf_themes theme as the PDF
# (colours; the images keep the Inter font of mockups.py for every theme, the themes define no font).
#   - A PDF written with a premium theme decides: the images follow the PDF, always.
#   - A PDF in the old Style A / B keeps the matching old image colours (nothing changes).
#   - No themed PDF (spreadsheet, resume, workspace template, builder formats): the theme of the spec,
#     exactly as _build_planner picks it (spec theme -> niche default -> title hint -> Calm Sage).
# mockups.py draws; when it has no real pages for a format (flashcards, wall art, email signature,
# event planner), the same drawing functions are fed the product's REAL image files / PDF pages.
DESIGN_THEME_LABEL = "Design theme:"
LISTING_IMAGE_NAMES = ("cover.png", "preview-1.png", "preview-2.png", "preview-3.png", "pinterest.png", "thumbnail.png")
KIND_LABELS = {"wall_art": "WALL ART PRINT SET", "flashcards": "PRINTABLE FLASHCARDS", "event_planner": "EVENT PLANNER",
               "email_signature": "EMAIL SIGNATURE TEMPLATES", "habit_tracker": "HABIT TRACKER",
               "study_planner": "STUDY PLANNER", "recipe_book": "RECIPE BOOK TEMPLATE"}


def recorded_theme(folder):
    """The theme name written into listing.md when the product was built, or None."""
    try:
        with open(os.path.join(folder, "listing.md"), encoding="utf-8") as f:
            match = re.search(r"(?m)^" + re.escape(DESIGN_THEME_LABEL) + r"\s*(.+?)\.?\s*$", f.read())
    except OSError:
        return None
    return match.group(1).strip() if match else None


def listing_theme(folder, spec=None):
    """The pdf_themes theme for this product's listing images, or None to keep the old Style A / B look."""
    import pdf_design
    import pdf_themes
    pdf_themes.register_themes_in_pdf_design()          # so the PDF's own style name is recognised
    spec = dict(spec or {})
    try:
        pdfs = sorted(n for n in os.listdir(folder) if n.lower().endswith(".pdf"))
    except OSError:
        pdfs = []
    for name in sorted(pdfs, key=lambda n: (not n.endswith("-A4.pdf"), n)):
        style = pdf_design.pdf_style(os.path.join(folder, name))
        if style:
            try:
                return pdf_themes.get_theme(style)       # the images follow the PDF
            except KeyError:
                return None                              # old Style A / B: mockups already matches it
    if not spec.get("theme") and recorded_theme(folder):
        spec["theme"] = recorded_theme(folder)
    return pdf_themes.get_theme_for_spec(spec)


class _listing_style:
    """While active, mockups sees `slug` as the product's style (it reads the style from the
    Truth Checker's image facts). The facts themselves are untouched: only the colour choice changes."""

    def __init__(self, slug):
        self.slug = slug

    def __enter__(self):
        import truth_check
        self.module, self.original = truth_check, truth_check.image_facts
        if self.slug:
            def themed(folder, _original=self.original, _slug=self.slug):
                return dict(_original(folder), style=_slug)
            truth_check.image_facts = themed
        return self

    def __exit__(self, *exc):
        self.module.image_facts = self.original
        return False


def _real_picture_pages(folder, facts):
    """Pages for formats mockups cannot read: the product's own image files, else its PDF pages."""
    import mockups
    from PIL import Image
    names = sorted(os.listdir(folder))
    pictures = [n for n in names if n.lower().endswith((".png", ".jpg", ".jpeg"))
                and n not in LISTING_IMAGE_NAMES and not n.lower().startswith(("cover", "preview-", "pinterest", "thumbnail"))]
    pages = []
    for name in pictures[:6]:
        path = os.path.join(folder, name)
        try:
            with Image.open(path) as im:
                width, height = im.size
        except Exception:
            continue

        def picture(h, _path=path):
            with Image.open(_path) as im:
                return mockups._fit_height(im.convert("RGB"), h)
        title = re.sub(r"[_-]+", " ", os.path.splitext(name)[0]).strip().title()
        pages.append({"title": title, "kind": "page", "number": None, "picture": picture,
                      "bullets": ["Real file from this pack", f"{width} x {height} px image",
                                  os.path.splitext(name)[1].lstrip(".").upper() + " file"]})
    if not pages and facts.get("pdf_path") and mockups.available():
        total = int(facts.get("pages") or 0)
        for number in range(1, min(total, 4) + 1):
            def picture(h, _n=number, _pdf=facts["pdf_path"]):
                return mockups.render_pdf_page(_pdf, _n, h)
            sizes = " + ".join("US Letter" if s == "Letter" else s for s in (facts.get("sizes") or []))
            pages.append({"title": f"Page {number} of the PDF", "kind": "page", "number": number, "picture": picture,
                          "bullets": [b for b in (f"{total} pages in the PDF", sizes, "Printable PDF") if b]})
        if len(pages) > 1:
            pages = pages[1:] + pages[:1]               # show inside pages first, the PDF's own cover last
    return pages, pictures


def _fallback_listing_images(folder, spec, theme):
    """Cover / previews / pin / thumbnail for a format without mockups pages, drawn by the same mockups
    functions from real files only. Raises ValueError when the folder has nothing to show."""
    import mockups
    import truth_check
    facts = truth_check.image_facts(folder)
    pages, pictures = _real_picture_pages(folder, facts)
    if not pages:
        raise ValueError("no real pages to show")
    spec = spec or {}
    chips = []
    if pictures:
        chips.append(f"{len(pictures)} image files")
    if facts.get("pdf_path"):
        chips.append(f"{facts['pages']} PDF pages")
    if facts.get("xlsx"):
        chips.append("Spreadsheet file (.xlsx)")
    chips = chips or ["Digital template files"]
    slug = theme["slug"] if theme else "A"
    info = {"name": mockups.no_brands(spec.get("name") or spec_from_folder(folder)["name"]) or "Digital template",
            "label": KIND_LABELS.get(spec.get("kind"), "DIGITAL TEMPLATE"), "kind": facts["kind"],
            "theme": mockups._theme_for(slug), "chips": chips[:4], "pages": pages, "total": facts.get("pages") or len(pages),
            "description": mockups._join(chips[:3]) + ", ready to download."}
    previews = pages[:3]
    while len(previews) < 3:
        previews.append(previews[len(previews) % len(pages)])
    images = {"cover.png": mockups.cover_image(info), "pinterest.png": mockups.pin_image(info)}
    for index, page in enumerate(previews, 1):
        images[f"preview-{index}.png"] = mockups.preview_image(info, page)
    images["thumbnail.png"] = mockups.thumbnail_image(info, images["cover.png"])
    for name, image in images.items():
        image.save(os.path.join(folder, name), optimize=True)
    return list(LISTING_IMAGE_NAMES)


def write_listing_images(folder, spec=None):
    """Write the listing images (and draft pins) of one product folder in its theme. Returns the names."""
    import mockups
    spec = spec or {}
    theme = listing_theme(folder, spec)
    with _listing_style(theme["slug"] if theme else None):
        try:
            names = list(mockups.write_mockups(folder, spec))
        except ValueError:
            names = _fallback_listing_images(folder, spec, theme)
        if spec.get("pins"):
            names += mockups.write_pins(folder, spec)     # draft Pinterest pins, only when the spec lists them
    return names


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
    recorded = recorded_theme(folder)
    extra = {"theme": recorded} if recorded else {}
    pdfs = [n for n in names if n.endswith(".pdf")]
    if pdfs:
        pdf = next((n for n in pdfs if n.endswith("-A4.pdf")), pdfs[0])
        return dict({"kind": "planner", "name": name,
                     "pages": planner_pages_from_pdf(os.path.join(folder, pdf))}, **extra)
    if any(n.endswith("-resume.docx") for n in names):
        return dict({"kind": "resume", "name": name, "resume": [], "cover_letter": []}, **extra)
    if any(n.endswith(("-notion-import.zip", "-workspace-import.zip")) for n in names):
        return dict({"kind": "notion", "name": name, "pages": [], "databases": []}, **extra)
    return dict({"kind": "sheets", "name": name, "sheets": []}, **extra)


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
            write_listing_images(folder, spec_from_folder(folder))
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
# The holds above were decided for the drafts #4 and #6 that existed on that day. A new or restored database
# reuses those numbers for other drafts, which must not be blocked silently (CCE-2, found by the day simulator).
DATED_HOLDS = {4: "2026-10-16", 6: "2026-10-16"}


def is_held(approval_id, db_path=None):
    """True when the owner's NOT FOR SALE hold applies to this draft. Unknown date -> held (the safe side)."""
    if approval_id not in OWNER_HOLDS:
        return False
    decided = DATED_HOLDS.get(approval_id)
    if not decided:
        return True
    try:
        item = guardrails.get_approval(approval_id, db_path)
    except Exception:
        return True
    try:
        made = datetime.date.fromisoformat(str((item or {}).get("ts") or "")[:10])
    except ValueError:
        return True
    return made <= datetime.date.fromisoformat(decided)


def apply_owner_holds(db_path=None, out_dir=None):
    """Mark held products NOT FOR SALE in the build table and at the top of their README. Never deletes."""
    out_dir = out_dir or PRODUCTS_DIR
    done = []
    conn = connect(db_path)
    for approval_id, reason in OWNER_HOLDS.items():
        if not is_held(approval_id, db_path):
            continue
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
    if result["verdict"] == "ready":
        try:
            import upload_pack
            pack_dir = upload_pack.make(folder, os.path.join(out_dir, "upload"))
            complete_buyer_zip(folder, pack_dir)
        except Exception as exc:
            print(f"Upload pack refused: {exc}", file=sys.stderr)
    held = is_held(approval_id, db_path)
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
        if is_held(approval_id, db_path) or not _rebuild_target_ok(approval_id, spec, db_path):
            continue
        folder = f"{approval_id}-{version}-{spec['short_name']}"
        revision = int(spec.get("revision") or 1)
        if os.path.isdir(os.path.join(out_dir, folder)) and _built_revision(db_path, folder) >= revision:
            continue
        old = next((b["folder"] for b in builds(db_path) if b["approval_id"] == approval_id), "") or ""
        build_product(spec, approval_id, out_dir, folder_name=folder)   # same folder: files are replaced
        _set_revision(db_path, folder, revision)
        _record(db_path, approval_id, "built", folder, f"{version} rev {revision}; older version kept in "
                                                        f"{old if old != folder else '-'}")
        verdict = truth_check_folder(approval_id, folder, db_path, out_dir, notify_fn)["verdict"]
        done.append((approval_id, folder, verdict))
    return done


def _rebuild_target_ok(approval_id, spec, db_path=None):
    """A hand-checked spec names its draft by number only. Build it only for that real draft: the draft must
    be approved and come from the spec's niche agent (or, with no draft row at all, already have a built
    product to replace). A new or restored database where #<id> is a different or pending draft must never
    get this product."""
    item = guardrails.get_approval(approval_id, db_path)
    if item is None:
        return any(b["approval_id"] == approval_id and b["folder"] for b in builds(db_path))
    if item.get("status") != "approved" or item.get("kind") != "public_draft":
        return False
    return not spec.get("niche") or niche_of(item.get("title")) == spec["niche"]


def _built_revision(db_path, folder):
    """Which revision of a hand-checked spec a folder was built from (1 when it was built before revisions)."""
    conn = connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS spec_revisions (folder TEXT PRIMARY KEY, revision INTEGER)")
    row = conn.execute("SELECT revision FROM spec_revisions WHERE folder = ?", (folder,)).fetchone()
    conn.close()
    return row[0] if row else 1


def _set_revision(db_path, folder, revision):
    conn = connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS spec_revisions (folder TEXT PRIMARY KEY, revision INTEGER)")
    conn.execute("INSERT OR REPLACE INTO spec_revisions (folder, revision) VALUES (?, ?)", (folder, revision))
    conn.commit()
    conn.close()


BUNDLE_DIR = os.path.join(BASE_DIR, "specs", "bundles")


def bundle_spec(path):
    """A bundle spec with its pages taken from the hand-checked specs it names (the real pages, in order)."""
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    pages, daily = [], False
    for part in spec["bundle_of"]:
        with open(os.path.join(BASE_DIR, part), encoding="utf-8") as f:
            source = json.load(f)
        daily = daily or bool(source.get("daily_page"))
        pages += source.get("pages") or []
    spec.update({"kind": "planner", "pages": pages, "daily_page": daily})
    return spec


def build_bundles(db_path=None, out_dir=None, spec_dir=None, notify_fn=None):
    """Build each bundle in specs/bundles once per revision into bundle-<name>/, truth-check it, and send one
    Telegram line when its verdict changes. A bundle is never marked for sale: the owner decides.
    Returns [(folder, verdict)]."""
    import truth_check
    out_dir, spec_dir = out_dir or PRODUCTS_DIR, spec_dir or BUNDLE_DIR
    done = []
    if not os.path.isdir(spec_dir):
        return done
    for name in sorted(os.listdir(spec_dir)):
        if not name.endswith(".json"):
            continue
        spec = bundle_spec(os.path.join(spec_dir, name))
        folder = f"bundle-{spec['short_name']}"
        revision = int(spec.get("revision") or 1)
        if os.path.isdir(os.path.join(out_dir, folder)) and _built_revision(db_path, folder) >= revision:
            continue
        build_product(spec, "bundle", out_dir, folder_name=folder)
        _set_revision(db_path, folder, revision)
        result = truth_check.check(os.path.join(out_dir, folder))
        with open(os.path.join(out_dir, folder, "TRUTH_CHECK.md"), "w", encoding="utf-8") as f:
            f.write(truth_check.report_md(spec["name"], result))
        conn = connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS bundle_builds (folder TEXT PRIMARY KEY, verdict TEXT, "
                     "score INTEGER, notified TEXT)")
        old = conn.execute("SELECT notified FROM bundle_builds WHERE folder = ?", (folder,)).fetchone()
        sent = old[0] if old else None
        if sent != result["verdict"]:
            try:
                if notify_fn is None:
                    import notify
                    notify_fn = notify.send
                text = truth_check.telegram_text("bundle", spec["name"], result).replace("Product #bundle", "Bundle:")
                if notify_fn(text):
                    sent = result["verdict"]
            except Exception:
                pass
        conn.execute("INSERT OR REPLACE INTO bundle_builds (folder, verdict, score, notified) VALUES (?, ?, ?, ?)",
                     (folder, result["verdict"], result["score"], sent))
        conn.commit()
        conn.close()
        done.append((folder, result["verdict"]))
    return done


def bundles(db_path=None):
    conn = connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS bundle_builds (folder TEXT PRIMARY KEY, verdict TEXT, "
                 "score INTEGER, notified TEXT)")
    rows = conn.execute("SELECT folder, verdict, score FROM bundle_builds ORDER BY folder").fetchall()
    conn.close()
    return rows


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
    made = [(f, v) for f, v, _ in bundles(db_path) if os.path.isdir(os.path.join(out_dir, f))]
    if made:
        lines += ["", "## Bundles (pages from the products above, in one file)", "", "| Bundle | Status |",
                  "|---|---|"]
        lines += [f"| [{f}]({f}/) | " + ("ready (truth check passed) - the owner decides if it goes on sale"
                                         if v == "ready" else "needs fix - see TRUTH_CHECK.md") + " |"
                  for f, v in made]
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
    import cubes
    if not guardrails.automation_allowed(db_path) or cubes.get_cube_state("product", db_path) != "on":
        return []
    out_dir = out_dir or PRODUCTS_DIR
    results, attempts = [], 0
    for item in _core_todo(db_path, out_dir):
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
            spec, reason = request_spec(lambda prompt: workforce._ask({"llm": llm}, prompt, size="large"),
                                        niche, item["payload"])
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
        tc_res = truth_check_folder(item["id"], folder, db_path, out_dir)
        verdict = tc_res["verdict"]
        if verdict != "ready":
            # Auto-rebuild once if truth check failed
            folder = build_product(spec, item["id"], out_dir, folder_name=folder)
            tc_res = truth_check_folder(item["id"], folder, db_path, out_dir)
            verdict = tc_res["verdict"]
        results.append({"id": item["id"], "status": "built" if verdict == "ready" else "needs_fix",
                        "folder": folder, "reasons": tc_res.get("problems", [])})
    if results:
        write_index(db_path, out_dir)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chilli Gavva Product Builder")
    parser.add_argument("--max", type=int, default=MAX_BUILDS_PER_RUN, help="legacy batch hint; approved work drains within time and AI-call budgets")
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
    if not guardrails.automation_allowed(args.db):
        print("Product Builder: automation held by owner policy.")
        return
    import cubes
    if cubes.get_cube_state("product", args.db) != "on":
        print("Product Builder: product cube is Off.")
        return
    import rest_window
    if rest_window.in_rest_window():
        print("Product Builder: daily rest window, nothing built.")
        return
    try:
        _build_all(args)
    except OSError as exc:
        if not disk_full(exc):
            raise
        # A full disk is not a code problem: no traceback, no red run. One owner line a day (CCD-1 gate in
        # notify.send); the draft keeps its place and the next run builds it again (CCE-3 bad-day simulator).
        print("Product Builder: disk full (no space left), nothing more written this run; next run retries.")
        try:
            import notify
            notify.send(DISK_FULL_MESSAGE, db_path=args.db)
        except Exception as send_exc:
            print(f"Product Builder: disk full message not sent ({type(send_exc).__name__}).")


DISK_FULL_MESSAGE = ("Chilli Gavva: disk full ayyindi, product files raayalekapoyanu. Drafts safe ga unnayi; "
                     "space free ayyaka next run lo robot malli build chestundi.")


def disk_full(exc):
    """True for 'no space left' style errors (full disk, quota, read-only disk)."""
    import errno
    return isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1), errno.EROFS)


def _build_all(args):
    import product_pipeline
    for approval_id, name in product_pipeline.check_ready_files(args.db):
        print(f"Product Builder: pack #{approval_id} has a damaged file ({name}); taken back for a rebuild.")
    results = product_pipeline.run_pending(db_path=args.db, max_builds=args.max)
    rebuilt = rebuild_versions(args.db)
    for approval_id, folder, verdict in rebuilt:
        print(f"Product Builder: new version of #{approval_id} -> {folder} ({verdict})")
    for folder, verdict in build_bundles(args.db):
        rebuilt.append((None, folder, verdict))
        print(f"Product Builder: bundle {folder} ({verdict})")
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
