"""Why approved product drafts are not ready packs yet: robot fix vs owner decision (CCZ-2).

The live daily report said "Pack fixes leda Hold: /ready (48)" with no split. This module sorts each
approved product draft that is not a ready pack into one of three sides:
  robot  - code-side problem the robot retries/repairs by itself (spec, build, truth check, images ...)
  wait   - not a problem with the draft: AI down, daily AI cap, disk full, encryption step pending
  owner  - only the owner can decide (NOT FOR SALE hold, Council/Checker said no, format not buildable,
           paused niche)
Read-only; nothing is changed. Run `python3 fix_reasons.py --db agent_data.db` for the reason table.
"""
import argparse
import re
import sqlite3
from collections import Counter
from pathlib import Path

SIDES = ("robot", "wait", "owner")
LABELS = {"robot": "Robot fix chestondi", "wait": "AI/limit kosam wait", "owner": "Owner decision kavali: /ready"}

_PREFIX = re.compile(r"^(?:Product #\d+:\s*)?(?:needs fix:\s*)?")
_WAIT = re.compile(r"no reply from the model|AI reply raaledu|AI kosam waiting|Daily AI call cap|disk full|"
                   r"awaiting (?:private )?encryption|Encrypted product archive", re.I)
_OWNER = re.compile(r"NOT FOR SALE|Council malli no|Checker did not pass|Unsupported product niche|"
                    r"feasibility|cheyyalemu|build cheyyaledu|owner review|Owner Draft|paused", re.I)


def classify(status, note, held=False):
    """(side, short reason) for one approved draft's build row."""
    note = str(note or "")
    reason = _PREFIX.sub("", note).strip()[:80] or (status or "not started")
    if held or status == "not_for_sale":
        return "owner", "Owner hold: NOT FOR SALE"
    if status in ("waiting_ai", "awaiting_encryption") or _WAIT.search(note):
        return "wait", reason
    if status == "council_rejected" or _OWNER.search(note):
        return "owner", reason
    return "robot", reason


def rows(db_path, ready_ids=()):
    """Every approved product draft with a build row that is not in ready_ids: id, status, side, reason."""
    if not db_path or not Path(db_path).is_file():
        return []
    import product_builder
    with sqlite3.connect(db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"product_builds", "approvals"} <= tables:
            return []
        found = conn.execute("SELECT b.approval_id, b.status, b.note, a.title FROM product_builds b "
                             "JOIN approvals a ON a.id=b.approval_id WHERE a.status='approved' "
                             "AND a.kind='public_draft' AND b.status != 'rejected' ORDER BY b.approval_id").fetchall()
        delivery = {}
        if "product_deliveries" in tables:
            delivery = {aid: (status, reason) for aid, status, reason in
                        conn.execute("SELECT approval_id, status, reason FROM product_deliveries")}
    out = []
    for aid, status, note, title in found:
        if aid in set(ready_ids):
            continue
        d_status, d_reason = delivery.get(aid, ("", ""))
        if d_status in ("waiting_ai", "council_rejected"):
            status, note = d_status, d_reason or note
        side, reason = classify(status, note, product_builder.is_held(aid, db_path))
        if side == "robot" and status == "built":
            reason = "pack built; upload kit / sell check pending"
        out.append({"id": aid, "status": status, "side": side, "reason": reason, "title": (title or "")[:60]})
    return out


def counts(db_path, ready_ids=()):
    """Counter of LABELS[side] for the sell-test Blocks line."""
    return Counter(LABELS[row["side"]] for row in rows(db_path, ready_ids))


def table(db_path, ready_ids=()):
    """Markdown table: reason, side, count (biggest first)."""
    grouped = Counter((row["side"], row["reason"]) for row in rows(db_path, ready_ids))
    lines = ["| Reason | Side | Count |", "|---|---|---|"]
    for (side, reason), n in sorted(grouped.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {reason} | {side} | {n} |")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Approved drafts not ready yet: robot fix vs owner decision")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    import guardrails
    print(table(args.db or guardrails.DB_PATH))


if __name__ == "__main__":
    main()
