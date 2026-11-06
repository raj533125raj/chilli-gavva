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


class LegacyPriceIdentityTests(Base):
    def test_every_niche_counts_old_done_tasks_as_the_same_price_free_topics(self):
        from pathlib import Path
        for path in (Path(ROOT) / 'recipes').glob('niche_*.yaml'):
            with self.subTest(recipe=path.name):
                recipe = recipe_runner._load_yaml(str(path))
                db = os.path.join(self.tmp.name, path.stem + '.db')
                originals = []
                for index, task in enumerate(recipe['tasks']):
                    payload = dict(task['payload'])
                    old_topic = payload['topic'].replace(', sold on', ', priced 5-9 USD, sold on', 1)
                    self.assertNotEqual(old_topic,payload['topic'])
                    self.assertEqual(nt.split_topic(old_topic),nt.split_topic(payload['topic']))
                    payload['topic'] = old_topic
                    workforce.enqueue('research' if index % 2 == 0 else 'draft',payload,db_path=db)
                    originals.append(old_topic)
                conn = workforce.connect(db)
                conn.execute("UPDATE tasks SET status = 'done'")
                conn.commit();conn.close()
                self.assertEqual(nt.fresh_tasks(recipe,db_path=db,llm=None),[])
                conn = workforce.connect(db)
                stored = [json.loads(row[0])['topic'] for row in conn.execute('SELECT payload FROM tasks ORDER BY id')]
                conn.close()
                self.assertEqual(stored,originals,'Deduplication must not rewrite historical task payloads')

    def test_old_used_pool_rows_block_new_price_free_starters(self):
        recipe = recipe_runner._load_yaml(RECIPE)
        conn = nt.connect(self.db)
        for task in recipe['tasks']:
            idea = nt.split_topic(task['payload']['topic'])[1]
            conn.execute("INSERT INTO niche_topics (niche,idea,status) VALUES (?,?,'used')",
                         (recipe['name'],idea + ', priced 5-9 USD'))
        conn.commit();conn.close()
        self.assertEqual(nt.fresh_tasks(recipe,db_path=self.db,llm=None),[])
        self.assertTrue(all('priced' not in idea for idea in nt.done_topics(recipe['name'],self.db)))

    def test_ai_price_variants_do_not_repeat_used_topics_or_reintroduce_prices(self):
        self.run_niche()
        recipe = recipe_runner._load_yaml(RECIPE)
        old_idea = nt.split_topic(recipe['tasks'][0]['payload']['topic'])[1]
        reply = old_idea + ', priced 5-9 USD\nSinking funds planner for families saving for school fees, priced 5-9 USD'
        result = self.run_niche(reply)
        self.assertEqual(len(result['enqueued']),1)
        topics = self.queued_topics()
        self.assertEqual(sum('irregular-income' in topic for topic in topics),1)
        self.assertTrue(all('priced' not in topic for topic in topics))


class HelperTests(unittest.TestCase):
    def test_similar(self):
        self.assertTrue(nt.similar("Debt snowball payoff tracker for adults with several debts",
                                   "debt snowball payoff tracker with a progress chart for adults paying several debts"))
        self.assertFalse(nt.similar("Wedding budget for couples", "Sinking funds planner for school fees"))

    def test_split_topic(self):
        agent, idea, suffix = nt.split_topic("[A Agent] some idea here, priced 5-9 USD, sold on Gumroad")
        self.assertEqual((agent, idea, suffix), ("A Agent", "some idea here", ", sold on Gumroad"))
        self.assertIsNone(nt.split_topic("no agent label"))

    def test_price_metadata_is_ignored_but_years_and_page_counts_are_retained(self):
        plain = '[A Agent] 2027 focus planner with 12 monthly pages, sold on Gumroad'
        for price in ('priced 5-9 USD','priced at $5-$9','priced between 5 and 9 USD',
                      'priced USD 5 to USD 9','priced $5','priced 5 USD','priced 5–9 USD'):
            with self.subTest(price=price):
                old = plain.replace(', sold on',', ' + price + ', sold on')
                self.assertEqual(nt.normalize_topic(old),plain)
                self.assertEqual(nt.split_topic(old),nt.split_topic(plain))
                self.assertTrue(nt.similar(old,plain))
        self.assertEqual(nt.normalize_topic('2027 planner with 12 monthly pages'),'2027 planner with 12 monthly pages')
        self.assertEqual(nt.normalize_topic('Couples paying in USD'),'Couples paying in USD')

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
