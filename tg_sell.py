"""Telegram plugin: tg_sell.py — CC1-2: "/sell N" = everything to list pack N on Gumroad in one go.

  /sell        list of packs that can be listed now (SELLABLE / ALMOST), with "/sell N" buttons
  /sell N      1) verdict on top (sell_ready.evaluate_pack)
               2) Gumroad fields text from the Gumroad kit (store_kits): title, description, tags,
                  "Price: owner decides"
               3) product files as documents (store_kits.get_files_to_upload)
               4) cover + preview images (store_kits.get_images_to_upload)
               5) Tenglish click-by-click checklist for gumroad.com
  Verdict NOT YET -> only the list of fixes is sent; no files, no fields.

Also plain words: "sell 3", "pack 3 sell chey", "3 sell cheyali", "pack 3 gumroad lo pettu".

Rules kept:
  - Plugin interface: COMMANDS + handle(text, ctx). Sends only through ctx callbacks.
  - telegram_inbox is never imported here.
  - Nothing is listed, published or sold by the robot: the owner clicks everything on gumroad.com.
  - No money amounts: the price is always "owner decides".
  - Only files inside the pack folder are sent (no symlinks, nothing outside).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent

COMMANDS = [
    {
        "cmd": "/sell",
        "words": ["sell", "sell chey", "sell cheyali", "gumroad lo pettu", "gumroad listing"],
        "help_en": "Everything to list pack N on Gumroad: fields, files, images, click checklist (/sell N)",
        "help_te": "Pack N ni Gumroad lo pettadaniki anni okesari: fields, files, images, checklist (/sell N)",
    },
]

STATE_KEY_LAST_SELL_PACK = "last_sell_pack_id"
MAX_TEXT = 3500                      # Telegram allows 4096; keep room
MAX_FILE_BYTES = 45 * 1024 * 1024    # Telegram bot upload limit is 50 MB
MAX_DOCUMENTS = 20
MAX_IMAGES = 10
OWNER_ONLY_FILES = {"checklist.txt", "listing.txt", "listing.md"}   # never part of the buyer download
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
MONEY_RE = re.compile(
    r"(?:[$₹€£]\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:USD|INR|EUR|GBP|dollars?|rupees?|cents?)\b|\bRs\.?\s?\d[\d.,]*)",
    re.IGNORECASE)

_SELL_WORDS = r"(?:sell|ammu|ammali|ammandi)"   # "sale ..." belongs to tg_sales
_TAIL = r"(?:\s+(?:chey|cheyi|cheyyi|cheyali|cheyyali|cheddam|cheseddam|cheyandi|pettu|pettali|cheyava|kavali))?"
_PATTERNS_WITH_ID = [
    re.compile(rf"^/sell(?:@\w+)?\s+(?:pack\s+)?#?(\d+)$", re.IGNORECASE),
    re.compile(rf"^{_SELL_WORDS}\s+(?:pack\s+)?#?(\d+){_TAIL}$", re.IGNORECASE),
    re.compile(rf"^(?:pack\s+)?#?(\d+)\s*(?:ni\s+|ki\s+)?{_SELL_WORDS}{_TAIL}$", re.IGNORECASE),
    re.compile(rf"^(?:pack\s+)?#?(\d+)\s*(?:ni\s+)?gumroad\s+lo\s+(?:pettu|pettali|list\s+chey|sell\s+chey|upload\s+chey)$",
               re.IGNORECASE),
    re.compile(rf"^gumroad\s+(?:lo\s+pettu|listing)\s+(?:pack\s+)?#?(\d+)$", re.IGNORECASE),
]
_PATTERNS_LIST = [
    re.compile(r"^/sell(?:@\w+)?$", re.IGNORECASE),
    re.compile(rf"^{_SELL_WORDS}{_TAIL}$", re.IGNORECASE),
    re.compile(r"^(?:sell\s+list|sell\s+packs|em\s+sell\s+cheyali|edi\s+sell\s+cheyali|gumroad\s+lo\s+pettu|gumroad\s+listing)$",
               re.IGNORECASE),
]


# ---- parsing -------------------------------------------------------------------
def parse(text: Any) -> Optional[Tuple[str, Optional[int]]]:
    """("sell", N), ("list", None) or None when the text is not for this plugin."""
    if not isinstance(text, str):
        return None
    raw = " ".join(text.strip().split())
    raw = raw.rstrip("?.! ").strip()
    if not raw or len(raw) > 80:
        return None
    for pattern in _PATTERNS_WITH_ID:
        match = pattern.match(raw)
        if match:
            return "sell", int(match.group(1))
    for pattern in _PATTERNS_LIST:
        if pattern.match(raw):
            return "list", None
    return None


# ---- folders ---------------------------------------------------------------------
def products_dir(ctx: Optional[Dict[str, Any]] = None) -> Path:
    value = (ctx or {}).get("products_dir") or os.environ.get("CHILLI_PRODUCTS_DIR")
    return Path(value).resolve() if value else REPO_ROOT / "exported_content" / "products"


def marketing_base(pack_dir: Path, ctx: Optional[Dict[str, Any]] = None) -> Path:
    """Where this pack's marketing lives: ctx/env override, else the private folder made by
    marketing_auto (exported_content/products/marketing), else the older shared marketing folder."""
    value = (ctx or {}).get("marketing_dir") or os.environ.get("CHILLI_MARKETING_DIR")
    if value:
        return Path(value).resolve()
    private = products_dir(ctx) / "marketing"
    if (private / pack_dir.name).is_dir():
        return private
    return REPO_ROOT / "exported_content" / "marketing"


def find_pack(pack_id: int, ctx: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Ready upload pack N (product.zip + listing.txt), same discovery as /ready."""
    import sell_ready
    for number, _title, path in sell_ready.find_all_ready_packs(products_dir(ctx)):
        if number == pack_id:
            return Path(path)
    return None


def _inside(path: Path, folder: Path) -> bool:
    """A real file directly inside the pack folder (no symlink, no path tricks)."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        return path.resolve().parent == folder.resolve()
    except OSError:
        return False


# ---- text pieces -----------------------------------------------------------------
def clean(text: Any) -> str:
    """No money amounts ever leave this plugin."""
    return MONEY_RE.sub("[amount]", str(text))


def chunks(text: str, limit: int = MAX_TEXT) -> List[str]:
    """Split long text at line ends so every Telegram message stays under the limit."""
    parts: List[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return [part for part in parts if part.strip()]


def verdict_text(pack_id: int, evaluation: Dict[str, Any]) -> str:
    import sell_ready
    status = evaluation.get("status")
    icon = {sell_ready.STATUS_SELLABLE: "🟢", sell_ready.STATUS_ALMOST: "🟡"}.get(status, "🔴")
    lines = [f"{icon} Pack #{pack_id} — {status} (Score {evaluation.get('score', 0)}/100)",
             f"Title: {evaluation.get('title') or evaluation.get('pack_name')}"]
    fixes = [str(fix) for fix in (evaluation.get("fixes") or [])]
    if status == sell_ready.STATUS_NOT_YET:
        lines += ["", "Ee pack ni ippude Gumroad lo pettakandi. Mundu ee fixes kavali:"]
        lines += [f"{index}. {fix}" for index, fix in enumerate(fixes[:10], 1)] or ["1. Pack files check cheyandi"]
        lines += ["", f"Fixes ayyaka malli /sell {pack_id} ani pampandi. Full card: /ready {pack_id}"]
    elif status == sell_ready.STATUS_ALMOST:
        lines += ["", "List cheyochu, kani ee chinna fixes unte inka baguntundi:"]
        lines += [f"- {fix}" for fix in fixes[:3]]
    elif fixes:
        lines += ["", "Gumroad lo pettochu. Optional ga ivi kuda chudandi (inka baguntundi):"]
        lines += [f"- {fix}" for fix in fixes[:3]]
    else:
        lines += ["", "Anni checks pass. Direct ga Gumroad lo pettochu."]
    return clean("\n".join(lines))


def gumroad_fields(pack_dir: Path) -> str:
    """Copy-paste fields from the real Gumroad kit (store_kits): title, descriptions, tags, price."""
    import store_kits
    info = store_kits.parse_listing_file(pack_dir / "listing.txt")
    kit = store_kits.generate_kit_text("gumroad", pack_dir, info)
    fields = kit.split("--- FIELD: FILES TO UPLOAD", 1)[0].rstrip()
    wanted = []
    for line in fields.splitlines():
        if line.startswith("Note: Strict rule"):
            continue
        wanted.append(line)
    text = "\n".join(wanted).rstrip()
    if "Price: owner decides" not in text:
        text += "\n\n--- FIELD: PRICING ---\nPrice: owner decides"
    return clean("1/4 GUMROAD FIELDS (copy-paste cheyandi)\n\n" + text
                 + "\n(Price robot cheppadu, enter cheyadu — meere decide chesi type cheyali.)")


def product_files(pack_dir: Path) -> List[Path]:
    """Buyer files in the Gumroad kit's order; owner-only notes and images are left out."""
    import store_kits
    found: List[Path] = []
    for name in store_kits.get_files_to_upload(pack_dir):
        path = pack_dir / name
        if name in OWNER_ONLY_FILES or path.suffix.lower() in IMAGE_SUFFIXES:
            continue
        if _inside(path, pack_dir) and path not in found:
            found.append(path)
    return found[:MAX_DOCUMENTS]


def image_files(pack_dir: Path) -> List[Path]:
    """Cover first, then previews, in the Gumroad kit's order."""
    import store_kits
    found: List[Path] = []
    for name in store_kits.get_images_to_upload(pack_dir):
        path = pack_dir / name
        if _inside(path, pack_dir) and path not in found:
            found.append(path)
    return found[:MAX_IMAGES]


def checklist_text(pack_id: int, files: List[Path], images: List[Path]) -> str:
    """Click-by-click steps for gumroad.com, in Tenglish, with this pack's real file names."""
    file_names = ", ".join(path.name for path in files) or "product.zip"
    cover = images[0].name if images else "cover image"
    previews = ", ".join(path.name for path in images[1:])
    steps = [
        "Browser lo gumroad.com open chesi mee account lo login avvandi (login meere cheyali).",
        "Left menu lo 'Products' click cheyandi, tarvata 'New product' button click cheyandi.",
        "'Name' box lo paina message lo unna Title paste cheyandi.",
        "Product type lo 'Digital product' select cheyandi.",
        "Price box lo meeru decide chesina price meere type cheyandi (Price: owner decides).",
        "'Next: Customize' click cheyandi.",
        "'Description' box lo paina unna Description paste cheyandi.",
        f"'Cover' section lo 'Upload images or videos' click chesi {cover} upload cheyandi."
        + (f" Tarvata previews kuda add cheyandi: {previews}." if previews else ""),
        "'Thumbnail' ki ade cover image upload cheyandi.",
        "Summary box unte Short Description paste cheyandi.",
        f"'Content' tab open chesi 'Upload your files' click cheyandi: {file_names}.",
        "'Share' tab lo Category select chesi, paina unna Tags okkokkati type chesi Enter kottandi.",
        "Top right lo 'Save changes' click cheyandi, tarvata 'Preview' tho page okasari chudandi.",
        "Anni correct ga unte 'Publish' meere click cheyandi. Robot emi publish cheyadu.",
        f"Publish ayyaka Pack #{pack_id} product link copy chesi mee notes lo save chesukondi.",
    ]
    lines = [f"4/4 GUMROAD CHECKLIST — Pack #{pack_id} (click-by-click)", ""]
    lines += [f"[ ] {index}. {step}" for index, step in enumerate(steps, 1)]
    return clean("\n".join(lines))


def list_text(evaluations: List[Dict[str, Any]]) -> Tuple[str, List[int]]:
    """Text + ids for "/sell" alone: what can be listed now."""
    import sell_ready
    ready, waiting = [], 0
    for evaluation in evaluations:
        match = re.match(r"^(\d+)", str(evaluation.get("pack_name", "")))
        if evaluation.get("status") in (sell_ready.STATUS_SELLABLE, sell_ready.STATUS_ALMOST) and match:
            ready.append((int(match.group(1)), evaluation))
        else:
            waiting += 1
    if not evaluations:
        return "Ready upload packs inka levu. Drafts approve cheste robot packs build chestundi (/list).", []
    if not ready:
        return (f"Ippudu sell cheyadaniki ready ga unna pack ledu ({waiting} packs ki fixes kavali). "
                "Details: /ready"), []
    ready.sort(key=lambda item: item[0])
    lines = ["Gumroad lo pettadaniki ready packs:", ""]
    for number, evaluation in ready:
        icon = "🟢" if evaluation["status"] == sell_ready.STATUS_SELLABLE else "🟡"
        lines.append(f"{icon} /sell {number} — {evaluation.get('title') or evaluation.get('pack_name')} "
                     f"({evaluation['status']}, {evaluation.get('score', 0)}/100)")
    if waiting:
        lines += ["", f"Inko {waiting} packs ki fixes kavali: /ready"]
    lines += ["", "Pack number tho pampandi, udaharanaki: /sell " + str(ready[0][0])]
    return clean("\n".join(lines)), [number for number, _ in ready]


# ---- sending -----------------------------------------------------------------------
def _send(ctx: Dict[str, Any], text: str, keyboard=None) -> List[str]:
    """Send text in safe chunks; returns the chunks (for callers without send_text)."""
    parts = chunks(text)
    send_text = ctx.get("send_text")
    if callable(send_text):
        for index, part in enumerate(parts):
            if keyboard is not None and index == len(parts) - 1:
                send_text(part, keyboard=keyboard)
            else:
                send_text(part)
    return parts


def _send_documents(pack_id: int, files: List[Path], ctx: Dict[str, Any]) -> Tuple[int, List[str]]:
    send_document = ctx.get("send_document")
    sent, problems = 0, []
    for index, path in enumerate(files, 1):
        try:
            size = path.stat().st_size
        except OSError:
            problems.append(f"{path.name}: file chadavalekapoya")
            continue
        if size > MAX_FILE_BYTES:
            problems.append(f"{path.name}: chala pedda file ({size // (1024 * 1024)} MB). Telegram lo raadu — "
                            "Mac lo pack folder nundi direct ga upload cheyandi")
            continue
        if not callable(send_document):
            problems.append(f"{path.name}: document pampe daari ledu")
            continue
        ok = send_document(str(path), caption=f"2/4 Pack #{pack_id} file {index}/{len(files)}: {path.name} "
                                              "— Gumroad 'Content' lo upload cheyandi")
        if ok is False:
            problems.append(f"{path.name}: Telegram ki pampadam fail ayyindi")
        else:
            sent += 1
    return sent, problems


def _send_images(pack_id: int, images: List[Path], ctx: Dict[str, Any]) -> Tuple[int, List[str]]:
    send_photo, send_document = ctx.get("send_photo"), ctx.get("send_document")
    sent, problems = 0, []
    for index, path in enumerate(images):
        role = "Cover image" if index == 0 else f"Preview {index}"
        caption = f"3/4 Pack #{pack_id} {role}: {path.name}"
        ok = send_photo(str(path), caption=caption) if callable(send_photo) else False
        if ok is False and callable(send_document):   # e.g. photo too large for a Telegram photo
            ok = send_document(str(path), caption=caption)
        if ok is False:
            problems.append(f"{path.name}: image pampadam fail ayyindi")
        else:
            sent += 1
    return sent, problems


# ---- the two commands -------------------------------------------------------------
def handle_list(ctx: Dict[str, Any]) -> Dict[str, Any]:
    import sell_ready
    base = products_dir(ctx)
    evaluations = []
    for _number, _title, path in sell_ready.find_all_ready_packs(base):
        evaluations.append(sell_ready.evaluate_pack(path, marketing_base(Path(path), ctx)))
    text, ids = list_text(evaluations)
    if ids and callable(ctx.get("send_text")):
        keyboard = [[f"/sell {number}" for number in ids[row:row + 3]] for row in range(0, min(len(ids), 9), 3)]
        _send(ctx, text, keyboard=keyboard)
        return {"handled": True}
    return {"text": text}


def handle_sell(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    import sell_ready
    pack_dir = find_pack(pack_id, ctx)
    if pack_dir is None:
        return {"text": f"Pack #{pack_id} ready upload pack dorakaledu. Sell cheyagalige packs kosam /sell ani pampandi."}
    state_set = ctx.get("state_set")
    if callable(state_set):
        try:
            state_set(STATE_KEY_LAST_SELL_PACK, pack_id)
        except Exception:
            pass
    evaluation = sell_ready.evaluate_pack(pack_dir, marketing_base(pack_dir, ctx))
    verdict = verdict_text(pack_id, evaluation)
    if evaluation.get("status") == sell_ready.STATUS_NOT_YET:
        return {"text": verdict}          # fixes only: no fields, no files

    try:
        fields = gumroad_fields(pack_dir)
    except Exception as error:
        return {"text": verdict + "\n\n" + clean(f"Gumroad fields tayaru cheyalekapoya: {error}. "
                                                    f"listing.txt check cheyandi (/ready {pack_id}).")}
    files, images = product_files(pack_dir), image_files(pack_dir)
    checklist = checklist_text(pack_id, files, images)

    if not callable(ctx.get("send_text")):
        return {"text": "\n\n".join([verdict, fields, checklist])}

    _send(ctx, verdict)
    _send(ctx, fields)
    sent_files, problems = _send_documents(pack_id, files, ctx)
    sent_images, image_problems = _send_images(pack_id, images, ctx)
    problems += image_problems
    if not files:
        problems.append("Product file okkati kuda dorakaledu (product.zip ledu)")
    if not images:
        problems.append("Cover image ledu — Gumroad ki cover kavali")
    _send(ctx, checklist)
    summary = [f"Pack #{pack_id}: {sent_files} product files, {sent_images} images pampanu. "
               "Paina checklist follow ayyi meere Publish cheyandi."]
    if problems:
        summary += ["", "Gamaninchandi:"] + [f"- {problem}" for problem in problems]
    _send(ctx, clean("\n".join(summary)))
    return {"handled": True}


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Standard Telegram plugin entry point."""
    parsed = parse(text)
    if parsed is None:
        return None
    ctx = ctx if isinstance(ctx, dict) else {}
    action, pack_id = parsed
    try:
        if action == "sell" and pack_id is not None:
            return handle_sell(pack_id, ctx)
        return handle_list(ctx)
    except Exception as error:
        return {"text": clean(f"/sell lo problem vachindi ({type(error).__name__}). /doctor tho chudandi.")}
