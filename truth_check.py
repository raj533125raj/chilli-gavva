"""truth_check.py — the Truth Checker: does a BUILT product deliver every promise its listing makes?

Rules adapted from the "Reality Checker" agent in msitarzewski/agency-agents (MIT licence) — credit to
that project:
  * the default answer is NEEDS FIX: a product is "ready" only when the files prove every promise;
  * a score of 95 or more is a red flag that needs extra proof (enough promises checked, no empty pages);
  * claims are checked against the REAL FILES in the product folder, never against the draft text.

It also checks the quality of the built files: page count, almost-empty pages, broken characters
("?" where a dash or quote should be), text cut off on the pages or in the listing images, raw "**"
in images, and words that lost their apostrophe.

Usage:  python3 truth_check.py exported_content/products/4-notion-semester-planner
"""
import csv
import io
import os
import re
import sys
import zipfile

READY_MIN = 80        # below this a product always needs a fix
RED_FLAG = 95         # at or above this, extra proof is required
EXTRA_PROOF_PROMISES = 3

# What a plain import / printable file can NEVER deliver, by product kind (claim -> why not).
# A promise matching one of these must be proven by the files, or it is not delivered.
CLAIMS = [
    ("dashboard", r"\bdashboards?\b"),
    ("linked databases", r"\b(?:linked|relations?|relational|linked to|roll-?ups?)\b"),
    ("formulas / automatic calculation", r"\b(?:formulas?|auto(?:matic(?:ally)?)?[- ]calculat\w*|calculates? (?:automatically|instantly)|instant(?:ly)? (?:gpa|totals?|calculation)|updat\w* automatically|recalculat\w*)\b"),
    ("calendar / board / timeline views", r"\b(?:calendar views?|kanban|timeline views?|gallery views?|board views?|(?:calendar|list|table)\s*,\s*(?:list|kanban|table|calendar))\b"),
    ("buttons", r"\b(?:buttons?|one[- ]click|single click)\b"),
    ("duplicate link", r"\bduplicate\b"),
    ("fillable / editable PDF", r"\b(?:fillable|editable pdf|type (?:on|into) the pdf|hyperlinked|hyperlinks?|clickable)\b"),
    ("charts / progress bars", r"\b(?:charts?|graphs?|progress bars?)\b"),
    ("drop-down menus", r"\b(?:drop-?downs?|select (?:field|option|menu)s?)\b"),
    ("weekly or daily dated pages", r"\b(?:weekly (?:spreads?|pages?)|daily (?:dated )?pages?|dated weekly|dated daily)\b"),
    ("stickers / digital tablet version", r"\b(?:stickers?|goodnotes|notability|digital planner|tablet version)\b"),
]
APOSTROPHE_WORDS = re.compile(r"\b(?:dont|doesnt|didnt|cant|wont|isnt|arent|wasnt|youre|youll|youve|theyre|"
                              r"thats|whats|lets go|todays|weeks goals|months goals|its time)\b", re.IGNORECASE)
CUT_LIMITS = {18, 44, 64, 70, 88}   # the old PDF builder cut text at these lengths


# ---- facts: what the files really contain ---------------------------------------------------------------
def _pdf_pages(path):
    """Text items per page of a PDF made by our builder: [[(font, size, x, y, text)], ...]."""
    with open(path, "rb") as f:
        data = f.read().decode("latin-1")
    op = re.compile(r"BT /(F1|F2) ([\d.]+) Tf ([\d.]+) ([\d.]+) Td \((.*)\) Tj ET$")
    pages = []
    for stream in re.findall(r"stream\n(.*?)\nendstream", data, re.S):
        items, low = [], None
        for line in stream.splitlines():
            m = op.match(line)
            if m:
                raw = m.group(5).replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
                text = raw.encode("latin-1", "replace").decode("cp1252", "replace")
                items.append((m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4)), text))
                ys = [float(m.group(4))]
            else:   # lines, boxes and shapes: the lowest point they reach
                ys = [float(v) for v in re.findall(r"[\d.]+ ([\d.]+) (?:m|l)\b", line)]
                ys += [float(v) for v in re.findall(r"[\d.]+ ([\d.]+) [\d.]+ [\d.]+ re [SfB]", line)]
            for y in ys:
                if y > 50 and (low is None or y < low):    # the footer (under y = 50) does not count
                    low = y
        drawn = len(re.findall(r" (?:l|re|h) [SfB]\b", stream))
        pages.append({"items": items, "drawn": drawn, "low": low})
    return pages


def facts(folder):
    """Everything the folder really delivers."""
    names = sorted(os.listdir(folder))
    f = {"files": names, "kind": "unknown", "pages": 0, "titles": [], "headings": [], "texts": [],
         "pdf_sizes": [], "sheets": [], "formulas": 0, "charts": 0, "validations": 0, "csv": {}, "md": {},
         "page_items": []}
    pdfs = [n for n in names if n.endswith(".pdf")]
    if pdfs:
        f["kind"] = "pdf"
        f["pdf_sizes"] = [n.rsplit("-", 1)[-1][:-4] for n in pdfs]
        pages = _pdf_pages(os.path.join(folder, next((n for n in pdfs if n.endswith("-A4.pdf")), pdfs[0])))
        f["pages"] = len(pages)
        f["page_items"] = pages
        with open(os.path.join(folder, next((n for n in pdfs if n.endswith("-A4.pdf")), pdfs[0])), "rb") as fh:
            box = re.search(rb"/MediaBox \[0 0 [\d.]+ ([\d.]+)\]", fh.read())
        f["page_height"] = float(box.group(1)) if box else 842
        for p in pages:
            big = []
            for font, size, _, _, text in p["items"]:
                f["texts"].append(text)
                if font == "F2" and size >= 18:
                    big.append(text)
                elif font == "F2" and size >= 10.5:
                    f["headings"].append(text)
            if big:      # one title per page: "January" + "2027" -> "January 2027", two-line titles joined
                f["titles"].append(" ".join(big))
    for n in names:
        path = os.path.join(folder, n)
        if n.endswith(".zip"):
            f["kind"] = "notion" if "notion" in n else f["kind"]
            with zipfile.ZipFile(path) as z:
                for member in z.namelist():
                    body = z.read(member).decode("utf-8", "ignore")
                    if member.endswith(".csv"):
                        rows = list(csv.reader(io.StringIO(body)))
                        f["csv"][member] = {"columns": rows[0] if rows else [], "rows": max(0, len(rows) - 1)}
                    elif member.endswith(".md"):
                        f["md"][member] = body
        elif n.endswith(".xlsx"):
            f["kind"] = "sheets" if f["kind"] == "unknown" else f["kind"]
            with zipfile.ZipFile(path) as z:
                wb = z.read("xl/workbook.xml").decode("utf-8", "ignore")
                f["sheets"] += re.findall(r'<sheet [^>]*name="([^"]+)"', wb)
                for member in z.namelist():
                    if member.startswith("xl/worksheets/"):
                        body = z.read(member).decode("utf-8", "ignore")
                        f["formulas"] += body.count("<f>") + body.count("<f ")
                        f["validations"] += body.count("<dataValidation ")
                    if member.startswith("xl/charts/"):
                        f["charts"] += 1
        elif n.endswith(".docx"):
            f["kind"] = "resume" if f["kind"] == "unknown" else f["kind"]
    return f


def _names(f):
    """Every named part the files really contain (pages, headings, sheets, databases, md pages)."""
    out = list(f["titles"]) + list(f["headings"]) + list(f["sheets"])
    out += [os.path.splitext(n)[0].replace("-", " ") for n in list(f["csv"]) + list(f["md"])]
    out += [line.lstrip("# ").strip() for body in f["md"].values() for line in body.splitlines()
            if line.startswith("#")]
    return [n.lower() for n in out if n]


def _proven(claim, f):
    """Return (True, evidence) or (False, reason) for one claim, using only the files."""
    names = _names(f)
    kind = f["kind"]
    if claim == "dashboard":
        if kind == "sheets" and any("dashboard" in s.lower() for s in f["sheets"]):
            return True, "a Dashboard sheet exists"
        if kind == "pdf" and any("dashboard" in t.lower() for t in f["titles"]):
            return True, "a Dashboard page exists"
        return False, "no real dashboard in the files (a page that only describes one is not a dashboard)"
    if claim == "linked databases":
        if kind == "sheets" and f["formulas"]:
            return True, "sheets are linked by formulas"
        return False, "the files contain separate tables only; nothing is linked"
    if claim == "formulas / automatic calculation":
        if f["formulas"]:
            return True, f"{f['formulas']} formulas in the workbook"
        return False, "no formulas in the files (CSV and PDF files cannot calculate)"
    if claim == "calendar / board / timeline views":
        return False, "no saved views in the files (a CSV import makes plain tables)"
    if claim == "buttons":
        return False, "no buttons in the files"
    if claim == "duplicate link":
        return False, "there is no 'Duplicate' template link, only files to import"
    if claim == "fillable / editable PDF":
        return False, "the PDFs are print-only (no fillable fields or links)"
    if claim == "charts / progress bars":
        if f["charts"]:
            return True, "the workbook has charts"
        return False, "no charts in the files"
    if claim == "drop-down menus":
        if f["validations"]:
            return True, "the workbook has drop-down lists"
        return False, "no drop-down lists in the files"
    if claim == "weekly or daily dated pages":
        dated = [t for t in f["titles"] if re.search(r"\bweek\b|\b\d{1,2} \w+ 20\d\d\b", t.lower())]
        return (True, "dated weekly/daily pages exist") if dated else (False, "no weekly or daily dated pages")
    if claim == "year overview page":
        ok = any(re.search(r"\bat a glance\b|\byear overview\b", t.lower()) for t in f["titles"])
        return (True, "a year overview page exists") if ok else (False, "no year overview page in the files")
    if claim == "12 monthly calendar pages":
        import calendar
        months = {t.split()[0] for t in f["titles"] if len(t.split()) == 2 and t.split()[0] in calendar.month_name[1:]}
        return (True, "12 month pages exist") if len(months) == 12 else (False, f"only {len(months)} month pages")
    if claim == "stickers / digital tablet version":
        return False, "no stickers or tablet version in the files"
    return False, "not found in the files"


_NUMBERS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                             "thirteen fourteen fifteen sixteen".split())}
_COUNT = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen"
                    r"|fifteen|sixteen)\s+(?:[a-z-]+,?\s){0,4}?(pages|sheets|tabs|worksheets)\b")
_NOT_NAMES = {"the", "this", "each", "a", "an", "your", "one", "every", "new", "all", "any", "both", "extra",
              "blank", "printable", "simple", "single", "notion", "pdf", "letter", "a4", "gumroad", "start",
              "home", "first", "last", "next", "main", "two", "three", "four", "five", "six"}
_NAMED = re.compile(r"\b((?:[A-Z][\w'’&-]*\s){0,3}[A-Z][\w'’&-]*)\s+(database|page|sheet|tab|tracker|section)s?\b")


def promises(folder):
    """(claim, where) pairs from the listing, README and the product's own pages."""
    texts = {}
    for n in ("listing.md", "README.md"):   # HOW_TO_IMPORT.md is the builder's own import guide
        path = os.path.join(folder, n)
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="ignore") as fh:
                body = fh.read()
            # the builder's own boilerplate is not a promise to the buyer
            body = re.sub(r"(?s)## Cover image idea.*?(?=\n## |\Z)", "", body)
            body = re.sub(r"(?s)## What's inside.*?(?=\n## |\Z)", "", body)
            texts[n] = body
    f = facts(folder)
    for member, body in f["md"].items():
        texts[member] = body
    found = []
    for where, body in texts.items():
        low = body.lower()
        for m in _COUNT.finditer(low):
            n = _NUMBERS.get(m.group(1), m.group(1))
            found.append((f"count: {n} {m.group(2)}", where))
        if "at a glance" in low or "year overview" in low:
            found.append(("year overview page", where))
        if re.search(r"\b(?:12|twelve) monthly\b", low):
            found.append(("12 monthly calendar pages", where))
        if where in ("listing.md", "README.md"):
            if re.search(r"\ba4\b", low):
                found.append(("size: A4", where))
            if re.search(r"\b(?:us )?letter\b(?! to\b)", low) and "cover letter" not in low:
                found.append(("size: Letter", where))
        for claim, rx in CLAIMS:
            if re.search(rx, low) and (claim, where) not in found:
                found.append((claim, where))
        for m in _NAMED.finditer(body):
            words = m.group(1).split()
            while words and words[0].lower() in _NOT_NAMES:     # "A Resources database" -> "Resources"
                words = words[1:]
            if not words:
                continue
            part = f"{' '.join(words)} {m.group(2)}".strip()
            found.append((f"named part: {part}", where))
    return found, f


_SHORT_OK = {"am", "pm", "to", "of", "in", "on", "up", "it", "is", "a", "i", "by", "at", "go", "do", "me", "my",
             "we", "us", "or", "an", "as", "be", "no", "so"}


def looks_cut(text):
    """A line that stops in the middle: an open bracket, a word broken after a hyphen, or a stray stub."""
    t = text.rstrip()
    last = re.findall(r"[A-Za-z]+$", t)
    return (t.count("(") > t.count(")") or bool(re.search(r"-[A-Za-z]$", t))
            or bool(last and len(last[0]) <= 2 and last[0].lower() not in _SHORT_OK))


def file_quality(f, image_texts=None):
    """Problems with the built files themselves."""
    problems = []
    if f["kind"] == "pdf":
        if f["pages"] < 2:
            problems.append(f"only {f['pages']} page(s)")
        for i, page in enumerate(f["page_items"], 1):
            ys = [y for _, _, _, y, _ in page["items"] if y > 45]
            used = (max(ys) - min(ys)) if len(ys) > 1 else 0
            if i > 1 and len(page["items"]) <= 4 and page["drawn"] < 8 and used < 300:
                problems.append(f"page {i} is almost empty")
            elif page.get("low") is not None and page["low"] > 0.35 * f.get("page_height", 842):
                problems.append(f"page {i} has a big empty area at the bottom")
        broken = [t for t in f["texts"] if re.search(r"\w\?\w|\s\?\s|\(\d+\?\d+", t) or "�" in t]
        if broken:
            problems.append(f"broken characters ('?' instead of a dash or quote) in {len(broken)} line(s)")
        cut = [t for t in f["texts"] if len(t) in CUT_LIMITS and looks_cut(t)]
        if cut:
            problems.append(f"text cut off on the page ({len(cut)} line(s), e.g. '{cut[0][-12:]}')")
        lost = [t for t in f["texts"] if APOSTROPHE_WORDS.search(t)]
        if lost:
            problems.append(f"missing apostrophes ({len(lost)} line(s))")
    if f["kind"] == "notion":
        short = [m for m, body in f["md"].items() if len(body) < 700]
        if short:
            problems.append(f"{len(short)} short text page(s) under 700 characters")
        thin = [m for m, info in f["csv"].items() if info["rows"] <= 3]
        if thin:
            problems.append(f"{len(thin)} table(s) with only 3 or fewer sample rows")
    for text in image_texts or []:
        if "**" in text:
            problems.append("raw ** symbols in the listing images")
            break
    for text in image_texts or []:
        if (text.endswith("...") or text.endswith("\u2026")) and len(text) > 6:
            problems.append("text cut off in the listing images")
            break
    if image_texts:   # a number on an image must be a proven fact
        shown = {t for t in image_texts if re.fullmatch(r"\d+ (?:pages|sheets)", t)}
        allowed = {f"{f.get('pages', 0)} pages", f"{len(f.get('sheets') or [])} sheets"}
        if shown - allowed:
            problems.append(f"the listing images show '{sorted(shown - allowed)[0]}', the files do not prove it")
    return problems


def image_facts(folder):
    """The facts the listing images may show, ALL proven by the files: page count, paper sizes, the year
    of a dated planner (only when all 12 month pages exist), sheet count, formulas, drop-downs."""
    import pdf_design
    f = facts(folder)
    out = {"kind": f["kind"], "pages": f["pages"], "sizes": sorted(set(f["pdf_sizes"]), key=["A4", "Letter"].index)
           if set(f["pdf_sizes"]) <= {"A4", "Letter"} else sorted(set(f["pdf_sizes"])), "dated": None,
           "style": "A", "sheet_count": len(f["sheets"]), "formulas": f["formulas"] > 0,
           "dropdowns": f["validations"] > 0, "pdf_path": None,
           "xlsx": [n for n in f["files"] if n.endswith(".xlsx")], "docx": [n for n in f["files"] if n.endswith(".docx")],
           "zips": [n for n in f["files"] if n.endswith(".zip")]}
    if f["kind"] == "pdf":
        pdfs = [n for n in f["files"] if n.endswith(".pdf")]
        out["pdf_path"] = os.path.join(folder, next((n for n in pdfs if n.endswith("-A4.pdf")), pdfs[0]))
        out["style"] = pdf_design.pdf_style(out["pdf_path"]) or "A"
        if _proven("12 monthly calendar pages", f)[0]:
            years = {t.split()[1] for t in f["titles"] if len(t.split()) == 2 and t.split()[1].isdigit()}
            out["dated"] = years.pop() if len(years) == 1 else None
    return out


def image_texts(folder):
    """The words the listing images show (drawn again in memory, nothing is written). Needs the free image
    tools (Pillow + poppler); without them there is nothing to check."""
    import mockups
    if not mockups.available():
        return []
    mockups.DRAWN = []
    try:
        mockups.make_images(folder)
        return list(mockups.DRAWN)
    except Exception:
        return []
    finally:
        mockups.DRAWN = None


def check(folder, with_images=True):
    """The Truth Checker verdict for one product folder."""
    found, f = promises(folder)
    unmet, met = [], []
    for claim, where in found:
        if claim.startswith("count: "):
            n, what = claim[len("count: "):].split(" ", 1)
            have = f["pages"] if f["kind"] == "pdf" else len(f["sheets"]) or len(f["md"]) + len(f["csv"])
            ok = int(n) <= have
            (met if ok else unmet).append((claim, where, f"the files have {have}" if ok
                                           else f"promises {n} {what}, the files have only {have}"))
            continue
        if claim.startswith("size: "):
            size = claim[len("size: "):]
            ok = size in f["pdf_sizes"] or f["kind"] != "pdf"
            (met if ok else unmet).append((claim, where, f"{size} file present" if ok else f"no {size} file"))
            continue
        if claim.startswith("named part: "):
            part = claim[len("named part: "):].lower()
            words = [w for w in re.findall(r"[a-z]+", part) if w not in ("database", "page", "sheet", "tab",
                                                                        "tracker", "section", "the", "and")]
            ok = words and any(all(w in n for w in words) for n in _names(f))
            (met if ok else unmet).append((claim, where, "found in the files" if ok
                                           else "promised but not in the files"))
            continue
        ok, why = _proven(claim, f)
        (met if ok else unmet).append((claim, where, why))
    quality = file_quality(f, image_texts(folder) if with_images else [])
    score = max(0, 100 - 12 * len({c for c, _, _ in unmet}) - 8 * len(quality))
    problems = [f"Promises '{c.replace('named part: ', '')}' ({w}): {why}" for c, w, why in unmet]
    problems += [q[0].upper() + q[1:] for q in quality]
    verdict = "needs fix"      # default answer, as the Reality Checker says
    if not problems and score >= READY_MIN:
        verdict = "ready"
        if score >= RED_FLAG and (len(met) < EXTRA_PROOF_PROMISES or (f["kind"] == "pdf" and f["pages"] < 2)):
            verdict = "needs fix"
            problems.append("Score 95+ without enough proof from the files (red flag)")
    return {"verdict": verdict, "score": score, "problems": list(dict.fromkeys(problems)),
            "proven": list(dict.fromkeys(c for c, _, _ in met)),
            "kind": f["kind"], "pages": f["pages"], "files": f["files"]}


def report_md(name, result):
    lines = [f"# Truth check: {name}", "", f"**Verdict: {result['verdict'].upper()}** (score {result['score']}/100)",
             "", "Checked against the real files in this folder, not against the draft.", ""]
    if result["problems"]:
        lines += ["## Problems", ""] + [f"- {p}" for p in result["problems"]] + [""]
    if result["proven"]:
        lines += ["## Proven by the files", ""] + [f"- {p}" for p in result["proven"]] + [""]
    return "\n".join(lines)


def telegram_text(approval_id, name, result):
    head = f"Product #{approval_id} {name[:60]}: {'READY' if result['verdict'] == 'ready' else 'NEEDS FIX'}"
    return "\n".join([head] + [f"- {p[:90]}" for p in result["problems"][:3]])


if __name__ == "__main__":
    for folder in sys.argv[1:]:
        r = check(folder)
        print(telegram_text("?", os.path.basename(folder), r))
