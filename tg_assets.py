"""tg_assets.py - Telegram Plugin for Owner Assets (BINNY-W4B).

Allows the owner to manage branding assets (logo, face icons, product photos) directly
from Telegram by sending pictures or documents to the bot.

Features:
- Handles photos and documents from the owner chat.
- Validates PNG, JPG, and WEBP magic bytes (<= 2 MB), refusing SVG.
- Automatically stores valid images in the private SQLite table `owner_assets` (never in repo).
- Classifies asset kind from caption:
    * "idi logo", "logo", "brand logo" -> logo
    * "idi icon", "icon", "face icon", "avatar" -> icon
    * default -> photo
- Commands:
    * /assets (or 'assets', 'na assets') -> lists saved assets with index numbers, kind, size, filename.
    * /asset N (or 'asset N', 'asset N pampu') -> sends back asset N as photo or document.
    * /asset delete N (or 'asset N delete', 'asset N tey') -> prompts for confirmation and deletes.
    * Confirmation response 'Yes' / 'avunu' / 'sare' completes deletion; 'No' / 'vaddu' cancels.
- Replies in Tenglish house style; strictly avoids currency mentions and leaks.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

COMMANDS = [
    {
        "cmd": "/assets",
        "words": ["assets", "na assets", "asset list", "assets list", "my assets"],
        "category": "Settings",
        "help_en": "List all saved owner branding assets (logos, icons, photos).",
        "help_te": "Owner branding assets list chupisthundi (logo, icon, photo).",
    },
    {
        "cmd": "/asset",
        "words": ["asset", "asset pampu", "download asset"],
        "category": "Settings",
        "help_en": "View or manage a saved asset: /asset <N> or /asset delete <N>",
        "help_te": "Asset chudadaniki /asset <N>, leda delete cheyadaniki /asset delete <N>",
    },
]

STATE_PENDING_DELETE = "tg_assets_pending_delete"


def parse_kind_from_caption(caption: str) -> Tuple[str, str]:
    """Parse asset kind and cleaned caption/tags from caption text.

    Returns (kind, tags).
    Recognized kinds: 'logo', 'icon', 'photo'.
    """
    text = (caption or "").strip()
    lower = text.lower()

    # Telugu & English phrases for logo
    if re.search(r"\b(?:idi\s+)?logo\b", lower) or "brand logo" in lower:
        return "logo", "logo"

    # Telugu & English phrases for icon
    if re.search(r"\b(?:idi\s+)?icon\b", lower) or "face icon" in lower or "avatar" in lower or "face" in lower:
        return "icon", "icon"

    return "photo", "photo"


def _format_size(size_bytes: int) -> str:
    """Format bytes into readable KB/MB."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{round(size_bytes / 1024, 1)} KB"
    return f"{round(size_bytes / (1024 * 1024), 2)} MB"


def _handle_list_assets(ctx: dict) -> dict:
    """Handle /assets list command."""
    import owner_assets
    db_path = ctx.get("db_path")
    items = owner_assets.list_assets(db_path)

    if not items:
        return {
            "text": (
                "📦 Owner Assets emi levu.\n\n"
                "Mee logo, face icon leda product photo ikkadiki pampandi (photo leda document ga).\n"
                "Caption lo 'idi logo' leda 'idi icon' ani raasthe automatically classify avutundi (max 2 MB, PNG/JPG/WEBP)."
            )
        }

    lines = [f"📦 Owner Assets ({len(items)} items):\n"]
    for idx, it in enumerate(items, 1):
        k_emoji = "🏷️" if it["kind"] == "logo" else ("👤" if it["kind"] == "icon" else "🖼️")
        size_str = _format_size(it["size_bytes"])
        fn = it.get("filename") or f"asset_{it['id']}"
        cap = f" - {it['caption']}" if it.get("caption") else ""
        lines.append(f"{idx}. {k_emoji} [{it['kind'].upper()}] {fn} ({size_str}){cap}")

    lines.append("\nTip: Send /asset <number> to retrieve it, or /asset delete <number> to delete.")
    return {"text": "\n".join(lines)}


def _handle_get_asset(idx_or_id: int, ctx: dict) -> dict:
    """Retrieve asset and send back to owner via send_photo or send_document."""
    import owner_assets
    db_path = ctx.get("db_path")
    items = owner_assets.list_assets(db_path)

    target_item = None
    if 1 <= idx_or_id <= len(items):
        target_item = items[idx_or_id - 1]
    else:
        # Check by database ID directly
        for it in items:
            if it["id"] == idx_or_id:
                target_item = it
                break

    if not target_item:
        return {"text": f"Asset #{idx_or_id} kanipinchaledu. /assets chusi correct number ivvandi."}

    full_asset = owner_assets.get_asset(db_path, target_item["id"])
    if not full_asset or not full_asset.get("data"):
        return {"text": f"Asset #{idx_or_id} file data read cheyadam kudaraledu."}

    # Write temporarily to disk to send
    mime = full_asset.get("mime_type", "image/png")
    ext = ".png"
    if "jpeg" in mime or "jpg" in mime:
        ext = ".jpg"
    elif "webp" in mime:
        ext = ".webp"

    fn = full_asset.get("filename") or f"asset_{target_item['id']}{ext}"
    size_str = _format_size(full_asset["size_bytes"])
    caption = f"📦 Asset #{idx_or_id}: {fn} ({full_asset['kind'].upper()}, {size_str})"

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(full_asset["data"])
        tmp_path = tmp.name

    try:
        send_fn = ctx.get("send_photo") or ctx.get("send_document")
        if send_fn:
            send_fn(tmp_path, caption=caption)
        return {"handled": True}
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def _handle_delete_prompt(idx_or_id: int, ctx: dict) -> dict:
    """Prompt owner for confirmation to delete asset."""
    import owner_assets
    db_path = ctx.get("db_path")
    items = owner_assets.list_assets(db_path)

    target_item = None
    display_num = idx_or_id
    if 1 <= idx_or_id <= len(items):
        target_item = items[idx_or_id - 1]
        display_num = idx_or_id
    else:
        for it in items:
            if it["id"] == idx_or_id:
                target_item = it
                break

    if not target_item:
        return {"text": f"Asset #{idx_or_id} kanipinchaledu. /assets chudandi."}

    state_set = ctx.get("state_set")
    if state_set:
        state_set(STATE_PENDING_DELETE, str(target_item["id"]))

    fn = target_item.get("filename") or f"asset_{target_item['id']}"
    return {
        "text": f"Asset #{display_num} ({fn}, {target_item['kind']}) delete cheyala?\nConfirm cheyandi: Yes / No",
        "keyboard": [["Yes", "No"]],
    }


def _handle_media_upload(ctx: dict) -> Optional[dict]:
    """Inspect ctx['message'] for photo or document upload and store it."""
    msg = ctx.get("message")
    if not msg:
        return None

    caption = msg.get("caption", "")
    download_file = ctx.get("download_file")
    db_path = ctx.get("db_path")

    file_id = None
    orig_filename = ""

    if "photo" in msg and isinstance(msg["photo"], list) and msg["photo"]:
        best_photo = max(msg["photo"], key=lambda p: p.get("file_size", 0))
        file_id = best_photo.get("file_id")
        orig_filename = "photo.jpg"
    elif "document" in msg and isinstance(msg["document"], dict):
        doc = msg["document"]
        file_id = doc.get("file_id")
        orig_filename = doc.get("file_name", "document")
        mime = (doc.get("mime_type") or "").lower()
        ext = os.path.splitext(orig_filename)[1].lower()
        # SVG refusal
        if "svg" in mime or ext == ".svg":
            return {
                "text": "❌ SVG files support ledu. Kevalam PNG, JPG leda WEBP images matrame pampandi."
            }
        # If it's not an image document, let media_brain handle it
        is_img_doc = mime.startswith("image/") or ext in (".png", ".jpg", ".jpeg", ".webp")
        if not is_img_doc:
            return None

    if not file_id:
        return None

    # Download file to temporary location
    with tempfile.NamedTemporaryFile(suffix=".tmp", delete=False) as tmp:
        tmp_dest = tmp.name

    try:
        if not download_file:
            return {"text": "File download cheyadam kudaraledu (download_file missing in ctx)."}

        # 2 MB max cap
        max_bytes = 2 * 1024 * 1024
        download_ok = download_file(file_id, tmp_dest, max_bytes=max_bytes)
        if not download_ok or not os.path.isfile(tmp_dest):
            return {
                "text": "❌ File download fail ayyindi leda size 2 MB minchindi. Max 2 MB allow chestham."
            }

        with open(tmp_dest, "rb") as f:
            data = f.read()

        import owner_assets
        kind, tags = parse_kind_from_caption(caption)

        res = owner_assets.store_asset(
            db_path=db_path,
            data=data,
            filename=orig_filename,
            kind=kind,
            caption=caption,
            tags=tags,
        )

        if not res.get("ok"):
            err_msg = res.get("error", "Unknown error")
            return {"text": f"❌ Asset save kaaledu: {err_msg}"}

        size_str = _format_size(res["size_bytes"])
        sha_short = res["sha256"][:8]

        dup_note = " (Duplicate asset: metadata updated)" if res.get("duplicate") else ""

        return {
            "text": (
                f"✅ Asset save ayyindi{dup_note}!\n"
                f"ID: #{res['id']}\n"
                f"Kind: {res['kind'].upper()}\n"
                f"Size: {size_str}\n"
                f"SHA256: {sha_short}\n"
                f"Private DB lo bhadram ga pettam (repo lo raadu)."
            )
        }
    finally:
        try:
            if os.path.exists(tmp_dest):
                os.unlink(tmp_dest)
        except OSError:
            pass


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Handle incoming message or command for tg_assets plugin.

    Returns dict with reply or None if unhandled.
    """
    clean = (text or "").strip()
    lower = clean.lower()

    # 1. First, check if this is an incoming photo/document upload in ctx['message']
    msg = ctx.get("message")
    if msg and ("photo" in msg or "document" in msg):
        res = _handle_media_upload(ctx)
        if res is not None:
            return res

    # 2. Check pending delete confirmation
    state_get = ctx.get("state_get")
    state_set = ctx.get("state_set")
    pending_delete_id = state_get(STATE_PENDING_DELETE) if state_get else None

    if pending_delete_id:
        if lower in ("yes", "avunu", "sare", "ha", "ok", "confirm"):
            import owner_assets
            db_path = ctx.get("db_path")
            del_ok = owner_assets.delete_asset(db_path, int(pending_delete_id))
            if state_set:
                state_set(STATE_PENDING_DELETE, None)
            if del_ok:
                return {
                    "text": f"✅ Asset #{pending_delete_id} delete chesam.",
                    "keyboard": "remove",
                }
            return {
                "text": f"Asset #{pending_delete_id} kanipinchaledu leda delete avvaledu.",
                "keyboard": "remove",
            }
        elif lower in ("no", "vaddu", "kaadu", "cancel", "stop"):
            if state_set:
                state_set(STATE_PENDING_DELETE, None)
            return {
                "text": "Deletion cancel chesam.",
                "keyboard": "remove",
            }

    # 3. Check /assets or word triggers
    if lower in ("/assets", "assets", "na assets", "asset list", "assets list", "my assets"):
        return _handle_list_assets(ctx)

    # 4. Check /asset delete N or 'asset delete N' or 'asset N delete'
    del_m = re.match(r"^/?asset\s+delete\s+(\d+)$", lower) or re.match(r"^/?asset\s+(\d+)\s+delete$", lower)
    if del_m:
        idx_or_id = int(del_m.group(1))
        return _handle_delete_prompt(idx_or_id, ctx)

    # 5. Check /asset N or 'asset N' or 'asset N pampu'
    get_m = re.match(r"^/?asset\s+(\d+)(?:\s+pampu)?$", lower)
    if get_m:
        idx_or_id = int(get_m.group(1))
        return _handle_get_asset(idx_or_id, ctx)

    # 6. Generic /asset help if called alone
    if lower in ("/asset", "asset"):
        return {
            "text": (
                "📦 Asset commands:\n"
                "/assets - Saved assets list\n"
                "/asset <number> - Retrieve asset\n"
                "/asset delete <number> - Delete asset\n\n"
                "Kotha image pampadaniki: photo leda document attach chesi 'idi logo' leda 'idi icon' caption ivvandi."
            )
        }

    return None
