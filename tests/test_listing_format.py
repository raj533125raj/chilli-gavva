"""Tests for the product-listing draft format: improver defaults/migration and the rule checklist."""
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import improver  # noqa: E402
import workforce  # noqa: E402

OLD_DRAFT = improver.LEGACY_PROMPTS["draft"][0]
OLD_RESEARCH = improver.LEGACY_PROMPTS["research"][0]
GOOD = ("PRODUCT NAME: Energy-Based Daily Planner\n"
        "BUYER: adults with ADHD who plan their day by energy level\n"
        "PRICE: 8 USD\n"
        "PLATFORM: Payhip\n"
        "WHAT'S INSIDE: 6 printable pages: energy check-in, top 3 tasks, time blocks, brain dump, "
        "weekly reset, habit tracker. A4 and US Letter.\n"
        "HOW TO USE: 1. Print a page each morning. 2. Tick your energy level. 3. Pick tasks that fit it.\n"
        "**LISTING TITLE:** Energy-Based Daily Planner for ADHD Adults, Printable PDF\n"
        "LISTING DESCRIPTION: Plan your day around the energy you actually have. Simple pages, no clutter.\n"
        "SEARCH TAGS: adhd planner, daily planner printable, energy planner, neurodivergent planner, focus planner")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "p.db")

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, name="draft"):
        conn = improver.connect(self.db)
        out = conn.execute("SELECT id, text, active, retired FROM prompt_versions WHERE name = ? ORDER BY id",
                           (name,)).fetchall()
        conn.close()
        return out

    def seed_legacy(self):
        """A database from before the change: old champions plus old and custom challengers."""
        conn = improver.connect(self.db)
        for name, text in (("draft", OLD_DRAFT), ("research", OLD_RESEARCH)):
            conn.execute("INSERT INTO prompt_versions (name, text, active, trials) VALUES (?, ?, 1, 9)", (name, text))
        conn.execute("INSERT INTO prompt_versions (name, text, active) VALUES ('draft', ?, 0)",
                     (OLD_DRAFT + " Keep it under 120 words.",))
        conn.execute("INSERT INTO prompt_versions (name, text, active) VALUES ('draft', ?, 0)",
                     ("Write an honest landing page for {topic}.",))
        conn.execute("INSERT INTO prompt_versions (name, text, active) VALUES ('draft', ?, 0)",
                     ("Custom spec prompt for {topic}.",))
        conn.commit()
        conn.close()


class DefaultsTests(Base):
    def test_fresh_database_uses_new_defaults(self):
        self.assertEqual(improver.get_active("draft", self.db)[1], improver.DEFAULT_PROMPTS["draft"])
        self.assertEqual(improver.get_active("research", self.db)[1], improver.DEFAULT_PROMPTS["research"])
        self.assertIsNone(improver.revert("draft", self.db))  # no history needed for a fresh start

    def test_new_defaults_are_valid_templates(self):
        for text in improver.DEFAULT_PROMPTS.values():
            self.assertTrue(improver._valid_template(text))
            self.assertNotIn("landing", text.lower())

    def test_mutations_do_not_fight_the_listing_format(self):
        joined = " ".join(improver.MUTATIONS).lower()
        for bad in ("120 words", "call to action", "benefit"):
            self.assertNotIn(bad, joined)


class MigrationTests(Base):
    def test_old_prompts_are_retired_not_deleted(self):
        self.seed_legacy()
        active_id, text = improver.get_active("draft", self.db)
        self.assertEqual(text, improver.DEFAULT_PROMPTS["draft"])
        self.assertEqual(improver.get_active("research", self.db)[1], improver.DEFAULT_PROMPTS["research"])
        rows = {t: (a, r) for _, t, a, r in self.rows()}
        self.assertEqual(rows[OLD_DRAFT], (0, 1))
        self.assertEqual(rows[OLD_DRAFT + " Keep it under 120 words."], (0, 1))
        self.assertEqual(rows["Write an honest landing page for {topic}."], (0, 1))
        self.assertEqual(rows["Custom spec prompt for {topic}."], (0, 0))   # not legacy: untouched
        conn = improver.connect(self.db)
        reason = conn.execute("SELECT reason FROM prompt_history WHERE name = 'draft'").fetchone()[0]
        conn.close()
        self.assertIn("product spec", reason)

    def test_migration_runs_once(self):
        self.seed_legacy()
        improver.ensure_defaults(self.db)
        improver.ensure_defaults(self.db)
        texts = [t for _, t, _, _ in self.rows()]
        self.assertEqual(texts.count(improver.DEFAULT_PROMPTS["draft"]), 1)
        conn = improver.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM prompt_history WHERE name = 'draft'").fetchone()[0], 1)
        conn.close()

    def test_retired_versions_never_trialled_or_promoted(self):
        self.seed_legacy()
        improver.ensure_defaults(self.db)
        always_challenger = random.Random()
        always_challenger.random = lambda: 0.0
        for _ in range(5):
            picked = improver.pick_for_trial("draft", always_challenger, self.db)
            self.assertFalse(improver.is_legacy("draft", picked[1]))
        conn = improver.connect(self.db)
        conn.execute("UPDATE prompt_versions SET trials = 10, score_sum = 1000 WHERE retired = 1")
        conn.commit()
        conn.close()
        promoted = improver.promote_if_better("draft", self.db)
        self.assertTrue(promoted is None or not improver.is_legacy("draft", dict(
            (i, t) for i, t, _, _ in self.rows())[promoted["to"]]))

    def test_revert_brings_back_the_old_prompt_if_a_human_wants_it(self):
        self.seed_legacy()
        improver.ensure_defaults(self.db)
        improver.revert("draft", self.db)
        self.assertEqual(improver.get_active("draft", self.db)[1], OLD_DRAFT)  # kept, not re-upgraded


class ReviewRuleTests(unittest.TestCase):
    def test_complete_listing_scores_full(self):
        self.assertEqual(workforce.review_text(GOOD), (100, []))

    def test_no_call_to_action_needed_and_long_listings_ok(self):
        long_listing = GOOD + "\n" + ("More detail about each page. " * 80)
        score, notes = workforce.review_text(long_listing)
        self.assertGreater(len(long_listing), 1500)
        self.assertEqual(score, 100, notes)

    def test_missing_sections_price_and_tags(self):
        text = GOOD.replace("PRICE: 8 USD\n", "").replace(", focus planner", "")
        score, notes = workforce.review_text(text)
        self.assertIn("missing: price", notes)
        self.assertIn("no USD price", notes)
        self.assertIn("4 search tags (need 5)", notes)
        self.assertEqual(score, 100 - 8 - 10 - 5)

    def test_landing_page_text_scores_low(self):
        score, notes = workforce.review_text("# Free tool\nBenefit one. Try it free today and subscribe.")
        self.assertLess(score, 50)
        self.assertTrue(any(n.startswith("missing:") for n in notes))

    def test_banned_claims_still_penalised(self):
        score, notes = workforce.review_text(GOOD + "\nGuaranteed passive income!")
        self.assertEqual(score, 50)
        self.assertEqual(len([n for n in notes if n.startswith("risky")]), 2)

    def test_tag_counting(self):
        self.assertEqual(workforce.count_tags("SEARCH TAGS:\n- a\n- b\n- c\n- d\n- e\n"), 5)
        self.assertEqual(workforce.count_tags("SEARCH TAGS: a, b, c\nNOTE: x, y"), 3)
        self.assertEqual(workforce.count_tags("SEARCH TAGS: #a #b #c #d #e"), 5)
        self.assertEqual(workforce.count_tags("SEARCH TAGS: a, b, c, d, e\nThanks for reading, friends"), 5)
        self.assertEqual(workforce.count_tags("no tags here"), 0)


class TopicTests(unittest.TestCase):
    def test_clean_topic_and_off_limits(self):
        self.assertEqual(workforce.clean_topic("[Resume Bundle Agent] ATS resume"), "ATS resume")
        self.assertEqual(workforce.clean_topic("ATS resume"), "ATS resume")
        self.assertEqual(workforce.off_limits("shrimp farming technology"), "shrimp")
        self.assertEqual(workforce.off_limits("Indian Recipes this week"), "Recipes")
        self.assertIsNone(workforce.off_limits("Notion client CRM for freelancers"))
        self.assertIsNone(workforce.off_limits("selfish cookie policy notes"))  # whole words only


if __name__ == "__main__":
    unittest.main()
