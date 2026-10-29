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

import guardrails
import niche_topics

SCOUT_STATE_KEY = "scout_last_run_day"
CATEGORIES = ("Printables", "Planners", "Templates")

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
        synced INTEGER DEFAULT 0)""")
    conn.execute("CREATE TABLE IF NOT EXISTS scout_state (key TEXT PRIMARY KEY, value TEXT)")
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


def get_candidate_ideas(llm_fn=None):
    if llm_fn is not None:
        text = _ask_llm(llm_fn)
        parsed = _parse_llm_ideas(text)
        if len(parsed) >= 5:
            return parsed
    # Fallback to rule-based candidates when LLM is absent or output is invalid
    return rule_based_scout()


def run_scout(db_path=None, llm=None):
    """Run SCOUT agent: generate candidate ideas, pick top 5, store in db and request approval."""
    if llm is None:
        try:
            import llm_router
            llm = lambda p, **kw: llm_router.chat(p, db_path=db_path, **kw)
        except Exception:
            llm = None

    candidates = get_candidate_ideas(llm)
    scored = [score_candidate(c) for c in candidates if c.get("idea")]
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    top_5 = scored[:5]

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = connect(db_path)
    stored_ideas = []

    for item in top_5:
        payload_text = (f"Category: {item['category']}\n"
                        f"Niche: {item['niche']}\n"
                        f"Scores: Demand={item['demand_score']}/25, Competition={item['competition_score']}/25, "
                        f"Fit={item['fit_score']}/25, Effort={item['effort_score']}/25 (Total: {item['total_score']}/100)\n"
                        f"Reason: {item['reason']}")

        review_str = f"Score: {item['total_score']}/100 | {item['reason']}"
        title = f"[Scout Idea] {item['idea']}"

        approval_id = guardrails.request_approval(
            kind="scout_idea",
            title=title,
            payload=payload_text,
            review=review_str,
            db_path=db_path
        )

        cur = conn.execute("""INSERT INTO scout_ideas
            (ts, category, idea, demand_score, competition_score, fit_score, effort_score, total_score, reason, niche, status, approval_id, synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0)""",
            (now_iso, item["category"], item["idea"], item["demand_score"], item["competition_score"],
             item["fit_score"], item["effort_score"], item["total_score"], item["reason"], item["niche"], approval_id))
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
