"""Telegram plugin: tg_files.py

Sends ready pack files and marketing assets to the owner's Telegram chat.
Supported commands:
  /files N     - send product.zip, cover.png, listing.txt as documents (zip > 45 MB -> send note)
  /covers N    - cover variants as a media group
  /pins N      - pins as media groups of max 10 + the 5 A/B titles as text
  /carousel N  - carousel slides in order + caption.txt text
  /reel N      - reel frames + reel.txt + 'make_reel.sh Mac lo run cheyandi'
  /blog N      - blog.md as a document
  /landing N   - landing/index.html zipped as a document
  /launch N    - LAUNCH.md as document

Also handles Tenglish phrasing, e.g.:
  'pack 3 files pampu', 'pins 3 pampu', 'pack 2 covers pampu', etc.

Safety and Constraints:
  - NEVER calls Telegram API directly (uses ctx callbacks).
  - NEVER imports telegram_inbox at module import time (lazy import only if needed).
  - NEVER sends anything outside exported_content.
  - Rate limit: max 30 files per command.
  - 1s pause between media groups (injectable via ctx.get('sleep_fn', time.sleep)).
  - Zip over 45 MB: sends a note instead.
  - Tenglish house style; no currency or money amounts anywhere.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import time
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

COMMANDS = [
    {
        "cmd": "/files",
        "words": ["files", "file pampu", "files pampu", "pack files"],
        "help_en": "Send ready pack files (product.zip, cover.png, listing.txt) as documents",
        "help_te": "Pack files pampistundi (product.zip, cover.png, listing.txt)",
    },
    {
        "cmd": "/covers",
        "words": ["covers", "covers pampu", "cover variants"],
        "help_en": "Send cover variants as a media group",
        "help_te": "Cover variants media group ga pampistundi",
    },
    {
        "cmd": "/pins",
        "words": ["pins", "pins pampu", "pin images"],
        "help_en": "Send Pinterest pins as media groups (max 10) + 5 A/B titles",
        "help_te": "Pins media group ga pampistundi + 5 A/B titles",
    },
    {
        "cmd": "/carousel",
        "words": ["carousel", "carousel pampu", "slides pampu"],
        "help_en": "Send Instagram carousel slides + caption text",
        "help_te": "Instagram carousel slides + caption pampistundi",
    },
    {
        "cmd": "/reel",
        "words": ["reel", "reel pampu", "reel frames"],
        "help_en": "Send reel frames + reel.txt + run script note",
        "help_te": "Reel frames + reel.txt + make_reel script note pampistundi",
    },
    {
        "cmd": "/blog",
        "words": ["blog", "blog pampu", "blog.md"],
        "help_en": "Send blog.md article as a document",
        "help_te": "Pack blog.md document ga pampistundi",
    },
    {
        "cmd": "/landing",
        "words": ["landing", "landing pampu", "landing page"],
        "help_en": "Send landing page (index.html) zipped as a document",
        "help_te": "Landing page zip document ga pampistundi",
    },
    {
        "cmd": "/launch",
        "words": ["launch", "launch pampu", "launch checklist"],
        "help_en": "Send LAUNCH.md checklist as a document",
        "help_te": "LAUNCH.md checklist document ga pampistundi",
    },
]

MAX_FILES_PER_COMMAND = 30
MAX_ZIP_SIZE_BYTES = 45 * 1024 * 1024  # 45 MB


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


def _is_safe_exported_path(path: Path | str, ctx: Optional[Dict[str, Any]] = None) -> bool:
    """Ensure path exists and resolves inside exported_content or configured test roots."""
    p = Path(path).resolve()
    # Check if inside exported_content or allowed test dirs
    repo_root = Path.cwd().resolve()
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
                    # Check ready upload pack criteria
                    if (p / "product.zip").is_file() and (p / "listing.txt").is_file():
                        return p
            except OSError:
                continue
    return None


def find_marketing_pack_dir(aid: int | str, ctx: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Marketing folder for pack aid (exact or '<aid>-' prefix names), or None."""
    aid_str = str(aid)
    _, marketing_dir = _get_base_dirs(ctx)
    if not marketing_dir.is_dir():
        return None

    try:
        entries = sorted(marketing_dir.iterdir())
    except OSError:
        return None

    for child in entries:
        try:
            if child.is_dir() and (child.name == aid_str or child.name.startswith(f"{aid_str}-")):
                return child
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Command Parsing
# ---------------------------------------------------------------------------

_SUPPORTED_ACTIONS = {
    "files": "files",
    "file": "files",
    "covers": "covers",
    "cover": "covers",
    "pins": "pins",
    "pin": "pins",
    "carousel": "carousel",
    "slides": "carousel",
    "slide": "carousel",
    "reel": "reel",
    "reels": "reel",
    "blog": "blog",
    "landing": "landing",
    "launch": "launch",
}


def parse_action_and_id(text: str) -> Optional[Tuple[str, Optional[int]]]:
    """Parse text for action and pack number.

    Returns:
      (action, pack_id) or None if text does not match any plugin command.
      pack_id may be None if the command was given without a pack number.
    """
    if not text:
        return None
    raw = text.strip()

    # 1. Slash commands: /files 3, /covers, /pins 4@bot, etc.
    m_slash = re.match(r"^/([a-zA-Z_]+)(?:@\w+)?(?:\s+(\d+))?$", raw)
    if m_slash:
        cmd_name = m_slash.group(1).lower()
        if cmd_name in _SUPPORTED_ACTIONS:
            num = int(m_slash.group(2)) if m_slash.group(2) else None
            return _SUPPORTED_ACTIONS[cmd_name], num

    # 2. Tenglish and natural language patterns
    # Examples:
    #   'pack 3 files pampu'
    #   'pins 3 pampu'
    #   'pack 3 ki carousel pampu'
    #   'files 3'
    #   '3 files pampu'
    #   'files pampu 3'
    #   'covers 3 pampu'
    #   'launch 3'
    #   'pack 3 blog'
    #   'blog 3 pampu'

    patterns = [
        # pack 3 files pampu / pack 3 files
        r"^(?:pack\s+)?(\d+)\s*(?:ki\s+)?(?:pack\s+)?([a-zA-Z_]+)(?:\s+pampu)?$",
        # files 3 pampu / files pack 3 / files 3
        r"^(?:pack\s+)?([a-zA-Z_]+)\s*(?:pack\s+)?(\d+)(?:\s+pampu)?$",
        # files pampu 3
        r"^([a-zA-Z_]+)\s+pampu\s+(\d+)$",
        # pack 3 pampu files
        r"^(?:pack\s+)?(\d+)\s+pampu\s+([a-zA-Z_]+)$",
    ]

    for pat in patterns:
        m = re.match(pat, raw, re.IGNORECASE)
        if m:
            g1, g2 = m.group(1), m.group(2)
            if g1.isdigit() and g2.lower() in _SUPPORTED_ACTIONS:
                return _SUPPORTED_ACTIONS[g2.lower()], int(g1)
            if g2.isdigit() and g1.lower() in _SUPPORTED_ACTIONS:
                return _SUPPORTED_ACTIONS[g1.lower()], int(g2)

    # Check for action without a number, e.g. "files pampu", "covers pampu", "pack files"
    no_num_patterns = [
        r"^pack\s+([a-zA-Z_]+)(?:\s+pampu)?$",
        r"^([a-zA-Z_]+)\s+pampu$",
    ]
    for pat in no_num_patterns:
        m = re.match(pat, raw, re.IGNORECASE)
        if m:
            act = m.group(1).lower()
            if act in _SUPPORTED_ACTIONS:
                return _SUPPORTED_ACTIONS[act], None

    return None


# ---------------------------------------------------------------------------
# Individual Action Handlers
# ---------------------------------------------------------------------------

def _get_sleep_fn(ctx: Dict[str, Any]) -> Callable[[float], None]:
    return ctx.get("sleep_fn", time.sleep)


def _handle_files(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /files N: product.zip, cover.png, listing.txt as documents."""
    send_doc = ctx.get("send_document")
    send_txt = ctx.get("send_text")

    pack_dir = find_ready_pack_dir(pack_id, ctx)
    if not pack_dir:
        return {"text": f"Pack #{pack_id} ready pack dorakaledu. /packs tho chudandi."}

    sent_count = 0

    # 1. product.zip
    zip_path = pack_dir / "product.zip"
    if zip_path.is_file():
        try:
            size = zip_path.stat().st_size
            if size > MAX_ZIP_SIZE_BYTES:
                size_mb = size / (1024 * 1024)
                if send_txt:
                    send_txt(
                        f"Pack #{pack_id} product.zip chala peddaga undi ({size_mb:.1f} MB > 45 MB). "
                        f"Telegram limit valla skip chesamu. Mac lo exported_content nundi copy cheyandi."
                    )
            else:
                if send_doc and _is_safe_exported_path(zip_path, ctx):
                    send_doc(str(zip_path), caption=f"Pack #{pack_id} product.zip")
                    sent_count += 1
        except OSError:
            pass

    # 2. cover.png
    cover_path = pack_dir / "cover.png"
    if cover_path.is_file():
        if send_doc and _is_safe_exported_path(cover_path, ctx):
            send_doc(str(cover_path), caption=f"Pack #{pack_id} cover.png")
            sent_count += 1

    # 3. listing.txt
    listing_path = pack_dir / "listing.txt"
    if listing_path.is_file():
        if send_doc and _is_safe_exported_path(listing_path, ctx):
            send_doc(str(listing_path), caption=f"Pack #{pack_id} listing.txt")
            sent_count += 1

    if sent_count == 0:
        return {"text": f"Pack #{pack_id} lo files emi pampadaniki ledu."}

    return {"handled": True}


def _handle_covers(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /covers N: cover variants as a media group."""
    send_media = ctx.get("send_media_group")
    mktg_pack = find_marketing_pack_dir(pack_id, ctx)
    if not mktg_pack:
        return {"text": f"Pack #{pack_id} ki marketing files inka ledu. /marketing {pack_id} tho chudandi."}

    covers_dir = mktg_pack / "covers"
    if not covers_dir.is_dir():
        return {"text": f"Pack #{pack_id} ki cover variants inka ledu."}

    # Find cover images (e.g. cover_a.png, cover_b.png, cover_c.png)
    cover_files: List[Path] = []
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        for f in sorted(covers_dir.glob(f"*{ext}")):
            if f.is_file() and _is_safe_exported_path(f, ctx):
                cover_files.append(f)

    # Exclude non-cover images if any, order deterministically
    cover_files = cover_files[:MAX_FILES_PER_COMMAND]
    if not cover_files:
        return {"text": f"Pack #{pack_id} covers folder lo images dorakaledu."}

    paths = [str(f) for f in cover_files]
    caption = f"Pack #{pack_id} cover variants ({len(paths)})"
    if send_media:
        send_media(paths, caption=caption)

    return {"handled": True}


def _get_ab_titles(mktg_pack: Path) -> List[str]:
    """Read the 5 A/B pin titles from pins.json or ab_titles.txt."""
    titles: List[str] = []
    pins_json = mktg_pack / "pins" / "pins.json"
    if pins_json.is_file():
        try:
            data = json.loads(pins_json.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("title"):
                        t = str(item["title"]).strip()
                    elif isinstance(item, str) and item.strip():
                        t = item.strip()
                    else:
                        t = ""
                    if t and t not in titles:
                        titles.append(t)
        except (OSError, ValueError):
            pass

    if not titles:
        ab_file = mktg_pack / "pins" / "ab_titles.txt"
        if ab_file.is_file():
            try:
                for line in ab_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.lstrip().startswith("#"):
                        continue
                    cleaned = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
                    if cleaned and cleaned not in titles:
                        titles.append(cleaned)
            except OSError:
                pass

    return titles[:5]


def _handle_pins(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /pins N: pins as media groups of max 10 + the 5 A/B titles as text."""
    send_media = ctx.get("send_media_group")
    send_txt = ctx.get("send_text")
    sleep_fn = _get_sleep_fn(ctx)

    mktg_pack = find_marketing_pack_dir(pack_id, ctx)
    if not mktg_pack:
        return {"text": f"Pack #{pack_id} ki marketing files inka ledu. /marketing {pack_id} tho chudandi."}

    pins_dir = mktg_pack / "pins"
    if not pins_dir.is_dir():
        return {"text": f"Pack #{pack_id} ki pins inka ledu."}

    pin_files: List[Path] = []
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        for f in sorted(pins_dir.glob(f"*{ext}")):
            if f.is_file() and _is_safe_exported_path(f, ctx):
                pin_files.append(f)

    pin_files = pin_files[:MAX_FILES_PER_COMMAND]

    if not pin_files:
        return {"text": f"Pack #{pack_id} pins folder lo pin images dorakaledu."}

    # Send media groups of max 10
    total = len(pin_files)
    chunk_size = 10
    for i in range(0, total, chunk_size):
        chunk = pin_files[i:i + chunk_size]
        paths = [str(f) for f in chunk]
        caption = f"Pack #{pack_id} pins ({i + 1}-{i + len(chunk)} of {total})"
        if send_media:
            send_media(paths, caption=caption)
        if i + chunk_size < total:
            sleep_fn(1.0)

    # Send the 5 A/B titles
    titles = _get_ab_titles(mktg_pack)
    if titles and send_txt:
        title_lines = [f"Pack #{pack_id} 5 A/B pin titles:"]
        for idx, t in enumerate(titles, 1):
            title_lines.append(f"{idx}. {t}")
        send_txt("\n".join(title_lines))

    return {"handled": True}


def _handle_carousel(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /carousel N: carousel slides in order + caption.txt text."""
    send_media = ctx.get("send_media_group")
    send_txt = ctx.get("send_text")
    sleep_fn = _get_sleep_fn(ctx)

    mktg_pack = find_marketing_pack_dir(pack_id, ctx)
    if not mktg_pack:
        return {"text": f"Pack #{pack_id} ki marketing files inka ledu. /marketing {pack_id} tho chudandi."}

    car_dir = mktg_pack / "carousel"
    if not car_dir.is_dir():
        return {"text": f"Pack #{pack_id} ki carousel inka ledu."}

    # Slides in order (slide-1.png, slide-2.png, ...)
    slide_files: List[Path] = []
    for f in sorted(car_dir.glob("slide-*.png")):
        if f.is_file() and _is_safe_exported_path(f, ctx):
            slide_files.append(f)

    # Fallback to any png if not slide-*.png
    if not slide_files:
        for f in sorted(car_dir.glob("*.png")):
            if f.is_file() and _is_safe_exported_path(f, ctx):
                slide_files.append(f)

    slide_files = slide_files[:MAX_FILES_PER_COMMAND]
    if not slide_files:
        return {"text": f"Pack #{pack_id} carousel slides dorakaledu."}

    # Send media groups of max 10
    total = len(slide_files)
    chunk_size = 10
    for i in range(0, total, chunk_size):
        chunk = slide_files[i:i + chunk_size]
        paths = [str(f) for f in chunk]
        caption = f"Pack #{pack_id} carousel slides ({len(chunk)})"
        if send_media:
            send_media(paths, caption=caption)
        if i + chunk_size < total:
            sleep_fn(1.0)

    # Send caption.txt text
    caption_file = car_dir / "caption.txt"
    if caption_file.is_file() and send_txt:
        try:
            caption_content = caption_file.read_text(encoding="utf-8", errors="replace").strip()
            if caption_content:
                msg = f"Pack #{pack_id} Carousel Caption:\n\n{caption_content}"
                send_txt(msg)
        except OSError:
            pass

    return {"handled": True}


def _handle_reel(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /reel N: reel frames + reel.txt + 'make_reel.sh Mac lo run cheyandi'."""
    send_media = ctx.get("send_media_group")
    send_txt = ctx.get("send_text")
    sleep_fn = _get_sleep_fn(ctx)

    mktg_pack = find_marketing_pack_dir(pack_id, ctx)
    if not mktg_pack:
        return {"text": f"Pack #{pack_id} ki marketing files inka ledu. /marketing {pack_id} tho chudandi."}

    reel_dir = mktg_pack / "reel"
    if not reel_dir.is_dir():
        return {"text": f"Pack #{pack_id} ki reel inka ledu."}

    # Reel frames in order
    frame_files: List[Path] = []
    for f in sorted(reel_dir.glob("frame-*.png")):
        if f.is_file() and _is_safe_exported_path(f, ctx):
            frame_files.append(f)

    if not frame_files:
        for f in sorted(reel_dir.glob("*.png")):
            if f.is_file() and _is_safe_exported_path(f, ctx):
                frame_files.append(f)

    frame_files = frame_files[:MAX_FILES_PER_COMMAND]
    if not frame_files:
        return {"text": f"Pack #{pack_id} reel frames dorakaledu."}

    # Send media groups of max 10
    total = len(frame_files)
    chunk_size = 10
    for i in range(0, total, chunk_size):
        chunk = frame_files[i:i + chunk_size]
        paths = [str(f) for f in chunk]
        caption = f"Pack #{pack_id} reel frames ({i + 1}-{i + len(chunk)} of {total})"
        if send_media:
            send_media(paths, caption=caption)
        if i + chunk_size < total:
            sleep_fn(1.0)

    # reel.txt text
    reel_txt_file = reel_dir / "reel.txt"
    reel_caption = ""
    if reel_txt_file.is_file():
        try:
            reel_caption = reel_txt_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            pass

    # Note about make_reel.sh
    note_lines = []
    if reel_caption:
        note_lines.append(f"Pack #{pack_id} Reel Caption:\n{reel_caption}\n")
    note_lines.append("make_reel.sh Mac lo run cheyandi (ffmpeg video tayaru chestundi).")

    if send_txt:
        send_txt("\n".join(note_lines))

    return {"handled": True}


def _handle_blog(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /blog N: blog.md as a document."""
    send_doc = ctx.get("send_document")
    mktg_pack = find_marketing_pack_dir(pack_id, ctx)
    if not mktg_pack:
        return {"text": f"Pack #{pack_id} ki marketing files inka ledu. /marketing {pack_id} tho chudandi."}

    blog_file = mktg_pack / "blog.md"
    if not blog_file.is_file():
        return {"text": f"Pack #{pack_id} ki blog.md inka ledu."}

    if not _is_safe_exported_path(blog_file, ctx):
        return {"text": f"Pack #{pack_id} path safe kaadu."}

    if send_doc:
        send_doc(str(blog_file), caption=f"Pack #{pack_id} blog.md")

    return {"handled": True}


def _handle_landing(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /landing N: landing/index.html zipped as a document."""
    send_doc = ctx.get("send_document")
    mktg_pack = find_marketing_pack_dir(pack_id, ctx)
    if not mktg_pack:
        return {"text": f"Pack #{pack_id} ki marketing files inka ledu. /marketing {pack_id} tho chudandi."}

    landing_html = mktg_pack / "landing" / "index.html"
    if not landing_html.is_file():
        return {"text": f"Pack #{pack_id} ki landing page (index.html) inka ledu."}

    if not _is_safe_exported_path(landing_html, ctx):
        return {"text": f"Pack #{pack_id} path safe kaadu."}

    # Create zip file of landing/index.html deterministically
    landing_dir = landing_html.parent
    zip_path = landing_dir / f"landing-pack-{pack_id}.zip"

    try:
        # Atomic zip write
        tmp_fd, tmp_path_str = tempfile.mkstemp(prefix="landing_zip_", suffix=".tmp", dir=str(landing_dir))
        os.close(tmp_fd)
        tmp_path = Path(tmp_path_str)

        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(landing_html, arcname="index.html")

        tmp_path.replace(zip_path)

        if send_doc and _is_safe_exported_path(zip_path, ctx):
            send_doc(str(zip_path), caption=f"Pack #{pack_id} landing page (index.html)")

        return {"handled": True}
    except Exception as exc:
        return {"text": f"Pack #{pack_id} landing zip tayaru cheyadam lo error: {exc}"}


def _handle_launch(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Handle /launch N: LAUNCH.md as document."""
    send_doc = ctx.get("send_document")
    mktg_pack = find_marketing_pack_dir(pack_id, ctx)
    if not mktg_pack:
        return {"text": f"Pack #{pack_id} ki marketing files inka ledu. /marketing {pack_id} tho chudandi."}

    launch_file = mktg_pack / "LAUNCH.md"
    if not launch_file.is_file():
        return {"text": f"Pack #{pack_id} ki LAUNCH.md inka ledu."}

    if not _is_safe_exported_path(launch_file, ctx):
        return {"text": f"Pack #{pack_id} path safe kaadu."}

    if send_doc:
        send_doc(str(launch_file), caption=f"Pack #{pack_id} LAUNCH.md checklist")

    return {"handled": True}


# ---------------------------------------------------------------------------
# Plugin Handle Entrypoint
# ---------------------------------------------------------------------------

def handle(text: str, ctx: dict) -> dict | None:
    """Plugin entrypoint.

    Args:
      text: The raw incoming message text from the user.
      ctx: Dict containing execution context and callbacks:
           db_path, chat_id, now, send_text, send_document, send_photo,
           send_media_group, state_get, state_set, sleep_fn (optional),
           products_dir (optional), marketing_dir (optional).

    Returns:
      None: if message is not for this plugin.
      {"text": "..."}: text reply to send to user.
      {"handled": True}: if plugin already handled everything and sent messages/files.
    """
    parsed = parse_action_and_id(text)
    if not parsed:
        return None

    action, pack_id = parsed

    if pack_id is None:
        return {"text": f"Pack number cheppandi (e.g. /{action} 3 leda 'pack 3 {action} pampu')."}

    if action == "files":
        return _handle_files(pack_id, ctx)
    elif action == "covers":
        return _handle_covers(pack_id, ctx)
    elif action == "pins":
        return _handle_pins(pack_id, ctx)
    elif action == "carousel":
        return _handle_carousel(pack_id, ctx)
    elif action == "reel":
        return _handle_reel(pack_id, ctx)
    elif action == "blog":
        return _handle_blog(pack_id, ctx)
    elif action == "landing":
        return _handle_landing(pack_id, ctx)
    elif action == "launch":
        return _handle_launch(pack_id, ctx)

    return None
