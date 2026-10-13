"""Tests for scheduler.py."""
import os, sys, tempfile, shutil, unittest, datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


class TestLoadSchedule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_default_if_missing(self):
        import scheduler
        path = os.path.join(self.tmp, "sched.yaml")
        slots = scheduler._load_schedule(path)
        self.assertTrue(os.path.exists(path))
        self.assertGreater(len(slots), 0)

    def test_slots_have_required_keys(self):
        import scheduler
        path = os.path.join(self.tmp, "sched.yaml")
        slots = scheduler._load_schedule(path)
        for s in slots:
            self.assertIn("name", s)
            self.assertIn("hour", s)
            self.assertIn("recipe", s)

    def test_custom_slot(self):
        path = os.path.join(self.tmp, "custom.yaml")
        with open(path, "w") as f:
            f.write("slots:\n  - name: test\n    hour: 5\n    recipe: my_recipe\n")
        import scheduler
        slots = scheduler._load_schedule(path)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["name"], "test")
        self.assertEqual(slots[0]["hour"], 5)
        self.assertEqual(slots[0]["recipe"], "my_recipe")


class TestPickSlot(unittest.TestCase):
    def test_picks_exact_hour(self):
        import scheduler
        slots = [{"name": "a", "hour": 6, "recipe": "r"}, {"name": "b", "hour": 14, "recipe": "r"}]
        self.assertEqual(scheduler.pick_slot(slots, 6)["name"], "a")
        self.assertEqual(scheduler.pick_slot(slots, 14)["name"], "b")

    def test_picks_within_window(self):
        import scheduler
        slots = [{"name": "a", "hour": 6, "recipe": "r"}]
        self.assertIsNotNone(scheduler.pick_slot(slots, 5))
        self.assertIsNotNone(scheduler.pick_slot(slots, 7))

    def test_returns_none_outside_window(self):
        import scheduler
        slots = [{"name": "a", "hour": 6, "recipe": "r"}]
        self.assertIsNone(scheduler.pick_slot(slots, 10))
        self.assertIsNone(scheduler.pick_slot(slots, 20))

    def test_no_slots_returns_none(self):
        import scheduler
        self.assertIsNone(scheduler.pick_slot([], 6))


class TestLock(unittest.TestCase):
    def setUp(self):
        import scheduler
        self.tmp = tempfile.mkdtemp()
        self._orig = scheduler.LOCK_DIR
        scheduler.LOCK_DIR = os.path.join(self.tmp, "locks")

    def tearDown(self):
        import scheduler
        scheduler.LOCK_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_not_locked_initially(self):
        import scheduler
        self.assertFalse(scheduler._is_locked("morning", "2026-01-01"))

    def test_acquire_then_locked(self):
        import scheduler
        scheduler._acquire_lock("morning", "2026-01-01")
        self.assertTrue(scheduler._is_locked("morning", "2026-01-01"))

    def test_different_date_not_locked(self):
        import scheduler
        scheduler._acquire_lock("morning", "2026-01-01")
        self.assertFalse(scheduler._is_locked("morning", "2026-01-02"))


class TestRunNow(unittest.TestCase):
    def setUp(self):
        import scheduler
        self.tmp = tempfile.mkdtemp()
        self._orig_lock = scheduler.LOCK_DIR
        self._orig_sched = scheduler.SCHEDULE_PATH
        scheduler.LOCK_DIR = os.path.join(self.tmp, "locks")
        scheduler.SCHEDULE_PATH = os.path.join(self.tmp, "sched.yaml")

    def tearDown(self):
        import scheduler
        scheduler.LOCK_DIR = self._orig_lock
        scheduler.SCHEDULE_PATH = self._orig_sched
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skips_when_no_slot_matches(self):
        import scheduler
        # Write a schedule with hour=3, current hour will be something else
        with open(scheduler.SCHEDULE_PATH, "w") as f:
            f.write("slots:\n  - name: night\n    hour: 3\n    recipe: daily_fishery_content\n")
        # Patch pick_slot to return None
        orig = scheduler.pick_slot
        scheduler.pick_slot = lambda slots, h: None
        try:
            result = scheduler.run_now()
            self.assertTrue(result.get("skipped"))
        finally:
            scheduler.pick_slot = orig

    def test_dry_run_does_not_lock(self):
        import scheduler, recipe_runner
        with open(scheduler.SCHEDULE_PATH, "w") as f:
            f.write("slots:\n  - name: m\n    hour: 0\n    recipe: daily_fishery_content\n")
        orig_pick = scheduler.pick_slot
        orig_run = scheduler.run_slot
        scheduler.pick_slot = lambda slots, h: slots[0]
        scheduler.run_slot = lambda slot, dry_run=False, db_path=None: {"enqueued": [], "errors": []}
        try:
            result = scheduler.run_now(dry_run=True)
            date_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            self.assertFalse(scheduler._is_locked("m", date_str))
        finally:
            scheduler.pick_slot = orig_pick
            scheduler.run_slot = orig_run

    def test_unknown_slot_name_errors(self):
        import scheduler
        with open(scheduler.SCHEDULE_PATH, "w") as f:
            f.write("slots:\n  - name: a\n    hour: 1\n    recipe: r\n")
        result = scheduler.run_now(slot_name="nonexistent")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
