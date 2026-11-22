"""Telegram plugin: tg_launch.py — CCA-3: "/launch N" = the 7-day launch plan of pack N.

  /launch N           the week at a glance + today's post (image, caption to copy, where to share)
  /launch N day K     the post of day K (1-7)
  /launch N restart   start the week again from today
  /launch             ready packs and which launch day each is on

Also plain words: "launch plan 3", "pack 3 launch plan", "3 launch plan".

The older "/launch N" of tg_files (send LAUNCH.md, the pre-launch checklist) stays reachable as
"launch checklist N" / "launch N pampu"; this plugin has PRIORITY so the slash command shows the plan
and mentions the checklist when the file exists.

Rules kept:
  - Plugin interface: COMMANDS + handle(text, ctx). Sends only through ctx callbacks.
  - telegram_inbox is never imported here.
  - Nothing is posted, published or scheduled: every post is done by the owner by hand.
  - No money amounts. Only image files inside the pack's marketing folder (or the pack's cover) are sent.
"""
from __future__ import annotations

from tg_menu import owner_reply

import datetime
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import launch_plan

REPO_ROOT = Path(__file__).resolve().parent
PRIORITY = 10          # telegram_inbox.call_plugins tries higher numbers first (default 0)

COMMANDS = [
    {
        "cmd": "/launch",
        "words": ["launch plan", "launch week", "7 day plan"],
        "help_en": "7-day launch plan after listing on Gumroad: today's post, caption, where to share (/launch N)",
        "help_te": "Gumroad lo pettaka 7 rojula plan: ee roju post, caption, ekkada share cheyali (/launch N)",
    },
]

STATE_START = "launch_start:{}"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_ID = r"(?:pack\s+)?#?(\d{1,9})"
_PATTERNS = [
    ("restart", re.compile(rf"^/launch(?:@\w+)?\s+{_ID}\s+(?:restart|reset|malli|start)$", re.IGNORECASE)),
    ("day", re.compile(rf"^/launch(?:@\w+)?\s+{_ID}\s+(?:day\s*)?([1-9]\d?)$", re.IGNORECASE)),
    ("day", re.compile(rf"^launch\s+plan\s+{_ID}\s+day\s*([1-9]\d?)$", re.IGNORECASE)),
    ("day", re.compile(rf"^{_ID}\s+launch\s+(?:plan\s+)?day\s*([1-9]\d?)$", re.IGNORECASE)),
    ("plan", re.compile(rf"^/launch(?:@\w+)?\s+{_ID}$", re.IGNORECASE)),
    ("plan", re.compile(rf"^(?:launch\s+plan|launch\s+week|7\s+day\s+plan)\s+{_ID}$", re.IGNORECASE)),
    ("plan", re.compile(rf"^{_ID}\s+(?:ki\s+)?(?:launch\s+plan|launch\s+week|7\s+day\s+plan)(?:\s+(?:chupinchu|pampu|ivvu|kavali))?$",
                        re.IGNORECASE)),
]
_LIST = re.compile(r"^(?:/launch(?:@\w+)?|launch\s+plan|launch\s+week|7\s+day\s+plan)$", re.IGNORECASE)


def parse(text: Any) -> Optional[Tuple[str, Optional[int], Optional[int]]]:
    """("plan", N, None), ("day", N, K), ("restart", N, None), ("list", None, None) or None."""
    if not isinstance(text, str):
        return None
    raw = " ".join(text.strip().split()).rstrip("?.! ").strip()
    if not raw or len(raw) > 80:
        return None
    if _LIST.match(raw):
        return "list", None, None
    for action, pattern in _PATTERNS:
        match = pattern.match(raw)
        if match:
            day = int(match.group(2)) if action == "day" else None
            return action, int(match.group(1)), day
    return None


# ---- folders -------------------------------------------------------------------------
def products_dir(ctx: Optional[Dict[str, Any]] = None) -> Path:
    value = (ctx or {}).get("products_dir") or os.environ.get("CHILLI_PRODUCTS_DIR")
    return Path(value).resolve() if value else REPO_ROOT / "exported_content" / "products"


def marketing_roots(ctx: Optional[Dict[str, Any]] = None) -> List[Path]:
    """Folders that can hold <pack>/ marketing files: override, private (marketing_auto), shared."""
    roots: List[Path] = []
    value = (ctx or {}).get("marketing_dir") or os.environ.get("CHILLI_MARKETING_DIR")
    if value:
        roots.append(Path(value).resolve())
    roots.append(products_dir(ctx) / "marketing")
    roots.append(REPO_ROOT / "exported_content" / "marketing")
    return roots


def find_pack(pack_id: int, ctx: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    import sell_ready
    for number, _title, path in sell_ready.find_all_ready_packs(products_dir(ctx)):
        if number == pack_id:
            return Path(path)
    return None


def find_marketing(pack_dir: Path, pack_id: int, ctx: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """The pack's marketing folder: same folder name, or any '<id>-...' folder."""
    for root in marketing_roots(ctx):
        if not root.is_dir():
            continue
        exact = root / pack_dir.name
        if exact.is_dir():
            return exact
        try:
            for child in sorted(root.iterdir()):
                if child.is_dir() and (child.name == str(pack_id) or child.name.startswith(f"{pack_id}-")):
                    return child
        except OSError:
            continue
    return None


# ---- dates and saved state -------------------------------------------------------------
def today_ist(ctx: Optional[Dict[str, Any]] = None) -> datetime.date:
    now = (ctx or {}).get("now")
    if not isinstance(now, datetime.datetime):
        now = datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(IST).date()


def listed_info(pack_id: int, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Link and date from /listed (tg_listed), when that plugin is there and the pack is listed."""
    try:
        import tg_listed
        return tg_listed.get_listed(pack_id, ctx.get("db_path"))
    except Exception:
        return None


def _parse_date(value: Any) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def start_date(pack_id: int, ctx: Dict[str, Any], create: bool = False) -> Optional[datetime.date]:
    """Day 1 of pack N's launch week: saved by /launch, else the /listed date; None = not started."""
    state_get, state_set = ctx.get("state_get"), ctx.get("state_set")
    saved = None
    if callable(state_get):
        try:
            saved = _parse_date(state_get(STATE_START.format(pack_id), None))
        except Exception:
            saved = None
    if saved is not None:
        return saved
    listed = listed_info(pack_id, ctx)
    found = _parse_date(listed.get("listed_ts")) if listed else None
    if found is None and create:
        found = today_ist(ctx)
    if found is not None and create and callable(state_set):
        try:
            state_set(STATE_START.format(pack_id), found.isoformat())
        except Exception:
            pass
    return found


def set_start(pack_id: int, ctx: Dict[str, Any], day: datetime.date) -> None:
    state_set = ctx.get("state_set")
    if callable(state_set):
        state_set(STATE_START.format(pack_id), day.isoformat())


# ---- replies -----------------------------------------------------------------------------
def plan_for(pack_id: int, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pack_dir = find_pack(pack_id, ctx)
    if pack_dir is None:
        return None
    listed = listed_info(pack_id, ctx)
    return launch_plan.build_plan(pack_dir, find_marketing(pack_dir, pack_id, ctx),
                                  (listed or {}).get("url"), pack_id)


def _safe_asset(path: Optional[Path], pack_id: int, ctx: Dict[str, Any]) -> Optional[Path]:
    """Only a real image inside a marketing folder or the pack folder is ever sent."""
    if path is None:
        return None
    try:
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in launch_plan.IMAGE_SUFFIXES:
            return None
        real = path.resolve()
        allowed = [root.resolve() for root in marketing_roots(ctx)] + [products_dir(ctx).resolve()]
        return real if any(root in real.parents for root in allowed) else None
    except OSError:
        return None


def _send_day(plan: Dict[str, Any], day: int, ctx: Dict[str, Any], intro: str = "") -> Dict[str, Any]:
    card = launch_plan.day_card(plan, day)
    text = (intro + "\n\n" + card) if intro else card
    send_text, send_photo = ctx.get("send_text"), ctx.get("send_photo")
    asset = _safe_asset(plan["days"][day - 1]["asset"], plan["pack_id"], ctx)
    if not callable(send_text):
        return {"text": text}
    if intro:
        send_text(intro)
    if asset is not None and callable(send_photo):
        ok = send_photo(str(asset), caption=f"Pack #{plan['pack_id']} Day {day} image: {asset.name}")
        if ok is False and callable(ctx.get("send_document")):
            ctx["send_document"](str(asset), caption=f"Pack #{plan['pack_id']} Day {day} image: {asset.name}")
    number = plan["pack_id"]
    keyboard = [[f"/launch {number} day {other}" for other in (day - 1, day + 1) if 1 <= other <= launch_plan.DAYS],
                [f"/launch {number}"]]
    send_text(card, keyboard=keyboard)
    return {"handled": True}


def _checklist_hint(pack_id: int, ctx: Dict[str, Any]) -> str:
    pack_dir = find_pack(pack_id, ctx)
    folder = find_marketing(pack_dir, pack_id, ctx) if pack_dir else None
    if folder is not None and (folder / "LAUNCH.md").is_file():
        return f"Pre-launch checklist file (LAUNCH.md) kavali ante: launch checklist {pack_id}"
    return ""


def handle_plan(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    plan = plan_for(pack_id, ctx)
    if plan is None:
        return {"text": f"Pack #{pack_id} ready upload pack dorakaledu. Packs kosam /launch leda /sell pampandi."}
    start = start_date(pack_id, ctx, create=True)
    today = launch_plan.day_number(start, today_ist(ctx))
    overview = launch_plan.overview_text(plan, today if 1 <= today <= launch_plan.DAYS else None)
    extra = [f"Launch start: {start.isoformat()}"]
    if not plan["store_url"]:
        extra.append(f"Gumroad lo pettaka link pampandi: /listed {pack_id} <link> (appudu captions lo link vastundi).")
    hint = _checklist_hint(pack_id, ctx)
    if hint:
        extra.append(hint)
    if today > launch_plan.DAYS:
        extra.append(f"Launch week aipoyindi (Day {today}). Malli start cheyadaniki: /launch {pack_id} restart. "
                     "Roju posts kosam /today chudandi.")
        return {"text": launch_plan.clean(overview + "\n\n" + "\n".join(extra))}
    if today < 1:
        extra.append(f"Launch inka start avvaledu. Day 1 chudadaniki: /launch {pack_id} day 1")
        return {"text": launch_plan.clean(overview + "\n\n" + "\n".join(extra))}
    extra.append(f"Ee roju: Day {today}/{launch_plan.DAYS}.")
    return _send_day(plan, today, ctx, intro=launch_plan.clean(overview + "\n\n" + "\n".join(extra)))


def handle_day(pack_id: int, day: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    if not 1 <= day <= launch_plan.DAYS:
        return {"text": f"Day 1 nundi {launch_plan.DAYS} varaku matrame. Udaharanaki: /launch {pack_id} day 1"}
    plan = plan_for(pack_id, ctx)
    if plan is None:
        return {"text": f"Pack #{pack_id} ready upload pack dorakaledu. Packs kosam /launch pampandi."}
    return _send_day(plan, day, ctx)


def handle_restart(pack_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    if find_pack(pack_id, ctx) is None:
        return {"text": f"Pack #{pack_id} ready upload pack dorakaledu. Packs kosam /launch pampandi."}
    today = today_ist(ctx)
    set_start(pack_id, ctx, today)
    return {"text": f"Pack #{pack_id} launch week malli start chesanu: Day 1 = {today.isoformat()}. "
                    f"Ee roju post kosam: /launch {pack_id}"}


def launch_status(pack_id: int, ctx: Dict[str, Any]) -> Tuple[str, Optional[int]]:
    """("not_started" | "running" | "done", today's day number or None) without changing anything."""
    start = start_date(pack_id, ctx, create=False)
    if start is None:
        return "not_started", None
    day = launch_plan.day_number(start, today_ist(ctx))
    if day < 1:
        return "not_started", None
    return ("running", day) if day <= launch_plan.DAYS else ("done", day)


def handle_list(ctx: Dict[str, Any]) -> Dict[str, Any]:
    import sell_ready
    packs = [(number, title) for number, title, _path in sell_ready.find_all_ready_packs(products_dir(ctx))
             if number is not None]
    if not packs:
        return {"text": "Ready upload packs inka levu. Drafts approve cheste robot packs build chestundi (/list)."}
    lines = ["7 rojula launch plan — packs:", ""]
    for number, title in packs[:20]:
        status, day = launch_status(number, ctx)
        listed = listed_info(number, ctx) is not None
        if status == "running":
            state = f"Day {day}/{launch_plan.DAYS} — ee roju post: /launch {number}"
        elif status == "done":
            state = f"launch week aipoyindi — malli: /launch {number} restart"
        elif listed:
            state = f"listed, plan start cheyandi: /launch {number}"
        else:
            state = f"inka list cheyaledu (/sell {number}); plan chudadaniki: /launch {number}"
        lines.append(f"#{number} {title[:50]} — {state}")
    if len(packs) > 20:
        lines.append(f"... inko {len(packs) - 20} packs")
    lines += ["", "Robot edi post cheyadu; roju ki okka post meere cheyali."]
    text = launch_plan.clean("\n".join(lines))
    send_text = ctx.get("send_text")
    if callable(send_text):
        ids = [number for number, _ in packs[:9]]
        send_text(text, keyboard=[[f"/launch {number}" for number in ids[row:row + 3]] for row in range(0, len(ids), 3)])
        return {"handled": True}
    return {"text": text}


@owner_reply
def handle(text: str, ctx: dict) -> Optional[dict]:
    """Standard Telegram plugin entry point."""
    parsed = parse(text)
    if parsed is None:
        return None
    ctx = ctx if isinstance(ctx, dict) else {}
    action, pack_id, day = parsed
    try:
        if action == "list":
            return handle_list(ctx)
        if action == "restart":
            return handle_restart(pack_id, ctx)
        if action == "day":
            return handle_day(pack_id, day, ctx)
        return handle_plan(pack_id, ctx)
    except Exception as error:
        return {"text": "/launch lo plan chadavaledu.\nNext: /doctor tho problem chudandi."}
