"""Tests for the QA Checker agent (offline: every LLM call is a stub)."""
import json
import os
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails  # noqa: E402
import workforce  # noqa: E402

DRAFT = ("PRODUCT NAME: Freelancer Budget Sheet\n"
         "BUYER: freelancers whose income changes every month\n"
         "PRICE: 7 USD\n"
         "PLATFORM: Payhip\n"
         "WHAT'S INSIDE: Instructions tab; Income Log (Date, Client, Amount); Bills (Bill, Due, Cost); "
         "Buffer Fund (Month, Target, Saved); Dashboard with monthly totals.\n"
         "HOW TO USE: 1. Make a copy. 2. Log each payment. 3. Check the Dashboard weekly.\n"
         "LISTING TITLE: Freelancer Budget Spreadsheet for Irregular Income\n"
         "LISTING DESCRIPTION: A calm monthly budget for people paid at different times. Log income as it "
         "arrives and see which bills are covered.\n"
         "SEARCH TAGS: freelancer budget, irregular income, google sheets budget, budget template, money tracker")
REWRITTEN = DRAFT.replace("A calm monthly budget", "A clear, calm monthly budget")


def checker_json(score, verdict, issues=()):
    return json.dumps({"score": score, "verdict": verdict, "issues": list(issues)})


def make_llm(checker_replies, rewrite=REWRITTEN, draft=DRAFT):
    """Stub LLM: answers by prompt type; checker replies are used in order (last one repeats)."""
    calls = {"checker": 0, "rewrite": 0, "draft": 0, "sizes": []}

    def llm(prompt, size=None):
        calls["sizes"].append(size)
        if prompt.startswith("You are the QA Checker"):
            i = min(calls["checker"], len(checker_replies) - 1)
            calls["checker"] += 1
            reply = checker_replies[i]
            if isinstance(reply, Exception):
                raise reply
            return reply
        if prompt.startswith("Rewrite the product draft"):
            calls["rewrite"] += 1
            return rewrite
        calls["draft"] += 1
        return draft
    return llm, calls


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "qa.db")
        self._old = (guardrails.KILL_FILE, guardrails.CONFIG_PATH, workforce.CONFIG_PATH)
        guardrails.KILL_FILE = os.path.join(self.tmp.name, "KILL_SWITCH")
        guardrails.CONFIG_PATH = os.path.join(self.tmp.name, "config.json")
        workforce.CONFIG_PATH = os.path.join(self.tmp.name, "wf_config.json")
        os.environ.pop("AGENT_PAUSED", None)
        # Only a human may approve or reject: the checker must never call decide()
        self._decide = patch.object(guardrails, "decide", side_effect=AssertionError("checker called decide()"))
        self._decide.start()

    def tearDown(self):
        self._decide.stop()
        guardrails.KILL_FILE, guardrails.CONFIG_PATH, workforce.CONFIG_PATH = self._old
        self.tmp.cleanup()

    def run_pipeline(self, llm):
        workforce.enqueue("draft", {"topic": "[Budget Sheets Agent] freelancer budget, 5-9 USD, Payhip",
                                    "research": "notes"}, db_path=self.db)
        return workforce.run_once(max_tasks=20, db_path=self.db, llm=llm, rng=random.Random(1))

    def only_draft(self):
        items = guardrails.pending(self.db)
        self.assertEqual(len(items), 1)
        return guardrails.get_approval(items[0]["id"], self.db)

    def task_kinds(self):
        conn = workforce.connect(self.db)
        kinds = [r[0] for r in conn.execute("SELECT kind FROM tasks ORDER BY id")]
        conn.close()
        return kinds


class PassTests(Base):
    def test_pass_waits_for_human(self):
        llm, calls = make_llm([checker_json(90, "pass")])
        self.assertEqual(self.run_pipeline(llm)["failed"], 0)
        item = self.only_draft()
        self.assertEqual(item["status"], "pending")
        self.assertIn("CHECKER: PASS", item["review"])
        self.assertTrue(item["review"].startswith("score "))
        import qa_checklist
        # the buyer checklist is the score; the AI checker's own number is shown as a second opinion
        self.assertEqual(guardrails._parse_score(item["review"]), qa_checklist.score(item["payload"])["score"])
        self.assertIn("(AI 90)", item["review"])
        self.assertEqual(self.task_kinds(), ["draft", "review"])
        self.assertEqual(calls["rewrite"], 0)

    def test_checker_uses_large_model(self):
        llm, calls = make_llm([checker_json(90, "pass")])
        self.run_pipeline(llm)
        self.assertEqual(calls["sizes"], ["large", "large"])  # draft + checker

    def test_risky_rule_hit_turns_pass_into_revise(self):
        llm, _ = make_llm([checker_json(95, "pass"), checker_json(95, "pass")],
                          draft=DRAFT + " Guaranteed results.")
        self.run_pipeline(llm)
        self.assertIn("rewrite", self.task_kinds())


class ReviseTests(Base):
    def test_revise_rewrites_once_then_stops(self):
        llm, calls = make_llm([checker_json(55, "revise", ["missing 5 search tags", "no buyer named"])])
        self.assertEqual(self.run_pipeline(llm)["failed"], 0)
        self.assertEqual(self.task_kinds(), ["draft", "review", "rewrite", "review", "rewrite", "review"])
        self.assertEqual(calls["rewrite"], 2)
        self.assertEqual(calls["checker"], 3)  # initial check + 2 retries checked again
        item = self.only_draft()               # replaced in place, no duplicate in the queue
        self.assertEqual(item["payload"], REWRITTEN)
        self.assertTrue(item["title"].endswith("(rewritten)"))
        self.assertEqual(item["status"], "pending")
        self.assertIn("CHECKER: REVISE", item["review"])
        self.assertIn("already rewritten", item["review"])

    def test_rewrite_prompt_carries_checker_issues(self):
        seen = []
        llm, _ = make_llm([checker_json(55, "revise", ["missing 5 search tags"]), checker_json(85, "pass")])

        def recording(prompt, size=None):
            seen.append(prompt)
            return llm(prompt, size=size)
        self.run_pipeline(recording)
        rewrite_prompt = next(p for p in seen if p.startswith("Rewrite the product draft"))
        self.assertIn("- missing 5 search tags", rewrite_prompt)
        self.assertIn(DRAFT[:40], rewrite_prompt)
        self.assertIn("CHECKER: PASS", self.only_draft()["review"])

    def test_failed_rewrite_keeps_original(self):
        llm, _ = make_llm([checker_json(55, "revise", ["unclear"])], rewrite=None)
        self.run_pipeline(llm)
        item = self.only_draft()
        self.assertEqual(item["payload"], DRAFT)
        self.assertIn("rewrite failed, original kept", item["review"])

    def test_no_rewrite_after_human_decided(self):
        aid = guardrails.request_approval("public_draft", "Draft: x", DRAFT, db_path=self.db)
        conn = guardrails.connect(self.db)
        conn.execute("UPDATE approvals SET status = 'rejected' WHERE id = ?", (aid,))
        conn.commit()
        conn.close()
        llm, calls = make_llm([checker_json(50, "revise")])
        ctx = {"db_path": self.db, "llm": llm, "rng": random.Random(1)}
        result = workforce.step_rewrite({"payload": {"approval_id": aid, "issues": ["x"]}}, ctx)
        self.assertIn("skipped", result["summary"])
        self.assertEqual(calls["rewrite"], 0)


class RejectTests(Base):
    def test_reject_is_marked_but_left_for_human(self):
        llm, calls = make_llm([checker_json(10, "reject", ["promises a job offer"])])
        self.run_pipeline(llm)
        item = self.only_draft()
        self.assertEqual(item["status"], "pending")
        self.assertIn("CHECKER: REJECT", item["review"])
        self.assertIn("promises a job offer", item["review"])
        self.assertEqual(self.task_kinds(), ["draft", "review"])
        self.assertEqual(calls["rewrite"], 0)


class FallbackTests(Base):
    def assert_rule_only(self):
        item = self.only_draft()
        import qa_checklist
        self.assertIn("CHECKER: unavailable (checklist only)", item["review"])
        self.assertEqual(guardrails._parse_score(item["review"]), qa_checklist.score(item["payload"])["score"])
        self.assertEqual(self.task_kinds(), ["draft", "review"])

    def test_bad_json_falls_back_to_rules(self):
        llm, _ = make_llm(["Looks great to me!"])
        self.run_pipeline(llm)
        self.assert_rule_only()

    def test_wrong_shape_json_falls_back(self):
        llm, _ = make_llm(['{"score": "high", "verdict": "ok"}'])
        self.run_pipeline(llm)
        self.assert_rule_only()

    def test_llm_unavailable_falls_back(self):
        llm, _ = make_llm([None])
        self.run_pipeline(llm)
        self.assert_rule_only()

    def test_llm_error_falls_back(self):
        llm, _ = make_llm([RuntimeError("rate limit")])
        self.assertEqual(self.run_pipeline(llm)["failed"], 0)
        self.assert_rule_only()


class ParseTests(unittest.TestCase):
    def test_reads_json_inside_code_fence(self):
        got = workforce.parse_checker_reply('```json\n{"score": 72, "verdict": "PASS", "issues": ["a"]}\n```')
        self.assertEqual(got, {"score": 72, "verdict": "pass", "issues": ["a"]})

    def test_clamps_score_and_caps_issues(self):
        got = workforce.parse_checker_reply(checker_json(150, "revise", [str(i) for i in range(9)] + [""]))
        self.assertEqual(got["score"], 100)
        self.assertEqual(len(got["issues"]), 5)

    def test_rejects_unusable_replies(self):
        for reply in (None, "", "no json", "{bad json}", "[1, 2]", '{"score": true, "verdict": "pass"}',
                      '{"score": 50, "verdict": "maybe"}', '{"verdict": "pass"}'):
            with self.subTest(reply=reply):
                self.assertIsNone(workforce.parse_checker_reply(reply))


class AgentListTests(unittest.TestCase):
    def test_checker_is_its_own_agent(self):
        names = [a["name"] for a in workforce.AGENTS]
        self.assertIn("QA Checker", names)
        self.assertEqual(workforce.KIND_TO_AGENT["review"], "QA Checker")
        self.assertEqual(workforce.KIND_TO_AGENT["rewrite"], "Draft Worker")
        self.assertIn("rewrite", workforce.STEPS)


if __name__ == "__main__":
    unittest.main()
