"""tg_keyboard.py - Reply-keyboard flow for draft decisions in Telegram.

Provides a text-message driven decision interface for drafts waiting approval,
replacing or supplementing inline callback queries which may be dropped.

Plugin Interface:
  COMMANDS = [...]
  def handle(text: str, ctx: dict) -> dict | None

ctx keys:
  db_path: path to sqlite db
  chat_id: Telegram chat id of sender
  now: datetime object (UTC)
  send_text(text, keyboard=None): send text message, keyboard can be reply markup or None
  send_document, send_photo, send_media_group
  state_get(key, default=None)
  state_set(key, value)
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, List, Optional, Tuple

COMMANDS = [
    {
        "cmd": "/decide",
        "words": ["decide", "nirnayinchi", "drafts decide", "decide drafts"],
        "help_en": "Review and decide drafts one by one using a reply keyboard.",
        "help_te": "Reply keyboard tho drafts okati okati ga chusi decide cheyandi.",
    }
]

# Idle timeout: 10 minutes in seconds
SESSION_TIMEOUT_SECONDS = 600

# Canonical reject reason labels from guardrails.REJECT_REASONS
# (Display order for reply keyboard)
REJECT_REASON_OPTIONS = [
    "Too generic",
    "Duplicate",
    "Weak niche",
    "Promises too much",
    "Other",
]

# Canonical mapping for reason labels
CANONICAL_REASONS = {
    "too generic": "Too generic",
    "duplicate": "Duplicate",
    "weak niche": "Weak niche",
    "promises too much": "Promises too much",
    "other": "Other",
}

# State keys
KEY_ACTIVE_SESSION = "tg_keyboard:active_session"
KEY_REJECTING_DRAFT = "tg_keyboard:rejecting_draft"
KEY_AUDIT_LOG = "tg_keyboard:audit_log"


def _get_now_dt(ctx: dict) -> datetime.datetime:
    now = ctx.get("now")
    if isinstance(now, datetime.datetime):
        if now.tzinfo is not None:
            return now.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return now
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _load_json_state(ctx: dict, key: str, default: Any = None) -> Any:
    state_get = ctx.get("state_get")
    if not callable(state_get):
        return default
    val = state_get(key, None)
    if val is None or val == "":
        return default
    if isinstance(val, (dict, list, int, float, bool)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return default


def _save_json_state(ctx: dict, key: str, value: Any) -> None:
    state_set = ctx.get("state_set")
    if callable(state_set):
        state_set(key, json.dumps(value, ensure_ascii=False))


def _clear_state_key(ctx: dict, key: str) -> None:
    state_set = ctx.get("state_set")
    if callable(state_set):
        state_set(key, "")


def _append_audit_log(ctx: dict, entry: dict) -> None:
    logs = _load_json_state(ctx, KEY_AUDIT_LOG, default=[])
    if not isinstance(logs, list):
        logs = []
    logs.append(entry)
    # keep last 500 audit entries
    if len(logs) > 500:
        logs = logs[-500:]
    _save_json_state(ctx, KEY_AUDIT_LOG, logs)


def _is_owner(ctx: dict) -> bool:
    chat_id = str(ctx.get("chat_id") or "")
    if not chat_id:
        return True
    try:
        import notify
        configured, _, owner_chat_id = notify._telegram_configured()
        if configured and owner_chat_id:
            return str(owner_chat_id) == chat_id
    except Exception:
        pass
    return True


def _get_guardrails():
    import guardrails
    return guardrails


def _get_approval_with_reason(approval_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    guardrails = _get_guardrails()
    conn = guardrails.connect(db_path)
    row = conn.execute(
        "SELECT id, ts, kind, title, payload, status, review, decided_ts, reason "
        "FROM approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    keys = ("id", "ts", "kind", "title", "payload", "status", "review", "decided_ts", "reason")
    return dict(zip(keys, row))


def _get_draft_card(item: dict, db_path: Optional[str] = None) -> str:
    """Format title, checker score, and 3-line summary for a draft."""
    try:
        import telegram_inbox
        return telegram_inbox.draft_card(item, db_path=db_path)
    except Exception:
        pass

    # Fallback formatting if telegram_inbox isn't available or errors
    lines = [f"#{item['id']} {item.get('title', '')[:70]}"]
    review = item.get("review") or ""
    try:
        import checker
        score, reasons = checker.parse_review(review)
        if score is not None:
            lines.append(f"Checker: {score}/100")
            lines += [f"- {r[:60]}" for r in reasons[:2]]
        else:
            lines.append("Checker: not checked yet")
    except Exception:
        lines.append("Checker: not checked yet")
    return "\n".join(lines)


def _get_view_text(approval_id: int, db_path: Optional[str] = None) -> str:
    try:
        import telegram_inbox
        return telegram_inbox.view_text(approval_id, db_path=db_path)
    except Exception:
        pass
    item = _get_approval_with_reason(approval_id, db_path)
    if not item:
        return f"Draft #{approval_id} not found."
    return f"#{item['id']} [{item.get('status')}] {item.get('title')}\n\n{item.get('payload') or ''}"


def _get_pending_drafts(db_path: Optional[str] = None) -> List[dict]:
    guardrails = _get_guardrails()
    return guardrails.pending(db_path)


def _build_decide_keyboard(draft_id: int) -> List[List[str]]:
    return [
        [f"✅ {draft_id}", f"❌ {draft_id}", f"👁 {draft_id}"],
        ["⏭ Next", "🛑 Stop"],
    ]


def _build_reject_keyboard(draft_id: int) -> List[List[str]]:
    # One-tap reject reasons + cancel
    return [
        ["Too generic", "Duplicate"],
        ["Weak niche", "Promises too much"],
        ["Other", f"↩️ Cancel #{draft_id}"],
    ]


def _build_remove_keyboard() -> dict:
    return {"remove_keyboard": True}


def _send(ctx: dict, text: str, keyboard: Any = None) -> bool:
    send_text = ctx.get("send_text")
    if callable(send_text):
        send_text(text, keyboard=keyboard)
        return True
    return False


def _check_session_expired(ctx: dict, now_dt: datetime.datetime) -> bool:
    session = _load_json_state(ctx, KEY_ACTIVE_SESSION, default=None)
    if not session:
        return False
    last_action_ts = session.get("last_action_ts")
    if not last_action_ts:
        return False
    try:
        last_dt = datetime.datetime.fromisoformat(last_action_ts)
        elapsed = (now_dt - last_dt).total_seconds()
        if elapsed > SESSION_TIMEOUT_SECONDS:
            # Expired! Clear session and notify with keyboard removal
            _clear_state_key(ctx, KEY_ACTIVE_SESSION)
            _clear_state_key(ctx, KEY_REJECTING_DRAFT)
            _send(ctx, "Decide session timeout aindi (10 min idle). Keyboard teesesanu.", keyboard=_build_remove_keyboard())
            return True
    except Exception:
        pass
    return False


def _execute_approve(draft_id: int, ctx: dict, db_path: Optional[str], now_dt: datetime.datetime) -> Tuple[bool, str]:
    guardrails = _get_guardrails()
    item = _get_approval_with_reason(draft_id, db_path)
    if not item:
        return False, f"Draft #{draft_id} kanipinchaledu (not found)."

    if item.get("status") == "approved":
        return True, f"Draft #{draft_id} idi varake Approved aindi (already approved) ✓"
    if item.get("status") == "rejected":
        return False, f"Draft #{draft_id} idi varake Rejected aindi (already rejected, cannot approve)."

    changed = guardrails.decide(draft_id, True, db_path)
    if changed:
        _append_audit_log(ctx, {
            "ts": now_dt.isoformat(),
            "draft_id": draft_id,
            "action": "approved",
            "chat_id": str(ctx.get("chat_id") or ""),
        })
        return True, f"Approved ✓ #{draft_id}"
    return False, f"Not changed (already decided): #{draft_id}"


def _execute_reject(draft_id: int, reason: Optional[str], ctx: dict, db_path: Optional[str], now_dt: datetime.datetime) -> Tuple[bool, str]:
    guardrails = _get_guardrails()
    item = _get_approval_with_reason(draft_id, db_path)
    if not item:
        return False, f"Draft #{draft_id} kanipinchaledu (not found)."

    if item.get("status") == "rejected":
        current_reason = item.get("reason") or "rejected"
        return True, f"Draft #{draft_id} idi varake Rejected aindi (already rejected: {current_reason})"
    if item.get("status") == "approved":
        return False, f"Draft #{draft_id} idi varake Approved aindi (already approved, cannot reject)."

    # Standardize reason label
    canon_reason = CANONICAL_REASONS.get(reason.strip().lower(), reason.strip()) if reason else None

    changed = guardrails.decide(draft_id, False, db_path, reason=canon_reason)
    if changed:
        if canon_reason and canon_reason in guardrails.REJECT_REASONS:
            guardrails.set_reason(draft_id, canon_reason, db_path)
        _append_audit_log(ctx, {
            "ts": now_dt.isoformat(),
            "draft_id": draft_id,
            "action": "rejected",
            "reason": canon_reason or "",
            "chat_id": str(ctx.get("chat_id") or ""),
        })
        msg = f"Rejected: #{draft_id}"
        if canon_reason:
            msg += f" ({canon_reason})"
        return True, msg
    return False, f"Not changed (already decided): #{draft_id}"


def _show_draft(draft: dict, ctx: dict, db_path: Optional[str], now_dt: datetime.datetime, prefix_msg: str = "") -> None:
    draft_id = draft["id"]
    session = {
        "current_draft_id": draft_id,
        "last_action_ts": now_dt.isoformat(),
    }
    _save_json_state(ctx, KEY_ACTIVE_SESSION, session)
    _clear_state_key(ctx, KEY_REJECTING_DRAFT)

    card = _get_draft_card(draft, db_path=db_path)
    text = f"{prefix_msg}\n\n{card}".strip()
    keyboard = _build_decide_keyboard(draft_id)
    _send(ctx, text, keyboard=keyboard)


def _show_next_draft(ctx: dict, db_path: Optional[str], now_dt: datetime.datetime, prefix_msg: str = "", skip_draft_id: Optional[int] = None) -> None:
    pending = _get_pending_drafts(db_path)
    if skip_draft_id is not None:
        pending = [d for d in pending if d["id"] != skip_draft_id]

    if not pending:
        _clear_state_key(ctx, KEY_ACTIVE_SESSION)
        _clear_state_key(ctx, KEY_REJECTING_DRAFT)
        done_text = f"{prefix_msg}\n\nDrafts anni aypoyayi! Waiting queue empty ga undi. ✅".strip()
        _send(ctx, done_text, keyboard=_build_remove_keyboard())
        return

    _show_draft(pending[0], ctx, db_path, now_dt, prefix_msg=prefix_msg)


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Handle incoming message if targeted at keyboard decision plugin."""
    if not isinstance(text, str):
        return None
    raw_text = text.strip()
    if not raw_text:
        return None

    # Owner check
    if not _is_owner(ctx):
        # Ignore non-owner interactions silently
        return None

    db_path = ctx.get("db_path")
    now_dt = _get_now_dt(ctx)

    # First check idle timeout
    _check_session_expired(ctx, now_dt)

    lower = raw_text.lower()

    # Match /decide or trigger words
    is_decide_start = (
        lower == "/decide"
        or lower.startswith("/decide ")
        or lower in ("decide", "nirnayinchi", "drafts decide", "decide drafts")
    )

    if is_decide_start:
        pending = _get_pending_drafts(db_path)
        if not pending:
            _clear_state_key(ctx, KEY_ACTIVE_SESSION)
            _clear_state_key(ctx, KEY_REJECTING_DRAFT)
            msg = "Waiting drafts em levu. No drafts waiting. ✅"
            _send(ctx, msg, keyboard=_build_remove_keyboard())
            return {"handled": True}

        # Show oldest waiting draft
        _show_draft(pending[0], ctx, db_path, now_dt, prefix_msg="Drafts decision mode on:")
        return {"handled": True}

    # Match '🛑 Stop' or stop commands
    if raw_text == "🛑 Stop" or lower in ("stop decide", "/stop", "decide stop"):
        _clear_state_key(ctx, KEY_ACTIVE_SESSION)
        _clear_state_key(ctx, KEY_REJECTING_DRAFT)
        _send(ctx, "Decide session aagipoindi. Keyboard removed. 🛑", keyboard=_build_remove_keyboard())
        return {"handled": True}

    # Match reply keyboard buttons first (even if previously in rejecting state)
    # Check if user taps an action button like '❌ <id>', '✅ <id>', '👁 <id>', '⏭ Next'
    m_approve_btn = re.match(r"^✅\s*(\d+)$", raw_text)
    if m_approve_btn:
        d_id = int(m_approve_btn.group(1))
        _clear_state_key(ctx, KEY_REJECTING_DRAFT)
        ok, app_msg = _execute_approve(d_id, ctx, db_path, now_dt)
        _show_next_draft(ctx, db_path, now_dt, prefix_msg=app_msg, skip_draft_id=d_id)
        return {"handled": True}

    m_reject_btn = re.match(r"^❌\s*(\d+)$", raw_text)
    if m_reject_btn:
        d_id = int(m_reject_btn.group(1))
        # Ask reject reason with keyboard
        _save_json_state(ctx, KEY_REJECTING_DRAFT, {
            "draft_id": d_id,
            "ts": now_dt.isoformat(),
        })
        # update session last activity
        session = _load_json_state(ctx, KEY_ACTIVE_SESSION, default={})
        session["last_action_ts"] = now_dt.isoformat()
        _save_json_state(ctx, KEY_ACTIVE_SESSION, session)

        reject_kb = _build_reject_keyboard(d_id)
        _send(
            ctx,
            f"Draft #{d_id} ni reject chestunnaru. Reason emiti? Reason select cheyandi:",
            keyboard=reject_kb,
        )
        return {"handled": True}

    m_view_btn = re.match(r"^👁\s*(\d+)$", raw_text)
    if m_view_btn:
        d_id = int(m_view_btn.group(1))
        v_text = _get_view_text(d_id, db_path)
        # Reshow view text and keep keyboard
        session = _load_json_state(ctx, KEY_ACTIVE_SESSION, default={})
        session["last_action_ts"] = now_dt.isoformat()
        _save_json_state(ctx, KEY_ACTIVE_SESSION, session)
        _send(ctx, v_text, keyboard=_build_decide_keyboard(d_id))
        return {"handled": True}

    if raw_text == "⏭ Next" or lower in ("next", "tarwatha"):
        session = _load_json_state(ctx, KEY_ACTIVE_SESSION, default=None)
        curr_id = session.get("current_draft_id") if session else None
        pending = _get_pending_drafts(db_path)
        if not pending:
            _clear_state_key(ctx, KEY_ACTIVE_SESSION)
            _send(ctx, "Waiting drafts em levu. ✅", keyboard=_build_remove_keyboard())
            return {"handled": True}

        # Find next draft in pending list after curr_id
        next_draft = None
        if curr_id is not None:
            for idx, d in enumerate(pending):
                if d["id"] == curr_id:
                    if idx + 1 < len(pending):
                        next_draft = pending[idx + 1]
                    else:
                        next_draft = pending[0]  # cycle back to start
                    break
        if not next_draft:
            next_draft = pending[0]

        _show_draft(next_draft, ctx, db_path, now_dt, prefix_msg="Next draft:")
        return {"handled": True}

    # Check if we are waiting for a reject reason for a specific draft
    rejecting_state = _load_json_state(ctx, KEY_REJECTING_DRAFT, default=None)
    if rejecting_state:
        r_draft_id = rejecting_state.get("draft_id")
        # Check cancel
        if raw_text.startswith("↩️ Cancel") or lower in ("cancel", "raddu", "back"):
            _clear_state_key(ctx, KEY_REJECTING_DRAFT)
            item = _get_approval_with_reason(r_draft_id, db_path)
            if item and item.get("status") == "pending":
                _show_draft(item, ctx, db_path, now_dt, prefix_msg="Reject cancel aindi.")
            else:
                _show_next_draft(ctx, db_path, now_dt, prefix_msg="Reject cancel aindi.")
            return {"handled": True}

        # Check if text matches one of the reject reasons or owner typed reason
        matched_reason = None
        for reason_opt in REJECT_REASON_OPTIONS:
            if lower == reason_opt.lower():
                matched_reason = reason_opt
                break
        if not matched_reason:
            # Check guardrails.REJECT_REASONS
            guardrails = _get_guardrails()
            for r in guardrails.REJECT_REASONS:
                if lower == r.lower():
                    matched_reason = CANONICAL_REASONS.get(r.lower(), r)
                    break
        if not matched_reason and len(raw_text) <= 50 and not raw_text.startswith("/"):
            # Custom reason provided by owner
            matched_reason = raw_text

        if matched_reason:
            ok, reject_msg = _execute_reject(r_draft_id, matched_reason, ctx, db_path, now_dt)
            _clear_state_key(ctx, KEY_REJECTING_DRAFT)
            _show_next_draft(ctx, db_path, now_dt, prefix_msg=reject_msg, skip_draft_id=r_draft_id)
            return {"handled": True}

    # Match plain text shortcuts:
    # 'approve 91', '91 ok', '91 vaddu', 'reject 91', '91 reject', etc.
    # Pattern 1: approve <id> / ok <id> / aamodinchu <id>
    m_plain_app = re.match(r"^(?:approve|ok|aamodinchu)\s+(\d+)$", lower)
    if not m_plain_app:
        # Pattern 2: <id> ok / <id> approve / <id> aamodinchu
        m_plain_app = re.match(r"^(\d+)\s+(?:ok|approve|aamodinchu)$", lower)

    if m_plain_app:
        d_id = int(m_plain_app.group(1))
        ok, app_msg = _execute_approve(d_id, ctx, db_path, now_dt)
        # If in an active session, advance to next draft with keyboard
        session = _load_json_state(ctx, KEY_ACTIVE_SESSION, default=None)
        if session:
            _show_next_draft(ctx, db_path, now_dt, prefix_msg=app_msg, skip_draft_id=d_id)
        else:
            _send(ctx, app_msg)
        return {"handled": True}

    # Pattern 3: reject <id> [reason] / vaddu <id>
    m_plain_rej = re.match(r"^(?:reject|vaddu|tirooskarinchu)\s+(\d+)(?:\s+(.*))?$", lower)
    if not m_plain_rej:
        # Pattern 4: <id> vaddu / <id> reject [reason]
        m_plain_rej = re.match(r"^(\d+)\s+(?:reject|vaddu|tirooskarinchu)(?:\s+(.*))?$", lower)

    if m_plain_rej:
        d_id = int(m_plain_rej.group(1))
        reason = m_plain_rej.group(2)
        if reason:
            reason = reason.strip()
        if not reason:
            # Enter reason selection mode
            _save_json_state(ctx, KEY_REJECTING_DRAFT, {
                "draft_id": d_id,
                "ts": now_dt.isoformat(),
            })
            reject_kb = _build_reject_keyboard(d_id)
            _send(
                ctx,
                f"Draft #{d_id} ni reject chestunnaru. Reason emiti? Reason select cheyandi:",
                keyboard=reject_kb,
            )
            return {"handled": True}
        else:
            ok, rej_msg = _execute_reject(d_id, reason, ctx, db_path, now_dt)
            session = _load_json_state(ctx, KEY_ACTIVE_SESSION, default=None)
            if session:
                _show_next_draft(ctx, db_path, now_dt, prefix_msg=rej_msg, skip_draft_id=d_id)
            else:
                _send(ctx, rej_msg)
            return {"handled": True}

    # Not for this plugin
    return None
