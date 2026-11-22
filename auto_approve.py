"""Safe Auto-Decision Engine for chilli-gavva robot (BINNY-V3C).

Drafts waiting more than 24 hours without an owner decision:
- Configurable settings stored in state 'auto_approve_settings'
  Defaults: enabled = True, threshold = 75 (or 70 if legacy), daily_limit = 2
- Max 2 auto-approvals per day (configurable, owner setting)
- Auto-approve only if checker score >= threshold (default 75, owner setting)
- Low score auto-reject (< threshold) with reason "Low score (auto)"
- Broken checker signal guard: never auto-approve if the last 5 drafts all had the same score
- Idea similarity guard: never auto-approve if the idea is similar (> 0.45 shingles)
  to an approved or rejected draft of the last 30 days
- Owner decision always wins: never auto-decide a draft the owner already decided
- Respects pause/kill switches
- Morning digest line: 'Auto-approved yesterday: …' in Tenglish house style
- Exactly one Telegram line per decision sent to owner chat
"""
import datetime
import json
import re
import sqlite3
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

import checker
import guardrails
import notify
import telegram_inbox

AUTO_APPROVE_HOURS = 24
DEFAULT_SCORE_THRESHOLD = 75
LEGACY_DEFAULT_THRESHOLD = 70
DEFAULT_DAILY_LIMIT = 2          # the OWNER chooses the number: /autoapprove limit N
MIN_DAILY_LIMIT, MAX_DAILY_LIMIT = 1, 20
AUTO_REJECT_REASON = "Low score (auto)"
STATE_SETTINGS_KEY = "auto_approve_settings"
MAX_SHINGLE_SIMILARITY = 0.45
QUOTA_MAX_WAIT_HOURS = 72
CHECKER_MAX_BLOCK_HOURS = 72
CHECKER_SINCE_KEY = "auto_approve_checker_block_since"

# Backwards compatibility constant (legacy tests expect 70 when unconfigured)
SCORE_THRESHOLD = 70

# CCG-2: only product drafts are auto-decided and use the daily limit. Everything else that waits in /list
# (Scout ideas, Trend proposals, code patches, tool drafts, media tasks) is a PROPOSAL: the owner decides it,
# it never takes an auto-approve slot or a product build slot, and reports show it on its own line.
PRODUCT_KINDS = ("public_draft",)
_CHECKER_VERDICT = re.compile(r"CHECKER:\s*(PASS|APPROVE|REVISE|REJECT|UNAVAILABLE)\b", re.IGNORECASE)


def is_product_draft(item) -> bool:
    """True for a product draft (kind public_draft); False for proposals and other approval items."""
    return (item or {}).get("kind") in PRODUCT_KINDS


def split_waiting(db_path=None):
    """(product drafts, proposals) waiting for the owner. Self-Improvement Proposals live in learned_rules and
    are counted by waiting_rule_proposals()."""
    items = guardrails.pending(db_path)
    return [i for i in items if is_product_draft(i)], [i for i in items if not is_product_draft(i)]


def waiting_rule_proposals(db_path=None) -> int:
    """Self-Improvement Proposals (improver_loop learned_rules, status 'proposed') waiting for the owner."""
    try:
        conn = guardrails.connect(db_path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM learned_rules WHERE status = 'proposed'").fetchone()
        finally:
            conn.close()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0   # table not made yet on a fresh database


def checker_blocks(review) -> bool:
    """True when the checker gave a verdict that the product builder will refuse (REVISE / REJECT / UNAVAILABLE).

    The builder needs 'CHECKER: PASS/APPROVE' (product_pipeline -> checker.review_passes). Auto-approving such a
    draft makes an approval that can never become a product and wastes a daily slot. A review with no verdict at
    all (older drafts) is judged on its score, as before; the builder checks it again itself."""
    match = _CHECKER_VERDICT.search(review or "")
    return bool(match and match.group(1).upper() not in ("PASS", "APPROVE"))


def _state_conn(db_path=None):
    """Obtain db connection ensuring telegram_state table exists."""
    conn = guardrails.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def get_settings(db_path=None, default_threshold: int = DEFAULT_SCORE_THRESHOLD) -> Dict:
    """Retrieve auto-approve settings from telegram_state.

    Returns dict with keys: 'enabled' (bool), 'threshold' (int), 'daily_limit' (int).
    """
    default_settings = {
        "enabled": True,
        "threshold": default_threshold,
        "daily_limit": DEFAULT_DAILY_LIMIT,
    }
    try:
        conn = _state_conn(db_path)
        row = conn.execute("SELECT value FROM telegram_state WHERE key = ?", (STATE_SETTINGS_KEY,)).fetchone()
        conn.close()
        if not row or not row[0]:
            return default_settings

        data = json.loads(row[0])
        return {
            "enabled": bool(data.get("enabled", True)),
            "threshold": int(data.get("threshold", default_threshold)),
            "daily_limit": int(data.get("daily_limit", DEFAULT_DAILY_LIMIT)),
        }
    except Exception:
        return default_settings


def save_settings(settings: Dict, db_path=None) -> bool:
    """Save auto-approve settings into telegram_state.

    Validates and stores JSON string.
    """
    clean_settings = {
        "enabled": bool(settings.get("enabled", True)),
        "threshold": max(0, min(100, int(settings.get("threshold", DEFAULT_SCORE_THRESHOLD)))),
        "daily_limit": max(0, int(settings.get("daily_limit", DEFAULT_DAILY_LIMIT))),
    }
    try:
        conn = _state_conn(db_path)
        val = json.dumps(clean_settings, sort_keys=True)
        conn.execute(
            "INSERT INTO telegram_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (STATE_SETTINGS_KEY, val),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def _parse_item_time(ts_str):
    """Parse timestamp string as UTC datetime."""
    if not ts_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        return None


def get_draft_score(item, db_path=None):
    """Extract score (0-100) from checker review or product council conviction."""
    review = item.get("review") or ""
    score, _ = checker.parse_review(review)
    if score is not None:
        return score

    # Fallback to product council if conviction score available (conviction is 0-10, scaled to 100)
    try:
        import product_council
        summary = product_council.get_council_summary(item.get("id"), db_path=db_path)
        if summary and summary.get("conviction") is not None:
            return summary["conviction"] * 10
    except Exception:
        pass

    # Regex fallback for score pattern
    m = re.search(r"(\d{1,3})\s*/\s*100", review)
    if m:
        return int(m.group(1))
    return 0


def tokenize_words(text: str) -> List[str]:
    """Tokenize text into lowercase alphanumeric words."""
    if not text:
        return []
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", text).strip().lower()
    return [w for w in clean.split() if w]


def get_word_shingles(words: List[str], k: int = 5) -> Set[str]:
    """Generate k-shingles (word n-grams) from a sequence of words.

    If words count is less than k and words is non-empty, falls back to a single
    shingle of all words to allow small texts (e.g. short titles) to be compared.
    """
    if not words:
        return set()
    if len(words) < k:
        return {" ".join(words)}
    shingles: Set[str] = set()
    for i in range(len(words) - k + 1):
        shingles.add(" ".join(words[i : i + k]))
    return shingles


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Calculate Jaccard similarity index between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def has_broken_checker_signal(db_path=None, check_window: int = 5) -> bool:
    """Check if the last N scored drafts in the database all had the exact same score.

    If last 5 drafts all had identical scores, it indicates a broken checker / mock / stuck signal.
    Auto-approvals must be paused/blocked under this condition.
    """
    try:
        conn = guardrails.connect(db_path)
        rows = conn.execute(
            "SELECT id, review FROM approvals WHERE review IS NOT NULL AND review != '' "
            "ORDER BY id DESC LIMIT ?",
            (check_window * 2,),
        ).fetchall()
        conn.close()

        scores = []
        for aid, rev in rows:
            sc, _ = checker.parse_review(rev or "")
            if sc is None:
                m = re.search(r"(\d{1,3})\s*/\s*100", rev or "")
                if m:
                    sc = int(m.group(1))
            if sc is not None:
                scores.append(sc)
            if len(scores) >= check_window:
                break

        if len(scores) < check_window:
            return False

        first = scores[0]
        return all(s == first for s in scores)
    except Exception:
        return False


def is_similar_to_recent_draft(
    candidate_text: str,
    candidate_id: Optional[int] = None,
    db_path=None,
    now=None,
    days: int = 30,
    threshold: float = MAX_SHINGLE_SIMILARITY,
) -> Tuple[bool, float, Optional[int]]:
    """Check if candidate draft is similar (> 0.45 shingles) to an approved or rejected draft of the last 30 days.

    Returns (is_similar, max_similarity, matched_id).
    """
    if not candidate_text:
        return False, 0.0, None

    cand_words = tokenize_words(candidate_text)
    if cand_words and cand_words[0] == "draft":
        cand_words = cand_words[1:]
    cand_shingles = get_word_shingles(cand_words, k=5)
    if not cand_shingles:
        return False, 0.0, None

    if now is None:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(now, (int, float)):
        now_dt = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    elif isinstance(now, datetime.datetime):
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    else:
        now_dt = datetime.datetime.now(datetime.timezone.utc)

    cutoff_dt = now_dt - datetime.timedelta(days=days)

    try:
        conn = guardrails.connect(db_path)
        rows = conn.execute(
            "SELECT id, title, payload, ts, decided_ts FROM approvals "
            "WHERE status IN ('approved', 'rejected')",
        ).fetchall()
        conn.close()

        max_sim = 0.0
        best_match_id = None

        for aid, title, payload, ts_str, decided_str in rows:
            if candidate_id is not None and aid == candidate_id:
                continue

            ref_time = _parse_item_time(decided_str) or _parse_item_time(ts_str)
            if ref_time and ref_time < cutoff_dt:
                continue

            ref_combined = f"{title or ''} {payload or ''}"
            ref_words = tokenize_words(ref_combined)
            if ref_words and ref_words[0] == "draft":
                ref_words = ref_words[1:]
            ref_shingles = get_word_shingles(ref_words, k=5)

            sim = jaccard_similarity(cand_shingles, ref_shingles)
            if sim > max_sim:
                max_sim = sim
                best_match_id = aid

        is_sim = max_sim > threshold
        return is_sim, max_sim, best_match_id
    except Exception:
        return False, 0.0, None


def count_auto_approvals_today(db_path=None, now=None) -> int:
    """Count how many drafts were auto-approved today (UTC day)."""
    if now is None:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(now, (int, float)):
        now_dt = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    elif isinstance(now, datetime.datetime):
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    else:
        now_dt = datetime.datetime.now(datetime.timezone.utc)

    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    day_end = (now_dt.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)).isoformat()

    try:
        conn = guardrails.connect(db_path)
        row = conn.execute(
            # Only the robot's own decisions: an owner button approval has no reason and must not use a slot.
            "SELECT COUNT(*) FROM approvals WHERE status = 'approved' "
            "AND reason = 'auto-approved' AND decided_ts >= ? AND decided_ts < ?",
            (day_start, day_end),
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def morning_digest_line(db_path=None, now=None) -> str:
    """Generate a morning digest line for auto-approvals in Tenglish house style.

    Format: 'Auto-approved yesterday: None' or 'Auto-approved yesterday: N drafts (titles...)'
    House style: Tenglish (Telugu sentence words in English letters + English nouns: Draft, Pack, etc.).
    No currency amounts.
    """
    if now is None:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(now, (int, float)):
        now_dt = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    elif isinstance(now, datetime.datetime):
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    else:
        now_dt = datetime.datetime.now(datetime.timezone.utc)

    # Yesterday's full day in UTC
    yesterday_date = (now_dt - datetime.timedelta(days=1)).date()
    yesterday_start = datetime.datetime.combine(yesterday_date, datetime.time.min, tzinfo=datetime.timezone.utc).isoformat()
    yesterday_end = datetime.datetime.combine(yesterday_date, datetime.time.max, tzinfo=datetime.timezone.utc).isoformat()

    try:
        conn = guardrails.connect(db_path)
        rows = conn.execute(
            "SELECT id, title FROM approvals WHERE status = 'approved' "
            "AND reason = 'auto-approved' AND decided_ts >= ? AND decided_ts <= ? "
            "ORDER BY id ASC",
            (yesterday_start, yesterday_end),
        ).fetchall()
        conn.close()

        if not rows:
            return "Auto-approved yesterday: None (ninna auto-approve aina drafts emi levu)."

        count = len(rows)
        titles = [f"#{aid} {(title or '')[:30]}".strip() for aid, title in rows]
        titles_str = ", ".join(titles)
        return f"Auto-approved yesterday: {count} drafts ({titles_str})."
    except Exception:
        return "Auto-approved yesterday: None."


def _as_utc(now=None) -> datetime.datetime:
    if isinstance(now, (int, float)):
        return datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    if isinstance(now, datetime.datetime):
        return now.astimezone(datetime.timezone.utc) if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def _checker_since(db_path=None, now=None, track=False):
    """Persist actual continuous checker-block duration; status reads never start it."""
    conn = _state_conn(db_path)
    try:
        row = conn.execute("SELECT value FROM telegram_state WHERE key=?", (CHECKER_SINCE_KEY,)).fetchone()
        since = _parse_item_time(row[0]) if row else None
        if track:
            if not has_broken_checker_signal(db_path):
                conn.execute("DELETE FROM telegram_state WHERE key=?", (CHECKER_SINCE_KEY,))
                since = None
            elif since is None or since > _as_utc(now):
                since = _as_utc(now)
                conn.execute("INSERT INTO telegram_state(key,value) VALUES (?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (CHECKER_SINCE_KEY, since.isoformat()))
            conn.commit()
        return since
    finally:
        conn.close()


def _expire_rule_proposals(db_path=None, now=None):
    """Expire imp: proposals without ever applying a learned rule."""
    conn = guardrails.connect(db_path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learned_rules'").fetchone():
            return []
        if "reason" not in {row[1] for row in conn.execute("PRAGMA table_info(learned_rules)")}:
            conn.execute("ALTER TABLE learned_rules ADD COLUMN reason TEXT")
        rows = conn.execute("SELECT id, rule_text, created_ts FROM learned_rules WHERE status='proposed'").fetchall()
        decisions = []
        now_dt = _as_utc(now)
        for rule_id, text, ts in rows:
            created = _parse_item_time(ts)
            if created is None or (now_dt - created).total_seconds() < AUTO_APPROVE_HOURS * 3600:
                continue
            result = conn.execute("UPDATE learned_rules SET status='rejected', reason=?, decided_ts=? "
                                  "WHERE id=? AND status='proposed'",
                                  ("owner time ayipoyindi", now_dt.isoformat(), rule_id))
            if result.rowcount:
                decisions.append({"id": f"imp:{rule_id}", "action": "rejected", "kind": "self_improvement",
                                  "title": str(text or "")[:50], "score": None,
                                  "line": f"Proposal imp:{rule_id} auto-rejected: owner time ayipoyindi."})
        conn.commit()
        return decisions
    finally:
        conn.close()


def effective_settings(db_path=None) -> Dict:
    """The settings auto_decide_drafts really uses. With no owner setting stored: threshold SCORE_THRESHOLD (70,
    legacy) and daily limit DEFAULT_DAILY_LIMIT (2). The owner changes the limit with /autoapprove limit N."""
    conn_state = _state_conn(db_path)
    state_row = conn_state.execute("SELECT value FROM telegram_state WHERE key = ?", (STATE_SETTINGS_KEY,)).fetchone()
    conn_state.close()
    if state_row and state_row[0]:
        return get_settings(db_path, default_threshold=DEFAULT_SCORE_THRESHOLD)
    return {"enabled": True, "threshold": SCORE_THRESHOLD, "daily_limit": DEFAULT_DAILY_LIMIT}


def set_daily_limit(limit: int, db_path=None) -> Dict:
    """Owner setting (CCG-3): save a daily auto-approve limit of 1..MAX_DAILY_LIMIT, keep the other settings."""
    limit = int(limit)
    if not MIN_DAILY_LIMIT <= limit <= MAX_DAILY_LIMIT:
        raise ValueError(f"daily limit must be {MIN_DAILY_LIMIT}-{MAX_DAILY_LIMIT}")
    settings = dict(effective_settings(db_path), daily_limit=limit)
    if not save_settings(settings, db_path):
        raise OSError("auto-approve settings not saved")
    return settings


def _plan(db_path=None, now=None, settings=None) -> List[Dict]:
    """What the 24-hour rule does with every waiting item right now, oldest first (nothing is changed).

    Each entry: id, kind, title, hours, product (bool), score, action ("approve" | "reject" | "wait") and why
    (a WHY_TEXT code; 'match' for a similar draft). auto_decide_drafts acts on this plan and waiting_report
    shows it, so the report can never disagree with what the robot does."""
    settings = settings or effective_settings(db_path)
    enabled = settings.get("enabled", True)
    threshold = settings.get("threshold", DEFAULT_SCORE_THRESHOLD)
    daily_limit = settings.get("daily_limit", DEFAULT_DAILY_LIMIT)
    now_dt = _as_utc(now)
    stopped = guardrails.is_paused(db_path) or guardrails.is_killed()
    broken_checker = None   # checked once, only when a draft gets that far
    checker_since = _checker_since(db_path)
    approved_today = count_auto_approvals_today(db_path, now=now_dt)
    decided: List[Tuple[int, Set[str]]] = []   # (id, shingles) approved/rejected earlier in this pass
    plan = []
    for item in guardrails.pending(db_path):
        current = guardrails.get_approval(item["id"], db_path)
        if not current or current.get("status") != "pending":
            continue   # the owner's decision always wins
        item_dt = _parse_item_time(current.get("ts"))
        hours = int((now_dt - item_dt).total_seconds() // 3600) if item_dt else 0
        entry = {"id": current["id"], "kind": current.get("kind") or "", "title": current.get("title") or "",
                 "hours": hours, "product": is_product_draft(current), "score": None, "action": "wait"}
        plan.append(entry)
        if not entry["product"]:
            entry["why"] = "proposal"
            if (str(current.get("kind", "")).startswith("imp:")
                    and item_dt and hours >= AUTO_APPROVE_HOURS and not stopped):
                entry["action"], entry["why"] = "reject", "proposal_timeout"
        elif stopped:
            entry["why"] = "paused"
        elif not item_dt:
            entry["why"] = "no_time"
        elif (now_dt - item_dt).total_seconds() < AUTO_APPROVE_HOURS * 3600:
            entry["why"] = "under_24h"
        else:
            text = f"{current.get('title') or ''} {current.get('payload') or ''}"
            entry["score"] = get_draft_score(current, db_path=db_path)
            if entry["score"] < threshold:
                entry["action"], entry["why"] = "reject", "low_score"
                decided.append((current["id"], _shingles(text)))
            elif not enabled:
                entry["why"] = "off"
            elif approved_today >= daily_limit and hours < QUOTA_MAX_WAIT_HOURS:
                entry["why"] = "daily_limit"
            else:
                if broken_checker is None:
                    broken_checker = has_broken_checker_signal(db_path)
                if broken_checker:
                    if checker_since and (now_dt - checker_since).total_seconds() >= CHECKER_MAX_BLOCK_HOURS * 3600:
                        entry["action"], entry["why"] = "reject", "checker_problem"
                    else:
                        entry["why"] = "checker_signal"
                elif checker_blocks(current.get("review")):
                    entry["why"] = "checker_revise"
                else:
                    is_sim, _, matched_id = is_similar_to_recent_draft(
                        text, candidate_id=current["id"], db_path=db_path, now=now_dt, days=30,
                        threshold=MAX_SHINGLE_SIMILARITY)
                    if not is_sim:   # decisions earlier in this same pass count like decided drafts
                        mine = _shingles(text)
                        matched_id = next((aid for aid, other in decided
                                           if jaccard_similarity(mine, other) > MAX_SHINGLE_SIMILARITY), None)
                        is_sim = matched_id is not None
                    if is_sim:
                        entry["why"], entry["match"] = "similar", matched_id
                    else:
                        entry["action"], entry["why"] = "approve", "ready"
                        approved_today += 1   # the next draft sees this slot as used
                        decided.append((current["id"], _shingles(text)))
    return plan


def _shingles(text: str) -> Set[str]:
    """5-word shingles exactly like is_similar_to_recent_draft (leading 'draft' dropped)."""
    words = tokenize_words(text)
    if words and words[0] == "draft":
        words = words[1:]
    return get_word_shingles(words, k=5)


def auto_decide_drafts(db_path=None, now=None):
    """Examine pending drafts waiting > 24 hours and apply auto-decisions.

    Returns a list of result dicts:
      [{"id": int, "action": "approved"|"rejected", "title": str, "score": int, "line": str}]
    """
    if guardrails.is_paused(db_path) or guardrails.is_killed():
        return []
    _checker_since(db_path, now, track=True)
    settings = effective_settings(db_path)
    threshold = settings.get("threshold", DEFAULT_SCORE_THRESHOLD)
    decisions = _expire_rule_proposals(db_path, now)
    for entry in _plan(db_path, now, settings):
        approval_id, score, title = entry["id"], entry["score"], entry["title"][:50]
        if entry["action"] == "approve":
            if guardrails.decide(approval_id, True, db_path, reason="auto-approved"):
                decisions.append({"id": approval_id, "action": "approved", "title": title, "score": score,
                                  "line": f"Draft #{approval_id} auto-approved (score {score}/100 >= {threshold}): "
                                          f"{title}"})
        elif entry["action"] == "reject":
            reason = {"checker_problem": "checker problem", "proposal_timeout": "owner time ayipoyindi"}.get(
                entry["why"], AUTO_REJECT_REASON)
            if reason == AUTO_REJECT_REASON:
                current = guardrails.get_approval(approval_id, db_path) or {}
                _, reasons = checker.parse_review(current.get("review") or "")
                if reasons:
                    reason += ": " + "; ".join(str(value) for value in reasons[:2])[:240]
            if guardrails.decide(approval_id, False, db_path, reason=reason):
                decisions.append({"id": approval_id, "action": "rejected", "title": title, "score": score,
                                  "line": f"Draft #{approval_id} auto-rejected: {reason}. {title}"})
        # guardrails.decide is a compare-and-set: a prior owner decision wins.
        # Use the injected clock for decisions actually made by this run.
        if decisions and decisions[-1]["id"] == approval_id:
            conn = guardrails.connect(db_path)
            try:
                conn.execute("UPDATE approvals SET decided_ts=? WHERE id=? AND status=? AND reason=?",
                             (_as_utc(now).isoformat(), approval_id, decisions[-1]["action"],
                              "auto-approved" if entry["action"] == "approve" else reason))
                conn.commit()
            finally:
                conn.close()
    return decisions


# Why a draft is still waiting, in the owner's Tenglish (CCG-3).
WHY_TEXT = {
    "under_24h": "inka 24 ganta kaaledu ({left}h migilindi)",
    "daily_limit": "ee roju daily limit ({limit}) aipoyindi; repu run lo vastundi",
    "similar": "recent draft #{match} laaga undi; meere choodandi",
    "checker_signal": "checker signal paadu (last 5 scores okate); auto-approve aagindi",
    "checker_revise": "Checker REVISE annadi; builder build cheyadu, meere decide cheyandi",
    "off": "auto-approve OFF lo undi",
    "paused": "robot pause lo undi",
    "no_time": "draft time teliyadu; meere decide cheyandi",
    "low_score": "score {score}/100 threshold {threshold} kanna takkuva; next run lo auto-reject",
    "ready": "next run lo auto-approve avutundi",
    "proposal": "proposal: meere decide cheyali (auto-approve cheyadu)",
    "proposal_timeout": "owner time ayipoyindi; next run lo proposal reject avutundi",
    "checker_problem": "checker problem; 3 rojulu aagindi, next run lo reject avutundi",
}
KIND_LABEL = {"scout_idea": "Scout idea", "trend_proposal": "Trend", "code_patch": "Code patch",
              "tool_draft": "Tool", "media_task": "Media"}


def waiting_report(db_path=None, now=None) -> Dict:
    """Every waiting item with hours waited and WHY it is still waiting (CCG-3).

    Returns {"settings", "approved_today", "drafts": [...], "proposals": [...], "rule_proposals": int}; each item
    has id, kind, title, hours, why (code) and why_text (Tenglish). Proposals are never counted as product drafts."""
    settings = effective_settings(db_path)
    out = {"settings": settings, "approved_today": count_auto_approvals_today(db_path, now=_as_utc(now)),
           "drafts": [], "proposals": [], "rule_proposals": waiting_rule_proposals(db_path)}
    for entry in _plan(db_path, now, settings):
        entry["why_text"] = WHY_TEXT[entry["why"]].format(
            left=max(1, AUTO_APPROVE_HOURS - entry["hours"]), limit=settings.get("daily_limit"),
            match=entry.get("match"), score=entry.get("score"), threshold=settings.get("threshold"))
        out["drafts" if entry["product"] else "proposals"].append(entry)
    return out


def waiting_report_text(db_path=None, now=None, max_lines: int = 12) -> str:
    """The waiting report for Telegram, in Tenglish. No amounts, no secrets."""
    report = waiting_report(db_path, now)
    settings, drafts, proposals = report["settings"], report["drafts"], report["proposals"]
    oldest = max((d["hours"] for d in drafts), default=0)
    lines = ["Auto-approve status (24 ganta rule)",
             f"Auto-Approve Settings: {'ON' if settings.get('enabled', True) else 'OFF'}, threshold {settings.get('threshold')}"
             f"/100, daily limit {settings.get('daily_limit')} (ee roju auto-approved: {report['approved_today']})",
             f"Product drafts waiting: {len(drafts)}" + (f" (oldest {oldest}h)" if drafts else "")]
    for d in drafts[:max_lines]:
        lines.append(f"#{d['id']} {d['hours']}h: {d['why_text']} | {d['title'][:45]}")
    if len(drafts) > max_lines:
        lines.append(f"... inka {len(drafts) - max_lines} drafts (/list)")
    lines.append(f"Proposals waiting: {len(proposals)} (product drafts kaadu; meere decide cheyali)")
    for p in proposals[:max_lines]:
        lines.append(f"#{p['id']} {p['hours']}h {KIND_LABEL.get(p['kind'], p['kind'])} | {p['title'][:45]}")
    if len(proposals) > max_lines:
        lines.append(f"... inka {len(proposals) - max_lines} proposals")
    lines.append(f"Self-Improvement Proposals waiting: {report['rule_proposals']}")
    lines.append(f"Limit marchadaniki: /autoapprove limit N ({MIN_DAILY_LIMIT}-{MAX_DAILY_LIMIT})")
    return "\n".join(lines)


def queue_digest(db_path=None, now=None):
    """Actual queue counts for today's morning message, using the supplied UTC day."""
    now_dt = _as_utc(now)
    start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    plan = _plan(db_path, now_dt)
    approved = rejected = 0
    reasons = Counter(row["why"] for row in plan if row["action"] == "wait")
    rule_waiting = 0
    conn = guardrails.connect(db_path)
    try:
        for status, ts in conn.execute("SELECT status,decided_ts FROM approvals WHERE status IN ('approved','rejected')"):
            decided = _parse_item_time(ts)
            if decided and start <= decided < end:
                approved += status == "approved"
                rejected += status == "rejected"
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learned_rules'").fetchone():
            for status, created_ts, decided_ts in conn.execute("SELECT status,created_ts,decided_ts FROM learned_rules"):
                if status == "proposed":
                    rule_waiting += 1
                    created = _parse_item_time(created_ts)
                    reasons["under_24h" if created and now_dt-created < datetime.timedelta(hours=24)
                            else "proposal"] += 1
                decided = _parse_item_time(decided_ts)
                if status in ("approved", "rejected") and decided and start <= decided < end:
                    approved += status == "approved"
                    rejected += status == "rejected"
    finally:
        conn.close()
    return {"waiting": len(plan)+rule_waiting, "approved": approved, "rejected": rejected,
            "decided": approved+rejected, "blocked": sum(reasons.values()), "reasons": dict(reasons),
            "new_drafts_paused": len(plan) > 10}


def queue_digest_line(db_path=None, now=None):
    """Short Tenglish line; counts come from persisted decisions, never a planned action."""
    data = queue_digest(db_path, now)
    labels = {"under_24h": "24h inka kaaledu", "daily_limit": "daily limit", "similar": "duplicate check",
              "checker_signal": "checker signal", "checker_revise": "Checker changes kavali",
              "off": "owner OFF pettaru", "paused": "robot pause", "no_time": "Draft time teliyadu",
              "proposal": "proposal: owner decision kavali"}
    why = "; ".join(f"{labels.get(reason, reason)} {count}" for reason,count in sorted(data["reasons"].items()))
    line = (f"Waiting {data['waiting']}; decided today {data['decided']} "
            f"(approved {data['approved']} / rejected {data['rejected']}); blocked {data['blocked']}")
    if why:
        line += f": {why}"
    if data["new_drafts_paused"]:
        line += ". Kotha Drafts ee run lo aapamu (waiting 10 minchindi)."
    return line


def _morning_queue_day(db_path=None, now=None):
    """One owner summary each local day, after the configured morning hour."""
    now_dt = _as_utc(now)
    local = now_dt.astimezone(datetime.timezone(datetime.timedelta(hours=5,minutes=30)))
    if guardrails.is_paused(db_path) or guardrails.is_killed() or not 8 <= local.hour < 22:
        return None
    import tg_settings
    if tg_settings.is_quiet_hour(now_dt, db_path) or local.hour < tg_settings.get_morning_post_hour(db_path):
        return None
    day = local.date().isoformat()
    conn = _state_conn(db_path)
    try:
        row = conn.execute("SELECT value FROM telegram_state WHERE key='auto_approve_queue_morning_day'").fetchone()
        return None if row and row[0] == day else day
    finally:
        conn.close()


def _mark_morning_queue(day, db_path=None):
    conn = _state_conn(db_path)
    try:
        conn.execute("INSERT INTO telegram_state(key,value) VALUES ('auto_approve_queue_morning_day',?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (day,))
        conn.commit()
    finally:
        conn.close()


def run_auto_decisions(db_path=None, token=None, owner_chat_id=None, now=None):
    """Run auto-decisions and send exactly one Telegram line per decision to owner chat."""
    decisions = auto_decide_drafts(db_path=db_path, now=now)
    configured, cfg_token, cfg_chat_id = notify._telegram_configured()
    tok = token or cfg_token
    chat = owner_chat_id or cfg_chat_id

    day = _morning_queue_day(db_path, now)
    summary = queue_digest_line(db_path, now) if day else None
    messages = [d["line"] for d in decisions]
    if summary:
        if messages:
            messages[0] += "\n" + summary
        else:
            messages.append(summary)
    from tg_menu import owner_reply_pages
    for index, line in enumerate(messages):
        line = owner_reply_pages(line, "/autoapprove status pampandi; waiting reasons chudandi.")[0]
        sent = False
        if tok and chat:
            sent = telegram_inbox.send_text(tok, chat, line)
        elif configured:
            sent = notify.send(line)
        if index == 0 and summary and sent is not False:
            _mark_morning_queue(day, db_path)

    return len(decisions)
