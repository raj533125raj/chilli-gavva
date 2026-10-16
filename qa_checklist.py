"""qa_checklist.py — the QA Checker's buyer checklist (version 2).

A draft is scored the way a buyer on Gumroad would judge the listing. Nine points, total 100:

  who it is for 15 | problem it solves 15 | what is inside 20 | what makes it different 10 |
  price fits the content 10 | listing title 10 | listing description 10 | 5 search tags 5 |
  no false claims 5

The same draft always gets the same score (plain rules, no AI), so scores can be compared and re-run.
A risky claim (income, health, job promises) caps the total at 40. The checklist only ADVISES: it never
approves or rejects a draft; a human does.
"""
import re

VERSION = "checklist v2"
LABELS = ["product name", "buyer", "price", "platform", "what's inside", "how to use",
          "listing title", "listing description", "search tags"]
BANNED = ["guaranteed", "get rich", "passive income", "earn $", "make money fast", "cure", "diagnose",
          "treats adhd", "land your dream job", "get hired", "100% results", "risk-free income"]
_GROUP_WORDS = ("students", "freelancers", "parents", "teachers", "adults", "creators", "seekers", "owners",
                "workers", "nurses", "couples", "families", "designers", "writers", "developers", "coaches",
                "consultants", "beginners", "professionals", "teams", "moms", "dads", "renters", "planners",
                "entrepreneurs", "sellers", "artists", "readers", "travelers", "travellers", "graduates")
_PROBLEM = re.compile(r"\b(need|needs|want|wants|struggl\w*|tired of|so (?:that )?(?:they|you) can|helps? (?:you|them)"
                      r"|without|instead of|keep track|stay on top|organi[sz]e\w*|never miss|avoid|overwhelm\w*"
                      r"|save time|in one place|forget|juggl\w*|messy|scattered)\b")
_STRONG_DIFFERENT = re.compile(r"\b(unlike|instead of|not just|what makes|compared (?:to|with)|no other"
                               r"|(?:built|designed|made) specifically|only (?:template|planner|tracker|kit|one))\b")
_HYPE = re.compile(r"\b(proven|best[- ]selling|#1|number one|thousands of|life[- ]changing|ultimate|revolutionary)\b")
_DIFFERENT = re.compile(r"\b(unlike|instead of|only|built for|designed (?:for|specifically)|without|one place|single"
                        r"|all[- ]in[- ]one|pre-?filled|ready[- ]made|automatic\w*|formulas?|dashboard|editable"
                        r"|printable|step[- ]by[- ]step|examples?|bonus|checklists?|ats[- ]friendly)\b")
_PRODUCT_WORDS = re.compile(r"\b(planner|tracker|template|sheet|spreadsheet|bundle|kit|workbook|dashboard|system"
                            r"|checklist|journal|resume|cv|crm|calendar|budget|organi[sz]er|pack|log)\b")


def _norm(text):
    return (text or "").replace("’", "'").replace("‘", "'")


def sections(text):
    """Split a draft into its labelled sections. Accepts 'LABEL: text', '**LABEL**' or '## Label' lines,
    with or without a colon, so a nicely formatted draft is not marked 'missing'."""
    found, current = {}, None
    label_re = "|".join(re.escape(l).replace("'", "'?") for l in LABELS)
    pattern = re.compile(rf"^\W*({label_re})\W*?(?::|\*\*|$)\s*(.*)$", re.IGNORECASE)
    for line in _norm(text).splitlines():
        m = pattern.match(line.strip())
        if m:
            current = m.group(1).lower().replace("whats", "what's")
            found[current] = (m.group(2).strip(" *:#") + "\n") if m.group(2).strip(" *:#") else ""
        elif current is not None:
            found[current] += line + "\n"
    return {k: v.strip() for k, v in found.items()}


def _parts(inside):
    """How many parts (pages, tabs, sections) the WHAT'S INSIDE section lists."""
    count = 0
    for line in inside.splitlines():
        s = line.strip()
        if re.match(r"^([-*•]|\d+[.)]|\|(?!\s*-))", s) and len(s.strip("-*|# ")) > 2:
            count += 1
    if count == 0:   # a comma list on one line: "6 pages: energy check-in, top 3 tasks, ..."
        count = len([p for p in re.split(r"[,;]", inside) if len(p.strip()) > 3])
    return count


def _tags(text, secs):
    body = secs.get("search tags", "")
    first = body.splitlines()[0] if body else ""
    if "," in first or ";" in first:
        return len([t for t in re.split(r"[,;]", first) if t.strip(" -*#.")])
    if first.count("#") > 1:
        return len([t for t in first.split("#") if t.strip()])
    return len([l for l in body.splitlines() if l.strip(" -*#•\t.") and len(l) < 80])


def _price(text):
    m = re.search(r"(?:\$\s?(\d+(?:\.\d+)?))|(?:(\d+(?:\.\d+)?)\s?(?:usd|us\$|dollars?)\b)", text.lower())
    return float(m.group(1) or m.group(2)) if m else None


def score(text):
    """Return {"score": 0-100, "items": [(name, got, max, reason or "")], "top_reasons": [...]}."""
    text = _norm(text)
    lower = text.lower()
    secs = sections(text)
    buyer, inside = secs.get("buyer", ""), secs.get("what's inside", "")
    desc, title = secs.get("listing description", ""), secs.get("listing title", "")
    how = secs.get("how to use", "")
    items = []

    def add(name, got, mx, reason):
        items.append((name, got, mx, reason if got < mx else ""))

    # 1. who it is for
    got = 0
    if buyer:
        got += 5
        got += 5 if len(buyer.split()) >= 8 else 0
        got += 5 if (any(w in buyer.lower() for w in _GROUP_WORDS)
                     or re.search(r"\b\w+(?:ers|ists|ants|ents)\b", buyer.lower())) else 0
    add("who it is for", got, 15, "No buyer named" if not buyer else "Buyer is vague")
    # 2. problem it solves
    got = (10 if _PROBLEM.search((buyer + " " + desc).lower()) else 0)
    steps = len([l for l in how.splitlines() if l.strip()]) or len(re.findall(r"\d+[.)]", how))
    got += 5 if steps >= 3 else 0
    add("problem it solves", got, 15, "No clear problem it solves" if got < 10
        else "How-to-use needs 3+ steps")
    # 3. what is inside
    parts = _parts(inside)
    got = (4 if inside else 0) + (8 if parts >= 3 else 0) + (8 if parts >= 6 else 0)
    add("what is inside", got, 20, "Contents not listed" if not inside
        else f"Only {parts} part(s) listed (want 6+)")
    # 4. what makes it different
    pitch = (desc + " " + secs.get("product name", "") + " " + title).lower()
    got = 10 if _STRONG_DIFFERENT.search(pitch) else 5 if _DIFFERENT.search(pitch + " " + inside.lower()) else 0
    add("what makes it different", got, 10, "Say why it beats a free template"
        if got else "Nothing says what makes it different")
    # 5. price fits the content
    price = _price(secs.get("price", "") or text)
    got = 0
    if price is not None:
        got += 5
        got += 3 if 3 <= price <= 49 else 0
        got += 2 if price <= 5 + 4 * max(parts, 1) else 0
    add("price fits the content", got, 10, "No USD price" if price is None
        else f"Price {price:g} USD high for the contents")
    # 6. listing title
    tl = title.lower()
    names_buyer = any(w in tl for w in _GROUP_WORDS) or " for " in f" {tl} "
    got = (4 if title else 0) + (3 if 20 <= len(title) <= 80 else 1 if 0 < len(title) <= 140 else 0)
    got += 3 if title and _PRODUCT_WORDS.search(tl) and names_buyer else 1 if title and _PRODUCT_WORDS.search(tl) else 0
    add("listing title", got, 10, "Listing title missing" if not title
        else "Title: under 80 chars, say what + who")
    # 7. listing description
    sentences = len([s for s in re.split(r"[.!?]+\s", desc) if len(s.strip()) > 10])
    speaks_to_buyer = " you" in f" {desc.lower()}" or any(w in desc.lower() for w in _GROUP_WORDS)
    got = (3 if desc else 0) + (3 if len(desc) >= 300 else 0) + (2 if sentences >= 3 else 0)
    got += 2 if speaks_to_buyer else 0
    add("listing description", got, 10, "Listing description missing" if not desc
        else "Description: 3+ sentences to the buyer")
    # 8. search tags
    tags = _tags(text, secs) if "search tags" in secs else 0
    got = 5 if tags == 5 else 2 if 3 <= tags <= 8 else 0
    add("5 search tags", got, 5, f"Needs 5 search tags (has {tags})")
    # 9. no false claims
    risky = [p for p in BANNED if p in lower]
    hype = _HYPE.search(lower)
    got = 0 if risky else 2 if hype else 5
    add("no false claims", got, 5, f"Risky claim: '{risky[0]}'" if risky
        else f"Hype word '{hype.group(1)}'" if hype else "")

    total = sum(i[1] for i in items)
    if risky:
        total = min(total, 40)
    losses = sorted((i for i in items if i[1] < i[2]), key=lambda i: (i[1] - i[2], items.index(i)))
    if risky:
        losses = [i for i in losses if i[0] == "no false claims"] + [i for i in losses if i[0] != "no false claims"]
    return {"score": total, "items": items, "top_reasons": [i[3] for i in losses[:3]]}


def summary(result, checker_note=""):
    """The review line saved with a draft (and shown in Telegram)."""
    line = f"score {result['score']}/100 ({VERSION})"
    if result["top_reasons"]:
        line += " | top: " + "; ".join(result["top_reasons"])
    return line + (f" | {checker_note}" if checker_note else "")


def top_reasons_from_review(review):
    m = re.search(r"\| top: ([^|]+)", review or "")
    return [r.strip() for r in m.group(1).split(";") if r.strip()][:3] if m else []


def rescore_old_reviews(db_path=None):
    """Give every draft scored before this checklist a checklist score. Keeps the old review text after
    'before:' and NEVER changes a draft's status (approved / rejected / waiting). Returns the ids done."""
    import guardrails
    conn = guardrails.connect(db_path)
    rows = conn.execute("SELECT id, payload, review FROM approvals WHERE kind = 'public_draft'").fetchall()
    done = []
    for approval_id, payload, review in rows:
        if VERSION in (review or ""):
            continue
        old = (review or "not reviewed").split(" | issues:")[0][:120]
        conn.execute("UPDATE approvals SET review = ? WHERE id = ?",
                     (summary(score(payload or ""), f"before: {old}"), approval_id))
        done.append(approval_id)
    conn.commit()
    conn.close()
    return done
