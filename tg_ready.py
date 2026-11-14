"""tg_ready.py — Telegram plugin for "Sellable Now" gate (/ready).

Commands provided:
  - /ready: Lists ready upload packs grouped by sellability status (SELLABLE, ALMOST, NOT YET) with top 3 fixes each.
  - /ready N: Displays the full sellability card for pack N with keyboard buttons [Kit] [Files] [Marketing].
  - Also handles Tenglish phrasing like 'ready', 'sell ready', 'ready packs', 'ready N', 'pack N ready aa'.

Button / Action Flows:
  - [Kit]: Shows listing kit information or delivers kit text for pack N.
  - [Files]: Delivers pack files or triggers files flow for pack N.
  - [Marketing]: Shows marketing asset summary for pack N.

Strict Constraints:
  - Follows standard Telegram plugin interface:
      COMMANDS = [...]
      def handle(text: str, ctx: dict) -> dict | None
  - Owner-only: only answers the owner chat.
  - Tenglish house style.
  - NEVER calls Telegram API directly (uses ctx callbacks).
  - NEVER imports telegram_inbox at module import time.
  - No money amounts or currency symbols.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

COMMANDS = [
    {
        "cmd": "/ready",
        "words": [
            "ready",
            "sell ready",
            "ready packs",
            "sellable",
            "ready ga unnaya",
            "ready list",
        ],
        "help_en": "List packs by sellability status with top 3 fixes each, or '/ready N' for full card",
        "help_te": "Packs sellability status (SELLABLE, ALMOST, NOT YET) mariyu top 3 fixes chupisthundi",
    }
]

STATE_KEY_LAST_READY_PACK = "last_ready_pack_id"


def _extract_pack_id(text: str) -> Optional[int]:
    """Extract integer pack ID from text if present."""
    # Match patterns like: /ready 2, ready 2, ready #2, pack 2 ready, pack 2
    patterns = [
        r"^(?:/ready|ready)\s+#?(\d+)\b",
        r"^pack\s+#?(\d+)\s+ready",
        r"^#?(\d+)\s+ready\b",
    ]
    for pat in patterns:
        m = re.search(pat, text.strip(), re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None


def _find_target_pack(aid: int, products_dir: Optional[str] = None) -> Optional[Path]:
    """Find folder for pack with ID `aid`."""
    base = Path(products_dir or "exported_content/products")
    if not base.is_dir():
        return None

    search_dirs = []
    if (base / "upload").is_dir():
        search_dirs.append(base / "upload")
    search_dirs.append(base)

    for sdir in search_dirs:
        try:
            for p in sorted(sdir.iterdir()):
                if not p.is_dir() or p.name == "upload":
                    continue
                if re.match(rf"^0*{aid}(?:-.*)?$", p.name):
                    return p
        except OSError:
            continue
    return None


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Standard telegram plugin handle function."""
    if not text or not isinstance(text, str):
        return None

    raw_text = text.strip()
    lower = raw_text.lower()

    # Guard: do not intercept other built-in commands
    if lower.startswith(("/status", "/packs", "/pack", "/list", "/approve", "/reject", "/help", "/start", "/menu")):
        return None

    # Check for direct button taps: Kit, Files, Marketing (or 'Kit 2', 'Files 2', 'Marketing 2')
    btn_match = re.match(r"^(kit|files|marketing)(?:\s+#?(\d+))?$", lower)
    if btn_match:
        action = btn_match.group(1)
        arg_id = btn_match.group(2)
        target_id: Optional[int] = None
        if arg_id:
            try:
                target_id = int(arg_id)
            except ValueError:
                pass
        else:
            # Look up last viewed pack from state
            state_get = ctx.get("state_get")
            if callable(state_get):
                val = state_get(STATE_KEY_LAST_READY_PACK, None)
                if val is not None:
                    try:
                        target_id = int(val)
                    except (ValueError, TypeError):
                        pass

        if target_id is not None:
            return _handle_button_action(action, target_id, ctx)
        # If no target ID known, don't hijack unless it was an explicit command
        if lower.startswith("/"):
            return {"text": "Dhayachesi pack number mention cheyandi (e.g. `/ready 1`)."}
        return None

    # Check if text matches /ready command or words
    is_ready_command = False
    for cmd_info in COMMANDS:
        if lower == cmd_info["cmd"] or lower.startswith(cmd_info["cmd"] + " "):
            is_ready_command = True
            break
        for word in cmd_info.get("words", []):
            if lower == word or lower.startswith(word + " ") or lower.endswith(" " + word):
                is_ready_command = True
                break

    # Also match Tenglish questions like "pack 2 ready aa", "pack 3 ready unna"
    if not is_ready_command:
        if re.search(r"\bpack\s+\d+\s+ready\b", lower) or re.search(r"\bready\s+\d+\b", lower):
            is_ready_command = True

    if not is_ready_command:
        return None

    # Check if a specific pack ID was requested
    aid = _extract_pack_id(raw_text)

    import sell_ready

    # Context helpers
    send_text = ctx.get("send_text")
    state_set = ctx.get("state_set")
    products_dir = ctx.get("products_dir")
    marketing_dir = ctx.get("marketing_dir")

    if aid is not None:
        # User requested specific pack: /ready N
        pack_folder = _find_target_pack(aid, products_dir)
        if not pack_folder:
            return {"text": f"Pack #{aid} kanipinchaledu. Ready packs chudadaniki `/ready` ani type cheyandi."}

        # Remember last viewed pack ID in state
        if callable(state_set):
            state_set(STATE_KEY_LAST_READY_PACK, aid)

        evaluation = sell_ready.evaluate_pack(pack_folder, marketing_dir)
        card_text = sell_ready.format_pack_card(evaluation)
        listed = _listed_note(ctx, aid)
        if listed:
            card_text = listed + "\n\n" + card_text

        # Build reply keyboard with [Kit] [Files] [Marketing]
        # Providing specific labels or generic labels
        keyboard = [
            [f"Kit {aid}", f"Files {aid}", f"Marketing {aid}"],
            ["📦 All Ready Packs"],
        ]

        if callable(send_text):
            send_text(card_text, keyboard=keyboard)
            return {"handled": True}
        return {"text": card_text}

    # If user tapped "All Ready Packs"
    if "all ready packs" in lower or lower in ("/ready", "ready", "sell ready", "ready packs", "sellable", "ready list"):
        evaluations = sell_ready.evaluate_all_packs(products_dir, marketing_dir)
        summary_text = sell_ready.format_ready_summary(evaluations)
        return {"text": _with_listed_summary(ctx, summary_text, evaluations)}

    # Fallback to evaluating all packs
    evaluations = sell_ready.evaluate_all_packs(products_dir, marketing_dir)
    return {"text": _with_listed_summary(ctx, sell_ready.format_ready_summary(evaluations), evaluations)}


def _listed_note(ctx: dict, aid: int) -> str:
    """Line for the /ready N card when the owner already listed pack N on Gumroad (/listed)."""
    try:
        import tg_listed
        return tg_listed.ready_note(aid, ctx.get("db_path"))
    except Exception:
        return ""


def _with_listed_summary(ctx: dict, summary_text: str, evaluations: list) -> str:
    """Add one line to the /ready list naming the packs already listed on Gumroad."""
    if not evaluations:
        return summary_text
    try:
        import tg_listed
        note = tg_listed.ready_summary_note(ctx.get("db_path"))
    except Exception:
        note = ""
    return summary_text + "\n\n" + note if note else summary_text


def _handle_button_action(action: str, aid: int, ctx: dict) -> dict:
    """Handle Kit, Files, Marketing button presses for pack N."""
    products_dir = ctx.get("products_dir")
    marketing_dir = ctx.get("marketing_dir")
    send_text = ctx.get("send_text")
    send_document = ctx.get("send_document")

    pack_folder = _find_target_pack(aid, products_dir)
    if not pack_folder:
        return {"text": f"Pack #{aid} directory kanipinchaledu."}

    if action == "kit":
        # Check store kits
        candidates = [
            pack_folder / "store-kits",
            pack_folder / "store_kits",
            pack_folder / "kits",
        ]
        kit_files: List[Path] = []
        for c in candidates:
            if c.is_dir():
                kit_files.extend(c.glob("*.txt"))
                kit_files.extend(c.glob("*.md"))
        if not kit_files:
            kit_files.extend(pack_folder.glob("kit-*.txt"))

        if kit_files and callable(send_document):
            for kf in kit_files[:3]:
                send_document(str(kf), caption=f"Store Kit: {kf.name}")
            return {"text": f"Pack #{aid} store kits pampinchanu ✓"}
        elif kit_files:
            return {"text": f"Pack #{aid} lo {len(kit_files)} store kit files unnayi."}
        else:
            return {"text": f"Pack #{aid} kosam inka store kits generate avvaledu. `/kit {aid}` choodandi."}

    elif action == "files":
        # Check core pack files
        p_zip = pack_folder / "product.zip"
        p_listing = pack_folder / "listing.txt"
        if p_zip.is_file() and callable(send_document):
            send_document(str(p_zip), caption=f"Pack #{aid} product.zip")
            if p_listing.is_file():
                send_document(str(p_listing), caption=f"Pack #{aid} listing.txt")
            return {"text": f"Pack #{aid} files pampinchanu ✓"}
        elif p_zip.is_file():
            return {"text": f"Pack #{aid} product.zip ready ga undi ({pack_folder}). `/files {aid}` choodandi."}
        else:
            return {"text": f"Pack #{aid} product.zip file inka ready kaledu."}

    elif action == "marketing":
        import sell_ready
        mkt_dir = sell_ready.find_marketing_dir(pack_folder, Path(marketing_dir) if marketing_dir else None)
        if not mkt_dir or not mkt_dir.is_dir():
            return {"text": f"Pack #{aid} marketing folder inka create avvaledu. `python3 make_marketing.py --pack {aid}` run cheyandi."}

        assets = []
        if (mkt_dir / "carousel").is_dir():
            assets.append("Carousel slides")
        if (mkt_dir / "pins").is_dir():
            assets.append("Pinterest pins")
        if (mkt_dir / "reel").is_dir():
            assets.append("Reel frames")
        if (mkt_dir / "blog.md").is_file():
            assets.append("Blog post (blog.md)")
        if (mkt_dir / "covers").is_dir():
            assets.append("Cover variants")
        if (mkt_dir / "LAUNCH.md").is_file():
            assets.append("Launch checklist (LAUNCH.md)")

        asset_str = ", ".join(assets) if assets else "Emi levu"
        return {"text": f"Pack #{aid} Marketing Assets:\n{asset_str}\n\nFiles kosam `/carousel {aid}`, `/pins {aid}`, `/covers {aid}` use cheyandi."}

    return {"text": f"Action '{action}' teliyadu."}
