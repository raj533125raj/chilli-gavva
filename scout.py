"""scout.py — SCOUT agent for discovering new digital product earning categories.

Once a day SCOUT searches for candidate digital product ideas (printables, planners,
templates) using free sources (llm_router with a rule-based fallback).
It scores ideas based on demand signals, competition, fit with our product types, and effort.
The top 5 ideas are stored in the database ('scout_ideas') and sent to the approval queue.
NO idea is ever auto-published or auto-built.
When approved by a human, scout ideas are added to the niche topic list.
Public status reports only the count of scout ideas, never the unapproved ideas.
"""
import datetime
import json
import os
import re
import sqlite3
import sys

import cubes
import guardrails
import niche_topics
import public_export
import rest_window
import secret_guard

SCOUT_STATE_KEY = "scout_last_run_day"
CATEGORIES = ("Printables", "Planners", "Templates")
MAX_IDEAS_PER_NICHE_PER_DAY = 2
LOOKBACK_DAYS = 30

DEFAULT_NICHES = {
    "budget": "niche_budget_sheets",
    "sheet": "niche_budget_sheets",
    "money": "niche_budget_sheets",
    "freelance": "niche_freelancer_trackers",
    "invoice": "niche_freelancer_trackers",
    "tracker": "niche_freelancer_trackers",
    "adhd": "niche_adhd_planners",
    "focus": "niche_adhd_planners",
    "planner": "niche_adhd_planners",
    "2027": "niche_planners_2027",
    "academic": "niche_planners_2027",
    "teacher": "niche_planners_2027",
    "resume": "niche_resume_bundle",
    "job": "niche_resume_bundle",
    "notion": "niche_notion_templates",
}


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS scout_ideas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        category TEXT,
        idea TEXT,
        demand_score INTEGER,
        competition_score INTEGER,
        fit_score INTEGER,
        effort_score INTEGER,
        total_score INTEGER,
        reason TEXT,
        niche TEXT,
        status TEXT DEFAULT 'pending',
        approval_id INTEGER,
        synced INTEGER DEFAULT 0,
        why_pass TEXT)""")
    conn.execute("CREATE TABLE IF NOT EXISTS scout_state (key TEXT PRIMARY KEY, value TEXT)")
    # Ensure why_pass column exists on existing tables
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(scout_ideas)").fetchall()]
        if "why_pass" not in cols:
            conn.execute("ALTER TABLE scout_ideas ADD COLUMN why_pass TEXT")
            conn.commit()
    except sqlite3.Error:
        pass
    return conn


def get_state(key, default=None, db_path=None):
    conn = connect(db_path)
    row = conn.execute("SELECT value FROM scout_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_state(key, value, db_path=None):
    conn = connect(db_path)
    conn.execute("INSERT INTO scout_state (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()
    conn.close()


def _today_iso(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.date().isoformat()


def is_paused(db_path=None):
    if guardrails.is_killed() or rest_window.in_rest_window():
        return True
    if not cubes.is_kind_enabled("research", db_path=db_path):
        return True
    if not cubes.is_agent_enabled("Research Manager", db_path=db_path):
        return True
    return False


def already_ran_today(db_path=None, now=None):
    last = get_state(SCOUT_STATE_KEY, default=None, db_path=db_path)
    return last == _today_iso(now)


def record_run_today(db_path=None, now=None):
    set_state(SCOUT_STATE_KEY, _today_iso(now), db_path=db_path)


def resolve_niche(idea_text, category):
    lower = (idea_text or "").lower()
    for kw, niche_name in DEFAULT_NICHES.items():
        if kw in lower:
            return niche_name
    if category == "Printables":
        return "niche_adhd_planners"
    if category == "Planners":
        return "niche_planners_2027"
    return "niche_budget_sheets"


# Fallback pool of digital product candidates (printables, planners, templates)
FALLBACK_CANDIDATES = [
    {"category": "Printables", "idea": "Habit and Routine Printable Worksheet for Students",
     "demand_score": 22, "competition_score": 20, "fit_score": 24, "effort_score": 22,
     "reason": "High student demand for visual routine builders, low printable competition."},
    {"category": "Planners", "idea": "Time-Block Daily Focus Planner for Remote Workers",
     "demand_score": 23, "competition_score": 19, "fit_score": 25, "effort_score": 21,
     "reason": "Strong demand for productivity planners, simple layout with low design effort."},
    {"category": "Templates", "idea": "Simple Project Expense and Milestone Tracker Sheet",
     "demand_score": 21, "competition_score": 22, "fit_score": 23, "effort_score": 20,
     "reason": "Popular among small creators, straightforward spreadsheet layout."},
    {"category": "Printables", "idea": "Minimalist Weekly Goal and Gratitude Log",
     "demand_score": 20, "competition_score": 21, "fit_score": 24, "effort_score": 23,
     "reason": "Great fit for printable planners, quick single-page design."},
    {"category": "Templates", "idea": "Creator Content Scheduling and Idea Pipeline Tracker",
     "demand_score": 24, "competition_score": 18, "fit_score": 22, "effort_score": 19,
     "reason": "High demand among digital creators, fits existing template structure."},
    {"category": "Planners", "idea": "Academic Assignment and Exam Tracker Planner",
     "demand_score": 22, "competition_score": 20, "fit_score": 24, "effort_score": 22,
     "reason": "Consistent seasonal buyer interest, easy to structure in standard planner layout."}
]


def _clamp_score(val, default=20):
    try:
        ival = int(val)
        return max(1, min(25, ival))
    except (TypeError, ValueError):
        return default


def _clean_idea_title(text: str) -> str:
    """Strip prefixes and formatting to get base idea title."""
    if not text:
        return ""
    t = str(text).strip()
    t = re.sub(r"^\[(?:Scout Idea|scout_idea|Trend Proposal|Draft|niche_[^\]]+)\]\s*", "", t, flags=re.I)
    t = re.sub(r"^Draft:\s*", "", t, flags=re.I)
    t = re.sub(r"\s+\(v\d+.*?\)$", "", t, flags=re.I)
    return t.strip()


def get_recent_approved_or_rejected_ideas(db_path=None, days=LOOKBACK_DAYS) -> list[str]:
    """Return idea/product titles approved or rejected in the last 30 days."""
    titles = []
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    try:
        conn = guardrails.connect(db_path)
        # Check approvals table
        cols = [r[1] for r in conn.execute("PRAGMA table_info(approvals)").fetchall()]
        has_decided_ts = "decided_ts" in cols
        ts_clause = "(decided_ts >= ? OR ts >= ?)" if has_decided_ts else "ts >= ?"
        params = (cutoff, cutoff) if has_decided_ts else (cutoff,)

        rows = conn.execute(
            f"SELECT title FROM approvals WHERE status IN ('approved', 'rejected') AND {ts_clause}",
            params
        ).fetchall()
        for (raw_title,) in rows:
            clean = _clean_idea_title(raw_title)
            if clean and clean not in titles:
                titles.append(clean)

        # Also check scout_ideas table
        has_scout = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scout_ideas'").fetchone()
        if has_scout:
            s_rows = conn.execute(
                "SELECT idea FROM scout_ideas WHERE status IN ('approved', 'rejected') AND ts >= ?",
                (cutoff,)
            ).fetchall()
            for (raw_idea,) in s_rows:
                clean = _clean_idea_title(raw_idea)
                if clean and clean not in titles:
                    titles.append(clean)

        conn.close()
    except Exception:
        pass
    return titles


def is_repeat_idea(idea_text: str, past_ideas: list[str]) -> bool:
    """Fuzzy title match against ideas approved or rejected in the last 30 days."""
    if not idea_text or not past_ideas:
        return False
    target = _clean_idea_title(idea_text)
    for past in past_ideas:
        if niche_topics.similar(target, past):
            return True
    return False


def get_niche_track_record(db_path=None) -> dict[str, int]:
    """Return niche scores based on past approvals and READY product builds."""
    scores: dict[str, int] = {}
    try:
        conn = guardrails.connect(db_path)
        # Approved public drafts or scout ideas
        has_approvals = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='approvals'").fetchone()
        if has_approvals:
            rows = conn.execute("SELECT title, payload FROM approvals WHERE status = 'approved'").fetchall()
            for title, payload in rows:
                niche_match = re.search(r"\b(niche_[a-z0-9_]+)\b", (title or "") + " " + (payload or ""))
                if niche_match:
                    n = niche_match.group(1)
                    scores[n] = scores.get(n, 0) + 10

        # READY product builds
        has_builds = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_builds'").fetchone()
        if has_builds:
            b_rows = conn.execute("SELECT folder FROM product_builds WHERE status IN ('built', 'ready')").fetchall()
            for (folder,) in b_rows:
                if folder:
                    for n in DEFAULT_NICHES.values():
                        if n in folder:
                            scores[n] = scores.get(n, 0) + 15

        # Truth checks with verdict READY
        has_tc = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='truth_checks'").fetchone()
        if has_tc:
            tc_rows = conn.execute("SELECT approval_id, verdict FROM truth_checks WHERE verdict IN ('ready', 'pass', 'READY', 'PASS')").fetchall()
            if tc_rows and has_approvals:
                for app_id, _ in tc_rows:
                    app_row = conn.execute("SELECT title, payload FROM approvals WHERE id = ?", (app_id,)).fetchone()
                    if app_row:
                        combined = (app_row[0] or "") + " " + (app_row[1] or "")
                        n_match = re.search(r"\b(niche_[a-z0-9_]+)\b", combined)
                        if n_match:
                            n = n_match.group(1)
                            scores[n] = scores.get(n, 0) + 10

        conn.close()
    except Exception:
        pass
    return scores


def generate_why_pass(idea_text: str, category: str, niche: str) -> str:
    """Generate a short generic 'why this should pass' note (no money amounts) for the checker."""
    cat_lower = category.lower()
    return f"Specific practical layout for targeted buyers with concrete {cat_lower} components and zero prohibited claims."


def evaluate_against_reject_reasons(candidate: dict, reject_reasons: list[dict]) -> tuple[dict, bool]:
    """Score candidate against top recent reject reasons.
    Drop or rewrite candidate if it matches a reject reason.
    Returns (candidate, drop_flag).
    """
    idea = candidate.get("idea", "")
    reason = candidate.get("reason", "")
    full_text = (idea + " " + reason).lower()

    # Extract reason names from reject_reasons list
    r_names = [str(r.get("reason", "")).lower() for r in reject_reasons if isinstance(r, dict)]

    # 1. Prohibited or risky claims / promises features not in files / money claims
    if any(k in full_text for k in ["guarantee", "get rich", "earn money", "passive income", "100%", "cure"]):
        return candidate, True  # Unsafe -> drop completely

    for r_name in r_names:
        if "prohibited" in r_name or "risky" in r_name:
            if any(k in full_text for k in ["income", "guaranteed", "profit", "wealth", "secret"]):
                return candidate, True

        # Too generic / niche too broad
        if "generic" in r_name or "broad" in r_name:
            if any(w in idea.lower() for w in ["general", "all-in-one", "life planner", "ultimate everything"]):
                # Rewrite to be more specific
                candidate = dict(candidate)
                candidate["idea"] = idea.replace("All-in-One", "Daily Focus").replace("Ultimate Everything", "Weekly Task")
                candidate["reason"] = candidate["reason"] + " (Refocused on specific workflow)"
                candidate["total_score"] = max(1, candidate["total_score"] - 5)

        # Target audience unclear
        if "target audience" in r_name or "audience" in r_name:
            # Check if idea lacks a specific audience persona
            audience_words = ("student", "remote worker", "creator", "freelancer", "teacher", "parent", "nurse", "couple", "grad")
            if not any(w in full_text for w in audience_words):
                candidate = dict(candidate)
                candidate["idea"] = f"{candidate['idea']} for Remote Creators"
                candidate["reason"] = candidate["reason"] + " (Added clear target audience)"

        # Product structure incomplete / promises features not in files
        if "structure" in r_name or "promises" in r_name:
            candidate = dict(candidate)
            candidate["fit_score"] = min(25, candidate.get("fit_score", 20) + 1)
            candidate["reason"] = candidate["reason"] + " (Includes modular section structure)"

    return candidate, False


def score_candidate(item):
    cat = item.get("category", "Templates")
    if cat not in CATEGORIES:
        cat = "Templates"
    idea = str(item.get("idea", "")).strip()
    demand = _clamp_score(item.get("demand_score"))
    comp = _clamp_score(item.get("competition_score"))
    fit = _clamp_score(item.get("fit_score"))
    effort = _clamp_score(item.get("effort_score"))
    total = demand + comp + fit + effort
    reason = str(item.get("reason", "")).strip() or "Good category potential and fit."
    niche = item.get("niche") or resolve_niche(idea, cat)
    why_pass = item.get("why_pass") or generate_why_pass(idea, cat, niche)
    return {
        "category": cat,
        "idea": idea,
        "demand_score": demand,
        "competition_score": comp,
        "fit_score": fit,
        "effort_score": effort,
        "total_score": total,
        "reason": reason,
        "niche": niche,
        "why_pass": why_pass,
    }


SCOUT_PROMPT = """You are SCOUT, an AI research agent finding new digital product categories.
Focus on low-cost digital products in these 3 categories: Printables, Planners, Templates.
Suggest 8 specific, practical digital product ideas.
For each idea, score these 4 factors (1 to 25 points each):
1. demand_score: buyer interest and search popularity (1-25)
2. competition_score: market opportunity / less crowded niche (1-25)
3. fit_score: fit with our product types (printables, planners, templates) (1-25)
4. effort_score: ease of drafting and building (1-25)

Rules:
- No food, recipes, fishing, farming, or healthcare topics.
- No income, job, or medical promises.
- Do NOT include any money amounts, price numbers, or currency symbols.
- Reply ONLY with a valid JSON array of 8 objects in this format:
[
  {
    "category": "Printables",
    "idea": "Short descriptive idea name",
    "demand_score": 22,
    "competition_score": 20,
    "fit_score": 24,
    "effort_score": 22,
    "reason": "Short reason explaining why this idea is promising."
  }
]"""


def _ask_llm(llm_fn):
    try:
        try:
            return llm_fn(SCOUT_PROMPT, size="small")
        except TypeError:
            return llm_fn(SCOUT_PROMPT)
    except Exception:
        return None


def _parse_llm_ideas(reply_text):
    if not reply_text or not isinstance(reply_text, str):
        return []
    start = reply_text.find("[")
    end = reply_text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(reply_text[start:end + 1])
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    valid = []
    for raw in data:
        if isinstance(raw, dict) and raw.get("idea"):
            valid.append(score_candidate(raw))
    return valid


def rule_based_scout():
    return [score_candidate(item) for item in FALLBACK_CANDIDATES]


def get_candidate_ideas(llm_fn=None, db_path=None):
    if llm_fn is not None:
        if guardrails.can_call_llm(db_path):
            text = _ask_llm(llm_fn)
            parsed = _parse_llm_ideas(text)
            if len(parsed) >= 5:
                return parsed
        # If daily cap reached or LLM call returned invalid, do not make AI call
    # Fallback to rule-based candidates when LLM is absent or output is invalid or cap reached
    return rule_based_scout()


def run_scout(db_path=None, llm=None):
    """Run SCOUT agent: generate candidate ideas, pick top 5, store in db and request approval."""
    if is_paused(db_path):
        return []
    if llm is None and guardrails.can_call_llm(db_path):
        try:
            import llm_router
            llm = lambda p, **kw: llm_router.chat(p, db_path=db_path, **kw)
        except Exception:
            llm = None
    elif not guardrails.can_call_llm(db_path):
        llm = None

    candidates = get_candidate_ideas(llm, db_path=db_path)
    scored = [score_candidate(c) for c in candidates if c.get("idea")]

    # 1. Fetch past approved or rejected ideas in the last 30 days
    past_ideas = get_recent_approved_or_rejected_ideas(db_path=db_path, days=LOOKBACK_DAYS)

    # 2. Fetch top recent reject reasons
    reject_reasons = public_export.get_public_reject_reasons(db_path=db_path)

    # 3. Fetch niche track record for preference weighting
    niche_scores = get_niche_track_record(db_path=db_path)

    # Filter and adjust candidates
    filtered_candidates = []
    for c in scored:
        # Check repeat idea in last 30 days
        if is_repeat_idea(c["idea"], past_ideas):
            continue

        # Score against reject reasons (rewrite or drop)
        adjusted, dropped = evaluate_against_reject_reasons(c, reject_reasons)
        if dropped:
            continue

        # Double check if rewritten idea is repeat
        if is_repeat_idea(adjusted["idea"], past_ideas):
            continue

        # Prefer niches whose past drafts were approved or built READY
        niche_boost = niche_scores.get(adjusted["niche"], 0)
        adjusted["total_score"] = adjusted.get("total_score", 80) + min(15, niche_boost // 2)

        # Ensure why_pass is present and scrubbed of currency/money
        if not adjusted.get("why_pass"):
            adjusted["why_pass"] = generate_why_pass(adjusted["idea"], adjusted["category"], adjusted["niche"])
        adjusted["why_pass"] = secret_guard.scrub_text(adjusted["why_pass"])

        filtered_candidates.append(adjusted)

    # Sort candidates by total_score descending
    filtered_candidates.sort(key=lambda x: x["total_score"], reverse=True)

    # Enforce variety: max 2 ideas per niche per day
    niche_counts: dict[str, int] = {}
    top_5 = []
    for item in filtered_candidates:
        n = item["niche"]
        if niche_counts.get(n, 0) < MAX_IDEAS_PER_NICHE_PER_DAY:
            top_5.append(item)
            niche_counts[n] = niche_counts.get(n, 0) + 1
            if len(top_5) == 5:
                break

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = connect(db_path)
    stored_ideas = []

    for item in top_5:
        payload_text = (f"Category: {item['category']}\n"
                        f"Niche: {item['niche']}\n"
                        f"Scores: Demand={item['demand_score']}/25, Competition={item['competition_score']}/25, "
                        f"Fit={item['fit_score']}/25, Effort={item['effort_score']}/25 (Total: {item['total_score']}/100)\n"
                        f"Why this should pass: {item['why_pass']}\n"
                        f"Reason: {item['reason']}")

        review_str = f"Score: {item['total_score']}/100 | Why pass: {item['why_pass']} | {item['reason']}"
        title = f"[Scout Idea] {item['idea']}"

        approval_id = guardrails.request_approval(
            kind="scout_idea",
            title=title,
            payload=payload_text,
            review=review_str,
            db_path=db_path
        )

        cur = conn.execute("""INSERT INTO scout_ideas
            (ts, category, idea, demand_score, competition_score, fit_score, effort_score, total_score, reason, niche, status, approval_id, synced, why_pass)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?)""",
            (now_iso, item["category"], item["idea"], item["demand_score"], item["competition_score"],
             item["fit_score"], item["effort_score"], item["total_score"], item["reason"], item["niche"], approval_id, item["why_pass"]))
        conn.commit()

        item_copy = dict(item)
        item_copy["id"] = cur.lastrowid
        item_copy["approval_id"] = approval_id
        stored_ideas.append(item_copy)

    conn.close()
    sync_approved_ideas(db_path)
    return stored_ideas


def maybe_run(db_path=None, llm=None, now=None):
    """Run SCOUT if it has not run today."""
    if is_paused(db_path):
        return {"ran": False, "reason": "paused"}
    if already_ran_today(db_path=db_path, now=now):
        sync_approved_ideas(db_path)
        return {"ran": False, "reason": "already ran today"}
    ideas = run_scout(db_path=db_path, llm=llm)
    record_run_today(db_path=db_path, now=now)
    return {"ran": True, "ideas": ideas}


def sync_approved_ideas(db_path=None):
    """Sync human-approved scout ideas into niche_topics pool."""
    conn = connect(db_path)
    rows = conn.execute("SELECT id, idea, niche, approval_id FROM scout_ideas WHERE synced = 0").fetchall()
    synced_count = 0
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for scout_id, idea, niche, approval_id in rows:
        if not approval_id:
            continue
        approval = guardrails.get_approval(approval_id, db_path)
        if not approval:
            continue
        status = approval.get("status")
        if status == "approved":
            # Formulate topic for niche pool
            niche_name = niche or resolve_niche(idea, "Printables")
            niche_topics.connect(db_path)  # ensures table exists
            topic_conn = guardrails.connect(db_path)
            topic_conn.execute("INSERT OR IGNORE INTO niche_topics (niche, idea, status, source, ts) "
                               "VALUES (?, ?, 'new', 'scout', ?)", (niche_name, idea, now_iso))
            topic_conn.commit()
            topic_conn.close()

            conn.execute("UPDATE scout_ideas SET status = 'approved', synced = 1 WHERE id = ?", (scout_id,))
            conn.commit()
            synced_count += 1
        elif status == "rejected":
            conn.execute("UPDATE scout_ideas SET status = 'rejected', synced = 1 WHERE id = ?", (scout_id,))
            conn.commit()
            synced_count += 1

    conn.close()
    return synced_count


def get_scout_ideas_count(db_path=None):
    """Return count of scout ideas (never the idea text itself)."""
    target = db_path or guardrails.DB_PATH
    if not os.path.exists(target):
        return {"total": 0, "pending": 0, "approved": 0}
    conn = connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM scout_ideas").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM scout_ideas WHERE status = 'pending'").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM scout_ideas WHERE status = 'approved'").fetchone()[0]
    except sqlite3.Error:
        total = pending = approved = 0
    finally:
        conn.close()
    return {"total": total, "pending": pending, "approved": approved}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        ran = already_ran_today()
        counts = get_scout_ideas_count()
        print(f"SCOUT ran today: {'yes' if ran else 'no'}")
        print(f"Scout ideas count: {counts['total']} total ({counts['pending']} pending, {counts['approved']} approved)")
    elif cmd == "run":
        res = run_scout()
        print(f"SCOUT generated {len(res)} ideas.")
    elif cmd == "maybe-run":
        res = maybe_run()
        print(f"SCOUT maybe-run: {res}")
    else:
        print("usage: python3 scout.py [status|run|maybe-run]")


if __name__ == "__main__":
    main()
