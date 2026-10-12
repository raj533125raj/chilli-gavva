"""Daily tracker for the two-week $0 validation test (see docs/VALIDATION_PLAN.md).

Log what you MEASURED (not estimates). Measured traffic is stored with source='real'.
  python3 log_validation.py add --day 2026-10-20 --visitors 85 --signups 4 --engaged 30
  python3 log_validation.py report
"""
import argparse
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "agent_data.db")

# Success criteria from docs/VALIDATION_PLAN.md (fixed before the test started)
TARGET_VISITORS = 1000
TARGET_SIGNUPS = 50
TARGET_ENGAGED_PCT = 30.0


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS validation_log (
        day TEXT PRIMARY KEY, visitors INTEGER, signups INTEGER,
        engaged INTEGER, source TEXT DEFAULT 'real')""")
    return conn


def add(day, visitors, signups, engaged):
    if engaged > visitors or signups > visitors:
        sys.exit("Invalid: signups and engaged visitors cannot exceed visitors.")
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO validation_log (day, visitors, signups, engaged, source) VALUES (?, ?, ?, ?, 'real')",
                 (day, visitors, signups, engaged))
    conn.commit()
    conn.close()
    print(f"logged {day}: visitors={visitors} signups={signups} engaged={engaged}")


def report():
    conn = connect()
    rows = conn.execute("SELECT day, visitors, signups, engaged FROM validation_log ORDER BY day").fetchall()
    conn.close()
    if not rows:
        print("No validation days logged yet.")
        return
    visitors = sum(r[1] for r in rows)
    signups = sum(r[2] for r in rows)
    engaged = sum(r[3] for r in rows)
    engaged_pct = (engaged / visitors * 100) if visitors else 0.0
    print(f"Days logged : {len(rows)} (first {rows[0][0]}, last {rows[-1][0]})")
    print(f"Visitors    : {visitors} / {TARGET_VISITORS}  {'MET' if visitors >= TARGET_VISITORS else 'not yet'}")
    print(f"Signups     : {signups} / {TARGET_SIGNUPS}  {'MET' if signups >= TARGET_SIGNUPS else 'not yet'}")
    print(f"Engagement  : {engaged_pct:.1f}% / {TARGET_ENGAGED_PCT:.0f}%  {'MET' if engaged_pct >= TARGET_ENGAGED_PCT else 'not yet'}")
    if len(rows) >= 14:
        ok = visitors >= TARGET_VISITORS and signups >= TARGET_SIGNUPS and engaged_pct >= TARGET_ENGAGED_PCT
        print("Verdict     : " + ("CONTINUE this stream" if ok else "STOP this stream, record result in STATUS.md"))
    else:
        print(f"Verdict     : test still running ({14 - len(rows)} days to go)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--day", required=True)
    a.add_argument("--visitors", type=int, required=True)
    a.add_argument("--signups", type=int, required=True)
    a.add_argument("--engaged", type=int, required=True)
    sub.add_parser("report")
    args = p.parse_args()
    if args.cmd == "add":
        add(args.day, args.visitors, args.signups, args.engaged)
    else:
        report()
