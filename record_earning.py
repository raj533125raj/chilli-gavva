"""Honest ledger for REAL money. Only things that actually paid you belong here.

Every real earning needs a proof reference (invoice number, payout id, statement line...).
Real rows are stored with source='real'. Simulated rows can never be turned into real ones here.

  python3 record_earning.py add --amount 12.50 --ref "Gumroad payout 2026-10-20" --title "Prompt pack" [--note "..."]
  python3 record_earning.py list
  python3 record_earning.py void <id> --reason "refunded"
  python3 record_earning.py total

Amounts are in USD. For another currency, convert first and keep the original in --note.
"""
import argparse
import datetime
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "agent_data.db")
MAX_AMOUNT = 1_000_000


def connect(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        niche TEXT, content_title TEXT, clicks INTEGER, earnings REAL, status TEXT, source TEXT DEFAULT 'simulated')""")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(campaigns)").fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN source TEXT DEFAULT 'simulated'")
    conn.execute("""CREATE TABLE IF NOT EXISTS earnings_proof (
        campaign_id INTEGER PRIMARY KEY, reference TEXT UNIQUE, note TEXT, recorded_ts TEXT,
        voided_ts TEXT, void_reason TEXT)""")
    return conn


def add(amount, reference, title, note="", db_path=None):
    reference = (reference or "").strip()
    if not reference:
        raise ValueError("A proof reference is required (invoice, payout id, statement line).")
    if not (0 < amount <= MAX_AMOUNT):
        raise ValueError(f"Amount must be between 0 and {MAX_AMOUNT} USD.")
    conn = connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM earnings_proof WHERE reference = ?", (reference,)).fetchone():
            raise ValueError("This proof reference is already recorded (duplicate).")
        now = datetime.datetime.now().isoformat(timespec="seconds")
        cur = conn.execute("INSERT INTO campaigns (timestamp, niche, content_title, clicks, earnings, status, source) "
                           "VALUES (?, 'real earning', ?, 0, ?, 'RECORDED', 'real')", (now, title or "(untitled)", amount))
        conn.execute("INSERT INTO earnings_proof (campaign_id, reference, note, recorded_ts) VALUES (?, ?, ?, ?)",
                     (cur.lastrowid, reference, note, now))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def void(campaign_id, reason, db_path=None):
    if not (reason or "").strip():
        raise ValueError("A reason is required to void an earning (refund, mistake...).")
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT c.status FROM campaigns c JOIN earnings_proof p ON p.campaign_id = c.id "
                           "WHERE c.id = ? AND c.source = 'real'", (campaign_id,)).fetchone()
        if not row:
            raise ValueError("No real earning with that id.")
        if row[0] == "VOIDED":
            raise ValueError("Already voided.")
        conn.execute("UPDATE campaigns SET status = 'VOIDED' WHERE id = ?", (campaign_id,))
        conn.execute("UPDATE earnings_proof SET voided_ts = ?, void_reason = ? WHERE campaign_id = ?",
                     (datetime.datetime.now().isoformat(timespec="seconds"), reason.strip(), campaign_id))
        conn.commit()
    finally:
        conn.close()


def rows(db_path=None):
    conn = connect(db_path)
    out = conn.execute("SELECT c.id, c.timestamp, c.content_title, c.earnings, c.status, p.reference, p.note "
                       "FROM campaigns c JOIN earnings_proof p ON p.campaign_id = c.id "
                       "WHERE c.source = 'real' ORDER BY c.id").fetchall()
    conn.close()
    return out


def total(db_path=None):
    conn = connect(db_path)
    value = conn.execute("SELECT COALESCE(SUM(earnings), 0) FROM campaigns "
                         "WHERE source = 'real' AND status != 'VOIDED'").fetchone()[0]
    conn.close()
    return round(value, 2)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--amount", type=float, required=True)
    a.add_argument("--ref", required=True)
    a.add_argument("--title", default="")
    a.add_argument("--note", default="")
    v = sub.add_parser("void")
    v.add_argument("id", type=int)
    v.add_argument("--reason", required=True)
    sub.add_parser("list")
    sub.add_parser("total")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "add":
            print(f"recorded real earning #{add(args.amount, args.ref, args.title, args.note)}: ${args.amount:.2f}")
        elif args.cmd == "void":
            void(args.id, args.reason)
            print("voided")
        elif args.cmd == "list":
            for r in rows():
                print(f"#{r[0]} {r[1]} ${r[3]:.2f} [{r[4]}] {r[2]} | proof: {r[5]}")
            if not rows():
                print("No real earnings recorded yet.")
        else:
            print(f"Real money total (excluding voided): ${total():.2f}")
    except ValueError as e:
        print("Not recorded:", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
