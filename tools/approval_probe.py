"""tools/approval_probe.py — why do approved drafts not become products? (CCG-1)

Runs the real robot cycle in the CCE day simulator (tools/robot_day_sim.py: fake clock, fake Telegram,
fake AI, a temp copy of the code; the repo and the real database are never touched) with a queue shaped
like the owner's 15 Nov report: 7 items waiting, the oldest 76 hours, and an owner who does NOT press any
button. The queue mixes what really waits in /list:
  * niche product drafts ("Draft: [Budget Sheets Agent] ...", kind public_draft) with real checker reviews
  * a Scout idea and a Trend proposal (other kinds, they also get Approve buttons)
  * a code patch proposal (kind code_patch)
  * one product draft the owner approved by hand today (reason empty)
  * a Self-Improvement Proposal (learned_rules, Approve button imp:a:N)

After the runs it prints, per item: kind, final status, decision reason, product build state and the
builder's reason. That is the proof for CCG-1 (no guess).

Usage:
  python3 tools/approval_probe.py              # 3 hourly runs
  python3 tools/approval_probe.py --runs 2 --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import robot_day_sim  # noqa: E402

START = "2026-11-15T12:05:00Z"
FIRST_ID = 9001

DRAFT = """PRODUCT NAME: {name}
BUYER: {buyer}
PRICE: [amount]
PLATFORM: Gumroad
WHAT'S INSIDE: {inside}
HOW TO USE: Step 1 download the file. Step 2 open it in your usual app. Step 3 replace the sample rows with your own items.
LISTING TITLE: {name} Digital Download
LISTING DESCRIPTION: A calm, clean {kind} that keeps everything for one task in one place. Personal use only.
SEARCH TAGS: {tags}
"""


def _review(score: int, verdict: str, reasons: List[str]) -> str:
    # Same shape checker.format_review writes for a public_draft.
    text = f"score {score}/100 | Checker: {score}/100 | CHECKER: {verdict.upper()} (AI {score})"
    if reasons:
        text += " | top: " + "; ".join(reasons[:2]) + " | issues: " + "; ".join(reasons[:5])
    return text


def queue(now: dt.datetime) -> List[Dict[str, Any]]:
    """(hours waited, kind, title, payload, review, status, reason) like the 15 Nov queue."""
    def draft(name, buyer, kind, inside, tags):
        return DRAFT.format(name=name, buyer=buyer, kind=kind, inside=inside, tags=tags)
    return [
        {"hours": 76, "kind": "scout_idea", "title": "[Scout Idea] Printable chore chart for shared flats",
         "payload": "Idea: Printable chore chart for shared flats\nCategory: Planners",
         "review": "Checker: 82/100 | CHECKER: PASS (rules) | Scout: 78/100 | Why sells: simple"},
        {"hours": 70, "kind": "trend_proposal", "title": "[Trend Proposal] Printable weekly planners",
         "payload": "Trend Item #1\nTitle: Printable weekly planners", "review": "Score: 90/100 | steady interest"},
        {"hours": 60, "kind": "public_draft",
         "title": "Draft: [Budget Sheets Agent] Couple budget sheet with shared bills tab (v1, llm)",
         "payload": draft("Couple Budget Sheet", "couples who share bills", "spreadsheet",
                          "Tab 1 Budget (Columns: Category, Planned, Actual), Tab 2 Bills (Columns: Bill, Due Date)",
                          "budget spreadsheet, couple budget, bill tracker"),
         "review": _review(84, "pass", [])},
        {"hours": 50, "kind": "code_patch", "title": "Proposal: shorter retry wait in llm_router",
         "payload": "--- a/llm_router.py\n+++ b/llm_router.py\n", "review": None},
        {"hours": 40, "kind": "public_draft",
         "title": "Draft: [ADHD Planners Agent] Gentle focus daily planner, printable (v2, llm)",
         "payload": draft("Gentle Focus Daily Planner", "adults who like short visual plans", "printable planner",
                          "Page 1 Daily Plan (top 3 tasks, time blocks), Page 2 Brain Dump",
                          "daily planner, focus planner, printable planner"),
         "review": _review(74, "revise", ["Listing title is long", "Add one more page idea"])},
        {"hours": 30, "kind": "public_draft",
         "title": "Draft: [2027 Planners Agent] 2027 teacher planner with class roster (v1, llm)",
         "payload": draft("2027 Teacher Planner", "teachers who plan the year on paper", "dated planner",
                          "Year at a glance, 12 monthly calendar pages for 2027, class roster page",
                          "2027 planner, teacher planner, dated planner"),
         "review": _review(88, "pass", [])},
        {"hours": 2, "kind": "public_draft",
         "title": "Draft: [Freelancer Trackers Agent] Invoice tracker for designers (v1, llm)",
         "payload": draft("Freelancer Invoice Tracker", "freelancers who send invoices", "tracker",
                          "Tab 1 Invoices (Columns: Client, Due Date, Status), Tab 2 Clients",
                          "invoice tracker, freelancer template"),
         "review": _review(86, "pass", [])},
        # Approved by the owner by hand 3 hours ago (button press: reason stays empty).
        {"hours": 28, "kind": "public_draft", "decided_hours": 3, "status": "approved",
         "title": "Draft: [Budget Sheets Agent] Sinking funds sheet for school supplies (v1, llm)",
         "payload": draft("Sinking Funds Sheet", "parents saving for school supplies", "spreadsheet",
                          "Tab 1 Funds (Columns: Goal, Target Date, Saved), Tab 2 Log (Columns: Date, Fund)",
                          "sinking funds, savings tracker, budget spreadsheet"),
         "review": _review(85, "pass", [])},
    ]


def seed_queue(db: Path, now: dt.datetime, only_drafts: bool = False) -> List[int]:
    """Insert the queue straight into the sandbox database (naive UTC ISO like guardrails writes).
    only_drafts: leave out the other kinds and the owner's own approval (second scenario)."""
    conn = sqlite3.connect(str(db))
    ids = []
    try:
        for item in queue(now):
            if only_drafts and (item["kind"] != "public_draft" or item.get("status")):
                continue
            ts = (now - dt.timedelta(hours=item["hours"])).replace(tzinfo=None).isoformat()
            decided = None
            if item.get("decided_hours") is not None:
                decided = (now - dt.timedelta(hours=item["decided_hours"])).replace(tzinfo=None).isoformat()
            # High ids like the real robot (specs/rebuild/<id>-*.json name small real ids; never collide).
            aid = FIRST_ID + len(ids)
            conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, review, status, decided_ts) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (aid, ts, item["kind"], item["title"], item["payload"], item["review"],
                          item.get("status", "pending"), decided))
            ids.append(aid)
        conn.execute("CREATE TABLE IF NOT EXISTS learned_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "rule_text TEXT, category TEXT, problem_key TEXT, status TEXT, evidence TEXT, "
                     "created_ts TEXT, decided_ts TEXT)")
        conn.execute("INSERT INTO learned_rules (rule_text, category, problem_key, status, evidence, created_ts) "
                     "VALUES (?, 'writer', 'probe_long_titles', 'proposed', '3 occurrence(s)', ?)",
                     ("Keep listing titles under 80 characters.", (now - dt.timedelta(hours=50)).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return ids


def probe(runs: int = 3, start: str = START, python: str = None, log=lambda line: None,
          only_drafts: bool = False, keep: str = None) -> Dict[str, Any]:
    now = dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    seeded: Dict[str, Any] = {}

    def before_first_run(paths, env):
        seeded["ids"] = seed_queue(paths["code"] / "agent_data.db", now, only_drafts)
        return None

    real_act = robot_day_sim.Owner.act
    real_report = robot_day_sim.build_report

    def quiet_owner(self, run_index, when):   # the owner is away: no button, no command
        return []

    def keep_rows(paths, steps_run, timeline, owner, runs_, failed_runs):
        report = real_report(paths, steps_run, timeline, owner, runs_, failed_runs)
        report["probe_rows"] = robot_day_sim.db_rows(
            paths["code"],
            "SELECT a.id, a.kind, a.title, a.status, COALESCE(a.reason,''), COALESCE(a.decided_ts,''), "
            "COALESCE(b.status,''), COALESCE(b.note,''), COALESCE(d.status,''), COALESCE(d.reason,'') "
            "FROM approvals a LEFT JOIN product_builds b ON b.approval_id=a.id "
            "LEFT JOIN product_deliveries d ON d.approval_id=a.id ORDER BY a.id")
        report["probe_rules"] = robot_day_sim.db_rows(paths["code"], "SELECT id, status FROM learned_rules")
        report["probe_builds"] = robot_day_sim.db_rows(paths["code"], "SELECT * FROM product_builds")
        report["probe_deliveries"] = robot_day_sim.db_rows(
            paths["code"], "SELECT approval_id, status, reason, folder FROM product_deliveries")
        report["probe_attempts"] = robot_day_sim.db_rows(paths["code"], "SELECT * FROM product_delivery_attempts")
        report["probe_build_logs"] = [s["output"][-1500:] for s in steps_run if s["name"] == "build"]
        report["probe_inbox_logs"] = [s["output"][-1500:] for s in steps_run if s["name"] == "inbox"]
        return report

    robot_day_sim.Owner.act = quiet_owner
    robot_day_sim.build_report = keep_rows
    try:
        report = robot_day_sim.simulate(hours=runs, every=60, start=start, events={0: before_first_run},
                                        log=log, python=python, keep=keep)
    finally:
        robot_day_sim.Owner.act = real_act
        robot_day_sim.build_report = real_report
    report["seeded_ids"] = seeded.get("ids", [])
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--python", default=None)
    parser.add_argument("--keep", default=None, help="keep the sandbox in this folder (outside the repo)")
    parser.add_argument("--only-drafts", action="store_true", help="queue of product drafts only")
    args = parser.parse_args(argv)
    report = probe(args.runs, python=args.python, log=lambda line: print(line, file=sys.stderr),
                   only_drafts=args.only_drafts, keep=args.keep)
    if args.json:
        print(json.dumps({k: report.get(k) for k in ("seeded_ids", "probe_rows", "probe_rules", "probe_builds",
                                                     "probe_deliveries", "probe_attempts",
                                                     "probe_build_logs", "probe_inbox_logs")}, indent=1))
        return 0
    seeded = set(report.get("seeded_ids") or [])
    print("id  kind            status    reason            build      build note / delivery reason")
    for aid, kind, title, status, reason, decided, bstatus, bnote, dstatus, dreason in report.get("probe_rows") or []:
        mark = "*" if aid in seeded else " "
        print(f"{mark}{aid:<3} {kind:<15} {status:<9} {reason[:17]:<17} {bstatus or dstatus or '-':<10} "
              f"{(dreason or bnote)[:90]}  | {title[:60]}")
    print("learned_rules:", report.get("probe_rules"))
    for i, text in enumerate(report.get("probe_build_logs") or [], 1):
        lines = [ln for ln in text.splitlines() if "Product Builder" in ln or "draft #" in ln]
        print(f"build run {i}:", " / ".join(lines[-4:]) or "(no builder lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
