"""daily_report.py — one short Telegram health report a day, plus a weekly letter on Sundays.

Sent by the Telegram step of the cloud run (telegram_inbox.run) to the owner chat only:
  - once a day, on the first run after 03:30 UTC
  - plain text, short: AI providers OK/FAIL, drafts made / waiting / approved, products built,
    GitHub minutes used this month, errors
  - on Sundays a weekly letter is added: made, approved, rejected (top reasons), real earnings only

Nothing here is sent to an AI model.
"""
import datetime
import sqlite3

import guardrails

SEND_AFTER = datetime.time(3, 30)   # UTC


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


def daily_text(db_path=None, now=None, status_fn=None):
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
    paused = guardrails.is_paused(db_path)
    conn.close()
    clock = _clock_status(status_fn)
    errors = []
    if failed:
        errors.append(f"{failed} task(s) failed for good")
    if retries:
        errors.append(f"{retries} task(s) waiting to retry")
    lines = [f"Chilli Gavva daily report ({now:%Y-%m-%d})",
             providers,
             f"Drafts: {made} made, {waiting} waiting, {approved} approved (last 24h)",
             f"Products built: {built}",
             _minutes(reply=clock),
             door_bell_line(clock),
             f"Button presses received in the last 24h: {buttons}",
             "Errors: " + ("; ".join(errors) if errors else "none")]
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
    return "\n".join([
        "Sunday letter (last 7 days)",
        f"Made: {made} drafts, {built} products built",
        f"Approved: {approved}. Rejected: {rejected} (top reasons: {top})",
        f"Real money: ${week:.2f} this week, ${total:.2f} in total (only payouts recorded with proof)"])


def report_text(db_path=None, now=None, status_fn=None):
    now = now or _now()
    text = daily_text(db_path, now, status_fn)
    if now.weekday() == 6:
        text += "\n\n" + weekly_text(db_path, now)
    return text


def run_weekly_improver(send, get_state, set_state, db_path=None, now=None):
    """Run improver_loop weekly proposals on Sunday flow."""
    import improver_loop
    return improver_loop.maybe_run_weekly(send, get_state, set_state, db_path=db_path, now=now)


def due(last_sent_day, now=None):
    now = now or _now()
    return now.time() >= SEND_AFTER and last_sent_day != now.date().isoformat()


def maybe_send(send, get_state, set_state, db_path=None, now=None, status_fn=None):
    """Send today's report once (first run after 03:30 UTC). send(text) -> bool. Returns True if sent."""
    now = now or _now()
    if not due(get_state("daily_report_day", None, db_path), now):
        return False
    if not send(report_text(db_path, now, status_fn)):
        return False   # Telegram not reachable: try again on the next run
    set_state("daily_report_day", now.date().isoformat(), db_path)
    return True
