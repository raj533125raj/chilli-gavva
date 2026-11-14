"""Telegram plugin for database care: /dbcare (owner only).

Shows the DB health card with [Clean now] [Check copy] buttons.  The buttons
are reply-keyboard texts, so the owner's next message is handled here too.
Never calls the Telegram API directly and never imports telegram_inbox.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

import db_maintenance

COMMANDS = [
    {
        "cmd": "/dbcare",
        "words": ["dbcare", "db care", "database health", "db health", "db check"],
        "help_en": "Show DB health, old rows plan, size, schema version and index advice. Buttons: Clean now / Check copy.",
        "help_te": "DB health, clean plan, size, schema version mariyu index advice chupisthundi. Buttons: Clean now / Check copy.",
    },
]

_CLEAN_WORDS = {
    "/dbcare clean", "/dbcare cleanup", "clean now", "db clean", "dbcare clean",
    "clean chey", "cleanup chey",
}
_COPY_WORDS = {
    "/dbcare copy", "/dbcare check-copy", "check copy", "copy check", "db copy check",
    "safe copy check",
}

_CMD_RE = re.compile(r"^/dbcare(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _matches(value: str, phrases) -> bool:
    return any(value == phrase or value.startswith(phrase + " ") for phrase in phrases)


def parse_action(text: str) -> Optional[str]:
    """Return 'report', 'clean' or 'copy' when the text belongs to this plugin."""
    value = _normalise(text)
    if not value:
        return None
    if _matches(value, _CLEAN_WORDS):
        return "clean"
    if _matches(value, _COPY_WORDS):
        return "copy"
    match = _CMD_RE.match((text or "").strip())
    if match:
        arg = _normalise(match.group(1) or "")
        if _matches(arg, ("clean", "cleanup")):
            return "clean"
        if _matches(arg, ("copy", "check copy", "check-copy")):
            return "copy"
        if _matches(arg, ("", "report", "status", "health")):
            return "report"
        return None
    if _matches(value, ("dbcare", "db care", "database health", "db health", "db check")):
        return "report"
    return None


def _is_owner_chat(ctx: Dict[str, Any]) -> bool:
    """Follow tg_keyboard: when configured, only the owner chat may use /dbcare."""
    try:
        import notify
        configured, _, owner_chat_id = notify._telegram_configured()
        if configured and owner_chat_id:
            return str(owner_chat_id) == str(ctx.get("chat_id"))
    except Exception:
        return False
    return True


def handle(text: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    action = parse_action(text)
    if action is None:
        return None
    if not _is_owner_chat(ctx):
        return None

    db_path = ctx.get("db_path") or db_maintenance.DB_PATH
    try:
        if action == "clean":
            result = db_maintenance.run_cleanup(db_path)
            return {"text": db_maintenance.format_clean_result(result)}
        if action == "copy":
            result = db_maintenance.safe_check_copy(db_path)
            return {"text": db_maintenance.format_copy_result(result)}

        report = db_maintenance.build_report(db_path, now=ctx.get("now"))
        return {
            "text": db_maintenance.format_report(report),
            "keyboard": [["Clean now", "Check copy"]],
        }
    except FileNotFoundError:
        return {"text": "Database file dorakaledu. Cloud run taruvata malli try cheyandi."}
    except Exception as exc:  # noqa: BLE001 - owner gets a clear message, never a plugin crash
        return {"text": f"DB care error: {exc}"}
