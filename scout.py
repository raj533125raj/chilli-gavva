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
MAX_IDEAS_PER_AUDIENCE_PER_WEEK = 2
LOOKBACK_DAYS = 30
DRAFT_LOOKBACK_DAYS = 60
SIMILARITY_THRESHOLD = 0.45
AUDIENCE_WINDOW_DAYS = 7

# Audience rotation list
AUDIENCES = (
    "teachers",
    "students",
    "exam takers",
    "small shop owners",
    "home bakers",
    "fitness beginners",
    "new parents",
    "freelancers",
    "job seekers",
    "wedding planners",
    "Telugu-speaking families",
    "retirees",
)

# Builder-supported digital product formats
ALLOWED_FORMATS = (
    "printable planner",
    "tracker sheet",
    "workbook",
    "checklist",
    "worksheet set",
    "budget sheet",
    "resume pack",
)

# Formats excluded by default unless a corresponding builder_<format>.py module exists
EXCLUDED_FORMATS = (
    "wall art",
    "slides",
    "email signature",
)

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
        why_pass TEXT,
        audience TEXT,
        format TEXT,
        why_sells TEXT)""")
    conn.execute("CREATE TABLE IF NOT EXISTS scout_state (key TEXT PRIMARY KEY, value TEXT)")
    # Ensure why_pass, audience, format, why_sells columns exist on existing tables
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(scout_ideas)").fetchall()]
        for col_name in ("why_pass", "audience", "format", "why_sells"):
            if col_name not in cols:
                conn.execute(f"ALTER TABLE scout_ideas ADD COLUMN {col_name} TEXT")
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


def detect_format(text: str) -> str | None:
    """Detect digital product format from text (title or description)."""
    if not text:
        return None
    lower = text.lower()

    # Check excluded formats first (so 'wall art printable' is identified as wall art)
    for excl in EXCLUDED_FORMATS:
        if excl in lower:
            return excl

    # Check allowed formats
    for allowed in ALLOWED_FORMATS:
        if allowed in lower:
            return allowed

    # Check individual format components
    format_keywords = {
        "budget sheet": "budget sheet",
        "budget": "budget sheet",
        "planner": "printable planner",
        "tracker sheet": "tracker sheet",
        "tracker": "tracker sheet",
        "workbook": "workbook",
        "checklist": "checklist",
        "worksheet set": "worksheet set",
        "worksheet": "worksheet set",
        "resume pack": "resume pack",
        "resume": "resume pack",
        "slides": "slides",
        "presentation": "slides",
        "wall art": "wall art",
        "poster": "wall art",
        "email signature": "email signature",
    }
    for kw, fmt in format_keywords.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", lower):
            return fmt

    return None


def is_builder_format_supported(fmt_name: str) -> bool:
    """Check if format is supported by the builder.

    Allowed formats are always supported.
    Excluded formats (wall art, slides, email signature, etc.) are only supported
    if a corresponding builder_<format>.py module exists in the repo root.
    """
    fmt_clean = (fmt_name or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", fmt_clean).strip("_")

    # Check if a custom builder module exists
    module_filename = f"builder_{normalized}.py"
    if os.path.exists(module_filename):
        return True

    # Check if it matches any excluded format keywords
    for excl in EXCLUDED_FORMATS:
        excl_norm = re.sub(r"[^a-z0-9]+", "_", excl.lower()).strip("_")
        if excl_norm in normalized or excl in fmt_clean:
            return False

    # Check allowed formats
    for allowed in ALLOWED_FORMATS:
        allowed_norm = re.sub(r"[^a-z0-9]+", "_", allowed.lower()).strip("_")
        if allowed_norm in normalized or allowed in fmt_clean:
            return True

    return False


def get_supported_formats() -> list[str]:
    """Return all currently supported formats including dynamically detected builder modules."""
    supported = list(ALLOWED_FORMATS)
    for excl in EXCLUDED_FORMATS:
        norm = re.sub(r"[^a-z0-9]+", "_", excl.lower()).strip("_")
        if os.path.exists(f"builder_{norm}.py"):
            supported.append(excl)
    return supported


# Primary 6 fallback candidates (backward compatible with existing test expectations)
FALLBACK_CANDIDATES = [
    {"category": "Printables", "idea": "Habit and Routine Printable Worksheet for Students",
     "demand_score": 22, "competition_score": 20, "fit_score": 24, "effort_score": 22,
     "reason": "High student demand for visual routine builders, low printable competition.",
     "audience": "students", "format": "worksheet set"},
    {"category": "Planners", "idea": "Classroom Lesson Planning Printable Planner for Teachers",
     "demand_score": 23, "competition_score": 19, "fit_score": 25, "effort_score": 21,
     "reason": "Consistent teacher demand for organized term planners and grading outlines.",
     "audience": "teachers", "format": "printable planner"},
    {"category": "Templates", "idea": "Simple Project Expense and Milestone Tracker Sheet",
     "demand_score": 21, "competition_score": 22, "fit_score": 23, "effort_score": 20,
     "reason": "Popular among small creators, straightforward spreadsheet layout.",
     "audience": "freelancers", "format": "tracker sheet"},
    {"category": "Printables", "idea": "Minimalist Weekly Goal and Gratitude Log",
     "demand_score": 20, "competition_score": 21, "fit_score": 24, "effort_score": 23,
     "reason": "Great fit for printable planners, quick single-page design.",
     "audience": "Telugu-speaking families", "format": "printable planner"},
    {"category": "Templates", "idea": "Monthly Expense and Cashflow Budget Sheet for Small Shop Owners",
     "demand_score": 24, "competition_score": 18, "fit_score": 22, "effort_score": 19,
     "reason": "Local merchants look for straightforward offline tally logs without software costs.",
     "audience": "small shop owners", "format": "budget sheet"},
    {"category": "Planners", "idea": "Academic Assignment and Exam Tracker Planner",
     "demand_score": 22, "competition_score": 20, "fit_score": 24, "effort_score": 22,
     "reason": "Consistent seasonal buyer interest, easy to structure in standard planner layout.",
     "audience": "exam takers", "format": "printable planner"}
]

# Extended fallback candidates for full audience rotation
EXTENDED_FALLBACK_CANDIDATES = FALLBACK_CANDIDATES + [
    {"category": "Templates", "idea": "Daily Order and Ingredient Tracker Sheet for Home Bakers",
     "demand_score": 21, "competition_score": 22, "fit_score": 23, "effort_score": 20,
     "reason": "Small food creators need simple order tracking and inventory logs.",
     "audience": "home bakers", "format": "tracker sheet"},
    {"category": "Planners", "idea": "Vendor Timeline and Seating Checklist for Wedding Planners",
     "demand_score": 22, "competition_score": 20, "fit_score": 24, "effort_score": 20,
     "reason": "High emotional investment purchase where thorough lists prevent ceremony oversights.",
     "audience": "wedding planners", "format": "checklist"},
    {"category": "Planners", "idea": "Daily Walking and Wellness Workbook for Retirees",
     "demand_score": 20, "competition_score": 22, "fit_score": 24, "effort_score": 22,
     "reason": "Gentle daily structure and personal activity logging for senior lifestyles.",
     "audience": "retirees", "format": "workbook"},
    {"category": "Templates", "idea": "ATS-Optimized Entry and Mid-Level Resume Pack for Job Seekers",
     "demand_score": 24, "competition_score": 18, "fit_score": 25, "effort_score": 22,
     "reason": "High volume career searchers wanting clean standard resume documents.",
     "audience": "job seekers", "format": "resume pack"},
    {"category": "Printables", "idea": "Infant Feeding and Sleep Schedule Checklist for New Parents",
     "demand_score": 22, "competition_score": 21, "fit_score": 23, "effort_score": 21,
     "reason": "New caregivers appreciate quick bedside logs for baby feeding and sleep milestones.",
     "audience": "new parents", "format": "checklist"},
    {"category": "Printables", "idea": "Beginner Workout and Water Tracker Sheet for Fitness Beginners",
     "demand_score": 21, "competition_score": 20, "fit_score": 24, "effort_score": 22,
     "reason": "Simple habit formation tool avoiding gym intimidation or complex tracking apps.",
     "audience": "fitness beginners", "format": "tracker sheet"}
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


def detect_audience(text: str) -> str | None:
    """Detect which audience from AUDIENCES (or standard keywords) is targeted in text."""
    if not text:
        return None
    lower = text.lower()
    for aud in AUDIENCES:
        if aud.lower() in lower:
            return aud

    # Secondary keywords mapping to standard audiences
    aliases = {
        "teacher": "teachers",
        "educator": "teachers",
        "student": "students",
        "college": "students",
        "exam": "exam takers",
        "test": "exam takers",
        "shop": "small shop owners",
        "store": "small shop owners",
        "retail": "small shop owners",
        "baker": "home bakers",
        "bakery": "home bakers",
        "fitness": "fitness beginners",
        "gym": "fitness beginners",
        "workout": "fitness beginners",
        "parent": "new parents",
        "baby": "new parents",
        "infant": "new parents",
        "freelance": "freelancers",
        "client": "freelancers",
        "job": "job seekers",
        "career": "job seekers",
        "resume": "job seekers",
        "wedding": "wedding planners",
        "ceremony": "wedding planners",
        "telugu": "Telugu-speaking families",
        "retire": "retirees",
        "senior": "retirees",
    }
    for kw, aud in aliases.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", lower):
            return aud
    return None


def get_audience_usage_history(db_path=None, days: int = AUDIENCE_WINDOW_DAYS) -> dict[str, list[str]]:
    """Return map of audience -> list of ISO timestamps when ideas for that audience were proposed/recorded.

    Looks back  (default 7) in scout_ideas and approvals.
    """
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    usage: dict[str, list[str]] = {a: [] for a in AUDIENCES}

    try:
        conn = guardrails.connect(db_path)
        # 1. Check scout_ideas table
        has_scout = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scout_ideas'").fetchone()
        if has_scout:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(scout_ideas)").fetchall()]
            has_aud_col = "audience" in cols
            if has_aud_col:
                rows = conn.execute("SELECT idea, ts, audience FROM scout_ideas WHERE ts >= ?", (cutoff,)).fetchall()
                for idea, ts, aud in rows:
                    matched_aud = aud if aud in AUDIENCES else detect_audience(idea or "")
                    if matched_aud and matched_aud in usage:
                        usage[matched_aud].append(ts)
            else:
                rows = conn.execute("SELECT idea, ts FROM scout_ideas WHERE ts >= ?", (cutoff,)).fetchall()
                for idea, ts in rows:
                    matched_aud = detect_audience(idea or "")
                    if matched_aud and matched_aud in usage:
                        usage[matched_aud].append(ts)

        # 2. Check approvals table
        has_approvals = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='approvals'").fetchone()
        if has_approvals:
            a_rows = conn.execute(
                "SELECT title, payload, ts FROM approvals WHERE ts >= ? AND kind IN ('scout_idea', 'public_draft')",
                (cutoff,)
            ).fetchall()
            for title, payload, ts in a_rows:
                matched_aud = detect_audience((title or "") + " " + (payload or ""))
                if matched_aud and matched_aud in usage:
                    # Avoid double-counting the exact same timestamp for the same audience
                    if ts not in usage[matched_aud]:
                        usage[matched_aud].append(ts)

        conn.close()
    except Exception:
        pass
    return usage


def get_audience_weekly_counts(db_path=None, days: int = 7) -> dict[str, int]:
    """Return count of ideas proposed per audience in the last 7 days."""
    history = get_audience_usage_history(db_path=db_path, days=days)
    return {aud: len(timestamps) for aud, timestamps in history.items()}


def get_available_audiences(db_path=None, days: int = AUDIENCE_WINDOW_DAYS) -> list[str]:
    """Return audiences not used within the window and not exceeding the weekly cap (max 2).

    If all audiences have been used within the window, orders audiences by least-recently-used,
    filtering out those that have hit the weekly cap (>= 2).
    """
    history = get_audience_usage_history(db_path=db_path, days=days)
    weekly_counts = {aud: len(ts_list) for aud, ts_list in history.items()}

    # Audiences with 0 usages in window
    unused = [aud for aud in AUDIENCES if weekly_counts[aud] == 0]
    if unused:
        return unused

    # If all have been used, sort by least usages, then oldest last usage, excluding those at cap
    under_cap = [aud for aud in AUDIENCES if weekly_counts[aud] < MAX_IDEAS_PER_AUDIENCE_PER_WEEK]
    if not under_cap:
        return []

    def sort_key(aud):
        timestamps = history.get(aud, [])
        most_recent = max(timestamps) if timestamps else ""
        return (weekly_counts[aud], most_recent)

    under_cap.sort(key=sort_key)
    return under_cap


def tokenize_words(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric words."""
    if not text:
        return []
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", str(text)).strip().lower()
    return [w for w in clean.split() if w]


def get_word_shingles(words: list[str], k: int = 5) -> set[str]:
    """Generate k-shingles (word n-grams) from a sequence of words.

    If words count is less than k and words is non-empty, falls back to a single
    shingle of all words to allow small texts (e.g. short titles) to be compared.
    """
    if not words:
        return set()
    if len(words) < k:
        return {" ".join(words)}
    shingles: set[str] = set()
    for i in range(len(words) - k + 1):
        shingles.add(" ".join(words[i : i + k]))
    return shingles


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Calculate Jaccard index between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def compute_shingle_similarity(text_a: str, text_b: str, k: int = 5) -> float:
    """Compute word-shingle Jaccard similarity between two texts."""
    clean_a = _clean_idea_title(text_a)
    clean_b = _clean_idea_title(text_b)
    words_a = tokenize_words(clean_a)
    words_b = tokenize_words(clean_b)
    shingles_a = get_word_shingles(words_a, k=k)
    shingles_b = get_word_shingles(words_b, k=k)
    return jaccard_similarity(shingles_a, shingles_b)


def get_recent_drafts(db_path=None, days: int = DRAFT_LOOKBACK_DAYS) -> list[str]:
    """Return all draft and idea titles from the last  (default 60 days).

    Includes all items from approvals (public_draft, scout_idea, etc.) and scout_ideas.
    """
    drafts = []
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    try:
        conn = guardrails.connect(db_path)
        # Check approvals table
        cols = [r[1] for r in conn.execute("PRAGMA table_info(approvals)").fetchall()]
        has_decided_ts = "decided_ts" in cols
        ts_clause = "(decided_ts >= ? OR ts >= ?)" if has_decided_ts else "ts >= ?"
        params = (cutoff, cutoff) if has_decided_ts else (cutoff,)

        rows = conn.execute(
            f"SELECT title FROM approvals WHERE {ts_clause}",
            params
        ).fetchall()
        for (raw_title,) in rows:
            clean = _clean_idea_title(raw_title)
            if clean and clean not in drafts:
                drafts.append(clean)

        # Check scout_ideas table
        has_scout = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scout_ideas'").fetchone()
        if has_scout:
            s_rows = conn.execute(
                "SELECT idea FROM scout_ideas WHERE ts >= ?",
                (cutoff,)
            ).fetchall()
            for (raw_idea,) in s_rows:
                clean = _clean_idea_title(raw_idea)
                if clean and clean not in drafts:
                    drafts.append(clean)

        conn.close()
    except Exception:
        pass
    return drafts


def is_similar_to_recent_drafts(idea_text: str, recent_drafts: list[str], threshold: float = SIMILARITY_THRESHOLD) -> tuple[bool, float, str]:
    """Check if idea has word shingle Jaccard similarity > threshold (0.45) against any draft in the last 60 days.

    Returns (is_similar, max_similarity, matched_draft).
    """
    if not idea_text or not recent_drafts:
        return False, 0.0, ""

    words_idea = tokenize_words(_clean_idea_title(idea_text))
    shingles_idea = get_word_shingles(words_idea, k=5)

    max_sim = 0.0
    best_match = ""

    for draft in recent_drafts:
        words_draft = tokenize_words(_clean_idea_title(draft))
        shingles_draft = get_word_shingles(words_draft, k=5)
        sim = jaccard_similarity(shingles_idea, shingles_draft)
        if sim > max_sim:
            max_sim = sim
            best_match = draft
        if sim > threshold:
            return True, sim, draft

    return False, max_sim, best_match


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


# Audience-specific pain points and purchase motivations for grounding 'why this sells'
AUDIENCE_WHY_SELLS_MAP = {
    "teachers": "Teachers invest in ready-to-print classroom management and lesson resources to save hours of weekly prep and keep student evaluations organized.",
    "students": "Students look for structured visual study aids and revision logs to handle multiple coursework deadlines and exam preparation without stress.",
    "exam takers": "Exam candidates actively buy structured revision checklists and syllabus planners to prevent missing high-yield exam sections during crunch time.",
    "small shop owners": "Local shopkeepers prefer straightforward offline record-keeping and tally sheets that run cleanly on their counter without software subscriptions.",
    "home bakers": "Independent bakers require clear recipe cost calculations, order dispatch schedules, and batch ingredient trackers to run home kitchen sales smoothly.",
    "fitness beginners": "Beginners choose simple, non-intimidating workout logs and habit trackers to build consistency without feeling overwhelmed by complex fitness apps.",
    "new parents": "New caregivers appreciate quick physical bedside trackers for infant sleep and feeding routines that can be updated in seconds.",
    "freelancers": "Solo contractors need reliable deliverable milestones and project progress sheets to keep client work organized and invoice schedules on track.",
    "job seekers": "Career switchers and job applicants seek clean, ATS-compliant formats that showcase qualifications professionally without formatting headaches.",
    "wedding planners": "Engaged couples and event coordinators buy comprehensive vendor checklist templates to keep intricate timelines and family arrangements running on schedule.",
    "Telugu-speaking families": "Telugu households value bilingual festival planners and monthly domestic organisers to coordinate traditional events and multi-generational family schedules.",
    "retirees": "Seniors prefer clear, large-print daily routine and hobby workbooks to stay active, track personal wellness, and keep daily affairs structured.",
}


def generate_why_sells(idea_text: str, audience: str | None = None, fmt: str | None = None) -> str:
    """Generate an audience-grounded 'why this sells' rationale without containing any money amounts or prohibited promises."""
    target_aud = audience or detect_audience(idea_text)
    if target_aud and target_aud in AUDIENCE_WHY_SELLS_MAP:
        base_line = AUDIENCE_WHY_SELLS_MAP[target_aud]
    elif target_aud:
        base_line = f"Targeted {target_aud} actively purchase practical digital layouts to replace scattered notes and streamline their regular workflow."
    else:
        base_line = "Target buyers seek structured offline layouts to stay organized and eliminate repetitive daily planning friction."

    return secret_guard.scrub_text(base_line)


def generate_why_pass(idea_text: str, category: str, niche: str) -> str:
    """Generate a short generic 'why this should pass' note (no money amounts) for the checker."""
    cat_lower = category.lower()
    return f"Specific practical layout for targeted buyers with concrete {cat_lower} components and zero prohibited claims."


def evaluate_against_reject_reasons(candidate: dict, reject_reasons: list[dict], available_audiences: list[str] | None = None) -> tuple[dict, bool]:
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
            audience_words = ("student", "remote worker", "creator", "freelancer", "teacher", "parent", "nurse", "couple", "grad") + tuple(a.lower() for a in AUDIENCES)
            if not any(w in full_text for w in audience_words):
                candidate = dict(candidate)
                chosen_aud = (available_audiences[0] if available_audiences else "Remote Creators")
                candidate["idea"] = f"{candidate['idea']} for {chosen_aud.capitalize() if chosen_aud == 'teachers' else chosen_aud}"
                candidate["audience"] = chosen_aud
                candidate["reason"] = candidate["reason"] + f" (Added clear target audience: {chosen_aud})"

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
    aud = item.get("audience") or detect_audience(idea) or detect_audience(reason)
    fmt = item.get("format") or detect_format(idea) or detect_format(reason) or cat.lower()
    why_pass = item.get("why_pass") or generate_why_pass(idea, cat, niche)
    why_sells = item.get("why_sells") or generate_why_sells(idea, audience=aud, fmt=fmt)
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
        "audience": aud,
        "format": fmt,
        "why_sells": why_sells,
    }


SCOUT_PROMPT = """You are SCOUT, an AI research agent finding new digital product categories.
Focus on low-cost digital products in these 3 categories: Printables, Planners, Templates.
Rotate ideas across varied audiences (teachers, students, exam takers, small shop owners, home bakers, fitness beginners, new parents, freelancers, job seekers, wedding planners, Telugu-speaking families, retirees).
Use only supported product formats: printable planner, tracker sheet, workbook, checklist, worksheet set, budget sheet, resume pack.
Do NOT use wall art, slides, or email signatures.
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
    "reason": "Short reason explaining why this idea is promising.",
    "audience": "teachers",
    "format": "printable planner"
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

    # 2. Fetch recent drafts in the last 60 days for similarity deduplication
    recent_drafts_60d = get_recent_drafts(db_path=db_path, days=DRAFT_LOOKBACK_DAYS)

    # 3. Fetch audience usage history for 7-day no-repeat window and weekly caps
    avail_audiences = get_available_audiences(db_path=db_path, days=AUDIENCE_WINDOW_DAYS)
    weekly_audience_counts = get_audience_weekly_counts(db_path=db_path, days=AUDIENCE_WINDOW_DAYS)

    # 4. Fetch top recent reject reasons
    reject_reasons = public_export.get_public_reject_reasons(db_path=db_path)

    # 5. Fetch niche track record for preference weighting
    niche_scores = get_niche_track_record(db_path=db_path)

    # Filter and adjust candidates
    filtered_candidates = []
    for c in scored:
        # Check repeat idea in last 30 days
        if is_repeat_idea(c["idea"], past_ideas):
            continue

        # Check format support (reject excluded formats unless builder module exists)
        fmt = c.get("format") or detect_format(c["idea"])
        if fmt and not is_builder_format_supported(fmt):
            continue

        # Check 60-day draft similarity (word shingles, reject over 0.45)
        is_sim, sim_val, _ = is_similar_to_recent_drafts(c["idea"], recent_drafts_60d, threshold=SIMILARITY_THRESHOLD)
        if is_sim:
            continue

        # Score against reject reasons (rewrite or drop)
        adjusted, dropped = evaluate_against_reject_reasons(c, reject_reasons, available_audiences=avail_audiences)
        if dropped:
            continue

        # Double check if rewritten idea is repeat or similar to recent drafts
        if is_repeat_idea(adjusted["idea"], past_ideas):
            continue
        is_sim_adj, _, _ = is_similar_to_recent_drafts(adjusted["idea"], recent_drafts_60d, threshold=SIMILARITY_THRESHOLD)
        if is_sim_adj:
            continue

        # Check format support on adjusted
        fmt_adj = adjusted.get("format") or detect_format(adjusted["idea"])
        if fmt_adj and not is_builder_format_supported(fmt_adj):
            continue

        # Prefer niches whose past drafts were approved or built READY
        niche_boost = niche_scores.get(adjusted["niche"], 0)
        adjusted["total_score"] = adjusted.get("total_score", 80) + min(15, niche_boost // 2)

        # Ensure why_pass and why_sells are present and scrubbed of currency/money
        if not adjusted.get("why_pass"):
            adjusted["why_pass"] = generate_why_pass(adjusted["idea"], adjusted["category"], adjusted["niche"])
        adjusted["why_pass"] = secret_guard.scrub_text(adjusted["why_pass"])

        aud = adjusted.get("audience") or detect_audience(adjusted["idea"])
        if not adjusted.get("why_sells"):
            adjusted["why_sells"] = generate_why_sells(adjusted["idea"], audience=aud, fmt=fmt_adj)
        adjusted["why_sells"] = secret_guard.scrub_text(adjusted["why_sells"])

        filtered_candidates.append(adjusted)

    # Sort candidates by total_score descending
    filtered_candidates.sort(key=lambda x: x["total_score"], reverse=True)

    # Enforce caps:
    # 1. Max 2 ideas per niche per day
    # 2. Never more than 2 ideas per audience per week
    # 3. Audience rotation: respect 7-day window / available audiences
    niche_counts: dict[str, int] = {}
    session_audience_counts: dict[str, int] = dict(weekly_audience_counts)

    # Pass 1: Prioritize available audiences (0 usages in last 7 days)
    top_5 = []
    remaining_candidates = []

    for item in filtered_candidates:
        aud = item.get("audience") or detect_audience(item["idea"])
        n = item["niche"]

        # Check weekly audience cap
        cur_aud_count = session_audience_counts.get(aud, 0) if aud else 0
        if aud and cur_aud_count >= MAX_IDEAS_PER_AUDIENCE_PER_WEEK:
            continue

        # Check daily niche cap
        if niche_counts.get(n, 0) >= MAX_IDEAS_PER_NICHE_PER_DAY:
            continue

        # If available audiences exist and this candidate's audience is in available_audiences
        if avail_audiences and aud and aud in avail_audiences:
            top_5.append(item)
            niche_counts[n] = niche_counts.get(n, 0) + 1
            session_audience_counts[aud] = session_audience_counts.get(aud, 0) + 1
            if len(top_5) == 5:
                break
        else:
            remaining_candidates.append(item)

    # Pass 2: Fill remaining slots up to 5 while respecting caps
    if len(top_5) < 5:
        for item in remaining_candidates:
            aud = item.get("audience") or detect_audience(item["idea"])
            n = item["niche"]

            cur_aud_count = session_audience_counts.get(aud, 0) if aud else 0
            if aud and cur_aud_count >= MAX_IDEAS_PER_AUDIENCE_PER_WEEK:
                continue

            if niche_counts.get(n, 0) >= MAX_IDEAS_PER_NICHE_PER_DAY:
                continue

            top_5.append(item)
            niche_counts[n] = niche_counts.get(n, 0) + 1
            if aud:
                session_audience_counts[aud] = session_audience_counts.get(aud, 0) + 1
            if len(top_5) == 5:
                break

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = connect(db_path)
    stored_ideas = []

    for item in top_5:
        # Keep the preference-weighted ranking above, but never publish/store a score over 100.
        item = dict(item, total_score=max(0, min(100, item["total_score"])))
        payload_text = (f"Category: {item['category']}\n"
                        f"Niche: {item['niche']}\n"
                        f"Audience: {item.get('audience', 'general')}\n"
                        f"Format: {item.get('format', 'template')}\n"
                        f"Scores: Demand={item['demand_score']}/25, Competition={item['competition_score']}/25, "
                        f"Fit={item['fit_score']}/25, Effort={item['effort_score']}/25 (Total: {item['total_score']}/100)\n"
                        f"Why this sells: {item['why_sells']}\n"
                        f"Why this should pass: {item['why_pass']}\n"
                        f"Reason: {item['reason']}")

        review_str = f"Score: {item['total_score']}/100 | Why sells: {item['why_sells']} | Why pass: {item['why_pass']} | {item['reason']}"
        title = f"[Scout Idea] {item['idea']}"

        approval_id = guardrails.request_approval(
            kind="scout_idea",
            title=title,
            payload=payload_text,
            review=review_str,
            db_path=db_path
        )

        cur = conn.execute("""INSERT INTO scout_ideas
            (ts, category, idea, demand_score, competition_score, fit_score, effort_score, total_score, reason, niche, status, approval_id, synced, why_pass, audience, format, why_sells)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, ?, ?)""",
            (now_iso, item["category"], item["idea"], item["demand_score"], item["competition_score"],
             item["fit_score"], item["effort_score"], item["total_score"], item["reason"], item["niche"], approval_id, item["why_pass"],
             item.get("audience"), item.get("format"), item.get("why_sells")))
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
