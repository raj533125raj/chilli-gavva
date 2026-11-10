"""weekly_routine.py — Sunday routine for Chilli Gavva robot (BINNY-V5B).

On Sunday (India time) once per week:
  1. make_marketing --all (time budget 4 min, skip if missing or timed out)
  2. newsletter draft (newsletter.make_weekly_newsletter)
  3. calendar_export (calendar_export.export_calendar)
  4. ops_report (ops_report.generate_ops_summary)
  5. pack_changelog for all ready packs (pack_changelog.process_all_ready_packs)
  6. dup_check (dup_check.check_duplicate_content)

Returns a short Tenglish summary for the Sunday letter:
  'Ee vaaram: 3 packs ki marketing ready, newsletter draft ready, 1 duplicate warning'

Persists once-per-week memory in the state store so it never runs twice on the same Sunday.
"""
from __future__ import annotations

import datetime
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PRODUCTS_DIR = REPO_ROOT / "exported_content" / "products"
DEFAULT_MARKETING_DIR = REPO_ROOT / "exported_content" / "marketing"
IST = ZoneInfo("Asia/Kolkata")
STATE_KEY = "weekly_routine_last_week"
MAKE_MARKETING_TIMEOUT_SECONDS = 240  # 4 minutes

# Regex to detect and sanitize any accidental currency / money amounts
MONEY_RE = re.compile(r"(\$|₹|£|€|\bUSD\b|\bINR\b|\bEUR\b|\bGBP\b|\bRs\.?\b|\bRupees\b)\s*\d+(?:\.\d+)?", re.IGNORECASE)


def sanitize_no_money(text: str) -> str:
    """Strip or redact currency patterns if present."""
    if not text:
        return ""
    return MONEY_RE.sub("[amount]", str(text))


def _now_utc() -> datetime.datetime:
    """Return current UTC datetime (tz-aware)."""
    return datetime.datetime.now(datetime.timezone.utc)


def to_ist(dt: Optional[datetime.datetime] = None) -> datetime.datetime:
    """Convert datetime to IST timezone. If naive, assume UTC."""
    if dt is None:
        dt = _now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(IST)


def is_sunday_ist(dt: Optional[datetime.datetime] = None) -> bool:
    """Check if given datetime is Sunday in IST (weekday == 6)."""
    return to_ist(dt).weekday() == 6


def get_week_key(dt: Optional[datetime.datetime] = None) -> str:
    """Generate ISO calendar week key based on IST date (e.g. '2026-W45')."""
    ist_dt = to_ist(dt)
    cal = ist_dt.isocalendar()
    return f"{cal[0]}-W{cal[1]:02d}"


def get_state(key: str, default: Optional[str] = None, db_path: Optional[str] = None) -> Optional[str]:
    """Retrieve key from telegram_state table."""
    try:
        import telegram_inbox
        return telegram_inbox.get_state(key, default=default, db_path=db_path)
    except Exception:
        pass
    if not db_path:
        return default
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM telegram_state WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default


def set_state(key: str, value: str, db_path: Optional[str] = None) -> None:
    """Store key in telegram_state table."""
    try:
        import telegram_inbox
        telegram_inbox.set_state(key, value, db_path=db_path)
        return
    except Exception:
        pass
    if not db_path:
        return
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO telegram_state (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
        conn.commit()
        conn.close()
    except Exception:
        pass


def run_make_marketing(
    packs_dir: Optional[Path | str] = None,
    marketing_dir: Optional[Path | str] = None,
    time_budget: int = MAKE_MARKETING_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Run make_marketing --all within time budget (default 4 min). Skip if missing."""
    result: Dict[str, Any] = {
        "status": "skipped",
        "ready_count": 0,
        "processed_count": 0,
        "errors": [],
        "elapsed": 0.0,
    }
    start_time = time.monotonic()
    try:
        import make_marketing
    except ImportError:
        result["status"] = "missing"
        result["errors"].append("make_marketing module missing")
        return result
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"make_marketing import error: {exc}")
        return result

    if packs_dir:
        p = Path(packs_dir)
        packs_p = p / "upload" if (p / "upload").is_dir() else p
    else:
        packs_p = DEFAULT_PRODUCTS_DIR / "upload"
    mkt_p = Path(marketing_dir) if marketing_dir else DEFAULT_MARKETING_DIR

    try:
        makers = make_marketing.load_makers()
        if not packs_p.is_dir():
            result["status"] = "ok"
            result["elapsed"] = time.monotonic() - start_time
            return result

        candidates = sorted(p for p in packs_p.iterdir() if p.is_dir())
        ready_packs = []
        for pack in candidates:
            row = make_marketing.report_row(pack)
            if row.get("ready"):
                ready_packs.append(pack)
        result["ready_count"] = len(ready_packs)

        results = []
        for pack in ready_packs:
            # Check elapsed time before processing each pack
            if (time.monotonic() - start_time) > time_budget:
                result["errors"].append(f"time budget exceeded ({time_budget}s), skipped remaining packs")
                result["status"] = "timed_out"
                break
            pack_res = make_marketing.process_pack(pack, mkt_p, makers)
            make_marketing.write_readme(mkt_p, pack_res["id"], pack_res)
            results.append(pack_res)

        result["processed_count"] = len(results)
        if result["status"] != "timed_out":
            result["status"] = "ok"
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"make_marketing error: {exc}")

    result["elapsed"] = time.monotonic() - start_time
    return result


def run_newsletter(
    products_dir: Optional[Path | str] = None,
    marketing_dir: Optional[Path | str] = None,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Generate weekly email newsletter draft. Skip if missing."""
    result: Dict[str, Any] = {"status": "skipped", "week": "", "files": [], "errors": []}
    try:
        import newsletter
    except ImportError:
        result["status"] = "missing"
        result["errors"].append("newsletter module missing")
        return result
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"newsletter import error: {exc}")
        return result

    p_dir = products_dir or DEFAULT_PRODUCTS_DIR
    m_dir = marketing_dir or DEFAULT_MARKETING_DIR
    target_d = to_ist(now).date() if now else None

    try:
        res = newsletter.make_weekly_newsletter(products_dir=p_dir, marketing_dir=m_dir, target_date=target_d)
        result["status"] = "ok"
        result["week"] = res.get("week", "")
        result["files"] = [str(res.get("md_file", "")), str(res.get("txt_file", ""))]
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"newsletter error: {exc}")

    return result


def run_calendar_export(
    plan_path: Optional[Path | str] = None,
    marketing_dir: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Export calendar post plan. Skip if missing."""
    result: Dict[str, Any] = {"status": "skipped", "events_count": 0, "errors": []}
    try:
        import calendar_export
    except ImportError:
        result["status"] = "missing"
        result["errors"].append("calendar_export module missing")
        return result
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"calendar_export import error: {exc}")
        return result

    plan_file = Path(plan_path) if plan_path else REPO_ROOT / "exported_content" / "post_plan.json"
    m_dir = Path(marketing_dir) if marketing_dir else DEFAULT_MARKETING_DIR
    ics_file = m_dir / "post_plan.ics"
    csv_file = m_dir / "post_plan.csv"

    try:
        if not plan_file.is_file():
            # If no post plan exists yet, try creating an empty one or export with empty plan
            res = calendar_export.export_calendar(plan=[], ics_path=ics_file, csv_path=csv_file)
        else:
            res = calendar_export.export_calendar(plan_path=plan_file, ics_path=ics_file, csv_path=csv_file)
        result["status"] = "ok"
        result["events_count"] = res.get("events_count", 0)
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"calendar_export error: {exc}")

    return result


def run_ops_report(
    db_path: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
    products_dir: Optional[str] = None,
    marketing_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate weekly ops report. Skip if missing."""
    result: Dict[str, Any] = {"status": "skipped", "summary": {}, "errors": []}
    try:
        import ops_report
    except ImportError:
        result["status"] = "missing"
        result["errors"].append("ops_report module missing")
        return result
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"ops_report import error: {exc}")
        return result

    try:
        summary = ops_report.generate_ops_summary(
            db_path=db_path,
            now=now,
            days=7,
            products_dir=products_dir,
            marketing_dir=marketing_dir,
        )
        result["status"] = "ok"
        result["summary"] = summary
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"ops_report error: {exc}")

    return result


def run_pack_changelog(
    products_dir: Optional[Path | str] = None,
    versions_dir: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Generate changelogs for all ready packs. Skip if missing."""
    result: Dict[str, Any] = {"status": "skipped", "packs_processed": 0, "errors": []}
    try:
        import pack_changelog
    except ImportError:
        result["status"] = "missing"
        result["errors"].append("pack_changelog module missing")
        return result
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"pack_changelog import error: {exc}")
        return result

    p_dir = Path(products_dir) if products_dir else DEFAULT_PRODUCTS_DIR
    v_dir = Path(versions_dir) if versions_dir else DEFAULT_PRODUCTS_DIR / "versions"

    try:
        packs = pack_changelog.process_all_ready_packs(products_dir=p_dir, versions_dir=v_dir)
        result["status"] = "ok"
        result["packs_processed"] = len(packs)
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"pack_changelog error: {exc}")

    return result


def run_dup_check(
    products_dir: Optional[Path | str] = None,
    marketing_dir: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Run duplicate content check across packs. Skip if missing."""
    result: Dict[str, Any] = {
        "status": "skipped",
        "warnings_count": 0,
        "pairs_above_50": 0,
        "has_high_similarity": False,
        "errors": [],
    }
    try:
        import dup_check
    except ImportError:
        result["status"] = "missing"
        result["errors"].append("dup_check module missing")
        return result
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"dup_check import error: {exc}")
        return result

    p_dir = Path(products_dir) if products_dir else DEFAULT_PRODUCTS_DIR
    m_dir = Path(marketing_dir) if marketing_dir else DEFAULT_MARKETING_DIR

    try:
        res = dup_check.check_duplicate_content(
            products_dir=p_dir,
            marketing_dir=m_dir,
            out_dir=m_dir,
        )
        pairs = res.get("pairs_above_50", [])
        warnings = len(pairs)
        result["status"] = "ok"
        result["warnings_count"] = warnings
        result["pairs_above_50"] = len(pairs)
        result["has_high_similarity"] = bool(res.get("has_high_similarity", False))
    except Exception as exc:
        result["status"] = "error"
        result["errors"].append(f"dup_check error: {exc}")

    return result


def compose_tenglish_summary(
    marketing_res: Dict[str, Any],
    newsletter_res: Dict[str, Any],
    calendar_res: Dict[str, Any],
    ops_res: Dict[str, Any],
    changelog_res: Dict[str, Any],
    dup_res: Dict[str, Any],
) -> str:
    """Format short Tenglish summary for Sunday letter.

    Example:
    'Ee vaaram: 3 packs ki marketing ready, newsletter draft ready, 1 duplicate warning'
    """
    parts: List[str] = []

    # 1. Marketing part
    if marketing_res.get("status") == "ok":
        p_count = marketing_res.get("processed_count", 0)
        r_count = marketing_res.get("ready_count", 0)
        count = p_count if p_count > 0 else r_count
        parts.append(f"{count} packs ki marketing ready")
    elif marketing_res.get("status") == "timed_out":
        p_count = marketing_res.get("processed_count", 0)
        parts.append(f"{p_count} packs ki marketing partial ready (timed out)")
    else:
        parts.append("marketing skipped")

    # 2. Newsletter part
    if newsletter_res.get("status") == "ok":
        parts.append("newsletter draft ready")
    else:
        parts.append("newsletter skipped")

    # 3. Duplicate check warnings part
    dup_warnings = dup_res.get("warnings_count", 0)
    if dup_res.get("status") == "ok":
        if dup_warnings == 1:
            parts.append("1 duplicate warning")
        elif dup_warnings > 1:
            parts.append(f"{dup_warnings} duplicate warnings")
        else:
            parts.append("no duplicate warnings")
    else:
        parts.append("dup check skipped")

    summary = f"Ee vaaram: {', '.join(parts)}"
    return sanitize_no_money(summary)


def run(
    now: Optional[datetime.datetime] = None,
    db_path: Optional[str] = None,
    force: bool = False,
    products_dir: Optional[Path | str] = None,
    marketing_dir: Optional[Path | str] = None,
    time_budget: int = MAKE_MARKETING_TIMEOUT_SECONDS,
) -> Optional[str]:
    """Execute Sunday weekly routine if today is Sunday in IST and not run yet this week.

    Returns the short Tenglish summary string if executed, or None if skipped.
    Guaranteed not to raise unhandled exceptions.
    """
    now_dt = now or _now_utc()

    # Must be Sunday in IST unless forced (for testing)
    if not force and not is_sunday_ist(now_dt):
        return None

    # Check once-per-week memory in state store
    week_key = get_week_key(now_dt)
    last_run_week = get_state(STATE_KEY, default=None, db_path=db_path)
    if not force and last_run_week == week_key:
        return None

    try:
        # Run the 6 routine jobs
        mkt_res = run_make_marketing(packs_dir=products_dir, marketing_dir=marketing_dir, time_budget=time_budget)
        news_res = run_newsletter(products_dir=products_dir, marketing_dir=marketing_dir, now=now_dt)
        cal_res = run_calendar_export(marketing_dir=marketing_dir)
        ops_res = run_ops_report(db_path=db_path, now=now_dt, products_dir=str(products_dir) if products_dir else None, marketing_dir=str(marketing_dir) if marketing_dir else None)
        clog_res = run_pack_changelog(products_dir=products_dir)
        dup_res = run_dup_check(products_dir=products_dir, marketing_dir=marketing_dir)

        summary = compose_tenglish_summary(
            marketing_res=mkt_res,
            newsletter_res=news_res,
            calendar_res=cal_res,
            ops_res=ops_res,
            changelog_res=clog_res,
            dup_res=dup_res,
        )

        # Record run in state store
        set_state(STATE_KEY, week_key, db_path=db_path)
        return summary
    except Exception as exc:
        # Never crash the caller
        fallback = f"Ee vaaram: routine error ({type(exc).__name__})"
        return sanitize_no_money(fallback)
