"""24h Auto-Decision Engine for chilli-gavva robot.

Drafts waiting more than 24 hours without an owner decision:
- Checker/product council score >= 70 -> auto-approved
- Checker/product council score < 70 -> auto-rejected with reason "Low score (auto)"
- The owner's own decision always wins (never auto-decide a draft the owner already decided).
- Respects pause/kill switches.
- Exactly one Telegram line per decision sent to owner chat.
"""
import datetime
import re
import checker
import guardrails
import notify
import telegram_inbox

AUTO_APPROVE_HOURS = 24
SCORE_THRESHOLD = 70
AUTO_REJECT_REASON = "Low score (auto)"


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


def auto_decide_drafts(db_path=None, now=None):
    """Examine pending drafts waiting > 24 hours and apply auto-decisions.

    Returns a list of result dicts: [{"id": int, "action": "approved"|"rejected", "title": str, "score": int, "line": str}]
    """
    if guardrails.is_paused(db_path) or guardrails.is_killed():
        return []

    if now is None:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(now, (int, float)):
        now_dt = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    elif isinstance(now, datetime.datetime):
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    else:
        now_dt = datetime.datetime.now(datetime.timezone.utc)

    cutoff_seconds = AUTO_APPROVE_HOURS * 3600
    pending_items = guardrails.pending(db_path)
    decisions = []

    for item in pending_items:
        approval_id = item["id"]
        # Owner's decision always wins: verify current status from db is still pending
        current = guardrails.get_approval(approval_id, db_path)
        if not current or current.get("status") != "pending":
            continue

        item_dt = _parse_item_time(current.get("ts"))
        if not item_dt:
            continue

        waiting_seconds = (now_dt - item_dt).total_seconds()
        if waiting_seconds < cutoff_seconds:
            continue

        score = get_draft_score(current, db_path=db_path)
        title = (current.get("title") or "")[:50]

        if score >= SCORE_THRESHOLD:
            # Auto-approve
            changed = guardrails.decide(approval_id, True, db_path)
            if changed:
                line = f"Draft #{approval_id} auto-approved (score {score}/100 >= 70): {title}"
                decisions.append({
                    "id": approval_id,
                    "action": "approved",
                    "title": title,
                    "score": score,
                    "line": line,
                })
        else:
            # Auto-reject with reason "Low score (auto)"
            changed = guardrails.decide(approval_id, False, db_path, reason=AUTO_REJECT_REASON)
            if changed:
                # Also ensure reason is saved in case decide() didn't record it directly
                guardrails.set_reason(approval_id, AUTO_REJECT_REASON, db_path)
                line = f"Draft #{approval_id} auto-rejected (score {score}/100 < 70, Low score (auto)): {title}"
                decisions.append({
                    "id": approval_id,
                    "action": "rejected",
                    "title": title,
                    "score": score,
                    "line": line,
                })

    return decisions


def run_auto_decisions(db_path=None, token=None, owner_chat_id=None, now=None):
    """Run auto-decisions and send exactly one Telegram line per decision to owner chat."""
    decisions = auto_decide_drafts(db_path=db_path, now=now)
    if not decisions:
        return 0

    configured, cfg_token, cfg_chat_id = notify._telegram_configured()
    tok = token or cfg_token
    chat = owner_chat_id or cfg_chat_id

    for d in decisions:
        line = d["line"]
        if tok and chat:
            telegram_inbox.send_text(tok, chat, line)
        elif configured:
            notify.send(line)

    return len(decisions)
