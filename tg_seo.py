"""Telegram plugin for /seokit N (owner only).

The command reads the real pack folder and sends the SEO report as a text
document plus a short summary.  It never calls the Telegram API directly and
never imports telegram_inbox.  /seo itself belongs to tg_reports.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import seo_helper

COMMANDS = [
    {
        "cmd": "/seokit",
        "words": ["seokit", "seo kit", "seo pack", "seo keywords"],
        "help_en": "Make an SEO kit for pack N: 40+ keywords, 30 A/B titles, 13 tags, meta, Pinterest and alt texts. Sends a text file.",
        "help_te": "Pack N ki SEO kit tayaru chestundi: 40+ keywords, 30 A/B titles, 13 tags, meta, Pinterest mariyu alt texts. Text file pampistundi.",
    },
]

_CMD_RE = re.compile(
    r"^(?:/seokit(?:@\w+)?|seokit|seo\s+kit|seo\s+pack|seo\s+keywords)"
    r"(?:\s+(?:for\s+)?(?:pack\s+)?(\d+))?\s*$",
    re.IGNORECASE,
)


def parse_pack_number(text: str) -> Optional[int]:
    """Return N for /seokit N (or 'seo kit N'), 0 when the command has no N, else None."""
    match = _CMD_RE.match((text or "").strip())
    if not match:
        return None
    raw = match.group(1)
    if raw is None:
        return 0
    try:
        number = int(raw)
    except ValueError:
        return None
    return number if number > 0 else 0


def _is_owner_chat(ctx: Dict[str, Any]) -> bool:
    try:
        import notify
        configured, _, owner_chat_id = notify._telegram_configured()
        if configured and owner_chat_id:
            return str(owner_chat_id) == str(ctx.get("chat_id"))
    except Exception:
        pass
    return True


def short_summary(output: Dict[str, Any]) -> str:
    return output["summary"]


def document_filename(pack_number: int) -> str:
    return f"seo_pack_{int(pack_number)}.txt"


def _products_dir(ctx: Dict[str, Any]) -> Optional[str]:
    return ctx.get("products_dir") or os.environ.get("CHILLI_PRODUCTS_DIR")


def handle(text: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pack_number = parse_pack_number(text)
    if pack_number is None:
        return None
    if not _is_owner_chat(ctx):
        return None
    if pack_number <= 0:
        return {"text": "Pack number cheppandi (example: /seokit 3)."}

    try:
        output = seo_helper.build_for_pack(pack_number, _products_dir(ctx))
    except seo_helper.PackNotFoundError:
        return {"text": f"Pack {pack_number} folder dorakaledu. /packs tho ready packs chudandi."}
    except Exception as exc:  # noqa: BLE001 - owner gets a clear message
        return {"text": f"SEO kit error: {exc}"}

    document = seo_helper.build_seo_document(output)
    summary = short_summary(output)
    send_document = ctx.get("send_document")
    if not callable(send_document):
        return {"text": document}

    tmp_dir: Optional[str] = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="seo-kit-")
        tmp_path = Path(tmp_dir) / document_filename(pack_number)
        tmp_path.write_text(document, encoding="utf-8")
        send_document(str(tmp_path), caption=summary)
    finally:
        if tmp_dir:
            try:
                folder = Path(tmp_dir)
                for child in folder.iterdir():
                    child.unlink(missing_ok=True)
                folder.rmdir()
            except OSError:
                pass
    return {"handled": True}
