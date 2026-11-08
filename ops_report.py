"""ops_report.py — Weekly ops summary from the robot's own state.

Read-only: reads the sqlite state db through existing helper functions / guardrails.connect.
Never decrypts anything.

Metrics gathered:
  - runs per day (from heartbeat or tasks/provider_log/usage_log activity)
  - failures by step (from tasks where status='failed' grouped by kind/step)
  - retries (queued tasks with error or attempts > 0)
  - provider up/down counts (from provider_log ok/fail totals)
  - drafts made / approved / rejected (from approvals table)
  - packs ready (under products_dir/upload/ with product.zip and listing.txt)
  - marketing files made (under marketing_dir: carousels, pins, seo_meta, blog, etc.)

Outputs:
  - Structured JSON dict
  - Formatted Tenglish text <= 3000 chars

CLI:
  python3 ops_report.py [--json] [--days 7] [--db-path PATH]
"""

import argparse
import datetime
import json
import os
import pathlib
import sqlite3
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import guardrails

IST = ZoneInfo("Asia/Kolkata")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _q(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> List[Any]:
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _count(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    rows = _q(conn, sql, args)
    return int(rows[0][0] or 0) if rows else 0


def count_ready_upload_packs(products_dir: Optional[str] = None) -> int:
    """Count folders under products_dir/upload/ containing product.zip and listing.txt."""
    if products_dir is None:
        try:
            import product_builder
            products_dir = product_builder.PRODUCTS_DIR
        except Exception:
            products_dir = "exported_content/products"
    upload_dir = pathlib.Path(products_dir) / "upload"
    if not upload_dir.is_dir():
        return 0
    count = 0
    try:
        entries = sorted(upload_dir.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_dir() and (entry / "product.zip").is_file() and (entry / "listing.txt").is_file():
                count += 1
        except OSError:
            continue
    return count


def count_marketing_files(marketing_dir: Optional[str] = None) -> Dict[str, Any]:
    """Scan marketing_dir (default: exported_content/marketing) and count generated files."""
    if marketing_dir is None:
        marketing_dir = "exported_content/marketing"
    base = pathlib.Path(marketing_dir)
    counts = {
        "total_files": 0,
        "carousels": 0,
        "pins": 0,
        "seo_meta": 0,
        "reels": 0,
        "blog_posts": 0,
        "keywords": 0,
        "by_pack": {},
    }
    if not base.is_dir():
        return counts

    try:
        for p in base.rglob("*"):
            if p.is_file():
                counts["total_files"] += 1
                name = p.name.lower()
                rel_parts = p.relative_to(base).parts
                if len(rel_parts) > 1:
                    pack_id = rel_parts[0]
                    counts["by_pack"][pack_id] = counts["by_pack"].get(pack_id, 0) + 1

                if "carousel" in [part.lower() for part in rel_parts]:
                    counts["carousels"] += 1
                elif "pins" in [part.lower() for part in rel_parts]:
                    counts["pins"] += 1
                elif "reel" in [part.lower() for part in rel_parts]:
                    counts["reels"] += 1
                elif name == "seo_meta.json":
                    counts["seo_meta"] += 1
                elif name.endswith(".md") and "blog" in name:
                    counts["blog_posts"] += 1
                elif name == "keywords.json":
                    counts["keywords"] += 1
    except OSError:
        pass

    return counts


def get_runs_per_day(conn: sqlite3.Connection, since_iso: str, days: int = 7) -> Dict[str, int]:
    """Calculate robot runs or task executions per day over the given window."""
    # First try heartbeat if it logs multiple records or machine activity
    # In SQLite, tasks table ts gives task run volume per day
    runs_by_day: Dict[str, int] = {}
    rows = _q(conn, "SELECT SUBSTR(ts, 1, 10) AS d, COUNT(*) FROM tasks WHERE ts >= ? GROUP BY d ORDER BY d", (since_iso,))
    for d, c in rows:
        if d:
            runs_by_day[str(d)] = int(c)

    # If no tasks or sparse tasks, check usage_log
    if not runs_by_day:
        since_day = since_iso[:10]
        u_rows = _q(conn, "SELECT day, llm_calls FROM usage_log WHERE day >= ? ORDER BY day", (since_day,))
        for d, c in u_rows:
            if d:
                runs_by_day[str(d)] = int(c)

    return runs_by_day


def get_failures_by_step(conn: sqlite3.Connection, since_iso: str) -> Dict[str, int]:
    """Calculate failed tasks grouped by kind/step."""
    rows = _q(conn, "SELECT kind, COUNT(*) FROM tasks WHERE status = 'failed' AND (finished_ts >= ? OR ts >= ?) GROUP BY kind ORDER BY COUNT(*) DESC", (since_iso, since_iso))
    return {str(k or "unknown"): int(c) for k, c in rows}


def get_retries_count(conn: sqlite3.Connection) -> int:
    """Queued tasks with previous error or attempts > 0."""
    return _count(conn, "SELECT COUNT(*) FROM tasks WHERE status = 'queued' AND (error IS NOT NULL OR attempts > 0)")


def get_provider_stats(conn: sqlite3.Connection, since_day: str) -> Dict[str, Dict[str, int]]:
    """Get provider up/down counts (ok vs fail) from provider_log."""
    rows = _q(conn, "SELECT provider, SUM(ok), SUM(fail) FROM provider_log WHERE day >= ? GROUP BY provider ORDER BY provider", (since_day,))
    stats: Dict[str, Dict[str, int]] = {}
    for p, ok, fail in rows:
        p_name = str(p or "unknown")
        stats[p_name] = {
            "ok": int(ok or 0),
            "fail": int(fail or 0),
            "status": "UP" if (ok or 0) >= (fail or 0) else "DOWN",
        }
    return stats


def get_draft_stats(conn: sqlite3.Connection, since_iso: str) -> Dict[str, int]:
    """Get count of drafts made, approved, and rejected in window."""
    made = _count(conn, "SELECT COUNT(*) FROM approvals WHERE ts >= ?", (since_iso,))
    approved = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'approved' AND decided_ts >= ?", (since_iso,))
    rejected = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'rejected' AND decided_ts >= ?", (since_iso,))
    pending = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'pending'")
    return {
        "made": made,
        "approved": approved,
        "rejected": rejected,
        "pending": pending,
    }


def generate_ops_summary(
    db_path: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
    days: int = 7,
    products_dir: Optional[str] = None,
    marketing_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate structured ops summary data dict."""
    now = now or _now()
    since_dt = now - datetime.timedelta(days=days)
    since_iso = since_dt.isoformat()
    since_day = since_dt.date().isoformat()

    conn = guardrails.connect(db_path)
    try:
        runs_per_day = get_runs_per_day(conn, since_iso, days=days)
        failures_by_step = get_failures_by_step(conn, since_iso)
        retries = get_retries_count(conn)
        providers = get_provider_stats(conn, since_day)
        drafts = get_draft_stats(conn, since_iso)
    finally:
        conn.close()

    packs_ready = count_ready_upload_packs(products_dir)
    marketing = count_marketing_files(marketing_dir)

    total_failures = sum(failures_by_step.values())

    return {
        "period_days": days,
        "generated_at": now.isoformat(),
        "runs_per_day": runs_per_day,
        "failures": {
            "total": total_failures,
            "by_step": failures_by_step,
        },
        "retries_queued": retries,
        "providers": providers,
        "drafts": drafts,
        "packs_ready": packs_ready,
        "marketing_files": marketing,
    }


def format_ops_text(data: Dict[str, Any]) -> str:
    """Format ops summary as Tenglish text lines (max 3000 chars)."""
    days = data.get("period_days", 7)
    now_str = data.get("generated_at", "")[:10]

    lines = [
        f"🤖 Chilli Gavva Weekly Ops Summary ({days} days varaku - {now_str})",
        "--------------------------------------------------",
    ]

    # Runs per day
    r_by_day = data.get("runs_per_day", {})
    if r_by_day:
        runs_str = ", ".join(f"{d[-5:]}: {c} runs" for d, c in sorted(r_by_day.items())[-7:])
        lines.append(f"📅 Runs per day: {runs_str}")
    else:
        lines.append("📅 Runs per day: Tasks record avvaledu / calm ga undi")

    # Failures by step & retries
    fails = data.get("failures", {})
    total_fails = fails.get("total", 0)
    by_step = fails.get("by_step", {})
    retries = data.get("retries_queued", 0)

    if total_fails == 0 and retries == 0:
        lines.append("✅ Failures & Retries: Anni smooth ga run ayyayi, zero failures!")
    else:
        step_parts = [f"{s} ({c})" for s, c in by_step.items()]
        step_summary = ", ".join(step_parts) if step_parts else "none"
        lines.append(f"⚠️ Failures ({total_fails}): {step_summary}")
        lines.append(f"🔄 Retries queued: {retries} tasks waiting for retry")

    # Providers
    providers = data.get("providers", {})
    if providers:
        p_parts = []
        for p, pdata in providers.items():
            status_tag = "UP" if pdata.get("status") == "UP" else "DOWN"
            p_parts.append(f"{p} {status_tag} (ok: {pdata.get('ok', 0)}, fail: {pdata.get('fail', 0)})")
        lines.append("🔌 AI Providers: " + "; ".join(p_parts))
    else:
        lines.append("🔌 AI Providers: Last 7 days lo direct AI usage log ledu")

    # Drafts
    drafts = data.get("drafts", {})
    lines.append(
        f"📝 Drafts: {drafts.get('made', 0)} made, {drafts.get('approved', 0)} approved, "
        f"{drafts.get('rejected', 0)} rejected (ippudu pending: {drafts.get('pending', 0)})"
    )

    # Packs & Marketing
    packs = data.get("packs_ready", 0)
    lines.append(f"📦 Upload packs ready: {packs} packs Gumroad/Topmate kosam ready")

    mkt = data.get("marketing_files", {})
    total_mkt = mkt.get("total_files", 0)
    lines.append(
        f"🎨 Marketing assets made: {total_mkt} files "
        f"(carousels: {mkt.get('carousels', 0)}, pins: {mkt.get('pins', 0)}, "
        f"reels: {mkt.get('reels', 0)}, blogs: {mkt.get('blog_posts', 0)})"
    )

    lines.append("--------------------------------------------------")
    lines.append("Owner ki tip: Next batch ready cheyadaniki packs & drafts review cheskondi.")

    text = "\n".join(lines)
    if len(text) > 3000:
        text = text[:2997] + "..."
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Chilli Gavva weekly ops report.")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON.")
    parser.add_argument("--days", type=int, default=7, help="Number of days to summarize (default 7).")
    parser.add_argument("--db-path", default=None, help="Path to sqlite database.")
    parser.add_argument("--products-dir", default=None, help="Path to products directory.")
    parser.add_argument("--marketing-dir", default=None, help="Path to marketing directory.")
    args = parser.parse_args()

    data = generate_ops_summary(
        db_path=args.db_path,
        days=args.days,
        products_dir=args.products_dir,
        marketing_dir=args.marketing_dir,
    )

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(format_ops_text(data))


if __name__ == "__main__":
    main()
