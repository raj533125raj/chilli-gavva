"""Telegram plugin for posting content from the phone: BINNY-V2C.

Allows the owner to review and repost scheduled social media content:
- /post today: sends each planned post ready to publish:
  * Pins as single photos with caption (title + description + hashtags).
  * Carousels as a media group with caption text following.
  * Reels as media group of frames + caption text.
  * Numbered like the plan ('Post 1', 'Post 2').
  * Reply keyboard after each post: ['✅ posted N', '⏭ later'].
- /post tomorrow: preview only (text listing, no media).
- /post week: list of posts for the current week.
- /posted N or 'posted N': marks post N as done via post_planner.mark_posted.

Adheres strictly to the Telegram plugin interface:
- Never calls Telegram API directly.
- Never imports telegram_inbox at module import time.
- Atomic writes and deterministic execution.
- No money amounts, no brands, no automated posting.
- Max 25 media items sent per command call.
- Respects quiet hours if owner_settings exists in state (read-only).
"""
from __future__ import annotations

from tg_menu import owner_reply

import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

import post_planner

ROOT = Path(__file__).resolve().parent


COMMANDS = [
    {
        "cmd": "/post",
        "words": [
            "post today",
            "today posts",
            "post tomorrow",
            "tomorrow posts",
            "post week",
            "week posts",
            "post chey",
            "ee roju posts",
            "repu posts",
            "ee vaaram posts",
        ],
        "help_en": "/post today (send ready assets), /post tomorrow (preview), /post week (list)",
        "help_te": "/post today (phone nunchi post cheyadaniki), /post tomorrow, /post week",
    },
    {
        "cmd": "/posted",
        "words": [
            "posted",
            "post done",
            "post chesa",
            "done post",
            "posted 1",
            "posted 2",
            "posted 3",
            "posted 4",
        ],
        "help_en": "/posted N - mark post N of today as done",
        "help_te": "/posted N - ee roju post N ayipoindi ani mark cheyandi",
    },
]

MAX_MEDIA_PER_COMMAND = 25
CURRENCY_WORDS = {
    "usd", "inr", "eur", "gbp", "aud", "cad", "nzd", "sgd", "hkd", "jpy", "cny",
    "rmb", "aed", "chf", "zar", "pkr", "bdt", "ngn", "php", "krw", "rub", "brl",
    "mxn", "rs", "rupee", "rupees", "dollar", "dollars", "euro", "euros", "pound",
    "pounds", "yen", "cent", "cents", "paise", "paisa", "price", "free", "cost", "sale",
}

_MONEY_PATTERN = re.compile(
    r"(?:\$|₹|€|£|¥|\b(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?)\s*\d+|\d+\s*(?:USD|INR|EUR|GBP|Rs\.?|rupees?|dollars?))",
    re.I,
)


def _get_ist_datetime(dt: Optional[datetime.datetime] = None) -> datetime.datetime:
    """Return datetime in Asia/Kolkata timezone."""
    if dt is None:
        dt = datetime.datetime.now(datetime.timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)

    if ZoneInfo:
        try:
            return dt.astimezone(ZoneInfo("Asia/Kolkata"))
        except Exception:
            pass

    # Fallback: UTC + 5:30
    ist_offset = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return dt.astimezone(ist_offset)


def is_quiet_hours(ctx: dict) -> Tuple[bool, str]:
    """Check if quiet hours are active according to owner_settings in state.

    Returns (is_quiet, reason_note).
    Reads 'owner_settings' from state_get without writing.
    """
    state_get = ctx.get("state_get")
    if not callable(state_get):
        return False, ""

    settings_raw = state_get("owner_settings", None)
    if not settings_raw:
        return False, ""

    settings: Dict[str, Any] = {}
    if isinstance(settings_raw, dict):
        settings = settings_raw
    elif isinstance(settings_raw, str):
        try:
            settings = json.loads(settings_raw)
        except Exception:
            return False, ""

    if not isinstance(settings, dict):
        return False, ""

    # Check for direct quiet/mute flags
    if settings.get("muted") or settings.get("silent") or settings.get("quiet_mode"):
        return True, "Quiet mode active (owner_settings: muted)"

    quiet_hours = settings.get("quiet_hours")
    if not quiet_hours:
        # Check alternate keys: quiet_start, quiet_end
        q_start = settings.get("quiet_start")
        q_end = settings.get("quiet_end")
        if q_start is not None and q_end is not None:
            quiet_hours = {"enabled": True, "start": q_start, "end": q_end}

    if not quiet_hours or not isinstance(quiet_hours, dict):
        return False, ""

    if quiet_hours.get("enabled", True) is False:
        return False, ""

    now_utc = ctx.get("now")
    ist_now = _get_ist_datetime(now_utc)
    current_hour = ist_now.hour
    current_minute = ist_now.minute
    current_time_val = current_hour * 60 + current_minute

    def parse_time_val(val: Any) -> Optional[int]:
        if isinstance(val, (int, float)):
            return int(val) * 60
        if isinstance(val, str):
            val = val.strip()
            if ":" in val:
                parts = val.split(":")
                try:
                    return int(parts[0]) * 60 + int(parts[1])
                except Exception:
                    return None
            try:
                return int(val) * 60
            except Exception:
                return None
        return None

    start_val = parse_time_val(quiet_hours.get("start", 22))
    end_val = parse_time_val(quiet_hours.get("end", 8))

    if start_val is None or end_val is None:
        return False, ""

    in_quiet = False
    if start_val < end_val:
        # e.g., 13:00 to 15:00
        in_quiet = start_val <= current_time_val < end_val
    else:
        # overnight e.g., 22:00 to 08:00
        in_quiet = current_time_val >= start_val or current_time_val < end_val

    if in_quiet:
        return True, f"Quiet hours ({quiet_hours.get('start')}-{quiet_hours.get('end')} IST) active"

    return False, ""


def _scrub_text(text: str) -> str:
    """Scrub currency and sensitive money references from text."""
    if not text:
        return ""
    cleaned = _MONEY_PATTERN.sub("", text)
    # Also strip words
    words = cleaned.split()
    safe_words = [w for w in words if w.lower().strip("$₹€£,.") not in CURRENCY_WORDS]
    result = " ".join(safe_words)
    return re.sub(r"\s+", " ", result).strip()


def _format_hashtags(tags: List[str]) -> str:
    """Format hashtag list into clean '#tag1 #tag2' string."""
    clean_tags = []
    for t in tags:
        t_clean = re.sub(r"[^a-zA-Z0-9]", "", str(t)).lower()
        if t_clean and t_clean not in CURRENCY_WORDS:
            clean_tags.append(f"#{t_clean}")
    return " ".join(clean_tags)


def _resolve_pack_directory(pack_id: str, base_dir: Optional[str] = None) -> Optional[Path]:
    root = Path(base_dir or os.environ.get("CHILLI_PRODUCTS_DIR") or ROOT / "exported_content" / "products")
    for candidate in (root / "upload" / pack_id, root / pack_id):
        if candidate.is_dir():
            return candidate
    return None


def _resolve_marketing_directory(pack_id: str, base_dir: Optional[str] = None) -> Optional[Path]:
    products = Path(os.environ.get("CHILLI_PRODUCTS_DIR") or ROOT / "exported_content" / "products")
    roots = ([Path(base_dir)] if base_dir else []) + [products / "marketing"]
    if os.environ.get("CHILLI_MARKETING_DIR"):
        roots.append(Path(os.environ["CHILLI_MARKETING_DIR"]))
    roots.append(ROOT / "exported_content" / "marketing")
    for root in roots:
        candidate = root / pack_id
        if candidate.is_dir():
            return candidate
    return None


def _resolve_asset_file(pack_id: str, asset_name: str, base_products: Optional[str] = None, base_marketing: Optional[str] = None) -> Optional[str]:
    """Resolve a single asset file (relative or absolute) to an existing file path."""
    asset_path = Path(asset_name)
    if not asset_path.is_absolute():
        asset_path = ROOT / asset_path
    if asset_path.is_file():
        return str(asset_path.resolve())

    # Try marketing dir first if asset starts with pins/, carousel/, reel/
    mkt_dir = _resolve_marketing_directory(pack_id, base_marketing)
    if mkt_dir:
        cand = mkt_dir / asset_name
        if cand.is_file():
            return str(cand.resolve())
        cand2 = mkt_dir / asset_path.name
        if cand2.is_file():
            return str(cand2.resolve())

    # Try pack folder
    pack_dir = _resolve_pack_directory(pack_id, base_products)
    if pack_dir:
        cand = pack_dir / asset_name
        if cand.is_file():
            return str(cand.resolve())
        cand2 = pack_dir / asset_path.name
        if cand2.is_file():
            return str(cand2.resolve())

    return None


def _get_pack_details(pack_id: str, base_products: Optional[str] = None, base_marketing: Optional[str] = None) -> Dict[str, Any]:
    """Read full title, description, tags from listing.txt and marketing assets."""
    out: Dict[str, Any] = {
        "title": pack_id,
        "description": "",
        "tags": [],
    }
    pack_dir = _resolve_pack_directory(pack_id, base_products)
    if pack_dir:
        listing = pack_dir / "listing.txt"
        if listing.is_file():
            try:
                lines = listing.read_text(encoding="utf-8-sig").splitlines()
                desc_lines = []
                collecting_desc = False
                for line in lines:
                    sline = line.strip()
                    lower = sline.lower()
                    if lower.startswith("title:"):
                        out["title"] = sline.split(":", 1)[1].strip()
                        collecting_desc = False
                    elif lower.startswith("tags:"):
                        tag_str = sline.split(":", 1)[1].strip()
                        out["tags"] = [t.strip() for t in tag_str.split(",") if t.strip()]
                        collecting_desc = False
                    elif lower.startswith("description:"):
                        desc_lines.append(sline.split(":", 1)[1].strip())
                        collecting_desc = True
                    elif collecting_desc:
                        if lower.startswith("what you get:") or lower.startswith("price:") or lower.startswith("tags:"):
                            collecting_desc = False
                        else:
                            desc_lines.append(sline)
                if desc_lines:
                    out["description"] = " ".join(desc_lines).strip()
            except Exception:
                pass
    return out


def _build_pin_caption(post: Dict[str, Any], pack_details: Dict[str, Any], mkt_dir: Optional[Path]) -> str:
    """Build exact title + description + hashtags as caption for a pin."""
    asset_str = post.get("asset", "")
    pin_file = Path(asset_str).name
    pin_title = ""
    pin_desc = ""

    if mkt_dir:
        pins_json_path = mkt_dir / "pins" / "pins.json"
        if pins_json_path.is_file():
            try:
                pdata = json.loads(pins_json_path.read_text(encoding="utf-8"))
                for p in pdata:
                    if p.get("file") == pin_file or Path(p.get("file", "")).name == pin_file:
                        pin_title = p.get("title", "")
                        pin_desc = p.get("description", "")
                        break
            except Exception:
                pass

    title = post.get("title") or pin_title or post.get("caption_short") or pack_details.get("title") or post.get("pack_id", "")
    desc = post.get("description") or pin_desc or pack_details.get("description") or ""
    tags = post.get("hashtags") or pack_details.get("tags") or []

    # Clean parts
    title = post_planner.clean_caption(title, 120)
    if desc:
        desc = post_planner.clean_caption(desc, 500)

    tags_str = _format_hashtags(tags)

    board = post_planner.clean_caption(str(post.get("board") or ""), 100)
    link = str(post.get("link") or "")
    parts = [p for p in [title, "Board: " + board if board else "", "Link: " + link if link else "", desc, tags_str] if p]
    caption = "\n\n".join(parts)
    return _scrub_text(caption)


def _collect_carousel_assets(pack_id: str, post: Dict[str, Any], mkt_dir: Optional[Path], pack_dir: Optional[Path]) -> Tuple[List[str], str]:
    """Find slides and caption for a carousel post."""
    slides: List[str] = []
    caption = str(post.get("caption") or "")
    planned = post.get("slides")
    if isinstance(planned, list) and planned:
        for item in planned:
            file = _resolve_asset_file(pack_id, str(item), base_marketing=str(mkt_dir.parent) if mkt_dir else None)
            if not file and mkt_dir:
                candidate = mkt_dir / "carousel" / str(item)
                file = str(candidate.resolve()) if candidate.is_file() else None
            if not file or Path(file).suffix.lower() not in post_planner.IMAGE_EXTENSIONS:
                return [], "Carousel Slide file ledu. Next: /today pampandi; ready plan malli chudandi."
            slides.append(file)
    if mkt_dir:
        car_dir = mkt_dir / "carousel"
        if not slides and car_dir.is_dir():
            slides = [str(file.resolve()) for file in sorted(car_dir.glob("slide-*.png")) if file.is_file()][:10]
        cap_file = Path(str(post.get("caption_file"))) if post.get("caption_file") else car_dir / "caption.txt"
        if not cap_file.is_absolute():
            cap_file = ROOT / cap_file
        if not caption and cap_file.is_file():
            caption = cap_file.read_text(encoding="utf-8").strip()
    elif post.get("caption_file"):
        cap_file = Path(str(post["caption_file"]))
        if not cap_file.is_absolute(): cap_file = ROOT / cap_file
        if cap_file.is_file(): caption = cap_file.read_text(encoding="utf-8").strip()

    # If caption is still empty, build from post or pack details
    if not caption:
        p_details = _get_pack_details(pack_id)
        title = post.get("caption_short") or p_details.get("title") or pack_id
        tags_str = _format_hashtags(post.get("hashtags") or p_details.get("tags") or [])
        caption = f"{title}\n\n{tags_str}"

    # Fallback slides if no carousel folder
    if not slides:
        asset_file = _resolve_asset_file(pack_id, post.get("asset", ""))
        if asset_file:
            slides = [asset_file]
        elif pack_dir:
            images = sorted([str(f.resolve()) for f in pack_dir.iterdir() if f.suffix.lower() in post_planner.IMAGE_EXTENSIONS])
            if images:
                slides = images[:10]

    import public_export
    return slides, public_export.scrub_text(caption)


def _collect_reel_assets(pack_id: str, post: Dict[str, Any], mkt_dir: Optional[Path], pack_dir: Optional[Path]) -> Tuple[List[str], str]:
    """Find frames and caption for a reel post."""
    frames: List[str] = []
    caption = ""

    if mkt_dir:
        reel_dir = mkt_dir / "reel"
        if reel_dir.is_dir():
            found_frames = sorted([str(f.resolve()) for f in reel_dir.glob("frame-*.png")])
            if found_frames:
                frames = found_frames[:8]
            cap_file = reel_dir / "reel.txt"
            if cap_file.is_file():
                try:
                    caption = cap_file.read_text(encoding="utf-8").strip()
                except Exception:
                    pass

    if not caption:
        p_details = _get_pack_details(pack_id)
        title = post.get("caption_short") or p_details.get("title") or pack_id
        tags_str = _format_hashtags(post.get("hashtags") or p_details.get("tags") or [])
        caption = f"{title}\n\n{tags_str}"

    if not frames:
        asset_file = _resolve_asset_file(pack_id, post.get("asset", ""))
        if asset_file:
            frames = [asset_file]
        elif pack_dir:
            images = sorted([str(f.resolve()) for f in pack_dir.iterdir() if f.suffix.lower() in post_planner.IMAGE_EXTENSIONS])
            if images:
                frames = images[:8]

    return frames, _scrub_text(caption)


def handle_post_today(ctx: dict, plan_path: Optional[Union[str, Path]] = None, products_dir: Optional[str] = None) -> dict:
    """Send today's planned items ready for the owner to repost.

    Pins: photo + caption + reply keyboard.
    Carousels: media group + caption text + reply keyboard.
    Reels: frames media group (or photos) + caption text + reply keyboard.
    """
    plan_path = plan_path or post_planner.PLAN_PATH   # read at call time (tests patch it)
    now_utc = ctx.get("now")
    ist_dt = _get_ist_datetime(now_utc)
    today_str = ist_dt.date().isoformat()

    # Check quiet hours
    is_quiet, quiet_reason = is_quiet_hours(ctx)
    if is_quiet:
        msg = f"Quiet hours lo unnam ({quiet_reason}). Phone notifications silent ga unchandi. /post today repu morning pampistha, leda quiet mode off cheyandi."
        return {"text": msg}

    import selltest
    if selltest.active(ctx.get('db_path')):
        base=products_dir or ctx.get('products_dir') or selltest.products_root()
        post_planner.refresh_ready_plan(base,plan_path=plan_path,now=now_utc,db_path=ctx.get('db_path'))
    plan = post_planner.load_plan(plan_path, products_dir=products_dir)
    if not plan:
        return {"text": "Post plan ledu. Upload packs ready ayyaka robot auto plan chestundi."}

    day_posts = [p for p in plan if p.get("date") == today_str]
    if not day_posts:
        return {"text": f"Ee roju ({today_str}) ki posts schedule avaledu. Send /post tomorrow to check repati posts."}

    send_text = ctx.get("send_text")
    send_photo = ctx.get("send_photo")
    send_media_group = ctx.get("send_media_group")

    if not callable(send_text) or not callable(send_photo):
        return {"text": post_planner.today_text(plan, date=today_str)}

    total_media_sent = 0
    posts_sent = 0

    header = f"📅 Today Post Plan ({today_str}) - {len(day_posts)} items ready to repost:"
    send_text(header)

    for idx, post in enumerate(day_posts, 1):
        if total_media_sent >= MAX_MEDIA_PER_COMMAND:
            send_text(f"Media limit reached ({MAX_MEDIA_PER_COMMAND} items max per command). Remaining posts text format lo unnai.")
            break

        post_num = idx
        status = post.get("status", "todo")
        pack_id = post.get("pack_id", "")
        channel = post.get("channel", "pinterest")
        asset_spec = post.get("asset", "")

        keyboard = [[f"✅ posted {post_num}", "⏭ later"]]

        if status == "done":
            send_text(f"Post {post_num} ({channel}) - [{pack_id}] already posted ✓", keyboard)
            continue

        if channel == 'action':
            send_text(post.get('notice') or post.get('caption_short',''))
            continue
        effective_products = products_dir or ctx.get("products_dir")
        mkt_dir = (_resolve_marketing_directory(pack_id, str(Path(effective_products) / "marketing")) if effective_products else _resolve_marketing_directory(pack_id))
        pack_dir = (_resolve_pack_directory(pack_id, effective_products) if effective_products else _resolve_pack_directory(pack_id))
        pack_details = (_get_pack_details(pack_id, base_products=effective_products) if effective_products else _get_pack_details(pack_id))

        if channel == "pinterest":
            # Single Pin photo
            pin_file = _resolve_asset_file(pack_id, asset_spec)
            caption = _build_pin_caption(post, pack_details, mkt_dir)
            full_caption = f"Post {post_num} (Pinterest)\n\n{caption}"
            if len(full_caption) > 1024:
                full_caption = full_caption[:1020] + "..."

            if pin_file and Path(pin_file).is_file() and total_media_sent < MAX_MEDIA_PER_COMMAND:
                send_photo(pin_file, caption=full_caption)
                total_media_sent += 1
                # Reply keyboard follows
                send_text(f"👆 Post {post_num} Pin ready to post on Pinterest.", keyboard)
            else:
                # Text fallback if image missing
                send_text(f"Post {post_num} (Pinterest) [photo missing: {asset_spec}]\n\n{caption}", keyboard)
            posts_sent += 1

        elif channel in ("carousel", "instagram"):
            # Check if it is a carousel or single image
            slides, car_caption = _collect_carousel_assets(pack_id, post, mkt_dir, pack_dir)
            available_slots = MAX_MEDIA_PER_COMMAND - total_media_sent

            if slides and callable(send_media_group) and len(slides) > 1:
                to_send = slides[:available_slots]
                total_media_sent += len(to_send)
                if len(slides) > len(to_send):
                    send_text(f"Media limit reached ({MAX_MEDIA_PER_COMMAND} items max per command). Carousel truncated to {len(to_send)} slides.")
                send_media_group(to_send, caption=f"Post {post_num} Carousel slides ({len(to_send)})")
                # Following text with exact caption for copy-pasting
                cap_msg = f"Post {post_num} (Instagram Carousel Caption):\n\n{car_caption}"
                send_text(cap_msg, keyboard)
            elif slides and total_media_sent < MAX_MEDIA_PER_COMMAND:
                to_send = slides[:available_slots]
                for number, slide in enumerate(to_send, 1):
                    label = (f"Post {post_num} (Instagram)\n\n{car_caption[:1000]}" if len(slides) == 1
                             else f"Post {post_num} Carousel Slide {number}/{len(slides)}")
                    send_photo(slide, caption=label)
                total_media_sent += len(to_send)
                if len(to_send) < len(slides):
                    send_text(f"Media limit reached ({MAX_MEDIA_PER_COMMAND} items max per command). Carousel truncated to {len(to_send)} slides.")
                send_text(f"Post {post_num} (Instagram Carousel Caption):\n\n{car_caption}", keyboard)
            else:
                send_text(f"Post {post_num} (Instagram Carousel)\n\n{car_caption}", keyboard)
            posts_sent += 1

        elif channel == "reel":
            frames, reel_caption = _collect_reel_assets(pack_id, post, mkt_dir, pack_dir)
            available_slots = MAX_MEDIA_PER_COMMAND - total_media_sent

            if frames and callable(send_media_group) and len(frames) > 1:
                to_send = frames[:available_slots]
                total_media_sent += len(to_send)
                if len(frames) > len(to_send):
                    send_text(f"Media limit reached ({MAX_MEDIA_PER_COMMAND} items max per command). Reel truncated to {len(to_send)} frames.")
                send_media_group(to_send, caption=f"Post {post_num} Reel frames ({len(to_send)})")
                cap_msg = f"Post {post_num} (Reel Caption):\n\n{reel_caption}"
                send_text(cap_msg, keyboard)
            elif frames and total_media_sent < MAX_MEDIA_PER_COMMAND:
                total_media_sent += 1
                send_photo(frames[0], caption=f"Post {post_num} (Reel)\n\n{reel_caption[:1000]}")
                send_text(f"👆 Post {post_num} Reel frame ready.", keyboard)
            else:
                send_text(f"Post {post_num} (Reel)\n\n{reel_caption}", keyboard)
            posts_sent += 1

        else:
            # Generic channel
            asset_file = _resolve_asset_file(pack_id, asset_spec)
            caption = post.get("caption_short", "") + "\n\n" + _format_hashtags(post.get("hashtags", []))
            if asset_file and Path(asset_file).is_file() and total_media_sent < MAX_MEDIA_PER_COMMAND:
                send_photo(asset_file, caption=f"Post {post_num} ({channel})\n\n{caption[:1000]}")
                total_media_sent += 1
                send_text(f"Post {post_num} ready.", keyboard)
            else:
                send_text(f"Post {post_num} ({channel})\n\n{caption}", keyboard)
            posts_sent += 1

    summary_note = f"Done! Sent today's posts ({posts_sent} posts, {total_media_sent} media files). Post chesaka 'posted N' button tap cheyandi."
    send_text(summary_note)
    return {"handled": True}


def handle_post_tomorrow(ctx: dict, plan_path: Optional[Union[str, Path]] = None, products_dir: Optional[str] = None) -> dict:
    """Preview-only text list of tomorrow's scheduled posts."""
    plan_path = plan_path or post_planner.PLAN_PATH   # read at call time (tests patch it)
    now_utc = ctx.get("now")
    ist_dt = _get_ist_datetime(now_utc)
    tomorrow_dt = ist_dt.date() + datetime.timedelta(days=1)
    tomorrow_str = tomorrow_dt.isoformat()

    plan = post_planner.load_plan(plan_path, products_dir=products_dir)
    if not plan:
        return {"text": "Post plan ledu. Upload packs ready ayyaka robot auto plan chestundi."}

    tomorrow_posts = [p for p in plan if p.get("date") == tomorrow_str]
    if not tomorrow_posts:
        return {"text": f"Repu ({tomorrow_str}) ki posts schedule avaledu."}

    lines = [f"🔮 Tomorrow Post Preview ({tomorrow_str}) - {len(tomorrow_posts)} items scheduled:"]
    for idx, post in enumerate(tomorrow_posts, 1):
        channel = post.get("channel", "pinterest")
        pack_id = post.get("pack_id", "")
        asset = Path(post.get("asset", "")).name
        caption = post.get("caption_short", "")
        tags = _format_hashtags(post.get("hashtags", []))
        line = f"{idx}) [{channel.upper()}] {pack_id} ({asset}): {caption}"
        if tags:
            line += f" {tags}"
        lines.append(_scrub_text(line))

    lines.append("\nPreview only: repati roju '/post today' pampisthe media files osthai.")
    return {"text": "\n".join(lines)}


def handle_post_week(ctx: dict, plan_path: Optional[Union[str, Path]] = None, products_dir: Optional[str] = None) -> dict:
    """Show weekly overview and post schedule."""
    plan_path = plan_path or post_planner.PLAN_PATH   # read at call time (tests patch it)
    now_utc = ctx.get("now")
    ist_dt = _get_ist_datetime(now_utc)
    cur_date = ist_dt.date()

    plan = post_planner.load_plan(plan_path, products_dir=products_dir)
    if not plan:
        return {"text": "Post plan ledu. Upload packs ready ayyaka robot auto plan chestundi."}

    summary = post_planner.week_summary(plan, date=cur_date)

    # 7-day schedule window starting today
    lines = [f"🗓 Weekly Post Schedule:\n{summary}\n"]
    for i in range(7):
        target_d = cur_date + datetime.timedelta(days=i)
        d_str = target_d.isoformat()
        day_posts = [p for p in plan if p.get("date") == d_str]
        day_name = target_d.strftime("%a")
        if not day_posts:
            lines.append(f"• {day_name} ({d_str}): No posts scheduled")
            continue
        done_cnt = sum(1 for p in day_posts if p.get("status") == "done")
        total_cnt = len(day_posts)
        channels = [p.get("channel", "pin")[:3] for p in day_posts]
        ch_str = ", ".join(channels)
        mark = "✓" if done_cnt == total_cnt else f"({done_cnt}/{total_cnt} done)"
        lines.append(f"• {day_name} ({d_str}): {total_cnt} posts [{ch_str}] {mark}")

    lines.append("\nTip: ee roju posts chudadaniki '/post today' send cheyandi.")
    return {"text": "\n".join(lines)}


def handle_posted_n(n: int, ctx: dict, plan_path: Optional[Union[str, Path]] = None, products_dir: Optional[str] = None) -> dict:
    """Mark post N of today as done."""
    plan_path = plan_path or post_planner.PLAN_PATH   # read at call time (tests patch it)
    now_utc = ctx.get("now")
    ist_dt = _get_ist_datetime(now_utc)
    today_str = ist_dt.date().isoformat()

    plan = post_planner.load_plan(plan_path, products_dir=products_dir)
    if not plan:
        return {
            "text": (
                "Post plan ledu. Ee command run cheyadaniki valid plan kavali. "
                "Next: /post today chudandi (plan ready ayyaka auto plan vastundi)."
            )
        }

    day_posts = [p for p in plan if p.get("date") == today_str]
    if not day_posts:
        return {"text": f"Ee roju ({today_str}) ki posts schedule avaledu."}

    if n < 1 or n > len(day_posts):
        return {"text": f"Invalid post number {n}. Ee roju 1 nunchi {len(day_posts)} daka unnai."}

    ok = post_planner.mark_posted(plan, date=today_str, n=n, now=now_utc)
    if not ok:
        return {"text": f"Post {n} mark cheyatam kudaraledu."}

    post_planner.save_plan(plan, plan_path=plan_path)
    summary = post_planner.week_summary(plan, date=today_str)
    reply = f"Sare! Post {n} done ✓ ani mark chesam.\n{summary}"
    return {"text": reply}


_POSTED_HELP_WORDS = {"/posted", "posted", "post done", "post chesa", "done post"}


def handle_posted_help(ctx: dict, plan_path: Optional[Union[str, Path]] = None,
                      products_dir: Optional[str] = None) -> dict:
    """Show unposted items and how to mark one when /posted has no number."""
    plan_path = plan_path or post_planner.PLAN_PATH   # read at call time (tests patch it)
    now_utc = ctx.get("now")
    today_str = _get_ist_datetime(now_utc).date().isoformat()
    plan = post_planner.load_plan(plan_path, products_dir=products_dir)
    if not plan:
        return {"text": "Post plan ledu. Upload packs ready ayyaka robot auto plan chestundi. /post today chudandi."}

    day_posts = [p for p in plan if p.get("date") == today_str]
    if day_posts:
        unposted = [(idx, post) for idx, post in enumerate(day_posts, 1) if post.get("status") != "done"]
        if not unposted:
            return {"text": f"Ee roju ({today_str}) posts anni done ✓. /post tomorrow tho repati plan chudandi."}
        lines = [f"Ee roju ({today_str}) unposted posts:"]
        for idx, post in unposted[:5]:
            channel = post.get("channel", "post")
            pack_id = post.get("pack_id", "")
            lines.append(f"• Post {idx}: {channel} — {pack_id}")
        if len(unposted) > 5:
            lines.append(f"... inko {len(unposted) - 5} posts (/post today lo full list).")
        first = unposted[0][0]
        lines += ["", f"Mark cheyadaniki: posted {first} (leda 'post chesa {first}')."]
        lines.append("Phone lo ✅ posted N button tap chesina same pani avutundi.")
        return {"text": "\n".join(lines)}

    pending = [(post.get("date", ""), idx, post) for idx, post in enumerate(plan, 1)
               if post.get("status") != "done"]
    if not pending:
        return {"text": "Post plan lo unposted items levu. Anni posts done ✓."}

    past = [item for item in pending if item[0] <= today_str]
    upcoming = [item for item in pending if item[0] > today_str]
    chosen = (sorted(past, key=lambda item: (item[0], item[1]), reverse=True)[:5]
              if past else sorted(upcoming, key=lambda item: (item[0], item[1]))[:5])
    label = "Chivari unposted posts" if past else "Next unposted posts"
    lines = [f"Ee roju ({today_str}) posts schedule avaledu. {label}:"]
    for date_str, idx, post in chosen:
        lines.append(f"• {date_str} Post {idx}: {post.get('channel', 'post')} — {post.get('pack_id', '')}")
    lines += ["", "Ee roju post chesaka /post today chudandi; appudu posted N tho mark cheyandi."]
    return {"text": "\n".join(lines)}


@owner_reply
def handle(text: str, ctx: dict) -> dict | None:
    """Telegram plugin handle entry point.

    Returns:
    - None if text is not handled by this plugin.
    - {"text": "..."} to send plain text.
    - {"handled": True} if everything was sent via context callbacks.
    """
    if not text or not isinstance(text, str):
        return None

    raw = text.strip()
    norm = re.sub(r"\s+", " ", raw.lower()).strip()

    # 1. Posted N checks:
    # Matches:
    # /posted 2, posted 2, post 2 done, post chesa 2, ✅ posted 2, posted 2 done, etc.
    posted_match = re.search(r"(?:^|[\s/✅])(?:posted|post(?:\s+chesa|\s+done)?)\s+(\d+)", norm)
    if not posted_match:
        # Check reverse: 2 posted, 2 done, post 2 done
        rev_match = re.search(r"\b(\d+)\s*(?:done|posted|post\s+done)\b", norm)
        if rev_match and ("post" in norm or "done" in norm):
            posted_match = rev_match

    # If it is explicitly "post today" or "post tomorrow" or "post week", don't treat as posted N
    if any(k in norm for k in ("today", "tomorrow", "week", "ee roju", "repu", "ee vaaram")):
        posted_match = None

    if posted_match:
        try:
            n = int(posted_match.group(1))
            return handle_posted_n(n, ctx)
        except Exception:
            pass

    # 2. /posted or its declared words without a number: show what is still unposted.
    if norm.rstrip("?.! ") in _POSTED_HELP_WORDS:
        return handle_posted_help(ctx)

    # 3. Check /post commands
    # Exact triggers or word inclusions
    is_today = bool(re.search(r"\b(today|ee\s*roju|eeroju|today'?s?)\b", norm))
    is_tomorrow = bool(re.search(r"\b(tomorrow|repu|repati)\b", norm))
    is_week = bool(re.search(r"\b(week|ee\s*vaaram|eevaaram|this\s*week)\b", norm))

    is_post_cmd = norm.startswith("/post") or norm.startswith("post ") or norm == "/post" or norm == "post"

    if norm == "⏭ later" or norm == "later":
        return {"text": "Sare, ee post taruvatha cheyandi. Phone lo ready ga untundi."}

    if is_post_cmd or any(w in norm for w in ("posts", "post plan", "post chey")):
        if is_tomorrow:
            return handle_post_tomorrow(ctx)
        if is_week:
            return handle_post_week(ctx)
        # "post chey" is a declared word of /post but had no branch here, so it fell through to
        # "Ardam kaaledu". It means: give me today's posts to post.
        if is_today or norm in ("/post today", "post today", "/post", "post", "today posts", "ee roju posts",
                                "post chey", "/post chey", "post cheyali", "posts chey"):
            return handle_post_today(ctx)

    # Specific slash commands
    if norm.startswith("/today"):
        import selltest_today
        checklist = selltest_today.checklist(ctx.get('db_path'),ctx.get('products_dir'),ctx.get('now'))
        if checklist: return {'text': checklist}
        return handle_post_today(ctx)

    if norm.startswith("/tomorrow"):
        return handle_post_tomorrow(ctx)

    if norm.startswith("/week"):
        return handle_post_week(ctx)

    return None
