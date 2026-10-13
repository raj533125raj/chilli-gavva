"""Tests for the always-on worker: loop, lock, crash recovery, heartbeat, kill switch."""
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import export_status  # noqa: E402
import guardrails  # noqa: E402
import worker_daemon  # noqa: E402
import workforce  # noqa: E402


class WorkerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "w.db")
        self._old = guardrails.KILL_FILE
        guardrails.KILL_FILE = os.path.join(self.tmp.name, "KILL")
        os.environ.pop("AGENT_PAUSED", None)
        self._wf_cfg = workforce.CONFIG_PATH
        workforce.CONFIG_PATH = os.path.join(self.tmp.name, "wf_config.json")
        with open(workforce.CONFIG_PATH, "w") as f:
            f.write('{"daily_drafts": 1}')
        os.environ.pop("GROQ_API_KEY", None)

    def tearDown(self):
        workforce.CONFIG_PATH = self._wf_cfg
        guardrails.KILL_FILE = self._old
        self.tmp.cleanup()


class LoopTests(WorkerBase):
    def run_loop(self, loops, **kw):
        sleeps = []
        n = worker_daemon.loop(db_path=self.db, interval=10, max_loops=loops, sleep=sleeps.append,
                               write_snapshot=False, **kw)
        return n, sleeps

    def test_worker_finishes_all_work_automatically(self):
        self.run_loop(3, enable_seeding=False)
        conn = workforce.connect(self.db)
        statuses = {r[0] for r in conn.execute("SELECT status FROM tasks")}
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        self.assertEqual(statuses, {"done"})
        self.assertEqual(count, 4)                              # plan, research, draft, review
        self.assertEqual(len(guardrails.pending(self.db)), 1)   # draft waits for a human

    def test_only_one_plan_per_day_across_passes(self):
        self.run_loop(6)
        conn = workforce.connect(self.db)
        plans = conn.execute("SELECT COUNT(*) FROM tasks WHERE kind = 'plan'").fetchone()[0]
        conn.close()
        self.assertEqual(plans, 1)

    def test_idle_backoff_grows_and_is_capped(self):
        _, sleeps = self.run_loop(8, enable_seeding=False)
        self.assertEqual(sleeps[0], 10)                          # first pass did work: base interval
        self.assertTrue(all(b >= a for a, b in zip(sleeps[1:], sleeps[2:])))
        self.assertLessEqual(max(sleeps), worker_daemon.MAX_IDLE_INTERVAL)
        self.assertGreater(sleeps[-1], 10)

    def test_kill_switch_pauses_and_heartbeat_says_so(self):
        guardrails.kill("test")
        self.run_loop(2, enable_seeding=False)
        conn = workforce.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        conn.close()
        status = workforce.worker_status(self.db)
        self.assertTrue(status["running"])
        self.assertIn("kill switch", status["info"])

    def test_loop_survives_a_failing_pass(self):
        original = workforce.run_once
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("disk hiccup")
            return original(*a, **k)

        workforce.run_once = flaky
        try:
            self.run_loop(4)
        finally:
            workforce.run_once = original
        conn = workforce.connect(self.db)
        done = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'done'").fetchone()[0]
        conn.close()
        self.assertEqual(done, 4)                                # recovered and finished the work

    def test_stop_flag_ends_loop(self):
        n = worker_daemon.loop(db_path=self.db, interval=10, sleep=lambda s: None, write_snapshot=False,
                               should_stop=lambda: True)
        self.assertEqual(n, 0)


class PlanAndEvolveTests(WorkerBase):
    def test_plan_makes_configured_number_of_drafts(self):
        with open(workforce.CONFIG_PATH, "w") as f:
            f.write('{"daily_drafts": 2}')
        workforce.enqueue("plan", {"topic": "demo"}, db_path=self.db)
        workforce.run_once(max_tasks=20, db_path=self.db, llm=lambda p: None)
        self.assertEqual(len(guardrails.pending(self.db)), 2)

    def test_plan_stops_when_too_many_drafts_wait_for_human(self):
        for i in range(workforce.MAX_PENDING_APPROVALS):
            guardrails.request_approval("public_draft", f"old {i}", "x", db_path=self.db)
        workforce.enqueue("plan", {"topic": "demo"}, db_path=self.db)
        workforce.run_once(max_tasks=20, db_path=self.db, llm=lambda p: None)
        self.assertEqual(len(guardrails.pending(self.db)), workforce.MAX_PENDING_APPROVALS)   # nothing added

    def test_worker_pass_creates_a_challenger_automatically(self):
        worker_daemon.one_pass(self.db, write_snapshot=False)
        conn = workforce.connect(self.db)
        variants = conn.execute("SELECT COUNT(*) FROM prompt_versions WHERE name = 'draft' AND active = 0").fetchone()[0]
        conn.close()
        self.assertEqual(variants, 1)
        self.assertIn("new challenger", workforce.worker_status(self.db)["info"])

    def test_worker_pass_promotes_a_clear_winner(self):
        import improver
        champion, _ = improver.get_active("draft", self.db)
        challenger = improver.add_variant("draft", "Better {topic} prompt", self.db)
        for _ in range(improver.MIN_TRIALS):
            improver.record_result(champion, 60, self.db)
            improver.record_result(challenger, 95, self.db)
        worker_daemon.one_pass(self.db, write_snapshot=False)
        self.assertEqual(improver.get_active("draft", self.db)[0], challenger)
        self.assertIn("promoted prompt", workforce.worker_status(self.db)["info"])


class RecoveryTests(WorkerBase):
    def test_stuck_running_task_is_requeued(self):
        workforce.enqueue("plan", {"topic": "x"}, db_path=self.db)
        task = workforce.claim_next(self.db)                     # now 'running', as if the worker crashed
        conn = workforce.connect(self.db)
        old = (datetime.datetime.now() - datetime.timedelta(minutes=30)).isoformat()
        conn.execute("UPDATE tasks SET started_ts = ? WHERE id = ?", (old, task["id"]))
        conn.commit(); conn.close()
        self.assertEqual(workforce.recover_stuck(self.db), 1)
        conn = workforce.connect(self.db)
        self.assertEqual(conn.execute("SELECT status FROM tasks").fetchone()[0], "queued")
        conn.close()

    def test_fresh_running_task_is_left_alone(self):
        workforce.enqueue("plan", {"topic": "x"}, db_path=self.db)
        workforce.claim_next(self.db)
        self.assertEqual(workforce.recover_stuck(self.db), 0)

    def test_task_that_keeps_crashing_ends_as_failed(self):
        workforce.enqueue("plan", {"topic": "x"}, db_path=self.db)
        for _ in range(workforce.MAX_ATTEMPTS):
            task = workforce.claim_next(self.db)
            conn = workforce.connect(self.db)
            old = (datetime.datetime.now() - datetime.timedelta(minutes=30)).isoformat()
            conn.execute("UPDATE tasks SET started_ts = ? WHERE id = ?", (old, task["id"]))
            conn.commit(); conn.close()
            workforce.recover_stuck(self.db)
        conn = workforce.connect(self.db)
        self.assertEqual(conn.execute("SELECT status FROM tasks").fetchone()[0], "failed")
        conn.close()


class LockAndHeartbeatTests(WorkerBase):
    def test_lock_blocks_second_worker_but_takes_over_dead_one(self):
        lock = os.path.join(self.tmp.name, "worker.lock")
        self.assertTrue(worker_daemon.acquire_lock(lock))
        self.assertFalse(worker_daemon.acquire_lock(lock))       # we are alive, so it is taken
        worker_daemon.release_lock(lock)
        with open(lock, "w") as f:
            f.write("999999999")                                 # a pid that cannot exist
        self.assertTrue(worker_daemon.acquire_lock(lock))
        worker_daemon.release_lock(lock)
        worker_daemon.release_lock(lock)                         # releasing twice is harmless

    def test_heartbeat_and_staleness(self):
        self.assertFalse(workforce.worker_status(self.db)["running"])
        workforce.heartbeat("ok", self.db)
        self.assertTrue(workforce.worker_status(self.db)["running"])
        conn = workforce.connect(self.db)
        old = (datetime.datetime.now() - datetime.timedelta(minutes=10)).isoformat(timespec="seconds")
        conn.execute("UPDATE heartbeat SET ts = ?", (old,))
        conn.commit(); conn.close()
        self.assertFalse(workforce.worker_status(self.db)["running"])

    def test_clean_stop_is_reported_as_stopped(self):
        workforce.heartbeat("ok", self.db)
        workforce.heartbeat("stopped", self.db)
        self.assertFalse(workforce.worker_status(self.db)["running"])

    def test_snapshot_includes_worker(self):
        workforce.heartbeat("ok: ran 1, failed 0", self.db)
        self.assertTrue(export_status.build_status(self.db)["worker"]["running"])


if __name__ == "__main__":
    unittest.main()
