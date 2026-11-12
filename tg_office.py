"""tg_office.py — Telegram plugin for controlling Binny Mills Office from phone (BINNY-W1B).

Allows owner full control of the office layout and agent appearances:
  - Command `/office`: shows an interactive menu with:
      * Current rooms list
      * Recent changes (last 5)
      * Undo last action option
  - Natural English & Tenglish phrases:
      * "office lo Pinterest room pedda chey" (resize room)
      * "QA room ni Products pakkana pettu" (move room next to)
      * "Pin Designer ki red cap pettu" (agent cap/styling)
      * "kotha room Research add chey" (add room)
      * "Marketing room remove chey" (remove room)
      * "Research room peru Discovery ga marchu" (rename room)
      * "office change undo" (undo last change)
  - Responds with confirmation, preview line, and keyboard `[["↩️ Undo", "🏢 Office menu"]]`.
  - When an office-like phrase is ambiguous or unknown, offers helpful Tenglish guidance.
  - Owner-only: only responds to owner chat.
  - No money amounts, no secrets, stdlib + Pillow only.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import cubes
import guardrails
import office_requests

COMMANDS = [
    {
        "cmd": "/office",
        "words": ["office", "office menu", "office lo", "office choodu", "office status"],
        "category": "Settings",
        "help_en": "View Binny Mills office layout, rooms, recent changes, or undo",
        "help_te": "Binny Mills office rooms list, recent changes, mariyu undo menu choodandi",
    }
]

KEYBOARD_OFFICE = [
    ["↩️ Undo", "🏢 Office menu"]
]


def _is_owner(ctx: dict) -> bool:
    chat_id = str(ctx.get("chat_id") or "")
    if not chat_id:
        return True
    owner_id = str(os.environ.get("TELEGRAM_CHAT_ID") or "")
    if owner_id:
        return chat_id == owner_id
    try:
        import notify
        configured, _, notify_chat = notify._telegram_configured()
        if configured and notify_chat:
            return str(notify_chat) == chat_id
    except Exception:
        pass
    return True


def _format_rooms_list() -> str:
    """Format known rooms list grouped by department/cube."""
    try:
        all_cubes = cubes.list_cubes()
    except Exception:
        all_cubes = []

    lines = []
    for c in all_cubes:
        c_name = c.get("name", "Department")
        sub_rooms = c.get("sub_rooms") or []
        if sub_rooms:
            rooms_str = ", ".join(sub_rooms)
            lines.append(f"• *{c_name}*: {rooms_str}")
        else:
            lines.append(f"• *{c_name}*")

    if not lines:
        return "Rooms list labhyam ga ledu."
    return "\n".join(lines)


def _format_recent_changes(db_path: Optional[str] = None, limit: int = 5) -> str:
    """Format recent changes from the office_requests database."""
    reqs = office_requests.list_office_requests(db_path=db_path, limit=limit)
    if not reqs:
        return "Recent changes em levu (queue empty)."

    lines = []
    for r in reqs:
        label = r.get("label_te") or f"{r.get('kind')}: {r.get('target')}"
        status = r.get("status", "queued")
        st_icon = "⏳" if status == "queued" else ("✅" if status == "applied" else "❌")
        lines.append(f"{st_icon} {label} ({status})")
    return "\n".join(lines)


def _office_menu_text(db_path: Optional[str] = None) -> str:
    """Build full /office menu text."""
    rooms_text = _format_rooms_list()
    recent_text = _format_recent_changes(db_path=db_path, limit=5)

    return (
        "🏢 *Binny Mills Office Menu*\n\n"
        "🏠 *Current Rooms:*\n"
        f"{rooms_text}\n\n"
        "🕒 *Recent Changes:*\n"
        f"{recent_text}\n\n"
        "💡 *Phrases meeru cheppavachhu:*\n"
        "• `office lo Pinterest room pedda chey` (resize)\n"
        "• `QA room ni Products pakkana pettu` (move)\n"
        "• `kotha room Research add chey` (add)\n"
        "• `Marketing room remove chey` (remove)\n"
        "• `Pin Designer ki red cap pettu` (agent cap)\n"
        "• `Doctor ki blue theme pettu` (agent theme)\n"
        "• `CEO ki star icon pettu` (agent icon)\n"
        "• `office change undo` (undo last change)"
    )


def _format_confirmation_reply(req: Dict[str, Any]) -> str:
    """Format confirmation response with preview line."""
    label_te = req.get("label_te") or f"{req.get('kind')}: {req.get('target')}"
    kind = req.get("kind", "")
    target = req.get("target", "")
    val = req.get("value")
    val_repr = f" → {val}" if val else ""

    preview = f"Preview: [{kind}] {target}{val_repr}"

    if kind == "undo":
        return (
            f"✅ *Office lo marcham!*\n"
            f"{label_te}\n\n"
            f"_{preview}_\n"
            f"(Binny Mills app lo change undo aindi)"
        )

    return (
        f"✅ *Office request queue lo pettam!*\n"
        f"{label_te}\n\n"
        f"_{preview}_\n"
        f"(Binny Mills app lo auto ga apply avutundi)"
    )


def _format_unknown_office_hint(text: str) -> str:
    """Helpful hint when text looks like an office command but cannot be parsed."""
    return (
        f"Office command sarigga ardam kaaledu: '{text[:60]}'.\n\n"
        "Ila try cheyandi:\n"
        "• `office lo <Room> pedda/chinna chey`\n"
        "• `<Room A> ni <Room B> pakkana pettu`\n"
        "• `<Agent> ki <color> cap pettu`\n"
        "• `kotha room <Name> add chey`\n"
        "• `office change undo`\n"
        "Leda `/office` ani type chesi menu choodandi."
    )


def handle(text: str, ctx: dict) -> dict | None:
    """Plugin entry point."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None

    # Safety: Owner-only check
    if not _is_owner(ctx):
        return None

    lower = raw.lower()
    db_path = ctx.get("db_path")
    send_text = ctx.get("send_text")

    # 1. /office command or menu word
    if lower in ("/office", "office", "office menu", "office status", "🏢 office menu"):
        reply_text = _office_menu_text(db_path=db_path)
        if callable(send_text):
            send_text(reply_text, keyboard=KEYBOARD_OFFICE)
            return {"handled": True}
        return {"text": reply_text, "keyboard": KEYBOARD_OFFICE}

    # 2. Undo shortcut from keyboard button or plain text
    if lower in ("↩️ undo", "undo", "office change undo", "office lo undo", "undo last"):
        undone = office_requests.undo_last_request(db_path=db_path)
        if undone:
            target_name = undone.get("target") or "last change"
            reply_text = (
                f"↩️ *Last office change undo chesam!*\n"
                f"Mundu chesina '{target_name}' request cancel aindi.\n\n"
                f"_Preview: [undo] {undone.get('id')}_\n"
                f"(Binny Mills app lo rollback apply avutundi)"
            )
        else:
            reply_text = "Undo cheyadaniki recent changes em levu (queue empty)."

        if callable(send_text):
            send_text(reply_text, keyboard=KEYBOARD_OFFICE)
            return {"handled": True}
        return {"text": reply_text, "keyboard": KEYBOARD_OFFICE}

    # 3. Parse natural language office phrase
    parsed = office_requests.parse_phrase(raw)
    if parsed is not None:
        # If it parsed as undo, handle undo specifically
        if parsed.get("kind") == "undo":
            undone = office_requests.undo_last_request(db_path=db_path)
            if undone:
                target_name = undone.get("target") or "last change"
                reply_text = (
                    f"↩️ *Last office change undo chesam!*\n"
                    f"Mundu chesina '{target_name}' request cancel aindi.\n\n"
                    f"_Preview: [undo] {undone.get('id')}_\n"
                    f"(Binny Mills app lo rollback apply avutundi)"
                )
            else:
                reply_text = "Undo cheyadaniki recent changes em levu (queue empty)."
        else:
            # Store queued request
            saved = office_requests.store_request(
                kind=parsed["kind"],
                target=parsed.get("target"),
                value=parsed.get("value"),
                label_te=parsed.get("label_te"),
                db_path=db_path,
                status="queued",
                now_dt=ctx.get("now"),
            )
            reply_text = _format_confirmation_reply(saved)

        if callable(send_text):
            send_text(reply_text, keyboard=KEYBOARD_OFFICE)
            return {"handled": True}
        return {"text": reply_text, "keyboard": KEYBOARD_OFFICE}

    # 4. If phrase explicitly targets office or rooms or agent caps, but parser didn't match:
    # provide a helpful hint rather than letting it fall into generic unknown fallback.
    if re.search(r"\b(office|room|cap|icon)\b", lower) and re.search(r"\b(pettu|chey|marchu|add|remove|delete|resize|move|theme|color)\b", lower):
        hint = _format_unknown_office_hint(raw)
        if callable(send_text):
            send_text(hint, keyboard=KEYBOARD_OFFICE)
            return {"handled": True}
        return {"text": hint, "keyboard": KEYBOARD_OFFICE}

    return None
