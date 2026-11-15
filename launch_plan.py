#!/usr/bin/env python3
"""launch_plan.py — CCA-3: a 7-day launch plan for one pack, after the owner listed it on Gumroad.

One post per day for seven days, as a Tenglish to-do for the owner:
  - which image to post (a REAL file from the pack's marketing folder: Pins, square posts, stories),
  - the caption, ready to copy (from pins.json / posts.json / captions.txt made by the marketing makers),
  - where to share it and the clicks to do it.

Nothing here posts, publishes, schedules or sends anything: the owner does every post by hand.
The plan only names files that exist; when the marketing folder has no image for a day, the day
becomes a text post (or uses the pack's own cover) and says how to get the images made.

Functions
  collect_assets(marketing_pack_dir)                        real images + their captions
  build_plan(pack_dir, marketing_pack_dir, store_url, ...)  the 7 days as plain dicts
  overview_text(plan) / day_card(plan, day)                 Tenglish text for Telegram
  day_number(start_date, today)                             which launch day "today" is

CLI (prints only):  python3 launch_plan.py <pack folder> [--marketing <folder>] [--day N]
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DAYS = 7
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
MAX_CAPTION = 900
MAX_HASHTAGS = 8
LINK_PLACEHOLDER = "<mee Gumroad link ikkada paste cheyandi>"
MONEY_RE = re.compile(
    r"(?:[$₹€£]\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:USD|INR|EUR|GBP|dollars?|rupees?|cents?)\b|\bRs\.?\s?\d[\d.,]*)",
    re.IGNORECASE)
BRAND_WORDS = {"etsy", "amazon", "canva", "notion", "gumroad", "google", "microsoft", "excel", "goodnotes",
               "notability", "ipad", "kindle", "pinterest", "instagram", "facebook", "tiktok", "youtube"}

# The week: what kind of post each day wants, and why (shown to the owner in Tenglish).
WEEK = [
    {"day": 1, "kind": "pin", "theme": "Launch day",
     "goal": "Pack live ayyindi ani cheppe modati Pin. Gumroad page okasari check cheyandi."},
    {"day": 2, "kind": "pin", "theme": "Lopala em undi",
     "goal": "Pack lopala unna pages / files chupinche Pin."},
    {"day": 3, "kind": "post", "theme": "Evari kosam",
     "goal": "Ee pack evariki use avutundo cheppe square post."},
    {"day": 4, "kind": "pin", "theme": "Ela vadali",
     "goal": "Ela use cheyalo chupinche Pin (kotha design, kotha title)."},
    {"day": 5, "kind": "story", "theme": "Story reminder",
     "goal": "Story lo chinna reminder. Link sticker pettandi."},
    {"day": 6, "kind": "pin", "theme": "Inko angle",
     "goal": "Vere title tho inko Pin — Pinterest lo kotha Pins ki ekkuva reach vastundi."},
    {"day": 7, "kind": "pin", "theme": "Week review",
     "goal": "Chivari Pin + ee vaaram em pani chesindo chusi next plan decide cheyandi."},
]
WHERE = {
    "pin": ["Pinterest lo 'Create' > 'Create Pin' click cheyandi.",
            "Image upload chesi, kinda unna Title ni Title box lo, caption ni Description box lo paste cheyandi.",
            "'Link' box lo mee Gumroad product link paste cheyandi.",
            "Board select chesi 'Publish' meere click cheyandi."],
    "post": ["Instagram leda Facebook page lo kotha post start cheyandi.",
             "Square image upload chesi caption paste cheyandi.",
             "Link ni bio lo leda first comment lo pettandi (Instagram caption lo link click avvadu).",
             "'Share' meere click cheyandi."],
    "story": ["Instagram Story leda WhatsApp Status open cheyandi.",
              "Story image upload cheyandi.",
              "Link sticker add chesi Gumroad link paste cheyandi.",
              "'Share' meere click cheyandi."],
    "text": ["Image lekunda text post: WhatsApp Status, Facebook page leda X lo caption paste cheyandi.",
             "Chivarlo Gumroad link undela chusukondi.",
             "'Post' meere click cheyandi."],
}
KIND_LABEL = {"pin": "Pinterest Pin", "post": "Square post (Instagram / Facebook)", "story": "Story (Instagram / WhatsApp)",
              "text": "Text post"}


def clean(text: Any) -> str:
    """No money amounts ever leave this module."""
    return MONEY_RE.sub("[amount]", str(text))


def _natural(path: Path) -> List[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def _read_json_list(path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def parse_captions_file(path: Path) -> List[Dict[str, str]]:
    """captions.txt (made by marketing_auto) -> [{"title", "english", "tenglish", "alt", "hashtags"}]."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    blocks: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    keys = {"title": "title", "english": "english", "tenglish": "tenglish", "alt text": "alt", "hashtags": "hashtags"}
    for line in text.splitlines():
        if line.startswith("== ") and line.rstrip().endswith("=="):
            current = {}
            blocks.append(current)
            continue
        if current is None or ":" not in line:
            continue
        label, _, value = line.partition(":")
        key = keys.get(label.strip().lower())
        if key and value.strip():
            current[key] = value.strip()
    return [block for block in blocks if block]


def _images(folder: Path, prefix: str = "") -> List[Path]:
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        try:
            if (path.is_file() and not path.is_symlink() and path.suffix.lower() in IMAGE_SUFFIXES
                    and path.name.lower().startswith(prefix)):
                found.append(path)
        except OSError:
            continue
    return sorted(found, key=_natural)


def _asset(path: Path, kind: str, meta: Dict[str, Any], fallback: Dict[str, str]) -> Dict[str, Any]:
    caption = (meta.get("caption_en") or fallback.get("english") or meta.get("description") or "")
    return {
        "kind": kind, "path": path, "name": path.name,
        "title": str(meta.get("title") or fallback.get("title") or "").strip(),
        "caption": str(caption).strip(),
        "caption_te": str(meta.get("caption_te") or fallback.get("tenglish") or "").strip(),
        "alt": str(meta.get("alt") or fallback.get("alt") or "").strip(),
        "hashtags": str(fallback.get("hashtags") or "").strip(),
    }


def collect_assets(marketing_pack_dir: Optional[Path]) -> Dict[str, Any]:
    """Real images in the pack's marketing folder, each with the caption its maker wrote.

    {"pins": [...], "posts": [...], "stories": [...], "landing": bool, "faq": bool, "captions_file": bool}
    """
    empty = {"pins": [], "posts": [], "stories": [], "landing": False, "faq": False, "captions_file": False}
    if marketing_pack_dir is None or not Path(marketing_pack_dir).is_dir():
        return empty
    base = Path(marketing_pack_dir)
    blocks = parse_captions_file(base / "captions.txt")
    pin_meta = {str(item.get("file")): item for item in _read_json_list(base / "pins" / "pins.json")}
    post_meta = {str(item.get("file")): item for item in _read_json_list(base / "posts" / "posts.json")}
    pins = []
    for index, path in enumerate(_images(base / "pins")):
        fallback = blocks[index % len(blocks)] if blocks else {}
        pins.append(_asset(path, "pin", pin_meta.get(path.name, {}), fallback))
    posts, stories = [], []
    for path in _images(base / "posts"):
        kind = "story" if path.name.lower().startswith("story") else "post"
        (stories if kind == "story" else posts).append(_asset(path, kind, post_meta.get(path.name, {}),
                                                              blocks[0] if blocks else {}))
    return {"pins": pins, "posts": posts, "stories": stories,
            "landing": (base / "landing" / "index.html").is_file(), "faq": (base / "FAQ.md").is_file(),
            "captions_file": bool(blocks)}


def listing_facts(pack_dir: Path) -> Dict[str, Any]:
    """Title, first sentence and tags from the pack's own listing.txt."""
    pack_dir = Path(pack_dir)
    facts = {"title": pack_dir.name, "sentence": "", "tags": []}
    try:
        text = (pack_dir / "listing.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return facts
    match = re.search(r"^Title:\s*(.+)$", text, re.MULTILINE)
    if match:
        facts["title"] = match.group(1).strip()
    match = re.search(r"^Description:\s*(.*?)(?=\n[A-Za-z][A-Za-z /_-]*:|\Z)", text, re.DOTALL | re.MULTILINE)
    if match:
        description = " ".join(match.group(1).split())
        first = re.match(r"(.+?[.!?])(?:\s|$)", description)
        facts["sentence"] = (first.group(1) if first else description)[:220]
    match = re.search(r"^Tags:\s*(.+)$", text, re.MULTILINE)
    if match:
        facts["tags"] = [tag.strip() for tag in re.split(r"[,;]", match.group(1)) if tag.strip()]
    return facts


def hashtags_from(tags: List[str], existing: str = "") -> str:
    """Up to 8 clean hashtags (letters and digits only, no brand names, no duplicates)."""
    found: List[str] = []
    for raw in re.findall(r"#\w+", existing) + list(tags):
        word = re.sub(r"[^A-Za-z0-9]", "", raw)
        if not word or len(word) > 30 or word.lower() in BRAND_WORDS or MONEY_RE.search(raw):
            continue
        if any(brand in word.lower() for brand in BRAND_WORDS):
            continue
        tag = "#" + word.lower()
        if tag not in found:
            found.append(tag)
        if len(found) == MAX_HASHTAGS:
            break
    return " ".join(found)


def _caption(asset: Optional[Dict[str, Any]], facts: Dict[str, Any], day: Dict[str, Any], link: str) -> str:
    """The text the owner copies: the maker's caption (or a plain one from the listing), link, hashtags."""
    body = (asset or {}).get("caption", "")
    if not body or MONEY_RE.search(body):
        body = facts["title"] + (". " + facts["sentence"] if facts["sentence"] else ".")
    body = body[:MAX_CAPTION].rstrip()
    tags = hashtags_from(facts["tags"], (asset or {}).get("hashtags", ""))
    lines = [body, "", f"Link: {link}"]
    if tags:
        lines += ["", tags]
    return clean("\n".join(lines))


def _pick(pools: Dict[str, List[Dict[str, Any]]], kind: str, used: List[str]) -> Optional[Dict[str, Any]]:
    """Next unused image of the wanted kind, else of any kind, else (when all are used) the least used."""
    order = [kind] + [other for other in ("pin", "post", "story") if other != kind]
    for name in order:
        for asset in pools[name]:
            if str(asset["path"]) not in used:
                return asset
    everything = [asset for name in order for asset in pools[name]]
    if not everything:
        return None
    return min(everything, key=lambda asset: used.count(str(asset["path"])))


def build_plan(pack_dir: Path, marketing_pack_dir: Optional[Path] = None, store_url: Optional[str] = None,
               pack_id: Optional[int] = None) -> Dict[str, Any]:
    """The 7-day plan as plain data. Only real files are named."""
    pack_dir = Path(pack_dir)
    facts = listing_facts(pack_dir)
    assets = collect_assets(marketing_pack_dir)
    pools = {"pin": assets["pins"], "post": assets["posts"], "story": assets["stories"]}
    link = store_url.strip() if isinstance(store_url, str) and store_url.strip() else LINK_PLACEHOLDER
    if pack_id is None:
        match = re.match(r"^(\d+)", pack_dir.name)
        pack_id = int(match.group(1)) if match else None
    cover = pack_dir / "cover.png"
    used: List[str] = []
    days = []
    for template in WEEK:
        asset = _pick(pools, template["kind"], used)
        note = ""
        if asset is not None:
            if str(asset["path"]) in used:
                note = "Ee image mundu vadinde (kotha images levu). Caption konchem marchi post cheyandi."
            used.append(str(asset["path"]))
            kind, path, name = asset["kind"], asset["path"], asset["name"]
        elif cover.is_file() and not cover.is_symlink():
            kind, path, name = template["kind"], cover, cover.name
            note = "Marketing images levu, anduke pack cover vadutunnam."
        else:
            kind, path, name = "text", None, ""
            note = "Image ledu — ee roju text post."
        days.append({
            "day": template["day"], "theme": template["theme"], "goal": template["goal"], "kind": kind,
            "channel": KIND_LABEL[kind], "asset": path, "asset_name": name,
            "title": clean((asset or {}).get("title") or facts["title"])[:100],
            "caption": _caption(asset, facts, template, link),
            "alt": clean((asset or {}).get("alt", ""))[:400],
            "where": list(WHERE[kind]), "note": note,
        })
    missing = []
    if not assets["pins"]:
        missing.append("Pins")
    if not assets["posts"] and not assets["stories"]:
        missing.append("square posts / stories")
    return {"pack_id": pack_id, "pack_name": pack_dir.name, "title": clean(facts["title"]), "store_url": store_url or "",
            "link": link, "days": days, "missing": missing,
            "counts": {"pins": len(assets["pins"]), "posts": len(assets["posts"]), "stories": len(assets["stories"])},
            "has_landing": assets["landing"], "has_faq": assets["faq"]}


def day_number(start_date: datetime.date, today: datetime.date) -> int:
    """1 on the start date, 2 the next day ... (0 or less = not started, 8+ = week over)."""
    return (today - start_date).days + 1


def _label(plan: Dict[str, Any]) -> str:
    number = f"#{plan['pack_id']}" if plan.get("pack_id") is not None else plan["pack_name"]
    return f"Pack {number}"


def overview_text(plan: Dict[str, Any], today_day: Optional[int] = None) -> str:
    """The whole week in a few lines."""
    number = plan.get("pack_id")
    lines = [f"🚀 {_label(plan)} — 7 rojula launch plan", f"Title: {plan['title']}",
             f"Gumroad link: {plan['store_url'] or 'inka ivvaledu (' + LINK_PLACEHOLDER + ')'}", "",
             "Roju ki okka post. Robot edi post cheyadu — prati post meere cheyali.", ""]
    for day in plan["days"]:
        mark = "👉" if today_day == day["day"] else "•"
        what = day["asset_name"] or "text post"
        lines.append(f"{mark} Day {day['day']} — {day['theme']}: {day['channel']} ({what})")
    lines.append("")
    counts = plan["counts"]
    lines.append(f"Marketing folder lo: {counts['pins']} Pins, {counts['posts']} posts, {counts['stories']} stories.")
    if plan["missing"]:
        tail = f"/marketing {number}" if number is not None else "/marketing N"
        lines.append(f"Levu: {', '.join(plan['missing'])}. Images kosam {tail} chudandi.")
    if number is not None:
        lines.append(f"Okka roju details: /launch {number} day 1 ... day 7")
    return clean("\n".join(lines))


def day_card(plan: Dict[str, Any], day: int) -> str:
    """One day's to-do, in Tenglish, with the caption ready to copy."""
    if not 1 <= day <= DAYS:
        raise ValueError(f"day must be 1..{DAYS}")
    item = plan["days"][day - 1]
    lines = [f"📅 {_label(plan)} — Day {day}/{DAYS}: {item['theme']}", item["goal"], "",
             f"Em post cheyali: {item['channel']}"]
    if item["asset_name"]:
        lines.append(f"Image: {item['asset_name']}")
    if item["note"]:
        lines.append(f"Note: {item['note']}")
    if item["kind"] == "pin":
        lines += ["", "TITLE (copy cheyandi):", item["title"]]
    lines += ["", "CAPTION (copy cheyandi):", item["caption"]]
    if item["alt"]:
        lines += ["", "ALT TEXT:", item["alt"]]
    lines += ["", "Ekkada, ela:"] + [f"[ ] {index}. {step}" for index, step in enumerate(item["where"], 1)]
    if day == 1:
        lines.append(f"[ ] {len(item['where']) + 1}. Gumroad product page open chesi title, images, files correct ga unnayo chudandi.")
    if day == DAYS:
        lines.append(f"[ ] {len(item['where']) + 1}. Ee vaaram e post ki ekkuva clicks vachayo chusi, adi malli cheyandi.")
    if plan["link"] == LINK_PLACEHOLDER:
        number = plan.get("pack_id")
        tail = f"/listed {number} <link>" if number is not None else "/listed N <link>"
        lines += ["", f"Gumroad link inka robot ki teliyadu. Caption lo link meere paste cheyandi (leda {tail})."]
    lines += ["", "Robot edi post cheyadu. Post chesaka tarvata roju repu."]
    return clean("\n".join(lines))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Print the 7-day launch plan of one pack (nothing is posted).")
    parser.add_argument("pack", help="pack folder (with listing.txt)")
    parser.add_argument("--marketing", help="the pack's marketing folder", default=None)
    parser.add_argument("--link", help="the Gumroad product link", default=None)
    parser.add_argument("--day", type=int, default=None, help="print only this day (1-7)")
    args = parser.parse_args(argv)
    pack = Path(args.pack)
    if not pack.is_dir():
        print(f"Pack folder dorakaledu: {pack}")
        return 1
    plan = build_plan(pack, Path(args.marketing) if args.marketing else None, args.link)
    if args.day is not None:
        if not 1 <= args.day <= DAYS:
            print(f"--day 1 nundi {DAYS} varaku undali")
            return 2
        print(day_card(plan, args.day))
    else:
        print(overview_text(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
