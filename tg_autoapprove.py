"""tg_autoapprove.py — Telegram plugin for Safe Auto-Approve settings (BINNY-V3C).

Provides owner commands for managing auto-approve behavior:
  - /autoapprove (or 'autoapprove'): displays current status, threshold, daily limit, today's count
  - 'autoapprove on': enables auto-approval (requires Yes/No confirmation)
  - 'autoapprove off': disables auto-approval (requires Yes/No confirmation)
  - 'autoapprove <score>': updates score threshold e.g. 'autoapprove 80' (requires Yes/No confirmation)
  - 'autoapprove limit <n>': updates daily auto-approval limit e.g. 'autoapprove limit 1' (requires Yes/No confirmation)

Confirmation flow:
  When a setting change command is issued, tg_autoapprove stages the change in state ('auto_approve_pending_confirm')
  and prompts the owner with a Yes/No confirmation question and keyboard.
  If the owner responds with 'Yes', 'avunu', 'sare', or confirms, the change is applied to 'auto_approve_settings'.
  If 'No', 'vaddu', or 'kaadu', the change is cancelled.
  Confirmations expire after 10 minutes.

Strict rules:
  - Never call Telegram API directly (uses ctx callbacks).
  - Never import telegram_inbox at module import time.
  - Strictly owner-facing responses only, written in Tenglish house style.
  - No money/currency amounts anywhere.
"""
import datetime
import json
import re
from typing import Any, Dict, List, Optional

import auto_approve

COMMANDS = [
    {
        "cmd": "/autoapprove",
        "words": [
            "autoapprove",
            "auto approve",
            "autoapprove status",
            "auto approve status",
            "autoapprove settings",
        ],
        "help_en": "View or configure safe auto-approval settings (threshold, limit, on/off)",
        "help_te": "Auto-approve settings choodandi leda on/off/threshold marchandi",
    }
]

STATE_PENDING_KEY = "auto_approve_pending_confirm"
CONFIRM_EXPIRY_SECONDS = 600  # 10 minutes

CONFIRM_YES_WORDS = {"yes", "avunu", "sare", "ok", "confirm", "✅ yes", "ha"}
CONFIRM_NO_WORDS = {"no", "vaddu", "kaadu", "cancel", "stop", "❌ no", "oddu"}


def _get_utc_now(ctx: Dict) -> datetime.datetime:
    now = ctx.get("now")
    if isinstance(now, datetime.datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    elif isinstance(now, (int, float)):
        return datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def _load_settings(ctx: Dict) -> Dict:
    db_path = ctx.get("db_path")
    raw = ctx.get("state_get", lambda k, d=None: None)(auto_approve.STATE_SETTINGS_KEY, None)
    if raw:
        try:
            data = json.loads(raw)
            return {
                "enabled": bool(data.get("enabled", True)),
                "threshold": int(data.get("threshold", auto_approve.DEFAULT_SCORE_THRESHOLD)),
                "daily_limit": int(data.get("daily_limit", auto_approve.DEFAULT_DAILY_LIMIT)),
            }
        except Exception:
            pass
    return auto_approve.get_settings(db_path, default_threshold=auto_approve.DEFAULT_SCORE_THRESHOLD)


def _save_settings(ctx: Dict, settings: Dict):
    db_path = ctx.get("db_path")
    clean = {
        "enabled": bool(settings.get("enabled", True)),
        "threshold": max(0, min(100, int(settings.get("threshold", auto_approve.DEFAULT_SCORE_THRESHOLD)))),
        "daily_limit": max(0, int(settings.get("daily_limit", auto_approve.DEFAULT_DAILY_LIMIT))),
    }
    json_str = json.dumps(clean, sort_keys=True)
    state_set = ctx.get("state_set")
    if callable(state_set):
        state_set(auto_approve.STATE_SETTINGS_KEY, json_str)
    auto_approve.save_settings(clean, db_path=db_path)


def _format_status_text(ctx: Dict) -> str:
    db_path = ctx.get("db_path")
    settings = _load_settings(ctx)
    now = _get_utc_now(ctx)
    approved_today = auto_approve.count_auto_approvals_today(db_path=db_path, now=now)
    broken_checker = auto_approve.has_broken_checker_signal(db_path=db_path)

    status_str = "ON (nadustondi)" if settings["enabled"] else "OFF (aapesam)"
    checker_alert = "\n⚠️ Warning: Last 5 drafts had identical scores (broken checker signal active, approvals paused)!" if broken_checker else ""

    lines = [
        "⚙️ *Auto-Approve Settings (BINNY-V3C)*",
        f"• Status: {status_str}",
        f"• Score Threshold: {settings['threshold']}/100",
        f"• Daily Limit: {settings['daily_limit']} drafts per day",
        f"• Today Auto-Approved: {approved_today}/{settings['daily_limit']}",
        checker_alert,
        "",
        "Commands:",
        "• `autoapprove on` — Auto-approval on cheyandi",
        "• `autoapprove off` — Auto-approval aapandi",
        "• `autoapprove <score>` — Threshold marchandi (e.g. `autoapprove 80`)",
        "• `autoapprove limit <n>` — Daily limit marchandi (e.g. `autoapprove limit 1`)",
    ]
    return "\n".join(l for l in lines if l is not None).strip()


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Handle autoapprove commands and Yes/No confirmation flows."""
    if not text or not isinstance(text, str):
        return None

    cleaned = text.strip()
    lower = cleaned.lower()

    send_text = ctx.get("send_text")
    state_get = ctx.get("state_get", lambda k, d=None: None)
    state_set = ctx.get("state_set", lambda k, v: None)
    now_dt = _get_utc_now(ctx)

    # 1. Check if there is an active pending confirmation
    pending_raw = state_get(STATE_PENDING_KEY, None)
    if pending_raw:
        try:
            pending_data = json.loads(pending_raw)
            pending_time = datetime.datetime.fromisoformat(pending_data.get("ts", ""))
            if (now_dt - pending_time).total_seconds() <= CONFIRM_EXPIRY_SECONDS:
                # We have a valid pending confirmation waiting for user response
                words = lower.split()
                first_word = words[0] if words else ""
                combined_two = " ".join(words[:2]) if len(words) >= 2 else ""

                if lower in CONFIRM_YES_WORDS or first_word in CONFIRM_YES_WORDS or combined_two in CONFIRM_YES_WORDS:
                    # Confirmed! Apply proposed settings
                    new_settings = pending_data["proposed_settings"]
                    _save_settings(ctx, new_settings)
                    state_set(STATE_PENDING_KEY, "")

                    summary = pending_data.get("summary", "Settings updated")
                    reply = f"✅ Sare, auto-approve settings marcham:\n{summary}."
                    if callable(send_text):
                        send_text(reply, keyboard=None)
                        return {"handled": True}
                    return {"text": reply}

                if lower in CONFIRM_NO_WORDS or first_word in CONFIRM_NO_WORDS or combined_two in CONFIRM_NO_WORDS:
                    # Cancelled
                    state_set(STATE_PENDING_KEY, "")
                    reply = "❌ Sare, auto-approve setting change cancel chesam. Old settings alage unnai."
                    if callable(send_text):
                        send_text(reply, keyboard=None)
                        return {"handled": True}
                    return {"text": reply}
            else:
                # Expired confirmation
                state_set(STATE_PENDING_KEY, "")
        except Exception:
            state_set(STATE_PENDING_KEY, "")

    # 2. Check for commands intended for this plugin
    # /autoapprove or autoapprove alone -> show status
    if lower in ("/autoapprove", "autoapprove", "auto approve", "/autoapprove status", "autoapprove status"):
        status_text = _format_status_text(ctx)
        if callable(send_text):
            send_text(status_text)
            return {"handled": True}
        return {"text": status_text}

    # Match setting changes:
    # 'autoapprove off', 'autoapprove on'
    # 'autoapprove 80' (or other integer score 0-100)
    # 'autoapprove limit 1' (or other integer limit)
    current_settings = _load_settings(ctx)
    proposed_settings = dict(current_settings)
    summary_text = ""

    # Check pattern: autoapprove off / /autoapprove off
    m_off = re.match(r"^/?auto\s*approve\s+off$", lower)
    if m_off:
        if not current_settings["enabled"]:
            msg = "Auto-approve already OFF lo undi (already disabled)."
            if callable(send_text):
                send_text(msg)
                return {"handled": True}
            return {"text": msg}
        proposed_settings["enabled"] = False
        summary_text = "Auto-approve: OFF"

    # Check pattern: autoapprove on / /autoapprove on
    m_on = re.match(r"^/?auto\s*approve\s+on$", lower)
    if m_on:
        if current_settings["enabled"]:
            msg = f"Auto-approve already ON lo undi (enabled, threshold: {current_settings['threshold']})."
            if callable(send_text):
                send_text(msg)
                return {"handled": True}
            return {"text": msg}
        proposed_settings["enabled"] = True
        summary_text = f"Auto-approve: ON (threshold {current_settings['threshold']})"

    # Check pattern: autoapprove limit <N>
    m_limit = re.match(r"^/?auto\s*approve\s+limit\s+(\d+)$", lower)
    if m_limit:
        new_lim = int(m_limit.group(1))
        proposed_settings["daily_limit"] = new_lim
        summary_text = f"Daily limit: {new_lim} drafts per day"

    # Check pattern: autoapprove <threshold> (e.g. autoapprove 80)
    m_thresh = re.match(r"^/?auto\s*approve\s+(\d+)$", lower)
    if m_thresh:
        val = int(m_thresh.group(1))
        if 0 <= val <= 100:
            proposed_settings["threshold"] = val
            summary_text = f"Score threshold: {val}/100"
        else:
            msg = "Threshold 0 nundi 100 madhyalo undali (score must be between 0 and 100)."
            if callable(send_text):
                send_text(msg)
                return {"handled": True}
            return {"text": msg}

    if not summary_text:
        # Not a command for this plugin
        return None

    # Stage pending confirmation
    pending_payload = {
        "ts": now_dt.isoformat(),
        "proposed_settings": proposed_settings,
        "summary": summary_text,
    }
    state_set(STATE_PENDING_KEY, json.dumps(pending_payload))

    confirm_msg = (
        f"Confirm cheyandi: {summary_text} apply cheyamantara?\n\n"
        "Tap Yes to apply leda No to cancel."
    )
    keyboard = [["Yes", "No"]]

    if callable(send_text):
        send_text(confirm_msg, keyboard=keyboard)
        return {"handled": True}
    return {"text": confirm_msg, "keyboard": keyboard}
