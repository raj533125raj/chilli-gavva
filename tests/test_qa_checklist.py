"""Tests for the buyer checklist (qa_checklist.py): fair scoring, clear reasons, advice only."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guardrails  # noqa: E402
import qa_checklist as q  # noqa: E402
import telegram_inbox as ti  # noqa: E402

GOOD = """PRODUCT NAME: Notion Student Semester Planner & Grade Tracker

BUYER: University students who need a single system to keep track of every course, assignment and exam date.

PRICE: 14 USD

PLATFORM: Gumroad

WHAT'S INSIDE:
- Dashboard page with quick links
- Courses database
- Assignments database with a due-date calendar
- Exams database
- Grade tracker with GPA formula
- Weekly planner

HOW TO USE:
1. Duplicate the template.
2. Add your courses.
3. Log assignments and exams.

LISTING TITLE: Notion Semester Planner for University Students

LISTING DESCRIPTION: Unlike a blank page, this planner is built for students who juggle many courses. You see every deadline in one calendar. You log grades and watch your GPA update. It keeps your semester calm and organised.

SEARCH TAGS: notion planner, student planner, semester planner, grade tracker, study template"""

PLACEHOLDER = ("# Free tool: [ADHD Planners Agent] printable energy-based daily planner f\n\nBenefit 1: Saves time.\n"
               "Benefit 2: Works in your browser.\n\nGet started: try the tool now and subscribe for updates.")


class ScoreTests(unittest.TestCase):
    def test_good_draft_scores_high_and_same_every_time(self):
        first = q.score(GOOD)
        self.assertGreaterEqual(first["score"], 95)
        self.assertEqual(first, q.score(GOOD))
        self.assertEqual(sum(i[2] for i in first["items"]), 100)
        self.assertEqual(len(first["items"]), 9)

    def test_placeholder_draft_scores_very_low_with_reasons(self):
        r = q.score(PLACEHOLDER)
        self.assertLessEqual(r["score"], 10)
        self.assertEqual(r["top_reasons"], ["Contents not listed", "No buyer named", "No clear problem it solves"])

    def test_bold_labels_without_colons_are_read(self):
        """The bug that cost draft #5 40 points: '**PRODUCT NAME**' on its own line was 'missing'."""
        bold = GOOD
        for label in ("PRODUCT NAME", "BUYER", "PRICE", "PLATFORM", "WHAT'S INSIDE", "HOW TO USE", "LISTING TITLE",
                      "LISTING DESCRIPTION", "SEARCH TAGS"):
            bold = bold.replace(f"{label}:", f"**{label}**  \n")
        self.assertEqual(q.score(bold)["score"], q.score(GOOD)["score"])
        self.assertEqual(set(q.sections(bold)), set(q.LABELS))

    def test_long_buyer_line_is_read(self):
        long_buyer = GOOD.replace("exam date.", "exam date, " + "and every grade they get " * 12 + ".")
        self.assertIn("buyer", q.sections(long_buyer))

    def test_missing_difference_and_weak_title(self):
        weak = GOOD.replace("Unlike a blank page, this", "This").replace(
            "LISTING TITLE: Notion Semester Planner for University Students", "LISTING TITLE: Semester thing")
        r = q.score(weak)
        self.assertIn("Say why it beats a free template", r["top_reasons"] + [i[3] for i in r["items"]])
        self.assertIn("Title: under 80 chars, say what + who", [i[3] for i in r["items"]])
        self.assertLess(r["score"], q.score(GOOD)["score"])

    def test_risky_claim_caps_score_and_comes_first(self):
        r = q.score(GOOD + "\nGuaranteed passive income for students!")
        self.assertLessEqual(r["score"], 40)
        self.assertTrue(r["top_reasons"][0].startswith("Risky claim"))

    def test_hype_word_costs_points(self):
        r = q.score(GOOD.replace("It keeps", "The ultimate planner, it keeps"))
        self.assertEqual(dict((i[0], i[1]) for i in r["items"])["no false claims"], 2)

    def test_price_without_usd(self):
        r = q.score(GOOD.replace("PRICE: 14 USD", "PRICE: cheap"))
        self.assertIn("No USD price", [i[3] for i in r["items"]])

    def test_reasons_are_short(self):
        for text in (GOOD, PLACEHOLDER, GOOD.replace("SEARCH TAGS", "TAGS")):
            for reason in q.score(text)["top_reasons"]:
                self.assertLessEqual(len(reason), 45, reason)


class AdviceOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "q.db")

    def test_rescore_keeps_status_and_old_review_and_runs_once(self):
        a = guardrails.request_approval("public_draft", "Draft A", PLACEHOLDER, db_path=self.db,
                                        review="score 18/100 (rules 30, checker 5) | CHECKER: REJECT | issues: x")
        b = guardrails.request_approval("public_draft", "Draft B", GOOD, db_path=self.db,
                                        review="score 98/100 (rules 100, checker 95) | CHECKER: PASS")
        guardrails.decide(a, True, self.db)
        self.assertEqual(q.rescore_old_reviews(self.db), [a, b])
        ra, rb = guardrails.get_approval(a, self.db), guardrails.get_approval(b, self.db)
        self.assertEqual((ra["status"], rb["status"]), ("approved", "pending"))
        self.assertTrue(ra["review"].startswith("score 5/100 (checklist v2)"))
        self.assertIn("before: score 18/100 (rules 30, checker 5) | CHECKER: REJECT", ra["review"])
        self.assertNotIn("issues: x", ra["review"])
        self.assertEqual(q.rescore_old_reviews(self.db), [])

    def test_telegram_card_shows_score_and_three_short_reasons(self):
        a = guardrails.request_approval("public_draft", "Draft: [ADHD Planners Agent] planner", PLACEHOLDER,
                                        db_path=self.db)
        q.rescore_old_reviews(self.db)
        card = ti.draft_card(guardrails.pending(self.db)[0], db_path=self.db)
        lines = card.splitlines()
        self.assertEqual(lines[0], f"#{a} Draft: [ADHD Planners Agent] planner")
        self.assertEqual(lines[1], "Checker: 5/100")
        self.assertEqual(lines[2:], ["- Contents not listed", "- No buyer named"])

    def test_card_without_review(self):
        self.assertIn("not checked yet", ti.draft_card({"id": 9, "title": "x", "review": None}))

    def test_low_score_and_ai_reject_never_decide(self):
        import workforce
        a = guardrails.request_approval("public_draft", "Draft x", PLACEHOLDER, db_path=self.db)
        reply = '{"score": 3, "verdict": "reject", "issues": ["unusable"]}'
        with mock.patch.object(workforce.improver, "record_result", lambda *a, **k: None):
            workforce.step_review({"payload": {"approval_id": a, "version_id": 1}},
                                  {"db_path": self.db, "llm": lambda p, size=None: reply, "rng": None})
        item = guardrails.get_approval(a, self.db)
        self.assertEqual(item["status"], "pending")          # only a human approves or rejects
        self.assertTrue(item["review"].startswith("score 5/100 (checklist v2)"))
        self.assertIn("CHECKER: REJECT (AI 3)", item["review"])


if __name__ == "__main__":
    unittest.main()
