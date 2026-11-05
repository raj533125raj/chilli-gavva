"""Unit tests for cube pack routing in workforce.py.

Verifies:
- An "on" pack gets a task of its kind
- A "planned" pack gets none
- Unknown kind ignored safely
- Old 6 cubes behave unchanged
- GITHUB_* env cleared in setUp with mock.patch.dict
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import cubes
import workforce


class TestCubePackRouting(unittest.TestCase):

    def setUp(self):
        # Clear GITHUB_ACTIONS, RUNNER_ENVIRONMENT, GITHUB_RUN_ID in setUp
        self.env_patch = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
        }, clear=False)
        self.env_patch.start()

        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_routing.db")
        self.packs_dir = os.path.join(self.test_dir, "cube_packs")
        os.makedirs(self.packs_dir, exist_ok=True)
        cubes.connect(self.db_path).close()
        workforce.connect(self.db_path).close()

    def tearDown(self):
        self.env_patch.stop()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_pack(self, pack_id, name, status, kinds, captain="Test Captain", main_agent="Test Agent"):
        folder = os.path.join(self.packs_dir, pack_id)
        os.makedirs(folder, exist_ok=True)
        data = {
            "id": pack_id,
            "name": name,
            "status": status,
            "captain": captain,
            "kinds": kinds,
            "sub_rooms": [
                {
                    "name": "Main Room",
                    "main_agent": main_agent,
                    "sub_agents": []
                }
            ],
            "tools_allowed": []
        }
        with open(os.path.join(folder, "cube.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_on_pack_gets_task_of_its_kind(self):
        self._create_pack("active-pack", "Active Pack", "on", ["active_kind"],
                           captain="Active Captain", main_agent="Active Worker")
        with mock.patch.object(cubes, "CUBE_PACKS_DIR", self.packs_dir):
            with mock.patch("guardrails.automation_allowed", return_value=True):
                task_id = workforce.enqueue("active_kind", {"topic": "active work"}, db_path=self.db_path)
                claimed = workforce.claim_next(db_path=self.db_path)

                self.assertIsNotNone(claimed)
                self.assertEqual(claimed["id"], task_id)
                self.assertEqual(claimed["kind"], "active_kind")
                self.assertEqual(claimed["agent"], "Active Worker")

                # Test execution via run_once on a new queued task
                task_id_2 = workforce.enqueue("active_kind", {"topic": "run_once work"}, db_path=self.db_path)
                res = workforce.run_once(max_tasks=1, db_path=self.db_path)
                self.assertEqual(res["ran"], 1)
                self.assertEqual(res["failed"], 0)

                with workforce.connect(self.db_path) as conn:
                    status = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id_2,)).fetchone()[0]
                    self.assertEqual(status, "done")

    def test_planned_pack_gets_none(self):
        self._create_pack("planned-pack", "Planned Pack", "planned", ["planned_kind"],
                           captain="Planned Captain", main_agent="Planned Worker")
        with mock.patch.object(cubes, "CUBE_PACKS_DIR", self.packs_dir):
            with mock.patch("guardrails.automation_allowed", return_value=True):
                task_id = workforce.enqueue("planned_kind", {"topic": "will not run"}, db_path=self.db_path)
                claimed = workforce.claim_next(db_path=self.db_path)
                self.assertIsNone(claimed)

                with workforce.connect(self.db_path) as conn:
                    status = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]
                    self.assertEqual(status, "queued")

    def test_unknown_kind_ignored_safely(self):
        with mock.patch.object(cubes, "CUBE_PACKS_DIR", self.packs_dir):
            with mock.patch("guardrails.automation_allowed", return_value=True):
                task_id = workforce.enqueue("completely_unknown_kind_xyz", {"foo": "bar"}, db_path=self.db_path)
                claimed = workforce.claim_next(db_path=self.db_path)
                self.assertIsNone(claimed)

                # Ensure run_once does not crash or pick it up
                res = workforce.run_once(max_tasks=1, db_path=self.db_path)
                self.assertEqual(res["ran"], 0)

                with workforce.connect(self.db_path) as conn:
                    status = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]
                    self.assertEqual(status, "queued")

    def test_old_cubes_unchanged(self):
        with mock.patch.object(cubes, "CUBE_PACKS_DIR", self.packs_dir):
            with mock.patch("guardrails.automation_allowed", return_value=True):
                # Enqueue research task (existing research cube)
                task_id = workforce.enqueue("research", {"topic": "test niche topic"}, db_path=self.db_path)
                claimed = workforce.claim_next(db_path=self.db_path)
                self.assertIsNotNone(claimed)
                self.assertEqual(claimed["id"], task_id)
                self.assertEqual(claimed["kind"], "research")
                self.assertEqual(claimed["agent"], "Research Worker")

                # Turning off research cube pauses it
                cubes.turn_off("research", db_path=self.db_path)
                task_id_2 = workforce.enqueue("research", {"topic": "test 2"}, db_path=self.db_path)
                self.assertIsNone(workforce.claim_next(db_path=self.db_path))

                # Other cube like draft works
                task_id_draft = workforce.enqueue("draft", {"topic": "test draft"}, db_path=self.db_path)
                claimed_draft = workforce.claim_next(db_path=self.db_path)
                self.assertIsNotNone(claimed_draft)
                self.assertEqual(claimed_draft["id"], task_id_draft)
                self.assertEqual(claimed_draft["agent"], "Draft Worker")


if __name__ == "__main__":
    unittest.main()
