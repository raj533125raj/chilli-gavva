"""Telegram plugin: tg_kits.py

Sends store listing kit files (<pack>/store-kits/<store>.txt) to the owner's Telegram chat.
Supported commands:
  /kit N [store]   - send the kit file for pack N as a document and send a short message with the first 3 fields (Title, Short Desc, Long Desc / Tags).
  /kits N          - alias for /kit N

Also handles Tenglish phrasing, e.g.:
  'pack 3 kit pampu', 'kit 3 pampu', 'kit 3 gumroad', 'kit 2 etsy pampu', 'pack 4 store kit', etc.

Supported stores:
  gumroad (default if unspecified), payhip, etsy, kofi, topmate, instamojo.

Safety and Constraints:
  - NEVER calls Telegram API directly (uses ctx callbacks).
  - NEVER imports telegram_inbox at module import time.
  - Confined strictly to exported_content.
  - Tenglish house style; strictly zero currency or money amounts anywhere.
  - Atomic generation if kit files do not exist yet.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

import store_kits

COMMANDS = [
    {
        "cmd": "/kit",
        "words": ["kit", "kits", "kit pampu", "store kit", "store kits", "store kit pampu"],
        "help_en": "Send store listing kit file as document + top 3 fields summary (/kit N [store])",
        "help_te": "Store listing kit file document ga + first 3 fields text ga pampistundi (/kit N [store])",
    },
]

STORE_ALIASES = {
    "gumroad": "gumroad",
    "gum": "gumroad",
    "payhip": "payhip",
    "hip": "payhip",
    "etsy": "etsy",
    "kofi": "kofi",
    "ko-fi": "kofi",
    "topmate": "topmate",
    "mate": "topmate",
    "instamojo": "instamojo",
    "mojo": "instamojo",
}


# ---------------------------------------------------------------------------
# Path and Pack Discovery
# ---------------------------------------------------------------------------

def _get_base_dirs(ctx: Optional[Dict[str, Any]] = None) -> Tuple[Path, Path]:
    """Return (products_dir, marketing_dir) as resolved Path objects."""
    prod = ctx.get("products_dir") if ctx else None
    mktg = ctx.get("marketing_dir") if ctx else None

    if not prod:
        prod_env = os.environ.get("CHILLI_PRODUCTS_DIR")
        prod = prod_env if prod_env else "exported_content/products"
    if not mktg:
        mktg_env = os.environ.get("CHILLI_MARKETING_DIR")
        mktg = mktg_env if mktg_env else "exported_content/marketing"

    return Path(prod).resolve(), Path(mktg).resolve()


def _is_safe_path(path: Path | str, ctx: Optional[Dict[str, Any]] = None) -> bool:
    """Ensure path exists and resolves inside exported_content or configured test roots."""
    p = Path(path).resolve()
    repo_root = Path(__file__).resolve().parent
    exported_root = (repo_root / "exported_content").resolve()

    allowed_roots = [exported_root]
    if ctx:
        if ctx.get("products_dir"):
            allowed_roots.append(Path(ctx["products_dir"]).resolve())
        if ctx.get("marketing_dir"):
            allowed_roots.append(Path(ctx["marketing_dir"]).resolve())

    for root in allowed_roots:
        try:
            p.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def find_ready_pack_dir(aid: int | str, ctx: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Find the directory path of ready pack aid, matching /pack N rules."""
    aid_str = str(aid)
    products_dir, _ = _get_base_dirs(ctx)
    if not products_dir.is_dir():
        return None

    search_dirs: List[Path] = []
    if (products_dir / "upload").is_dir():
        search_dirs.append(products_dir / "upload")
    search_dirs.append(products_dir)

    for sdir in search_dirs:
        try:
            entries = sorted(sdir.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                if not p.is_dir() or p.name == "upload":
                    continue
                if re.match(rf"^{aid_str}(?:-.*)?$", p.name):
                    # Ready pack has listing.txt
                    if (p / "listing.txt").is_file():
                        return p
            except OSError:
                continue
    return None


# ---------------------------------------------------------------------------
# Command Parsing
# ---------------------------------------------------------------------------

def parse_kit_command(text: str) -> Optional[Tuple[Optional[int], Optional[str]]]:
    """Parse incoming text for pack number and optional store name.

    Returns:
      (pack_id, store_key) or None if text is not a kit command.
      pack_id can be None if user typed just '/kit' or 'kit pampu'.
      store_key can be None (defaults to 'gumroad').
    """
    if not text:
        return None
    raw = text.strip()

    # 1. Slash commands: /kit, /kits, /kit 3, /kit 3 etsy, /kit@bot 4 topmate
    m_slash = re.match(r"^/(?:kit|kits)(?:@\w+)?(?:\s+(\d+))?(?:\s+([a-zA-Z_-]+))?$", raw, re.IGNORECASE)
    if m_slash:
        num = int(m_slash.group(1)) if m_slash.group(1) else None
        store_word = m_slash.group(2).lower() if m_slash.group(2) else None
        store_key = STORE_ALIASES.get(store_word) if store_word else None
        return (num, store_key)

    # 2. Tenglish patterns with pack number and store
    # e.g.:
    #   'pack 3 kit pampu'
    #   'pack 3 store kit'
    #   'kit 3 pampu'
    #   'kit 3 etsy'
    #   'kit 3 gumroad pampu'
    #   '3 kit pampu'
    #   'kit pampu 3'
    #   'pack 3 ki kit pampu'
    #   'pack 3 etsy kit'

    # Check for kit trigger word in message
    lowered = raw.lower()
    words = re.findall(r"[a-zA-Z0-9_-]+", lowered)

    # Look for "kit" or "kits" or "store-kit"
    kit_triggers = {"kit", "kits"}
    has_trigger = any(w in kit_triggers for w in words)
    if not has_trigger:
        return None

    # Check if this might be a built-in command like /status, /packs, /help, etc.
    if raw.startswith("/") and not raw.startswith("/kit"):
        return None

    # Extract digits and potential store name
    pack_id: Optional[int] = None
    store_key: Optional[str] = None

    for w in words:
        if w.isdigit() and pack_id is None:
            pack_id = int(w)
        elif w in STORE_ALIASES and store_key is None:
            store_key = STORE_ALIASES[w]

    return (pack_id, store_key)


# ---------------------------------------------------------------------------
# Summary Extractor (First 3 Fields)
# ---------------------------------------------------------------------------

def extract_first_3_fields(kit_text: str) -> str:
    """Extract Title, Short Description, and Long Description / Tags summary.

    Formats a concise Tenglish summary suitable for a quick Telegram text preview.
    """
    title_m = re.search(r"^Title:\s*(.*?)$", kit_text, re.MULTILINE)
    short_m = re.search(r"^Short Description:\s*(.*?)$", kit_text, re.MULTILINE)
    cat_m = re.search(r"^Category:\s*(.*?)$", kit_text, re.MULTILINE)
    tags_m = re.search(r"^Tags \(.*?\):\s*\n(.*?)$", kit_text, re.MULTILINE)

    title = title_m.group(1).strip() if title_m else ""
    short_desc = short_m.group(1).strip() if short_m else ""
    tags = tags_m.group(1).strip() if tags_m else ""
    cat = cat_m.group(1).strip() if cat_m else ""

    summary_lines = [
        "📦 Store Kit Ready!",
        f"1️⃣ Title: {title}",
        f"2️⃣ Short Desc: {short_desc}",
        f"3️⃣ Tags: {tags}" if tags else f"3️⃣ Category: {cat}",
    ]
    return "\n".join(summary_lines)


# ---------------------------------------------------------------------------
# Plugin Handle Entrypoint
# ---------------------------------------------------------------------------

def handle(text: str, ctx: dict) -> dict | None:
    """Plugin entrypoint.

    Args:
      text: The raw incoming message text from the user.
      ctx: Dict containing execution context and callbacks:
           db_path, chat_id, now, send_text, send_document, send_photo,
           send_media_group, state_get, state_set, products_dir (optional),
           marketing_dir (optional).

    Returns:
      None: if message is not for this plugin.
      {"text": "..."}: text reply to send to user.
      {"handled": True}: if plugin already handled everything and sent messages/files.
    """
    parsed = parse_kit_command(text)
    if not parsed:
        return None

    pack_id, store_key = parsed

    if pack_id is None:
        return {
            "text": (
                "Pack number cheppandi (e.g. '/kit 3' leda '/kit 3 etsy').\n"
                "Supported stores: gumroad, payhip, etsy, kofi, topmate, instamojo."
            )
        }

    # Default to gumroad if store not explicitly specified
    chosen_store = store_key if store_key else "gumroad"
    if chosen_store not in store_kits.STORE_LIMITS:
        return {
            "text": f"Store '{chosen_store}' teliyadu. Ee stores available: {', '.join(store_kits.SUPPORTED_STORES)}."
        }

    pack_dir = find_ready_pack_dir(pack_id, ctx)
    if not pack_dir:
        return {"text": f"Pack #{pack_id} ready pack dorakaledu. /packs tho chudandi."}

    # Ensure store kits exist in <pack>/store-kits/
    kits_dir = pack_dir / "store-kits"
    target_kit_file = kits_dir / store_kits.STORE_LIMITS[chosen_store]["file_name"]

    if not target_kit_file.is_file():
        try:
            store_kits.build(pack_dir)
        except Exception as exc:
            return {"text": f"Pack #{pack_id} store kits generate cheyadam lo error vachindi: {exc}"}

    if not target_kit_file.is_file():
        return {"text": f"Pack #{pack_id} {chosen_store} kit file dorakaledu."}

    if not _is_safe_path(target_kit_file, ctx):
        return {"text": f"Pack #{pack_id} path safe kaadu."}

    # Read kit content for top 3 fields summary
    try:
        content = target_kit_file.read_text(encoding="utf-8")
        summary_msg = f"Pack #{pack_id} ({chosen_store.capitalize()}):\n" + extract_first_3_fields(content)
    except Exception:
        summary_msg = f"Pack #{pack_id} {chosen_store.capitalize()} kit ready!"

    send_txt = ctx.get("send_text")
    send_doc = ctx.get("send_document")

    # Send document and short message
    if send_doc:
        store_display = store_kits.STORE_LIMITS[chosen_store]["store_name"]
        send_doc(str(target_kit_file), caption=f"Pack #{pack_id} {store_display} Listing Kit")

    if send_txt:
        send_txt(summary_msg)

    return {"handled": True}
