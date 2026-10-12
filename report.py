"""Daily report: a plain-text summary written from the database (no LLM, no network).

  python3 report.py            # write reports/<today>.md and print it
The worker writes one per day automatically. reports/ is git-ignored (it may contain draft titles).
"""
import datetime
import os
import sqlite3
import sys

import guardrails
import workforce

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "reports")


def build(db_path=None, day=None):
    day = day or datetime.date.today().isoformat()
    conn = workforce.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
                 "niche TEXT, content_title TEXT, clicks INTEGER, earnings REAL, status TEXT, "
                 "source TEXT DEFAULT 'simulated')")
    tasks = dict(conn.execute("SELECT status, COUNT(*) FROM tasks WHERE ts LIKE ? GROUP BY status",
                              (day + "%",)).fetchall())
    appr = dict(conn.execute("SELECT status, COUNT(*) FROM approvals GROUP BY status").fetchall())
    cols = [r[1] for r in conn.execute("PRAGMA table_info(campaigns)").fetchall()]
    if "source" in cols:
        real = conn.execute("SELECT COALESCE(SUM(earnings),0) FROM campaigns WHERE source='real' "
                            "AND status!='VOIDED'").fetchone()[0]
    else:
        real = 0.0
    row = conn.execute("SELECT llm_calls FROM usage_log WHERE day = ?", (day,)).fetchone()
    calls = row[0] if row else 0
    patches = conn.execute("SELECT COUNT(*) FROM approvals WHERE kind='code_patch' AND status='pending'").fetchone()[0]
    conn.close()
    lines = [f"# Chilli Gavva daily report {day}", "",
             f"- Tasks today: " + (", ".join(f"{v} {k}" for k, v in sorted(tasks.items())) or "none"),
             f"- Approvals: {appr.get('pending', 0)} waiting, {appr.get('approved', 0)} approved, "
             f"{appr.get('rejected', 0)} rejected (all time)",
             f"- Code patches waiting for you: {patches}",
             f"- LLM calls today: {calls}",
             f"- Real earnings (with proof): ${real:.2f}",
             f"- Kill switch: {'ON' if guardrails.is_killed() else 'off'}",
             "", "Nothing here is published. Simulated numbers are not counted as income."]
    return "\n".join(lines) + "\n"


def write_daily(db_path=None, report_dir=None, day=None):
    """Write today's report once. Returns the path if newly written, else None."""
    day = day or datetime.date.today().isoformat()
    report_dir = report_dir or REPORT_DIR
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, f"{day}.md")
    if os.path.exists(path):
        return None
    with open(path, "w", encoding="utf-8") as f:
        f.write(build(db_path, day))
    return path


if __name__ == "__main__":
    p = write_daily() or os.path.join(REPORT_DIR, datetime.date.today().isoformat() + ".md")
    print(open(p, encoding="utf-8").read())
    sys.exit(0)
