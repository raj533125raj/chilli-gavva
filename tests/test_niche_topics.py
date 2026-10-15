"""Tests for niche_topics.py: topic pool per niche, AI suggestions, and no repeated topics."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import niche_topics as nt  # noqa: E402
import recipe_runner  # noqa: E402
import workforce  # noqa: E402

RECIPE = os.path.join(ROOT, "recipes", "niche_budget_sheets.yaml")
AI_IDEAS = "\n".join([
    "1. Sinking funds planner for families saving for school fees, holidays and car repairs",
    "2. - Paycheck-to-paycheck budget for nurses who work rotating night shifts",
    "3) Wedding budget tracker for couples planning a small ceremony on a fixed budget",
    "4. Google Sheets irregular-income monthly budget for freelancers and gig workers",   # repeat of a starter
    "5. Weekly grocery and recipe cost planner",                                            # off-limits topic
    "6. Moving-house budget for renters relocating to a new city",
])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "n.db")
        self.prompts = []

    def llm(self, reply):
        def call(prompt, size=None):
            self.prompts.append(prompt)
            return reply
        return call

    def run_niche(self, reply=None):
        return recipe_runner.run_recipe(RECIPE, db_path=self.db, llm=self.llm(reply))

    def queued_topics(self):
        conn = workforce.connect(self.db)
        rows = conn.execute("SELECT payload FROM tasks ORDER BY id").fetchall()
        conn.close()
        return [json.loads(p)["topic"] for (p,) in rows]


class PoolTests(Base):
    def test_first_run_uses_starter_topics_without_ai(self):
        result = self.run_niche()
        self.assertEqual(len(result["enqueued"]), 3)
        self.assertEqual(self.prompts, [])                       # pool had enough: no AI call
        recipe = recipe_runner._load_yaml(RECIPE)
        self.assertEqual(self.queued_topics(), [t["payload"]["topic"] for t in recipe["tasks"]])

    def test_second_run_gets_new_ai_topics_never_repeats(self):
        self.run_niche()
        result = self.run_niche(AI_IDEAS)
        self.assertEqual(len(result["enqueued"]), 3)
        topics = self.queued_topics()
        self.assertEqual(len(topics), 6)
        self.assertEqual(len(set(topics)), 6)
        new = topics[3:]
        self.assertTrue(all(t.startswith("[Budget Sheets Agent] ") for t in new))
        self.assertTrue(all("sold on Gumroad" in t for t in new))          # recipe wording kept
        self.assertTrue(all("no financial advice" in t for t in new))      # strictest safety note kept
        self.assertIn("Sinking funds planner", new[0])
        joined = " ".join(new).lower()
        self.assertNotIn("grocery", joined)                                 # off-limits blocked
        self.assertEqual(sum("irregular-income" in t for t in topics), 1)   # repeat blocked
        self.assertIn("irregular-income monthly budget", self.prompts[0])   # AI is told what is done

    def test_no_repeats_when_ai_is_down(self):
        self.run_niche()
        result = self.run_niche(None)
        self.assertEqual(result["enqueued"], [])
        self.assertIn("nothing repeated", result["errors"][0])
        self.assertEqual(len(self.queued_topics()), 3)

    def test_leftover_ai_ideas_are_used_next_time_without_asking(self):
        self.run_niche()
        self.run_niche(AI_IDEAS)          # 4 usable ideas: 3 used, 1 left
        self.prompts.clear()
        result = self.run_niche(AI_IDEAS)
        self.assertGreaterEqual(len(result["enqueued"]), 1)
        self.assertEqual(len(set(self.queued_topics())), len(self.queued_topics()))

    def test_old_tasks_count_as_done(self):
        recipe = recipe_runner._load_yaml(RECIPE)
        workforce.enqueue("research", recipe["tasks"][0]["payload"], db_path=self.db)   # ran before the pool
        self.run_niche(AI_IDEAS)
        topics = self.queued_topics()
        self.assertEqual(len(topics), len(set(topics)))
        self.assertEqual(sum("irregular-income" in t for t in topics), 1)

    def test_dry_run_and_other_recipes_unchanged(self):
        result = recipe_runner.run_recipe(RECIPE, db_path=self.db, dry_run=True, llm=self.llm(AI_IDEAS))
        self.assertEqual(len(result["enqueued"]), 3)
        self.assertEqual(self.prompts, [])
        self.assertIsNone(nt.fresh_tasks({"name": "x", "tasks": [{"kind": "research",
                                                                   "payload": {"topic": "plain topic"}}]}, self.db))


class HelperTests(unittest.TestCase):
    def test_similar(self):
        self.assertTrue(nt.similar("Debt snowball payoff tracker for adults with several debts",
                                   "debt snowball payoff tracker with a progress chart for adults paying several debts"))
        self.assertFalse(nt.similar("Wedding budget for couples", "Sinking funds planner for school fees"))

    def test_split_topic(self):
        agent, idea, suffix = nt.split_topic("[A Agent] some idea here, priced 5-9 USD, sold on Gumroad")
        self.assertEqual((agent, idea, suffix), ("A Agent", "some idea here", ", priced 5-9 USD, sold on Gumroad"))
        self.assertIsNone(nt.split_topic("no agent label"))

    def test_clean_line(self):
        self.assertEqual(nt._clean_line("3) A planner for night-shift nurses"), "A planner for night-shift nurses")
        self.assertEqual(nt._clean_line("Ideas:"), "")
        self.assertEqual(nt._clean_line("short"), "")

    def test_prompt_has_no_private_data(self):
        prompt = nt.suggest_prompt("Budget Sheets Agent", "budget sheets", ["old idea"])
        for word in ("chat_id", "token", "$", "earn"):
            self.assertNotIn(word, prompt.lower())


if __name__ == "__main__":
    unittest.main()
