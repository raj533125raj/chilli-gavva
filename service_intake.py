"""Income stream D — small service request intake.

Someone wants a small piece of work done (write a bio, summarise a document,
create a prompt, etc.).  This module:

  1. Accepts the request (via CLI or imported by an HTTP endpoint).
  2. Queues a draft task → Draft Worker writes the deliverable.
  3. You approve it → exported for delivery.

NOTHING is sent to the client automatically. Every step needs your approval.

Service types (add more as needed):
  bio       — professional bio (1-2 paragraphs)
  summary   — summarise a provided text
  prompt    — write an AI prompt for a described use case
  outline   — outline for a blog post / article
  caption   — social media caption for a described image/product

CLI:
  python3 service_intake.py list                         # all requests
  python3 service_intake.py add bio "Client name" "..."  # add request
  python3 service_intake.py export <request_id>          # export approved draft
  python3 service_intake.py cancel <request_id> "reason" # cancel a request

Requests live in the SQLite DB (service_requests table).
Drafts go through the normal approval queue (kind='service_draft').
"""
import datetime
import json
import os
import re
import sqlite3
import sys

import guardrails
import workforce

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
EXPORTS_DIR = os.path.join(BASE_DIR, "service_exports")

ALLOWED_TYPES = {"bio", "summary", "prompt", "outline", "caption"}
MAX_BRIEF_LEN = 2000   # chars
MAX_TITLE_LEN = 120

STATUS_OPEN      = "open"
STATUS_DRAFTING  = "drafting"
STATUS_PENDING   = "pending_approval"
STATUS_APPROVED  = "approved"
STATUS_EXPORTED  = "exported"
STATUS_CANCELLED = "cancelled"


# ── database ───────────────────────────────────────────────────────────────────

def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS service_requests (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT    NOT NULL,
        kind       TEXT    NOT NULL,
        title      TEXT    NOT NULL,
        brief      TEXT    NOT NULL,
        status     TEXT    NOT NULL DEFAULT 'open',
        approval_id INTEGER,
        exported_ts TEXT,
        cancel_reason TEXT
    )""")
    conn.commit()
    return conn


# ── validation ─────────────────────────────────────────────────────────────────

def validate_request(kind, title, brief):
    errs = []
    if kind not in ALLOWED_TYPES:
        errs.append(f"unknown service type '{kind}'. Allowed: {', '.join(sorted(ALLOWED_TYPES))}")
    if not title or len(title) > MAX_TITLE_LEN:
        errs.append(f"title must be 1-{MAX_TITLE_LEN} chars")
    if not brief or len(brief) > MAX_BRIEF_LEN:
        errs.append(f"brief must be 1-{MAX_BRIEF_LEN} chars")
    return errs


# ── core operations ────────────────────────────────────────────────────────────

def add_request(kind, title, brief, db_path=None):
    """Add a new service request. Returns (request_id, errors)."""
    errs = validate_request(kind, title, brief)
    if errs:
        return None, errs
    conn = connect(db_path)
    cur = conn.execute(
        "INSERT INTO service_requests (ts, kind, title, brief, status) VALUES (?, ?, ?, ?, ?)",
        (datetime.datetime.now().isoformat(timespec="seconds"), kind, title, brief, STATUS_OPEN),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()

    # Queue a draft task immediately so the agent picks it up
    _queue_draft_task(rid, kind, title, brief, db_path)
    return rid, []


def _queue_draft_task(request_id, kind, title, brief, db_path=None):
    """Queue a workforce task so Draft Worker writes the deliverable."""
    prompt_map = {
        "bio":     f'Write a professional bio (2 paragraphs, ~150 words) for: {title}\nBrief: {brief}',
        "summary": f'Write a concise summary (3-5 key points + 1 paragraph) for: {title}\nContent: {brief}',
        "prompt":  f'Write a clear, reusable AI prompt for this use case: {title}\nDetails: {brief}',
        "outline": f'Write a detailed outline (intro + 5 sections + conclusion) for: {title}\nAngle: {brief}',
        "caption": f'Write 3 social media caption options (short/medium/long) for: {title}\nContext: {brief}',
    }
    payload = {
        "topic": prompt_map.get(kind, f"{kind}: {title}\n{brief}"),
        "service_request_id": request_id,
        "kind": kind,
        "title": title,
    }
    workforce.enqueue("draft", payload=payload, priority=2, db_path=db_path)  # priority 2 = high


def get_request(request_id, db_path=None):
    conn = connect(db_path)
    row = conn.execute(
        "SELECT id, ts, kind, title, brief, status, approval_id, exported_ts, cancel_reason "
        "FROM service_requests WHERE id = ?", (request_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    keys = ["id", "ts", "kind", "title", "brief", "status", "approval_id", "exported_ts", "cancel_reason"]
    return dict(zip(keys, row))


def list_requests(db_path=None, status=None):
    conn = connect(db_path)
    if status:
        rows = conn.execute(
            "SELECT id, ts, kind, title, status FROM service_requests WHERE status = ? ORDER BY id DESC",
            (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, ts, kind, title, status FROM service_requests ORDER BY id DESC"
        ).fetchall()
    conn.close()
    return [{"id": r[0], "ts": r[1], "kind": r[2], "title": r[3], "status": r[4]} for r in rows]


def cancel_request(request_id, reason="cancelled", db_path=None):
    """Cancel an open or pending request."""
    conn = connect(db_path)
    row = conn.execute("SELECT status FROM service_requests WHERE id = ?", (request_id,)).fetchone()
    if not row:
        conn.close()
        return False, "request not found"
    if row[0] in (STATUS_EXPORTED, STATUS_CANCELLED):
        conn.close()
        return False, f"cannot cancel a {row[0]} request"
    conn.execute(
        "UPDATE service_requests SET status = ?, cancel_reason = ? WHERE id = ?",
        (STATUS_CANCELLED, reason, request_id)
    )
    conn.commit()
    conn.close()
    return True, "cancelled"


def export_approved(request_id, db_path=None, exports_dir=None):
    """Export approved draft to a text file for delivery. Returns path or None."""
    exports_dir = exports_dir or EXPORTS_DIR
    req = get_request(request_id, db_path)
    if not req:
        return None, "request not found"
    if req["status"] != STATUS_APPROVED:
        # Check if the linked approval was approved
        if req["approval_id"]:
            conn = connect(db_path)
            row = conn.execute(
                "SELECT status, payload FROM approvals WHERE id = ?", (req["approval_id"],)
            ).fetchone()
            conn.close()
            if not row or row[0] != "approved":
                return None, f"approval #{req['approval_id']} not approved yet (status: {row[0] if row else 'missing'})"
            payload = row[1]
        else:
            return None, "no approval linked yet — draft not ready"
    else:
        conn = connect(db_path)
        row = conn.execute("SELECT payload FROM approvals WHERE id = ?", (req["approval_id"],)).fetchone()
        conn.close()
        payload = row[0] if row else ""

    os.makedirs(exports_dir, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", req["title"].lower()).strip("-")[:40]
    fname = f"SR{request_id:04d}-{slug}.txt"
    fpath = os.path.join(exports_dir, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(f"Service Request #{request_id}\n")
        f.write(f"Type: {req['kind']}\n")
        f.write(f"Title: {req['title']}\n")
        f.write(f"Date: {req['ts'][:10]}\n")
        f.write("─" * 40 + "\n\n")
        f.write(payload)
        f.write("\n")
    # Mark exported
    conn = connect(db_path)
    conn.execute(
        "UPDATE service_requests SET status = ?, exported_ts = ? WHERE id = ?",
        (STATUS_EXPORTED, datetime.datetime.now().isoformat(timespec="seconds"), request_id)
    )
    conn.commit()
    conn.close()
    return fpath, None


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv):
    if not argv or argv[0] == "list":
        reqs = list_requests()
        if not reqs:
            print("No service requests yet.")
            print(f"  Add one: python3 service_intake.py add <type> \"title\" \"brief\"")
            print(f"  Types: {', '.join(sorted(ALLOWED_TYPES))}")
            return 0
        print(f"{'ID':>4}  {'Type':8}  {'Status':16}  Title")
        print("─" * 60)
        for r in reqs:
            print(f"  {r['id']:>2}  {r['kind']:8}  {r['status']:16}  {r['title'][:40]}")
        return 0

    if argv[0] == "add" and len(argv) >= 4:
        kind, title, brief = argv[1], argv[2], argv[3]
        rid, errs = add_request(kind, title, brief)
        if errs:
            for e in errs:
                print(f"Error: {e}")
            return 1
        print(f"Service request #{rid} queued. Agent will draft it; you approve before export.")
        return 0

    if argv[0] == "export" and len(argv) >= 2:
        rid = int(argv[1])
        path, err = export_approved(rid)
        if err:
            print(f"Cannot export: {err}")
            return 1
        print(f"Exported to: {path}")
        print("Send this file to the client. Nothing was sent automatically.")
        return 0

    if argv[0] == "cancel" and len(argv) >= 2:
        rid = int(argv[1])
        reason = argv[2] if len(argv) >= 3 else "cancelled"
        ok, msg = cancel_request(rid, reason)
        print(f"{'Cancelled' if ok else 'Error'}: {msg}")
        return 0 if ok else 1

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
