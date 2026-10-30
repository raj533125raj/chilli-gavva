"""improver_loop.py — Autonomous self-improvement loop with owner approval.

Runs once a week (Sunday flow). Studies 30 days of database data:
  - Checker scores and reasons
  - Reject reasons
  - Truth check NEEDS FIX reasons
  - Retries
  - Failed task runs

Finds top 3 repeated problems, generates concrete proposed changes with evidence
(counts and example IDs only), and sends cards to Telegram with Approve / Reject buttons.

Approved proposals become active learned rules stored in the database.
They are injected into writer and checker prompts automatically.
Rejected proposals are stored so they are never proposed again.
The owner can list active rules with /rules and turn any rule off with an Off button.
Nothing changes code files at runtime.
"""

import datetime
import re
import sqlite3
import guardrails

REASON_TO_RULE = {
    "audience": ("writer", "Target audience is unclear", "Clearly specify the target buyer persona and their main use case in the BUYER section."),
    "beats_free": ("writer", "Value over free template unclear", "Explain why this product provides unique value beyond standard free templates."),
    "structure": ("writer", "Missing product features/structure", "Break down the product structure into concrete, labelled tabs, pages, or database fields under WHAT'S INSIDE."),
    "search_tags": ("writer", "Incorrect search tag count", "Provide exactly 5 buyer search tag phrases separated by commas under SEARCH TAGS."),
    "too generic": ("writer", "Draft is too generic", "Provide detailed, concrete listing descriptions without generic filler text."),
    "poor quality": ("writer", "Quality concerns reported", "Ensure draft content is detailed, well-formatted, and completely free of placeholders."),
    "bad niche": ("writer", "Niche alignment issues", "Focus strictly on practical, high-utility tools tailored to the target audience."),
    "price": ("writer", "Pricing formatting issues", "Include a clear pricing section matching standard digital product guidelines."),
    "no_formulas": ("builder", "Spreadsheet missing formulas", "Ensure spreadsheet templates include working formulas and calculated fields."),
    "broken_chars": ("builder", "Broken character encodings", "Use standard ASCII characters for quotes, dashes, and symbols."),
    "cut_off": ("builder", "Text cut off in layout", "Format text layout to avoid truncation on pages or listing images."),
    "draft_rewrite": ("writer", "Draft required QA rewrite", "Address QA Checker guidelines directly in the first draft pass to prevent rewrites."),
    "ai_unavailable": ("checker", "AI provider failure during checks", "Use concise prompt structures and graceful fallback rules when AI model responses delay."),
}


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS learned_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_text TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        problem_key TEXT UNIQUE,
        status TEXT DEFAULT 'proposed',
        evidence TEXT,
        created_ts TEXT,
        decided_ts TEXT
    )""")
    conn.commit()
    return conn


def _cutoff_iso(days=30, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - datetime.timedelta(days=days)).isoformat()


def analyze_database(db_path=None, days=30, now=None):
    """Study last 30 days of data and return candidate problem dicts sorted by count desc."""
    conn = connect(db_path)
    cutoff = _cutoff_iso(days=days, now=now)
    problems = {}  # problem_key -> {count, ids, key, title, category, rule_text}

    def add_problem(key, category, title, rule_text, example_id):
        if key not in problems:
            problems[key] = {
                "problem_key": key,
                "category": category,
                "title": title,
                "rule_text": rule_text,
                "count": 0,
                "example_ids": []
            }
        problems[key]["count"] += 1
        if example_id and example_id not in problems[key]["example_ids"]:
            problems[key]["example_ids"].append(example_id)

    # 1. Checker scores and reasons
    try:
        rows = conn.execute("SELECT id, review FROM approvals WHERE (ts >= ? OR ts IS NULL) AND review IS NOT NULL", (cutoff,)).fetchall()
        for app_id, review in rows:
            rev_lower = (review or "").lower()
            ex_id = f"Draft #{app_id}"
            if "audience" in rev_lower:
                cat, title, rule = REASON_TO_RULE["audience"]
                add_problem("checker:audience", cat, title, rule, ex_id)
            if "free template" in rev_lower or "beats" in rev_lower:
                cat, title, rule = REASON_TO_RULE["beats_free"]
                add_problem("checker:beats_free", cat, title, rule, ex_id)
            if "structure" in rev_lower or "missing:" in rev_lower or "features" in rev_lower:
                cat, title, rule = REASON_TO_RULE["structure"]
                add_problem("checker:structure", cat, title, rule, ex_id)
            if "search tags" in rev_lower or "tags (" in rev_lower:
                cat, title, rule = REASON_TO_RULE["search_tags"]
                add_problem("checker:search_tags", cat, title, rule, ex_id)
    except sqlite3.Error:
        pass

    # 2. Reject reasons
    try:
        rows = conn.execute("SELECT id, reason, review FROM approvals WHERE status = 'rejected' AND (decided_ts >= ? OR ts >= ?)", (cutoff, cutoff)).fetchall()
        for app_id, reason, review in rows:
            ex_id = f"Draft #{app_id}"
            reason_clean = (reason or "").strip().lower()
            if reason_clean in REASON_TO_RULE:
                cat, title, rule = REASON_TO_RULE[reason_clean]
                add_problem(f"reject:{reason_clean}", cat, title, rule, ex_id)
            elif reason_clean:
                add_problem(f"reject:{reason_clean}", "writer", f"Rejected: {reason_clean}", f"Avoid issues causing rejection for '{reason_clean}'.", ex_id)
            else:
                add_problem("reject:quality", "writer", "Rejected for quality", "Ensure complete compliance with listing quality requirements.", ex_id)
    except sqlite3.Error:
        pass

    # 3. Truth Check NEEDS FIX reasons
    try:
        rows = conn.execute("SELECT approval_id, verdict FROM truth_checks WHERE verdict = 'needs_fix'", ()).fetchall()
        for app_id, verdict in rows:
            ex_id = f"Approval #{app_id}"
            add_problem("truth_fix:needs_fix", "builder", "Truth check reported NEEDS FIX", "Verify all listing promises are fully backed by generated files.", ex_id)
    except sqlite3.Error:
        pass

    try:
        rows = conn.execute("SELECT approval_id, note FROM product_builds WHERE status = 'needs_fix' AND (ts >= ? OR ts IS NULL)", (cutoff,)).fetchall()
        for app_id, note in rows:
            ex_id = f"Approval #{app_id}"
            note_lower = (note or "").lower()
            if "formula" in note_lower:
                cat, title, rule = REASON_TO_RULE["no_formulas"]
                add_problem("truth_fix:no_formulas", cat, title, rule, ex_id)
            elif "character" in note_lower or "broken" in note_lower:
                cat, title, rule = REASON_TO_RULE["broken_chars"]
                add_problem("truth_fix:broken_chars", cat, title, rule, ex_id)
            elif "cut off" in note_lower:
                cat, title, rule = REASON_TO_RULE["cut_off"]
                add_problem("truth_fix:cut_off", cat, title, rule, ex_id)
            else:
                add_problem("truth_fix:general", "builder", "Product build needs fix", "Ensure build scripts validate output files against specifications.", ex_id)
    except sqlite3.Error:
        pass

    # 4. Retries
    try:
        rows = conn.execute("SELECT id, kind, payload FROM tasks WHERE (kind = 'rewrite' OR attempts > 1) AND (finished_ts >= ? OR ts >= ?)", (cutoff, cutoff)).fetchall()
        for task_id, kind, payload in rows:
            ex_id = f"Task #{task_id}"
            cat, title, rule = REASON_TO_RULE["draft_rewrite"]
            add_problem("retry:draft_rewrite", cat, title, rule, ex_id)
    except sqlite3.Error:
        pass

    # 5. Failed runs
    try:
        rows = conn.execute("SELECT id, error FROM tasks WHERE status = 'failed' AND (finished_ts >= ? OR ts >= ?)", (cutoff, cutoff)).fetchall()
        for task_id, error in rows:
            ex_id = f"Task #{task_id}"
            err_str = (error or "").lower()
            if "aiunavailable" in err_str or "ai" in err_str:
                cat, title, rule = REASON_TO_RULE["ai_unavailable"]
                add_problem("failed:ai_unavailable", cat, title, rule, ex_id)
            else:
                add_problem("failed:task_error", "general", "Task execution failed", "Handle operational exceptions and retries gracefully.", ex_id)
    except sqlite3.Error:
        pass

    conn.close()

    candidates = list(problems.values())
    candidates.sort(key=lambda p: (p["count"], p["problem_key"]), reverse=True)
    return candidates


def generate_proposals(db_path=None, days=30, limit=3, now=None):
    """Find top repeated problems not yet proposed/approved/rejected/off, and save proposals."""
    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    candidates = analyze_database(db_path=db_path, days=days, now=now_dt)
    conn = connect(db_path)

    existing_keys = {row[0] for row in conn.execute("SELECT problem_key FROM learned_rules").fetchall()}

    proposals = []
    for cand in candidates:
        if len(proposals) >= limit:
            break
        key = cand["problem_key"]
        if key in existing_keys:
            continue

        examples_str = ", ".join(cand["example_ids"][:5]) if cand["example_ids"] else "none"
        evidence_text = f"{cand['count']} occurrence(s) in last {days} days (Examples: {examples_str})"

        cur = conn.execute(
            "INSERT INTO learned_rules (rule_text, category, problem_key, status, evidence, created_ts) VALUES (?, ?, ?, 'proposed', ?, ?)",
            (cand["rule_text"], cand["category"], key, evidence_text, now_dt.isoformat())
        )
        rule_id = cur.lastrowid
        existing_keys.add(key)
        proposals.append({
            "id": rule_id,
            "rule_text": cand["rule_text"],
            "category": cand["category"],
            "problem_key": key,
            "title": cand["title"],
            "evidence": evidence_text
        })

    conn.commit()
    conn.close()
    return proposals


def format_proposal_card(proposal):
    """Return (card_text, inline_buttons) for a proposed rule card in Telegram."""
    proposal_id = proposal["id"]
    text = (
        f"💡 Self-Improvement Proposal #{proposal_id}\n\n"
        f"Problem: {proposal.get('title') or proposal['problem_key']}\n"
        f"Evidence: {proposal['evidence']}\n\n"
        f"Proposed Rule ({proposal['category']}):\n"
        f"\"{proposal['rule_text']}\""
    )
    buttons = [[("✅ Approve", f"imp:a:{proposal_id}"), ("❌ Reject", f"imp:r:{proposal_id}")]]
    return text, buttons


def decide_proposal(rule_id, approved, db_path=None, now=None):
    """Approve or reject a proposed rule."""
    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    conn = connect(db_path)
    new_status = "approved" if approved else "rejected"
    cur = conn.execute(
        "UPDATE learned_rules SET status = ?, decided_ts = ? WHERE id = ? AND status = 'proposed'",
        (new_status, now_dt.isoformat(), rule_id)
    )
    conn.commit()
    affected = cur.rowcount > 0
    conn.close()
    return affected


def turn_off_rule(rule_id, db_path=None, now=None):
    """Turn an approved rule off."""
    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    conn = connect(db_path)
    cur = conn.execute(
        "UPDATE learned_rules SET status = 'off', decided_ts = ? WHERE id = ? AND status = 'approved'",
        (now_dt.isoformat(), rule_id)
    )
    conn.commit()
    affected = cur.rowcount > 0
    conn.close()
    return affected


def get_active_rules(category=None, db_path=None):
    """Return list of active rule_text strings."""
    conn = connect(db_path)
    if category:
        rows = conn.execute(
            "SELECT rule_text FROM learned_rules WHERE status = 'approved' AND (category = ? OR category = 'general') ORDER BY id",
            (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT rule_text FROM learned_rules WHERE status = 'approved' ORDER BY id"
        ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_active_rules_block(category=None, db_path=None):
    """Return a formatted text block of active rules to inject into prompts, or empty string."""
    rules = get_active_rules(category=category, db_path=db_path)
    if not rules:
        return ""
    lines = ["\nLearned Rules (follow these strictly):"]
    lines.extend(f"- {r}" for r in rules)
    return "\n".join(lines)


def list_active_rules(db_path=None):
    """Return list of active rule dicts."""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT id, rule_text, category FROM learned_rules WHERE status = 'approved' ORDER BY id"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "rule_text": r[1], "category": r[2]} for r in rows]


def format_rules_list(db_path=None):
    """Format the /rules output and inline Off buttons for Telegram."""
    rules = list_active_rules(db_path)
    if not rules:
        return "No active learned rules.", None

    lines = ["📜 Active Learned Rules:\n"]
    buttons = []
    for r in rules:
        lines.append(f"#{r['id']} [{r['category']}]: {r['rule_text']}")
        buttons.append([("Off #" + str(r['id']), f"imp:off:{r['id']}")])

    return "\n".join(lines), buttons


def handle_callback(data, db_path=None):
    """Handle Telegram callback for improver_loop (imp:a:ID, imp:r:ID, imp:off:ID)."""
    m = re.match(r"^imp:(a|r|off):(\d+)$", data or "")
    if not m:
        return None
    action, rule_id = m.group(1), int(m.group(2))
    conn = connect(db_path)
    row = conn.execute("SELECT id, rule_text, status FROM learned_rules WHERE id = ?", (rule_id,)).fetchone()
    conn.close()
    if not row:
        return f"Rule #{rule_id} not found."

    if action == "a":
        if decide_proposal(rule_id, True, db_path=db_path):
            return f"Approved rule #{rule_id}: \"{row[1]}\" is now active."
        return f"Rule #{rule_id} could not be approved (status: {row[2]})."
    elif action == "r":
        if decide_proposal(rule_id, False, db_path=db_path):
            return f"Rejected proposal #{rule_id}. It will not be proposed again."
        return f"Proposal #{rule_id} could not be rejected (status: {row[2]})."
    elif action == "off":
        if turn_off_rule(rule_id, db_path=db_path):
            return f"Switched off learned rule #{rule_id}."
        return f"Rule #{rule_id} could not be turned off (status: {row[2]})."
    return None


def maybe_run_weekly(send_fn, get_state_fn, set_state_fn, db_path=None, now=None):
    """Run improver loop on Sundays once a week."""
    if not guardrails.automation_allowed(db_path):
        return 0
    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    if now_dt.weekday() != 6:  # 6 is Sunday
        return 0

    today_str = now_dt.date().isoformat()
    if get_state_fn("improver_loop_last_day", None, db_path) == today_str:
        return 0

    proposals = generate_proposals(db_path=db_path, now=now_dt)
    sent_count = 0
    for prop in proposals:
        card_text, buttons = format_proposal_card(prop)
        if send_fn(card_text, buttons):
            sent_count += 1

    set_state_fn("improver_loop_last_day", today_str, db_path)
    return sent_count
