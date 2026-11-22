"""CCG-2: one test per cause found by tools/approval_probe.py (see .jobs PROGRESS CCG-1).

1. auto-approve used its daily slots on proposals (Scout idea, Trend, code patch) and auto-rejected a code patch
2. the owner's own button approvals used up the auto-approve slots
3. a draft the checker said REVISE was auto-approved, then the builder refused it for ever
4. a draft no builder can make took a daily build slot
5. proposals are reported on their own, never as product drafts
"""
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import guardrails

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import auto_approve  # noqa: E402
import checker  # noqa: E402

NOW = datetime.datetime(2026, 11, 15, 12, 5, tzinfo=datetime.timezone.utc)


def _pass(score):
    return checker.format_review(score, [], "pass")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "agent_data.db")
        guardrails.connect(self.db).close()
        self.n = 0
        # never send anything
        p = mock.patch("telegram_inbox.send_text", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def add(self, kind, title, review, hours=30, status="pending", reason=None, decided_hours=None, payload=None):
        self.n += 1
        ts = (NOW - datetime.timedelta(hours=hours)).replace(tzinfo=None).isoformat()
        decided = None
        if decided_hours is not None:
            decided = (NOW - datetime.timedelta(hours=decided_hours)).isoformat()
        conn = guardrails.connect(self.db)
        cur = conn.execute("INSERT INTO approvals (ts, kind, title, payload, review, status, reason, decided_ts) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (ts, kind, title, payload or f"PRODUCT NAME: item {self.n} unique words {self.n * 7}",
                            review, status, reason, decided))
        conn.commit()
        conn.close()
        return cur.lastrowid

    def status(self, aid):
        return guardrails.get_approval(aid, self.db)["status"]


class ProposalsAreNotAutoDecided(Base):
    def test_scout_trend_and_code_patch_wait_for_the_owner(self):
        scout = self.add("scout_idea", "[Scout Idea] Chore chart", "Checker: 82/100 | CHECKER: PASS (rules)", 76)
        trend = self.add("trend_proposal", "[Trend Proposal] Weekly planners", "Score: 90/100 | why", 70)
        patch = self.add("code_patch", "Proposal: retry wait", None, 50)
        draft = self.add("public_draft", "Draft: [Budget Sheets Agent] Couple budget", _pass(84), 60)
        decisions = auto_approve.auto_decide_drafts(self.db, now=NOW)
        self.assertEqual([d["id"] for d in decisions], [draft])
        self.assertEqual([self.status(i) for i in (scout, trend, patch)], ["pending"] * 3)   # never auto-rejected
        self.assertEqual(self.status(draft), "approved")


class OwnerApprovalsDoNotUseAutoSlots(Base):
    def test_owner_button_approval_is_not_an_auto_approval(self):
        self.add("public_draft", "Draft: [Budget Sheets Agent] Sinking funds", _pass(85), 28,
                 status="approved", reason=None, decided_hours=3)
        self.add("public_draft", "Draft: [ADHD Planners Agent] Focus", _pass(85), 28,
                 status="approved", reason=None, decided_hours=2)
        self.assertEqual(auto_approve.count_auto_approvals_today(self.db, now=NOW), 0)
        a = self.add("public_draft", "Draft: [2027 Planners Agent] Teacher planner", _pass(88), 30)
        b = self.add("public_draft", "Draft: [Freelancer Trackers Agent] Invoices", _pass(86), 26)
        self.assertEqual({d["id"] for d in auto_approve.auto_decide_drafts(self.db, now=NOW)}, {a, b})
        # the owner's approvals of 15 Nov are not reported as auto-approved the next morning
        digest = auto_approve.morning_digest_line(self.db, now=NOW + datetime.timedelta(days=1))
        self.assertIn("Auto-approved yesterday: 2 drafts", digest)
        self.assertIn(f"#{a} ", digest)
        self.assertIn(f"#{b} ", digest)
        self.assertNotRegex(digest, r"#[12]\b")


class RevisedDraftsAreNotAutoApproved(Base):
    def test_revise_waits_and_the_pass_draft_gets_the_slot(self):
        revise = self.add("public_draft", "Draft: [ADHD Planners Agent] Gentle planner",
                          checker.format_review(74, ["Listing title is long"], "revise"), 40)
        good = self.add("public_draft", "Draft: [2027 Planners Agent] Teacher planner", _pass(88), 30)
        decisions = auto_approve.auto_decide_drafts(self.db, now=NOW)
        self.assertEqual([d["id"] for d in decisions], [good])
        self.assertEqual(self.status(revise), "pending")

    def test_checker_blocks(self):
        self.assertTrue(auto_approve.checker_blocks("score 74/100 | Checker: 74/100 | CHECKER: REVISE (AI 74)"))
        self.assertTrue(auto_approve.checker_blocks("Checker: 80/100 | CHECKER: unavailable (checklist only)"))
        self.assertFalse(auto_approve.checker_blocks(_pass(80)))
        self.assertFalse(auto_approve.checker_blocks("score 80/100"))          # older drafts: score only
        self.assertTrue(checker.review_passes(_pass(80)))                      # same rule the builder uses


class UnbuildableDraftsDoNotTakeBuildSlots(Base):
    def test_slot_goes_to_the_niche_draft(self):
        import product_council
        import product_pipeline
        odd = self.add("public_draft", "Draft: Cloud billing web app", _pass(90), 30, status="approved",
                       payload="PRODUCT NAME: Cloud Billing\nWHAT'S INSIDE: Python backend and React frontend")
        legacy = self.add("public_draft", "Draft: free planner landing page", _pass(90), 30, status="approved",
                          payload="PRODUCT NAME: Daily Planner\nWHAT'S INSIDE: printable daily planner pages")
        niche = self.add("public_draft", "Draft: [Budget Sheets Agent] Couple budget", _pass(90), 30,
                         status="approved")
        out = os.path.join(self.tmp.name, "products")
        with mock.patch.object(product_pipeline, "_allowed", return_value=True), \
                mock.patch("workforce.default_llm", return_value=lambda *a, **k: ""), \
                mock.patch.object(product_council, "get_council_summary", return_value={"verdict": "FAIL"}), \
                mock.patch.object(product_council, "run_council", return_value={"verdict": "FAIL"}):
            product_pipeline.run_pending(db_path=self.db, out_dir=out, max_builds=1, day="2026-11-15")
        conn = sqlite3.connect(self.db)
        reserved = [r[0] for r in conn.execute("SELECT approval_id FROM product_delivery_attempts")]
        conn.close()
        self.assertEqual(reserved, [legacy, niche])                    # the one real product draft got the slot
        self.assertEqual(self.status(odd), "rejected")      # no builder can make it: owner told why
        self.assertIn(legacy, reserved)  # registered planners no longer require a legacy agent title
        with guardrails.connect(self.db) as conn:
            self.assertTrue(conn.execute("SELECT reason FROM approvals WHERE id=?", (odd,)).fetchone()[0])
        self.assertFalse(os.path.isdir(out) and os.listdir(out))


class ProposalsReportedSeparately(Base):
    def test_split_waiting_and_rule_proposals(self):
        self.add("scout_idea", "[Scout Idea] x", None)
        self.add("code_patch", "Proposal: y", None)
        d = self.add("public_draft", "Draft: [Budget Sheets Agent] z", _pass(80))
        drafts, proposals = auto_approve.split_waiting(self.db)
        self.assertEqual([i["id"] for i in drafts], [d])
        self.assertEqual(sorted(i["kind"] for i in proposals), ["code_patch", "scout_idea"])
        self.assertEqual(auto_approve.waiting_rule_proposals(self.db), 0)   # fresh db: no table, no crash
        conn = guardrails.connect(self.db)
        conn.execute("CREATE TABLE learned_rules (id INTEGER PRIMARY KEY, rule_text TEXT, category TEXT, "
                     "problem_key TEXT, status TEXT, evidence TEXT, created_ts TEXT, decided_ts TEXT)")
        conn.execute("INSERT INTO learned_rules (rule_text, status) VALUES ('Keep titles short', 'proposed')")
        conn.commit()
        conn.close()
        self.assertEqual(auto_approve.waiting_rule_proposals(self.db), 1)


class FifteenNovemberQueue(Base):
    """The probe's queue, in process: the two PASS product drafts get the two daily slots."""

    def test_probe_queue_now_approves_the_right_drafts(self):
        import approval_probe
        approval_probe.seed_queue(Path(self.db), NOW)
        decisions = auto_approve.auto_decide_drafts(self.db, now=NOW)
        expired = [d for d in decisions if d.get("kind") == "self_improvement"]
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["action"], "rejected")
        self.assertEqual(auto_approve.waiting_rule_proposals(self.db), 0)
        products = [d for d in decisions if d not in expired]
        titles = [d["title"] for d in products]
        self.assertEqual(len(products), 2, titles)
        self.assertTrue(all(t.startswith("Draft: [") for t in titles), titles)
        self.assertTrue(any("Couple budget" in t for t in titles) and any("2027 teacher" in t for t in titles))
        left = {i["kind"] for i in guardrails.pending(self.db)}
        self.assertTrue({"scout_idea", "trend_proposal", "code_patch"} <= left)


class OwnerApprovalsBuildFirst(Base):
    def test_owner_choice_before_auto_approved(self):
        import product_pipeline
        auto = self.add("public_draft", "Draft: [Budget Sheets Agent] a", _pass(90), status="approved",
                        reason="auto-approved", decided_hours=1)
        owner = self.add("public_draft", "Draft: [ADHD Planners Agent] b", _pass(90), status="approved",
                         decided_hours=1)
        self.assertEqual([i["id"] for i in product_pipeline.pending(self.db)], [owner, auto])


if __name__ == "__main__":
    unittest.main()
