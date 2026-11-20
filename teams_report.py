#!/usr/bin/env python3
"""CCH-1: the /teams report — what each team really made in the last 7 days.

Teams: Pinterest, Instagram, Marketing, Product, Research. Every number comes from a real file or a real
database row; a team with no output shows 0 and its percent shows "-" (never a made-up number).

Sources
  Pinterest  ready Pins: <products>/marketing/<pack>/pinterest/pins.json (made by pin_ready, checker ok,
             PNG files present); post plan rows with channel "pinterest"
  Instagram  ready carousels: <products>/marketing/<pack>/carousel/carousel.json (checker ok, slides
             present); post plan rows with channel "carousel" / "instagram" / "reel"
  Marketing  the post plan (exported_content/products/post_plan.json): posts planned / posted by hand
             ("/posted N"); rows the owner did not post in time are "missed" (counted as due, not done),
             marketing_ready rows (packs with marketing files)
  Product    product_builds (built / needs fix), approvals kind public_draft (drafts waiting for the owner)
  Research   scout_ideas rows, proposals (approvals that are not product drafts)

Percent per team (only when there is something to divide by):
  Pinterest / Instagram  READY packs that have ready Pins / a ready carousel
  Marketing              posts due in the last 7 days that the owner marked posted
  Product                products built / (built + needs fix) in the last 7 days
  Research               proposals the owner decided that were approved (last 7 days)

Nothing is posted or sent from here; tg_teams.py shows the text to the owner.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent
PRODUCTS_DIR = REPO_ROOT / "exported_content" / "products"
PLAN_PATH = PRODUCTS_DIR / "post_plan.json"     # post_planner.PLAN_PATH (CCH-4: saved with the products)
DAYS = 7
TEAMS = ("pinterest", "instagram", "marketing", "product", "research")
TEAM_LABELS = {"pinterest": "Pinterest", "instagram": "Instagram", "marketing": "Marketing",
               "product": "Product", "research": "Research"}
PIN_CHANNELS = ("pinterest",)
INSTA_CHANNELS = ("carousel", "instagram", "reel")
PRODUCT_KIND = "public_draft"
PINS_FOLDER = "pinterest"
PINS_FILE = "pins.json"
CAROUSEL_FOLDER = "carousel"
CAROUSEL_FILE = "carousel.json"


# ---- small helpers -----------------------------------------------------------
def _utc_naive(now: Optional[datetime.datetime]) -> datetime.datetime:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return now


def _parse_ts(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        ts = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            ts = datetime.datetime.combine(datetime.date.fromisoformat(str(value)[:10]), datetime.time())
        except ValueError:
            return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return ts


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pct(part: int, whole: int) -> Optional[int]:
    return int(round(100.0 * part / whole)) if whole > 0 else None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _count(conn: sqlite3.Connection, table: str, sql: str, args: tuple = ()) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(sql, args).fetchone()
    return int(row[0] or 0) if row else 0


def _connect(db_path=None) -> sqlite3.Connection:
    import guardrails
    return guardrails.connect(db_path)


# ---- files: ready Pins and carousels ----------------------------------------------
def marketing_root(products_dir: Optional[Path | str] = None) -> Path:
    return Path(products_dir or PRODUCTS_DIR) / "marketing"


def ready_pins(pack_marketing_dir: Path) -> Dict[str, Any]:
    """Ready Pins of one pack: pins.json says the checker passed and every PNG it names exists."""
    data = _read_json(pack_marketing_dir / PINS_FOLDER / PINS_FILE)
    if not isinstance(data, dict) or not (data.get("check") or {}).get("ok"):
        return {"count": 0, "made_at": None}
    pins = [p for p in data.get("pins") or [] if isinstance(p, dict)
            and (pack_marketing_dir / PINS_FOLDER / str(p.get("file") or "")).is_file()
            and str(p.get("file") or "")]
    if len(pins) != len(data.get("pins") or []):
        return {"count": 0, "made_at": None}           # a file went missing: not ready
    return {"count": len(pins), "made_at": _parse_ts(data.get("made_at"))}


def ready_carousel(pack_marketing_dir: Path) -> Dict[str, Any]:
    """Ready carousel of one pack: carousel.json says the checker passed and every slide exists."""
    folder = pack_marketing_dir / CAROUSEL_FOLDER
    data = _read_json(folder / CAROUSEL_FILE)
    if not isinstance(data, dict) or not (data.get("check") or {}).get("ok"):
        return {"count": 0, "slides": 0, "made_at": None}
    slides = [s for s in data.get("slides") or [] if isinstance(s, dict)]
    if not slides or not all((folder / str(s.get("file") or "x/")).is_file() for s in slides):
        return {"count": 0, "slides": 0, "made_at": None}
    return {"count": 1, "slides": len(slides), "made_at": _parse_ts(data.get("made_at"))}


def scan_marketing(products_dir: Optional[Path | str] = None) -> Dict[str, Dict[str, Any]]:
    """{pack folder: {"pins": {...}, "carousel": {...}}} for every pack marketing folder."""
    root = marketing_root(products_dir)
    found: Dict[str, Dict[str, Any]] = {}
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.is_symlink() and not child.name.startswith("."):
            found[child.name] = {"pins": ready_pins(child), "carousel": ready_carousel(child)}
    return found


# ---- the post plan -------------------------------------------------------------
def load_plan(plan_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    data = _read_json(Path(plan_path or PLAN_PATH))
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _posted_at(row: Dict[str, Any]) -> Optional[datetime.datetime]:
    """When the owner marked the post: 'posted_at', else the planned day of a 'done' row."""
    if row.get("status") != "done":
        return None
    return _parse_ts(row.get("posted_at")) or _parse_ts(row.get("date"))


def plan_numbers(plan: List[Dict[str, Any]], channels: Optional[tuple], now: datetime.datetime) -> Dict[str, int]:
    """Counts of plan rows of these channels (None = all): planned (todo, today or later), posted in the last
    7 days, due in the last 7 days (up to today) and how many of those are done, overdue (todo before today)."""
    today = now.date()
    since = now - datetime.timedelta(days=DAYS)
    first_due = today - datetime.timedelta(days=DAYS - 1)
    out = {"planned": 0, "posted": 0, "due": 0, "due_done": 0, "overdue": 0, "today_todo": 0}
    for row in plan:
        if channels is not None and str(row.get("channel") or "") not in channels:
            continue
        day = _parse_ts(row.get("date"))
        day = day.date() if day else None
        done = row.get("status") == "done"
        posted = _posted_at(row)
        if posted is not None and posted >= since.replace(hour=0, minute=0, second=0, microsecond=0):
            out["posted"] += 1
        if day is None:
            continue
        if not done and day >= today:
            out["planned"] += 1
        if not done and day == today:
            out["today_todo"] += 1
        if row.get("status") == "todo" and day < today:
            out["overdue"] += 1
        if first_due <= day <= today:
            out["due"] += 1
            out["due_done"] += int(done)
    return out


# ---- the database ------------------------------------------------------------------
def ready_pack_folders(db_path=None) -> List[str]:
    """Folders of READY, owner-approved packs (the packs that can get Pins and carousels)."""
    conn = _connect(db_path)
    try:
        if not _table_exists(conn, "product_deliveries"):
            return []
        rows = conn.execute("SELECT d.folder FROM product_deliveries d JOIN approvals a ON a.id = d.approval_id "
                            "WHERE d.status='ready' AND a.status='approved' AND COALESCE(d.folder,'') != '' "
                            "ORDER BY d.approval_id").fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def db_numbers(db_path=None, now: Optional[datetime.datetime] = None) -> Dict[str, int]:
    now = _utc_naive(now)
    since = (now - datetime.timedelta(days=DAYS)).isoformat()
    conn = _connect(db_path)
    try:
        return {
            "built": _count(conn, "product_builds",
                            "SELECT COUNT(*) FROM product_builds WHERE status='built' AND ts >= ?", (since,)),
            "needs_fix": _count(conn, "product_builds",
                                "SELECT COUNT(*) FROM product_builds WHERE status IN ('needs_fix','retry','failed') "
                                "AND ts >= ?", (since,)),
            "drafts_made": _count(conn, "approvals", "SELECT COUNT(*) FROM approvals WHERE kind=? AND ts >= ?",
                                  (PRODUCT_KIND, since)),
            "drafts_waiting": _count(conn, "approvals",
                                     "SELECT COUNT(*) FROM approvals WHERE kind=? AND status='pending'",
                                     (PRODUCT_KIND,)),
            "ideas": _count(conn, "scout_ideas", "SELECT COUNT(*) FROM scout_ideas WHERE ts >= ?", (since,)),
            "proposals_made": _count(conn, "approvals",
                                     "SELECT COUNT(*) FROM approvals WHERE kind != ? AND ts >= ?",
                                     (PRODUCT_KIND, since)),
            "proposals_waiting": _count(conn, "approvals",
                                        "SELECT COUNT(*) FROM approvals WHERE kind != ? AND status='pending'",
                                        (PRODUCT_KIND,)),
            "proposals_decided": _count(conn, "approvals",
                                        "SELECT COUNT(*) FROM approvals WHERE kind != ? AND status IN "
                                        "('approved','rejected') AND decided_ts >= ?", (PRODUCT_KIND, since)),
            "proposals_approved": _count(conn, "approvals",
                                         "SELECT COUNT(*) FROM approvals WHERE kind != ? AND status='approved' "
                                         "AND decided_ts >= ?", (PRODUCT_KIND, since)),
            "marketing_packs": _count(conn, "marketing_ready",
                                      "SELECT COUNT(*) FROM marketing_ready WHERE status IN ('ready','partial') "
                                      "AND ts >= ?", (since,)),
        }
    finally:
        conn.close()


# ---- the report --------------------------------------------------------------------
def team_stats(db_path=None, products_dir: Optional[Path | str] = None, plan_path: Optional[Path | str] = None,
               now: Optional[datetime.datetime] = None) -> Dict[str, Dict[str, Any]]:
    """{team: {"made": {...}, "waiting": {...}, "percent": int|None, "percent_of": str, "next": str}}."""
    now = _utc_naive(now)
    since = now - datetime.timedelta(days=DAYS)
    files = scan_marketing(products_dir)
    plan = load_plan(plan_path)
    db = db_numbers(db_path, now)
    ready = ready_pack_folders(db_path)

    def fresh(info):
        return info.get("made_at") is not None and info["made_at"] >= since

    pins_made = sum(v["pins"]["count"] for v in files.values() if fresh(v["pins"]))
    pin_packs = [name for name, v in files.items() if v["pins"]["count"]]
    car_made = sum(v["carousel"]["count"] for v in files.values() if fresh(v["carousel"]))
    slides_made = sum(v["carousel"]["slides"] for v in files.values() if fresh(v["carousel"]))
    car_packs = [name for name, v in files.items() if v["carousel"]["count"]]
    pin_plan = plan_numbers(plan, PIN_CHANNELS, now)
    insta_plan = plan_numbers(plan, INSTA_CHANNELS, now)
    all_plan = plan_numbers(plan, None, now)
    ready_with_pins = sum(1 for f in ready if f in pin_packs)
    ready_with_car = sum(1 for f in ready if f in car_packs)

    stats: Dict[str, Dict[str, Any]] = {}

    # Pinterest
    if not ready:
        nxt = "Inka READY pack ledu: mundu oka product approve cheyandi."
    elif ready_with_pins < len(ready):
        nxt = f"{len(ready) - ready_with_pins} READY pack(s) ki inka ready Pins levu: robot next run lo chestundi."
    elif pin_plan["today_todo"]:
        nxt = f"Ee roju {pin_plan['today_todo']} Pin(s) post cheyandi: /post today, tarvata /posted N."
    else:
        nxt = "Ee roju Pin pani ledu."
    stats["pinterest"] = {
        "made": {"pins": pins_made, "packs_with_pins": len(pin_packs)},
        "planned": pin_plan["planned"], "posted": pin_plan["posted"],
        "waiting": {"to_post_today": pin_plan["today_todo"], "overdue": pin_plan["overdue"]},
        "percent": _pct(ready_with_pins, len(ready)), "percent_of": "READY packs with ready Pins",
        "next": nxt}

    # Instagram
    if not ready:
        nxt = "Inka READY pack ledu: mundu oka product approve cheyandi."
    elif ready_with_car < len(ready):
        nxt = (f"{len(ready) - ready_with_car} READY pack(s) ki inka ready carousel ledu: "
               "robot next run lo chestundi.")
    elif insta_plan["today_todo"]:
        nxt = f"Ee roju {insta_plan['today_todo']} Instagram post cheyandi: /post today, tarvata /posted N."
    else:
        nxt = "Ee roju Instagram pani ledu."
    stats["instagram"] = {
        "made": {"carousels": car_made, "slides": slides_made, "packs_with_carousel": len(car_packs)},
        "planned": insta_plan["planned"], "posted": insta_plan["posted"],
        "waiting": {"to_post_today": insta_plan["today_todo"], "overdue": insta_plan["overdue"]},
        "percent": _pct(ready_with_car, len(ready)), "percent_of": "READY packs with a ready carousel",
        "next": nxt}

    # Marketing (the plan the owner follows by hand)
    if not plan:
        nxt = "Post plan inka ledu: ready Pins / carousel unna pack vachaka robot plan chestundi."
    elif all_plan["today_todo"]:
        nxt = f"Ee roju {all_plan['today_todo']} post(s) undi: /post today chusi post cheyandi, tarvata /posted N."
    elif all_plan["overdue"]:
        nxt = f"{all_plan['overdue']} paata post(s) post cheyaledu: veelaite /post week chudandi."
    else:
        nxt = "Ee roju post pani ledu."
    stats["marketing"] = {
        "made": {"posts_planned": all_plan["planned"], "posts_posted": all_plan["posted"],
                 "packs_with_marketing": db["marketing_packs"]},
        "planned": all_plan["planned"], "posted": all_plan["posted"],
        "waiting": {"to_post_today": all_plan["today_todo"], "overdue": all_plan["overdue"]},
        "percent": _pct(all_plan["due_done"], all_plan["due"]), "percent_of": "posts due this week marked posted",
        "next": nxt}

    # Product
    if db["drafts_waiting"]:
        nxt = f"{db['drafts_waiting']} product draft(s) meeru chudali: /list."
    elif db["needs_fix"]:
        nxt = f"{db['needs_fix']} product(s) fix kavali: /doctor chudandi."
    elif not db["built"]:
        nxt = "Ee vaaram product build avvaledu: draft approve cheste robot build chestundi."
    else:
        nxt = "Product pani ok: READY packs ni store lo upload cheyandi."
    stats["product"] = {
        "made": {"packs_built": db["built"], "drafts_made": db["drafts_made"], "needs_fix": db["needs_fix"]},
        "waiting": {"drafts": db["drafts_waiting"]},
        "percent": _pct(db["built"], db["built"] + db["needs_fix"]), "percent_of": "builds that worked",
        "next": nxt}

    # Research
    if db["proposals_waiting"]:
        nxt = f"{db['proposals_waiting']} idea / proposal(s) meeru decide cheyali: /list."
    elif not (db["ideas"] or db["proposals_made"]):
        nxt = "Ee vaaram kotha ideas raledu: robot niche agent next night run lo try chestundi."
    else:
        nxt = "Research pani ok: kotha ideas vachinappudu /list lo chustaru."
    stats["research"] = {
        "made": {"ideas": db["ideas"], "proposals": db["proposals_made"]},
        "waiting": {"proposals": db["proposals_waiting"]},
        "percent": _pct(db["proposals_approved"], db["proposals_decided"]),
        "percent_of": "decided proposals you approved",
        "next": nxt}
    return stats


def _made_text(team: str, s: Dict[str, Any]) -> str:
    m = s["made"]
    if team == "pinterest":
        return (f"{m['pins']} Pins made, {s['planned']} planned, {s['posted']} posted "
                f"({m['packs_with_pins']} pack(s) with ready Pins)")
    if team == "instagram":
        return (f"{m['carousels']} carousel(s) / {m['slides']} slides made, {s['planned']} planned, "
                f"{s['posted']} posted")
    if team == "marketing":
        return (f"{m['posts_planned']} posts planned, {m['posts_posted']} marked posted, "
                f"{m['packs_with_marketing']} pack(s) got marketing files")
    if team == "product":
        return f"{m['packs_built']} packs built, {m['drafts_made']} drafts made, {m['needs_fix']} need fix"
    return f"{m['ideas']} ideas, {m['proposals']} proposals"


def _waiting_text(team: str, s: Dict[str, Any]) -> str:
    w = s["waiting"]
    if team in ("pinterest", "instagram", "marketing"):
        return f"{w['to_post_today']} to post today, {w['overdue']} overdue"
    if team == "product":
        return f"{w['drafts']} draft(s) for you"
    return f"{w['proposals']} proposal(s) for you"


def report_text(stats: Optional[Dict[str, Dict[str, Any]]] = None, team: Optional[str] = None, **kw: Any) -> str:
    """Owner text (Tenglish, no amounts). `team` limits it to one team."""
    stats = stats if stats is not None else team_stats(**kw)
    names = [team] if team in TEAMS else list(TEAMS)
    lines = [f"Teams report (last {DAYS} rojulu) — real files / DB nunchi matrame:"]
    for name in names:
        s = stats[name]
        pct = f"{s['percent']}%" if s["percent"] is not None else "-"
        lines += ["", f"{TEAM_LABELS[name]}: {pct} ({s['percent_of']})",
                  f"  Chesindi: {_made_text(name, s)}",
                  f"  Meekosam waiting: {_waiting_text(name, s)}",
                  f"  Next step: {s['next']}"]
    lines += ["", "Robot edi post cheyadu — meere post chesi /posted N pampandi."]
    return "\n".join(lines)


def team_of(word: str) -> Optional[str]:
    word = (word or "").strip().lower()
    aliases = {"pin": "pinterest", "pins": "pinterest", "pinterest": "pinterest",
               "insta": "instagram", "instagram": "instagram", "ig": "instagram", "carousel": "instagram",
               "marketing": "marketing", "posts": "marketing", "product": "product", "products": "product",
               "research": "research", "ideas": "research"}
    return aliases.get(word)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="What each team made in the last 7 days.")
    parser.add_argument("--team", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    stats = team_stats(db_path=args.db)
    if args.json:
        print(json.dumps(stats, indent=2, default=str))
    else:
        print(report_text(stats, team=team_of(args.team or "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
