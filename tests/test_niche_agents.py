"""Tests for the 5 niche agents: recipes, weekday rotation and the approval-queue guard."""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import recipe_runner  # noqa: E402
import scheduler  # noqa: E402

NICHES = ["niche_budget_sheets", "niche_freelancer_trackers", "niche_adhd_planners",
          "niche_planners_2027", "niche_resume_bundle"]
MONDAY = datetime.date(2026, 10, 12)
ROTATING = {"name": "night", "hour": 21, "rotate": ", ".join(NICHES), "recipe": NICHES[0]}


def _real_schedule():
    return scheduler._load_schedule(os.path.join(BASE_DIR, "schedule.yaml"))


def _recipe(name):
    return recipe_runner._load_yaml(os.path.join(BASE_DIR, "recipes", name + ".yaml"))


class ResolveRecipeTests(unittest.TestCase):
    def test_plain_slot_uses_recipe(self):
        self.assertEqual(scheduler.resolve_recipe({"name": "a", "recipe": "r"}), "r")

    def test_slot_without_recipe_is_empty(self):
        self.assertEqual(scheduler.resolve_recipe({"name": "a"}), "")

    def test_rotation_follows_weekday(self):
        self.assertEqual(MONDAY.weekday(), 0)
        picked = [scheduler.resolve_recipe(ROTATING, MONDAY + datetime.timedelta(days=i)) for i in range(7)]
        self.assertEqual(picked, NICHES + NICHES[:2])

    def test_every_niche_runs_each_week(self):
        week = {scheduler.resolve_recipe(ROTATING, MONDAY + datetime.timedelta(days=i)) for i in range(7)}
        self.assertEqual(week, set(NICHES))

    def test_rotation_ignores_blank_names(self):
        slot = {"name": "n", "rotate": "a, , b,"}
        self.assertEqual(scheduler.resolve_recipe(slot, MONDAY), "a")
        self.assertEqual(scheduler.resolve_recipe(slot, MONDAY + datetime.timedelta(days=1)), "b")

    def test_schedule_parser_reads_rotate(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "s.yaml")
            with open(path, "w") as f:
                f.write("slots:\n  - name: night\n    hour: 21\n    rotate: x, y\n    recipe: x\n")
            slot = scheduler._load_schedule(path)[0]
            self.assertEqual(slot["rotate"], "x, y")
            self.assertEqual(scheduler.resolve_recipe(slot, MONDAY + datetime.timedelta(days=1)), "y")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RunSlotTests(unittest.TestCase):
    def setUp(self):
        self._orig_full = scheduler._queue_full
        self._orig_run = recipe_runner.run_recipe
        self.calls = []
        recipe_runner.run_recipe = lambda path, db_path=None, dry_run=False: (
            self.calls.append(os.path.basename(path)) or {"name": "x", "enqueued": [], "errors": []})

    def tearDown(self):
        scheduler._queue_full = self._orig_full
        recipe_runner.run_recipe = self._orig_run

    def test_runs_todays_niche(self):
        scheduler._queue_full = lambda db_path=None: False
        scheduler.run_slot(ROTATING, today=MONDAY + datetime.timedelta(days=2))
        self.assertEqual(self.calls, ["niche_adhd_planners.yaml"])

    def test_skips_niche_when_queue_full(self):
        scheduler._queue_full = lambda db_path=None: True
        result = scheduler.run_slot(ROTATING, today=MONDAY)
        self.assertTrue(result.get("skipped"))
        self.assertIn("queue is full", result["reason"])
        self.assertEqual(self.calls, [])

    def test_plain_slot_ignores_queue_guard(self):
        scheduler._queue_full = lambda db_path=None: True
        scheduler.run_slot({"name": "m", "recipe": "niche_budget_sheets"})
        self.assertEqual(self.calls, ["niche_budget_sheets.yaml"])

    def test_dry_run_ignores_queue_guard(self):
        scheduler._queue_full = lambda db_path=None: True
        scheduler.run_slot(ROTATING, dry_run=True, today=MONDAY)
        self.assertEqual(self.calls, ["niche_budget_sheets.yaml"])


class QueueFullTests(unittest.TestCase):
    def setUp(self):
        try:
            import guardrails
            import workforce
        except ImportError:
            self.skipTest("guardrails/workforce not available")
        self.guardrails, self.workforce = guardrails, workforce
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "q.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_queue_is_not_full(self):
        self.assertFalse(scheduler._queue_full(self.db))

    def test_full_queue_at_workforce_limit(self):
        for i in range(self.workforce.MAX_PENDING_APPROVALS):
            self.guardrails.request_approval("public_draft", f"old {i}", "x", db_path=self.db)
        self.assertTrue(scheduler._queue_full(self.db))


class RealScheduleTests(unittest.TestCase):
    def test_slot_windows_do_not_overlap(self):
        hours = [s["hour"] for s in _real_schedule()]
        for h in range(24):
            matches = [x for x in hours if abs(x - h) <= 1]
            self.assertLessEqual(len(matches), 1, f"hour {h} matches slots {matches}")

    def test_night_slot_rotates_all_niches(self):
        night = next(s for s in _real_schedule() if s["name"] == "night")
        week = {scheduler.resolve_recipe(night, MONDAY + datetime.timedelta(days=i)) for i in range(7)}
        self.assertEqual(week, set(NICHES))

    def test_fallback_schedule_is_the_night_niche_slot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            slots = scheduler._load_schedule(os.path.join(d, "schedule.yaml"))
        self.assertEqual([s["name"] for s in slots], ["night"])
        self.assertEqual([n.strip() for n in slots[0]["rotate"].split(",")], NICHES)
        for old in ("daily_chilli_content", "shrimp_market_brief", "tool_idea_sprint"):
            self.assertNotIn(old, scheduler.DEFAULT_SCHEDULE)

    def test_old_sprint_is_retired(self):
        self.assertFalse(os.path.exists(os.path.join(BASE_DIR, "recipes", "digital_product_sprint.yaml")))
        for s in _real_schedule():
            self.assertNotIn("digital_product_sprint", s.get("recipe", "") + s.get("rotate", ""))


class NicheRecipeTests(unittest.TestCase):
    def test_each_niche_recipe_is_valid(self):
        for name in NICHES:
            with self.subTest(name=name):
                recipe = _recipe(name)
                self.assertEqual(recipe["name"], name)
                self.assertTrue(1 <= len(recipe["tasks"]) <= 3, "keep new drafts to 3 a night")
                for t in recipe["tasks"]:
                    self.assertEqual(t["kind"], "research")
                    self.assertTrue(t["payload"].get("agent"))

    def test_topics_carry_agent_name_and_listing_brief(self):
        for name in NICHES:
            for t in _recipe(name)["tasks"]:
                topic = t["payload"]["topic"]
                with self.subTest(topic=topic[:50]):
                    # The draft title shows topic[:60], so the agent name must sit at the start
                    self.assertIn("[" + t["payload"]["agent"] + "]", topic[:60])
                    self.assertIn("USD", topic)
                    self.assertTrue("Payhip" in topic or "Gumroad" in topic)
                    for part in ("listing title", "listing description", "5 search tags"):
                        self.assertIn(part, topic)
                    self.assertNotIn("{", topic)  # topics go through str.format()

    def test_topics_avoid_banned_and_off_limits_words(self):
        try:
            import workforce
            banned = list(workforce.BANNED)
        except ImportError:
            banned = []
        off_limits = ["fish", "shrimp", "aquaculture"]
        for name in NICHES:
            with open(os.path.join(BASE_DIR, "recipes", name + ".yaml"), encoding="utf-8") as f:
                text = f.read().lower()
            for word in banned + off_limits:
                self.assertNotIn(word, text, f"{name} contains '{word}'")

    def test_adhd_agent_forbids_medical_claims(self):
        for t in _recipe("niche_adhd_planners")["tasks"]:
            self.assertIn("no medical", t["payload"]["topic"])


if __name__ == "__main__":
    unittest.main()
