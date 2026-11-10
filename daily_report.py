"""daily_report.py — one short Telegram health report a day, plus a weekly letter on Sundays.

Sent by the Telegram step of the cloud run (telegram_inbox.run) to the owner chat only:
  - once a day, on the first run after 03:30 UTC
  - plain text, short: AI providers OK/FAIL, drafts made / waiting / approved, products built,
    GitHub minutes used this month, errors
  - on Sundays a weekly letter is added: made, approved, rejected (top reasons), real earnings only

Nothing here is sent to an AI model.
"""
import datetime
import os
import pathlib
import sqlite3
from zoneinfo import ZoneInfo

import guardrails
import daily_receipts

SEND_AFTER = datetime.time(3, 30)   # UTC
IST = ZoneInfo("Asia/Kolkata")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _q(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []   # table not created yet on a fresh database


def _count(conn, sql, args=()):
    rows = _q(conn, sql, args)
    return int(rows[0][0] or 0) if rows else 0


def _providers(conn, since_day):
    rows = _q(conn, "SELECT provider, SUM(ok), SUM(fail) FROM provider_log WHERE day >= ? GROUP BY provider "
                    "ORDER BY provider", (since_day,))
    if not rows:
        return "AI: not used in the last day"
    parts = [f"{p} {'OK' if ok else 'FAIL'} ({ok} ok, {fail} failed)" for p, ok, fail in rows]
    return "AI: " + "; ".join(parts)


def _clock_status(status_fn=None):
    try:
        if status_fn is None:
            import always_on
            status_fn = always_on.status
        return status_fn() or {}
    except Exception:
        return {}


def door_bell_line(reply):
    """Door bell on/off from the alarm clock status (never shows the token)."""
    if "door_bell" not in reply:
        return "Door bell: unknown (alarm clock not reachable)"
    if not reply.get("door_bell"):
        return "Door bell: off"
    if reply.get("telegram_token_ok") == "no":
        return "Door bell: on, but Telegram does NOT accept its bot token (update it in Cloudflare)"
    return "Door bell: on"


def _minutes(status_fn=None, reply=None):
    reply = reply if reply is not None else _clock_status(status_fn)
    if "month_minutes" not in reply:
        return "GitHub minutes this month: unknown (alarm clock not reachable)"
    return f"GitHub minutes this month: {reply['month_minutes']} of 2000 free ({reply.get('month_runs', '?')} runs)"


def _count_ready_upload_packs(products_dir=None):
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


def upload_packs_line(products_dir=None):
    """One safe status line showing count of ready upload packs."""
    n = _count_ready_upload_packs(products_dir)
    if n > 0:
        return f"Upload packs ready: {n} (send /packs to see them)"
    return "Upload packs ready: 0"


def daily_text(db_path=None, now=None, status_fn=None, products_dir=None):
    now = now or _now()
    since = (now - datetime.timedelta(days=1)).isoformat()
    conn = guardrails.connect(db_path)
    made = _count(conn, "SELECT COUNT(*) FROM approvals WHERE ts >= ?", (since,))
    waiting = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'pending'")
    approved = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'approved' AND decided_ts >= ?", (since,))
    built = _count(conn, "SELECT COUNT(*) FROM product_builds WHERE status = 'built' AND ts >= ?", (since,))
    failed = _count(conn, "SELECT COUNT(*) FROM tasks WHERE status = 'failed' AND finished_ts >= ?", (since,))
    retries = _count(conn, "SELECT COUNT(*) FROM tasks WHERE status = 'queued' AND error IS NOT NULL")
    providers = _providers(conn, (now - datetime.timedelta(days=1)).date().isoformat())
    buttons = _count(conn, "SELECT COUNT(*) FROM button_log WHERE ts >= ?", (since,))
    auto_rejected = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'rejected' AND decided_ts >= ? "
                                 "AND reason = 'Low score (auto)'", (since,))
    auto_approved = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'approved' AND decided_ts >= ? "
                                 "AND (reason = 'auto-approved' OR reason IS NULL)", (since,))
    built_rows = _q(conn, "SELECT pb.approval_id, pb.folder, a.title, a.status "
                          "FROM product_builds pb LEFT JOIN approvals a ON a.id = pb.approval_id "
                          "WHERE pb.status = 'built' AND pb.ts >= ? "
                          "ORDER BY pb.ts DESC, pb.approval_id DESC LIMIT 5", (since,))
    paused = guardrails.is_paused(db_path)
    conn.close()

    built_titles = []
    for aid, folder, title, status in built_rows:
        if status == "pending":
            label = f"Draft #{aid}" if aid is not None else "Draft"
        else:
            label = (title or folder or (f"Draft #{aid}" if aid is not None else "Product")).strip()
        built_titles.append(label[:40])
    built_line = "Built products (last 24h): " + (", ".join(built_titles) if built_titles else "none")

    if waiting > 0:
        next_step = "Next step for owner: approve or reject drafts in /list"
    elif built > 0:
        next_step = "Next step for owner: check upload packs"
    else:
        next_step = "Next step for owner: nothing needed"

    clock = _clock_status(status_fn)
    errors = []
    if failed:
        errors.append(f"{failed} task(s) failed for good")
    if retries:
        errors.append(f"{retries} task(s) waiting to retry")

    lines = [f"Chilli Gavva daily report ({now:%Y-%m-%d})",
             providers,
             f"Drafts: {made} made, {waiting} waiting, {approved} approved (last 24h)",
             f"Auto-decided (last 24h): {auto_approved} approved, {auto_rejected} rejected",
             f"Products built: {built}",
             built_line,
             upload_packs_line(products_dir=products_dir),
             _minutes(reply=clock),
             door_bell_line(clock),
             f"Button presses received in the last 24h: {buttons}",
             "Errors: " + ("; ".join(errors) if errors else "none"),
             next_step]
    if paused:
        lines.append("Status: PAUSED (send /resume to start again)")
    return "\n".join(lines)


def weekly_text(db_path=None, now=None):
    now = now or _now()
    since = (now - datetime.timedelta(days=7)).isoformat()
    conn = guardrails.connect(db_path)
    made = _count(conn, "SELECT COUNT(*) FROM approvals WHERE ts >= ?", (since,))
    approved = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'approved' AND decided_ts >= ?", (since,))
    rejected = _count(conn, "SELECT COUNT(*) FROM approvals WHERE status = 'rejected' AND decided_ts >= ?", (since,))
    reasons = _q(conn, "SELECT reason, COUNT(*) FROM approvals WHERE status = 'rejected' AND decided_ts >= ? "
                       "AND reason IS NOT NULL GROUP BY reason ORDER BY COUNT(*) DESC, reason LIMIT 3", (since,))
    built = _count(conn, "SELECT COUNT(*) FROM product_builds WHERE status = 'built' AND ts >= ?", (since,))
    real_week = _q(conn, "SELECT COALESCE(SUM(earnings), 0) FROM campaigns WHERE source = 'real' "
                         "AND status != 'VOIDED' AND timestamp >= ?", (since,))
    real_all = _q(conn, "SELECT COALESCE(SUM(earnings), 0) FROM campaigns WHERE source = 'real' AND status != 'VOIDED'")
    conn.close()
    week = float(real_week[0][0]) if real_week else 0.0
    total = float(real_all[0][0]) if real_all else 0.0
    top = ", ".join(f"{r} ({n})" for r, n in reasons) or "no reasons given"

    lines = [
        "Sunday letter (last 7 days)",
        f"Made: {made} drafts, {built} products built",
        f"Approved: {approved}. Rejected: {rejected} (top reasons: {top})",
        f"Real money: ${week:.2f} this week, ${total:.2f} in total (only payouts recorded with proof)"
    ]

    # Guarded weekly routine run (BINNY-V5B) — never delays or breaks daily report; skipped when CHILLI_TEST=1
    if os.environ.get("CHILLI_TEST") != "1":
        try:
            import weekly_routine
            summary = weekly_routine.run(now=now, db_path=db_path)
            if summary:
                lines.append(summary)
        except Exception:
            pass

    return "\n".join(lines)


def report_text(db_path=None, now=None, status_fn=None, products_dir=None):
    now = now or _now()
    text = daily_text(db_path, now, status_fn, products_dir=products_dir)
    if now.weekday() == 6:
        text += "\n\n" + weekly_text(db_path, now)
    return text


def run_weekly_improver(send, get_state, set_state, db_path=None, now=None):
    """Run improver_loop weekly proposals on Sunday flow."""
    import improver_loop
    return improver_loop.maybe_run_weekly(send, get_state, set_state, db_path=db_path, now=now)


def due(last_sent_day, now=None):
    now = now or _now()
    utc = now.replace(tzinfo=datetime.timezone.utc) if now.tzinfo is None else now.astimezone(datetime.timezone.utc)
    local = utc.astimezone(IST)
    # Keep the 09:00 IST schedule when the IST date changes before UTC midnight.
    scheduled = datetime.datetime.combine(local.date(), SEND_AFTER, datetime.timezone.utc).astimezone(IST)
    return local.time() >= scheduled.time() and last_sent_day != local.date().isoformat()


def delivery_day(now=None):
    """One date key for the database and independent receipt, always an IST day."""
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(IST).date().isoformat()


def maybe_send(send, get_state, set_state, db_path=None, now=None, status_fn=None, claim=None):
    """Reserve today's report before sending; failed/ambiguous delivery is never retried that day."""
    now = now or _now()
    if not due(get_state("daily_report_day", None, db_path), now):
        return False
    text = report_text(db_path, now, status_fn)
    day = delivery_day(now)
    if not (claim or daily_receipts.claim)(day, db_path):
        print("Daily report skipped: date already reserved or reservation unavailable.")
        return False
    # Record the date first, too. A stale database is backed by the independent receipt above.
    set_state("daily_report_day", day, db_path)
    return bool(send(text))
