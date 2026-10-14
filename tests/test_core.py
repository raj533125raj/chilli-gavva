"""Core tests. Run: python3 -m unittest discover -s tests
All tests use a temporary database and never touch agent_data.db."""
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails  # noqa: E402
import improver  # noqa: E402
import workforce  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self._old = (guardrails.KILL_FILE, guardrails.CONFIG_PATH)
        guardrails.KILL_FILE = os.path.join(self.tmp.name, "KILL_SWITCH")
        guardrails.CONFIG_PATH = os.path.join(self.tmp.name, "config.json")
        os.environ.pop("AGENT_PAUSED", None)
        self._wf_cfg = workforce.CONFIG_PATH
        workforce.CONFIG_PATH = os.path.join(self.tmp.name, "wf_config.json")
        with open(workforce.CONFIG_PATH, "w") as f:
            f.write('{"daily_drafts": 1}')

    def tearDown(self):
        workforce.CONFIG_PATH = self._wf_cfg
        guardrails.KILL_FILE, guardrails.CONFIG_PATH = self._old
        self.tmp.cleanup()


class GuardrailTests(Base):
    def test_kill_switch(self):
        self.assertFalse(guardrails.is_killed())
        guardrails.kill("test")
        self.assertTrue(guardrails.is_killed())
        self.assertFalse(guardrails.can_call_llm(self.db))
        guardrails.resume()
        self.assertFalse(guardrails.is_killed())

    def test_env_pause(self):
        os.environ["AGENT_PAUSED"] = "1"
        try:
            self.assertTrue(guardrails.is_killed())
        finally:
            os.environ.pop("AGENT_PAUSED")

    def test_daily_cap(self):
        self.addCleanup(setattr, guardrails, "_TESTING", guardrails._TESTING); guardrails._TESTING = False
        with open(guardrails.CONFIG_PATH, "w") as f:
            json.dump({"daily_llm_call_cap": 2}, f)
        self.assertTrue(guardrails.can_call_llm(self.db))
        guardrails.record_call(self.db)
        guardrails.record_call(self.db)
        self.assertEqual(guardrails.calls_today(self.db), 2)
        self.assertFalse(guardrails.can_call_llm(self.db))

    def test_approvals(self):
        a = guardrails.request_approval("public_draft", "t", "body", db_path=self.db)
        self.assertEqual([p["id"] for p in guardrails.pending(self.db)], [a])
        self.assertTrue(guardrails.decide(a, True, self.db))
        self.assertFalse(guardrails.decide(a, False, self.db))   # already decided
        self.assertEqual(guardrails.get_approval(a, self.db)["status"], "approved")
        self.assertEqual(guardrails.pending(self.db), [])


class WorkforceTests(Base):
    def stub_llm(self, prompt):
        return ("# Free tool\nBenefit one. Benefit two. Benefit three. Try it free today and subscribe for "
                "updates. Works in the browser and needs no account, so you can start in seconds. "
                "Made for small creators who want simple, honest tools without any sign-up wall.")

    def run_pipeline(self, llm):
        workforce.enqueue("plan", {"topic": "demo"}, db_path=self.db)
        return workforce.run_once(max_tasks=10, db_path=self.db, llm=llm, rng=random.Random(1))

    def test_pipeline_with_llm(self):
        result = self.run_pipeline(self.stub_llm)
        self.assertEqual(result, {"status": "ok", "ran": 4, "failed": 0})
        pending = guardrails.pending(self.db)
        self.assertEqual(len(pending), 1)                         # draft waits for a human
        self.assertIn("score", pending[0]["review"])
        self.assertEqual(guardrails.get_approval(pending[0]["id"], self.db)["status"], "pending")

    def test_pipeline_fallback_without_llm(self):
        result = self.run_pipeline(lambda prompt: None)
        self.assertEqual(result["ran"], 4)
        self.assertIn("fallback", guardrails.pending(self.db)[0]["title"])

    def test_kill_switch_stops_work(self):
        workforce.enqueue("plan", {"topic": "demo"}, db_path=self.db)
        guardrails.kill("test")
        self.assertEqual(workforce.run_once(db_path=self.db, llm=self.stub_llm)["status"], "killed")
        conn = workforce.connect(self.db)
        self.assertEqual(conn.execute("SELECT status FROM tasks").fetchone()[0], "queued")
        conn.close()

    def test_retry_then_fail(self):
        original = workforce.STEPS["plan"]
        workforce.STEPS["plan"] = lambda task, ctx: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            workforce.enqueue("plan", {}, db_path=self.db)
            for _ in range(5):
                workforce.run_once(max_tasks=1, db_path=self.db, llm=self.stub_llm)
        finally:
            workforce.STEPS["plan"] = original
        conn = workforce.connect(self.db)
        status, attempts = conn.execute("SELECT status, attempts FROM tasks").fetchone()
        conn.close()
        self.assertEqual((status, attempts), ("failed", workforce.MAX_ATTEMPTS))

    def test_daily_plan_only_once(self):
        self.assertIsNotNone(workforce.ensure_daily_plan(self.db))
        self.assertIsNone(workforce.ensure_daily_plan(self.db))

    def test_review_rubric_blocks_risky_claims(self):
        score, notes = workforce.review_text("Guaranteed passive income, risk-free! " * 3)
        self.assertLess(score, 50)
        self.assertTrue(any("risky" in n for n in notes))

    def test_agent_states(self):
        states = workforce.agent_states(self.db)
        self.assertEqual({s["level"] for s in states}, {"high", "mid", "entry"})
        self.assertTrue(all(s["state"] == "idle" for s in states))

    def test_human_decision_feeds_improver(self):
        self.run_pipeline(self.stub_llm)
        approval_id = guardrails.pending(self.db)[0]["id"]
        guardrails.decide(approval_id, True, self.db)
        self.assertEqual(workforce.sync_human_decisions(self.db), 1)
        self.assertEqual(workforce.sync_human_decisions(self.db), 0)   # not counted twice


class ImproverTests(Base):
    def test_promote_and_revert(self):
        champion_id, _ = improver.get_active("draft", self.db)
        challenger_id = improver.add_variant("draft", "Better {topic} prompt", self.db)
        for _ in range(improver.MIN_TRIALS):
            improver.record_result(champion_id, 60, self.db)
            improver.record_result(challenger_id, 90, self.db)
        promoted = improver.promote_if_better("draft", self.db)
        self.assertEqual(promoted["to"], challenger_id)
        self.assertEqual(improver.get_active("draft", self.db)[0], challenger_id)
        self.assertEqual(improver.revert("draft", self.db), {"active": champion_id})
        self.assertEqual(improver.get_active("draft", self.db)[0], champion_id)

    def test_no_promotion_without_enough_trials_or_margin(self):
        champion_id, _ = improver.get_active("draft", self.db)
        challenger_id = improver.add_variant("draft", "Variant {topic}", self.db)
        improver.record_result(challenger_id, 82, self.db)           # only 1 trial
        self.assertIsNone(improver.promote_if_better("draft", self.db))
        for _ in range(improver.MIN_TRIALS):
            improver.record_result(champion_id, 80, self.db)
            improver.record_result(challenger_id, 82, self.db)       # win below margin
        self.assertIsNone(improver.promote_if_better("draft", self.db))

    def test_humans_outweigh_reviewer_score(self):
        champion_id, _ = improver.get_active("draft", self.db)
        challenger_id = improver.add_variant("draft", "Variant {topic}", self.db)
        for _ in range(improver.MIN_TRIALS):
            improver.record_result(champion_id, 70, self.db)
            improver.record_result(challenger_id, 95, self.db)
        for _ in range(4):
            improver.record_human_decision(challenger_id, False, self.db)   # humans reject it
            improver.record_human_decision(champion_id, True, self.db)
        self.assertIsNone(improver.promote_if_better("draft", self.db))

    def test_evolve_adds_one_valid_challenger_then_waits(self):
        first = improver.evolve("draft", self.db)
        self.assertIsNotNone(first)
        self.assertIsNone(improver.evolve("draft", self.db))              # a challenger is still waiting
        conn = improver.connect(self.db)
        text = conn.execute("SELECT text FROM prompt_versions WHERE id = ?", (first,)).fetchone()[0]
        conn.close()
        self.assertIn("{topic}", text)
        text.format(topic="x")                                           # must stay a valid template

    def test_evolve_uses_each_tweak_once_and_is_capped(self):
        seen = set()
        for _ in range(improver.MAX_VARIANTS + 5):
            vid = improver.evolve("draft", self.db)
            if vid is None:
                # let the waiting challenger finish its trials so another can be created
                conn = improver.connect(self.db)
                conn.execute("UPDATE prompt_versions SET trials = ? WHERE name = 'draft' AND active = 0",
                             (improver.MIN_TRIALS,))
                conn.commit(); conn.close()
                vid = improver.evolve("draft", self.db)
                if vid is None:
                    break
            seen.add(vid)
        conn = improver.connect(self.db)
        texts = [r[0] for r in conn.execute("SELECT text FROM prompt_versions WHERE name = 'draft'")]
        conn.close()
        self.assertEqual(len(texts), len(set(texts)))                    # no duplicates
        self.assertLessEqual(len(texts), improver.MAX_VARIANTS)

    def test_evolve_accepts_good_llm_template_and_rejects_bad(self):
        good = improver.evolve("draft", self.db, llm=lambda p: "Write a clear page about {topic}. Be honest.")
        conn = improver.connect(self.db)
        self.assertEqual(conn.execute("SELECT text FROM prompt_versions WHERE id = ?", (good,)).fetchone()[0],
                         "Write a clear page about {topic}. Be honest.")
        conn.execute("UPDATE prompt_versions SET trials = ? WHERE id = ?", (improver.MIN_TRIALS, good))
        conn.commit(); conn.close()
        bad = improver.evolve("draft", self.db, llm=lambda p: "No placeholder and {unknown} braces")
        conn = improver.connect(self.db)
        text = conn.execute("SELECT text FROM prompt_versions WHERE id = ?", (bad,)).fetchone()[0]
        conn.close()
        self.assertNotIn("{unknown}", text)                              # fell back to a safe tweak

    def test_revert_with_no_history(self):
        self.assertIsNone(improver.revert("draft", self.db))

    def test_verify_proposal(self):
        src = os.path.join(self.tmp.name, "src")
        os.makedirs(os.path.join(src, "tests"))
        with open(os.path.join(src, "mod.py"), "w") as f:
            f.write("def f(): return 1\n")
        with open(os.path.join(src, "tests", "test_mod.py"), "w") as f:
            f.write("import unittest\nfrom mod import f\n\n\nclass T(unittest.TestCase):\n"
                    "    def test_f(self):\n        self.assertEqual(f(), 2)\n\n\nif __name__ == '__main__':\n"
                    "    unittest.main()\n")
        good = ("diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n"
                "-def f(): return 1\n+def f(): return 2\n")
        bad = ("diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n"
               "-def f(): return 999\n+def f(): return 2\n")
        proposals = os.path.join(self.tmp.name, "proposals")
        os.makedirs(proposals)

        def propose(diff):
            aid = guardrails.request_approval("code_patch", "t", diff, db_path=self.db)
            with open(os.path.join(proposals, f"proposal-{aid}.diff"), "w") as f:
                f.write(diff)
            return aid

        ok_id, bad_id = propose(good), propose(bad)
        verify = lambda aid: improver.verify_proposal(aid, self.db, src, proposals)
        self.assertFalse(verify(ok_id)["ok"])                         # not approved yet
        guardrails.decide(ok_id, True, self.db)
        guardrails.decide(bad_id, True, self.db)
        self.assertTrue(verify(ok_id)["ok"])
        self.assertIn("does not apply", verify(bad_id)["why"])
        with open(os.path.join(src, "mod.py")) as f:                  # real tree untouched
            self.assertEqual(f.read(), "def f(): return 1\n")


if __name__ == "__main__":
    unittest.main()
