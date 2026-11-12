"""tg_health.py — Telegram plugin for /health, /logs, and /undo (BINNY-W4A).

Commands provided:
  - /health: runs quick self-check, answers single card: each part green/amber/red
             (DB, last run time, door bell, Telegram read errors, AI quota left today
             per provider, GitHub minutes if known, packs READY/NEEDS FIX counts,
             disk size of products, plugin load errors, auto-fix state)
             with one Tenglish fix hint per red line.
  - /logs: shows the last run summary (safe lines only, secret_guard scrub).
  - /undo: undoes the owner's last Telegram action when safe (approve/reject within 10 minutes,
           settings change, office request) with a confirm keyboard.

Strict rules:
  - Standard Telegram plugin interface: COMMANDS list and handle(text, ctx) -> dict | None.
  - Never call Telegram API directly (uses ctx callbacks).
  - Never import telegram_inbox at module import time.
  - Owner-only: only responds to owner chat.
  - Tenglish house style messages.
  - No secrets, no money or currency mentions.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

import health_check

COMMANDS = [
    {
        "cmd": "/health",
        "words": ["health", "health check", "system health", "arogyam", "health card"],
        "help_en": "Run quick self-check of all system parts with green/amber/red status and Tenglish fix hints.",
        "help_te": "System parts self-check chesi green/amber/red status mariyu red items ki fix hints chupisthundi.",
    },
    {
        "cmd": "/logs",
        "words": ["logs", "last run logs", "run logs", "chivara logs", "log chupinchu", "run summary"],
        "help_en": "Show the last robot run summary (safe lines only, scrubbed for secrets).",
        "help_te": "Chivara run summary mariyu safe log lines chupisthundi (secrets scrubbed).",
    },
    {
        "cmd": "/undo",
        "words": ["undo", "undo action", "venakki teesko", "undo chey", "revert last"],
        "help_en": "Undo the last owner Telegram action (approval/rejection within 10 min, settings, office request).",
        "help_te": "Owner chivara chesina action ni 10 min lopu undo cheyadaniki confirm keyboard tho sahayam chesthundi.",
    },
]

CONFIRM_EXPIRY_SECONDS = 600  # 10 minutes
UNDO_PENDING_KEY = "owner_undo_pending_confirm"

CONFIRM_YES_WORDS = {"yes", "avunu", "sare", "ok", "confirm", "✅ yes", "ha", "undo chey"}
CONFIRM_NO_WORDS = {"no", "vaddu", "kaadu", "cancel", "stop", "❌ no", "oddu"}


def _get_utc_now(ctx: Dict[str, Any]) -> datetime.datetime:
    now = ctx.get("now")
    if isinstance(now, datetime.datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    elif isinstance(now, (int, float)):
        return datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def handle_health(text: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Execute 10-part system health check and return formatted card."""
    db_path = ctx.get("db_path")
    now_dt = _get_utc_now(ctx)
    send_text = ctx.get("send_text")

    card = health_check.get_health_summary(
        db_path=db_path,
        now=now_dt,
    )

    if callable(send_text):
        send_text(card)
        return {"handled": True}
    return {"text": card}


def handle_logs(text: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and return safe, scrubbed summary lines from last run."""
    db_path = ctx.get("db_path")
    send_text = ctx.get("send_text")

    summary = health_check.get_last_run_summary(db_path=db_path)

    if callable(send_text):
        send_text(summary)
        return {"handled": True}
    return {"text": summary}


def handle_undo_request(text: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Stage an undo request and prompt owner with confirm keyboard."""
    db_path = ctx.get("db_path")
    now_dt = _get_utc_now(ctx)
    send_text = ctx.get("send_text")
    state_set = ctx.get("state_set", lambda k, v: None)

    record, err = health_check.get_last_undoable_action(db_path=db_path, now=now_dt)
    if err or not record:
        msg = f"❌ {err or 'Undoing kosam emi action dorakaledu.'}"
        if callable(send_text):
            send_text(msg)
            return {"handled": True}
        return {"text": msg}

    desc = health_check.describe_action_for_undo(record)
    pending_payload = {
        "ts": now_dt.isoformat(),
        "record": record,
        "description": desc,
    }

    if callable(state_set):
        state_set(UNDO_PENDING_KEY, json.dumps(pending_payload))

    prompt = (
        f"⚠️ *Undo Confirmation*\n\n"
        f"Meeru chivara chesina action:\n👉 *{desc}*\n\n"
        f"Deenni nijamgane undo cheyamantara?\n"
        f"Tap *Yes* to revert leda *No* to cancel."
    )
    keyboard = [["Yes", "No"]]

    if callable(send_text):
        send_text(prompt, keyboard=keyboard)
        return {"handled": True}
    return {"text": prompt, "keyboard": keyboard}


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Telegram plugin entrypoint."""
    if not text or not isinstance(text, str):
        return None

    cleaned = text.strip()
    lower = cleaned.lower()

    send_text = ctx.get("send_text")
    state_get = ctx.get("state_get", lambda k, d=None: None)
    state_set = ctx.get("state_set", lambda k, v: None)
    db_path = ctx.get("db_path")
    now_dt = _get_utc_now(ctx)

    # 1. Check for active pending undo confirmation
    pending_raw = state_get(UNDO_PENDING_KEY, None)
    if pending_raw:
        try:
            pending_data = json.loads(pending_raw)
            p_ts_str = pending_data.get("ts", "")
            clean_ts = p_ts_str.replace("Z", "+00:00")
            pending_time = datetime.datetime.fromisoformat(clean_ts)
            if pending_time.tzinfo is None:
                pending_time = pending_time.replace(tzinfo=datetime.timezone.utc)

            if (now_dt - pending_time).total_seconds() <= CONFIRM_EXPIRY_SECONDS:
                words = lower.split()
                first_word = words[0] if words else ""
                combined_two = " ".join(words[:2]) if len(words) >= 2 else ""

                if lower in CONFIRM_YES_WORDS or first_word in CONFIRM_YES_WORDS or combined_two in CONFIRM_YES_WORDS:
                    # Confirmed! Revert the action
                    action_rec = pending_data.get("record", {})
                    ok, msg = health_check.apply_undo(action_rec, db_path=db_path)
                    state_set(UNDO_PENDING_KEY, "")

                    reply = f"✅ {msg}" if ok else f"❌ Undo fail ayindi: {msg}"
                    if callable(send_text):
                        send_text(reply, keyboard=None)
                        return {"handled": True}
                    return {"text": reply}

                if lower in CONFIRM_NO_WORDS or first_word in CONFIRM_NO_WORDS or combined_two in CONFIRM_NO_WORDS:
                    # Cancelled
                    state_set(UNDO_PENDING_KEY, "")
                    reply = "❌ Sare, undo cancel chesam. Action alage unchadam jarigindi."
                    if callable(send_text):
                        send_text(reply, keyboard=None)
                        return {"handled": True}
                    return {"text": reply}
            else:
                # Expired confirmation
                state_set(UNDO_PENDING_KEY, "")
        except Exception:
            state_set(UNDO_PENDING_KEY, "")

    # 2. Match commands for this plugin

    # /health or health-related phrases
    first_token = lower.split()[0] if lower.split() else ""
    if (
        first_token in ("/health", "health")
        or lower.startswith("health check")
        or lower.startswith("system health")
        or lower.startswith("arogyam")
        or lower.startswith("health card")
    ):
        return handle_health(cleaned, ctx)

    # /logs or logs-related phrases
    if (
        first_token in ("/logs", "logs")
        or lower.startswith("last run logs")
        or lower.startswith("run logs")
        or lower.startswith("chivara logs")
        or lower.startswith("log chupinchu")
        or lower.startswith("run summary")
    ):
        return handle_logs(cleaned, ctx)

    # /undo or undo-related phrases
    if (
        first_token in ("/undo", "undo")
        or lower.startswith("undo action")
        or lower.startswith("venakki teesko")
        or lower.startswith("undo chey")
        or lower.startswith("revert last")
    ):
        return handle_undo_request(cleaned, ctx)

    return None
