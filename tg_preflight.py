"""Telegram plugin for checking whether a pack is ready to list on Gumroad."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent

COMMANDS = [
    {
        "cmd": "/preflight",
        "words": ["ready na", "preflight"],
        "help_en": "Check a product pack before Gumroad listing: /preflight N",
        "help_te": "Gumroad lo pettadaniki mundu pack check cheyandi: /preflight N",
    },
]

_WITH_ID = (
    re.compile(r"^/preflight(?:@\w+)?\s+(?:pack\s+)?#?(\d+)$", re.IGNORECASE),
    re.compile(r"^(?:preflight|ready\s+na)\s+(?:pack\s+)?#?(\d+)$", re.IGNORECASE),
    re.compile(r"^(?:pack\s+)?#?(\d+)\s+(?:preflight|ready\s+na)$", re.IGNORECASE),
)
_BARE = re.compile(r"^(?:/preflight(?:@\w+)?|preflight|ready\s+na)$", re.IGNORECASE)
_PACK_NAME = re.compile(r"^0*(\d+)(?:-.*)?$")
_LABELS = {
    "Thumbnail image exists": "Thumbnail image undi",
    "Cover image exists": "Cover image undi",
    "Thumbnail image is readable": "Thumbnail image readable ga undi",
    "Cover image is readable": "Cover image readable ga undi",
    "Thumbnail image size": "Thumbnail image size correct ga undi",
    "Cover image size": "Cover image size correct ga undi",
    "Product ZIP opens": "Product ZIP open avuthundi",
    "Buyer files exist": "Buyer files unnayi",
    "Buyer files are not empty": "Buyer files khali ga levu",
    "Listing has no money amount": "Listing lo money amount ledu",
    "Listing title length": "Listing title length okay",
    "Listing has at most 13 tags": "Tags limit lopala unnayi",
    "Listing description is not empty": "Listing description undi",
    "LICENSE is present": "LICENSE present ga undi",
    "Truth check is ready": "Truth check ready ga undi",
}


def _products_dir(ctx: Optional[Dict[str, Any]] = None) -> Path:
    value = (ctx or {}).get("products_dir") or os.environ.get("CHILLI_PRODUCTS_DIR")
    return Path(value).resolve() if value else REPO_ROOT / "exported_content" / "products"


def _search_dirs(products: Path) -> List[Path]:
    upload = products / "upload"
    return [upload, products] if upload.is_dir() else [products]


def _find_pack(pack_id: int, ctx: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    products = _products_dir(ctx)
    for folder in _search_dirs(products):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.is_symlink() or not path.is_dir() or path.name == "upload":
                continue
            match = _PACK_NAME.fullmatch(path.name)
            if match and int(match.group(1)) == pack_id:
                return path
    return None


def _last_packs(ctx: Optional[Dict[str, Any]] = None) -> List[int]:
    products = _products_dir(ctx)
    found = set()
    for folder in _search_dirs(products):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.is_symlink() or not path.is_dir() or path.name == "upload":
                continue
            match = _PACK_NAME.fullmatch(path.name)
            if match:
                found.add(int(match.group(1)))
    return sorted(found, reverse=True)[:5]


def _reply(text: str, ctx: dict) -> dict:
    send_text = ctx.get("send_text")
    if callable(send_text):
        send_text(text)
        return {"handled": True}
    return {"text": text}


def _help(ctx: dict) -> dict:
    packs = _last_packs(ctx)
    lines = ["Pack Gumroad lo pettadaniki mundu check: `/preflight N`",
             "Number lekunda pampithe recent packs ivi:"]
    lines.extend(f"- Pack #{pack_id}: `/preflight {pack_id}`" for pack_id in packs)
    if not packs:
        lines.append("Inka packs kanipinchaledu.")
    return _reply("\n".join(lines), ctx)


def _evaluate(pack_id: int, ctx: dict) -> dict:
    pack = _find_pack(pack_id, ctx)
    if pack is None:
        return _reply(f"Pack #{pack_id} kanipinchaledu. Recent packs kosam `/preflight` ani pampandi.", ctx)

    import gumroad_preflight

    checks = gumroad_preflight.check(pack)
    all_ok = all(item["ok"] for item in checks)
    lines = [f"Gumroad preflight — Pack #{pack_id}"]
    for item in checks:
        label = _LABELS.get(item["name"], item["name"])
        if item["ok"]:
            lines.append(f"✅ {label}")
        else:
            lines.append(f"❌ {label}: {item['fix_te']}")
    if all_ok:
        lines.extend(["", f"Anni ✅ aithe: /sell {pack_id}"])
    else:
        lines.extend(["", f"Fixes chesi malli `/preflight {pack_id}` check cheyandi."])
    return _reply("\n".join(lines), ctx)


def handle(text: str, ctx: dict) -> Optional[dict]:
    if not isinstance(text, str):
        return None

    owner_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if owner_id and str(ctx.get("chat_id", "")) != owner_id:
        return None

    raw = " ".join(text.strip().split())
    if not raw or len(raw) > 100:
        return None
    for pattern in _WITH_ID:
        match = pattern.fullmatch(raw)
        if match:
            return _evaluate(int(match.group(1)), ctx)
    if _BARE.fullmatch(raw):
        return _help(ctx)
    return None
