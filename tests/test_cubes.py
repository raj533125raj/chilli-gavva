"""Unit tests for cubes.py department registry, state management, workforce task flow,
Telegram inbox integration, and public status export shape.
"""
import json
import os
import sqlite3
import tempfile
import unittest

import cubes
import export_status
import telegram_inbox
import workforce


class TestCubesRegistry(unittest.TestCase):

    def test_load_registry(self):
        reg = cubes.load_registry()
        self.assertIsInstance(reg, list)
        self.assertGreater(len(reg), 0)

    def test_all_workforce_agents_mapped(self):
        reg = cubes.load_registry()
        wf_agents = {a["name"] for a in workforce.AGENTS}

        mapped_agents = set()
        for c in reg:
            if c.get("captain"):
                mapped_agents.add(c["captain"])
            for ag in c.get("agents", []):
                mapped_agents.add(ag)
            for ag in c.get("sub_agents", []):
                mapped_agents.add(ag)
            if c.get("right_hand"):
                mapped_agents.add(c["right_hand"])
            if c.get("left_hand"):
                mapped_agents.add(c["left_hand"])

        for wf_agent in wf_agents:
            self.assertIn(wf_agent, mapped_agents, f"Workforce agent {wf_agent} not mapped in cubes.json")

    def test_validate_registry_errors(self):
        with self.assertRaises(ValueError):
            cubes.validate_registry("not a list")

        invalid_cube = [{"id": "test", "name": "Test"}]  # missing keys
        with self.assertRaises(ValueError):
            cubes.validate_registry(invalid_cube)

        unmapped_wf_agents = [{"name": "Unmapped Agent"}]
        with self.assertRaises(ValueError):
            cubes.validate_registry(cubes.load_registry(), workforce_agents=unmapped_wf_agents)


class TestAgentAndKindMapping(unittest.TestCase):

    def test_get_cube_for_agent(self):
        c = cubes.get_cube_for_agent("Research Manager")
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "research")

        c2 = cubes.get_cube_for_agent("Draft Worker")
        self.assertIsNotNone(c2)
        self.assertEqual(c2["id"], "content")

        c3 = cubes.get_cube_for_agent("QA Reviewer")
        self.assertIsNotNone(c3)
        self.assertEqual(c3["id"], "qa")

        c4 = cubes.get_cube_for_agent("NonExistentAgent")
        self.assertIsNone(c4)

    def test_get_cube_for_kind(self):
        c = cubes.get_cube_for_kind("research")
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "research")

        c2 = cubes.get_cube_for_kind("draft")
        self.assertIsNotNone(c2)
        self.assertEqual(c2["id"], "content")

        c3 = cubes.get_cube_for_kind("review")
        self.assertIsNotNone(c3)
        self.assertEqual(c3["id"], "qa")

        c4 = cubes.get_cube_for_kind("unknown_kind")
        self.assertIsNone(c4)


class TestStateAndTaskFlow(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.db_path = self.tmp.name
        self.tmp.close()
        workforce.connect(self.db_path)
        cubes.connect(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_state_persistence(self):
        self.assertEqual(cubes.get_cube_state("research", self.db_path), "on")
        cubes.turn_off("research", self.db_path)
        self.assertEqual(cubes.get_cube_state("research", self.db_path), "off")
        self.assertFalse(cubes.is_kind_enabled("research", self.db_path))

        cubes.turn_on("research", self.db_path)
        self.assertEqual(cubes.get_cube_state("research", self.db_path), "on")
        self.assertTrue(cubes.is_kind_enabled("research", self.db_path))

    def test_archive_and_restore(self):
        cubes.archive_cube("research", self.db_path)
        self.assertEqual(cubes.get_cube_state("research", self.db_path), "archived")
        self.assertFalse(cubes.is_kind_enabled("research", self.db_path))

        cubes.restore_cube("research", self.db_path)
        self.assertEqual(cubes.get_cube_state("research", self.db_path), "on")
        self.assertTrue(cubes.is_kind_enabled("research", self.db_path))

    def test_task_queue_pausing_on_real_task_flow(self):
        # Enqueue research and draft tasks
        tid_res = workforce.enqueue("research", {"topic": "test idea 1"}, db_path=self.db_path)
        tid_draft = workforce.enqueue("draft", {"topic": "test idea 2"}, db_path=self.db_path)

        # Turn off Research cube
        cubes.turn_off("research", self.db_path)

        # Claim next task: Research task should be skipped and Draft task claimed
        claimed1 = workforce.claim_next(self.db_path)
        self.assertIsNotNone(claimed1)
        self.assertEqual(claimed1["id"], tid_draft)
        self.assertEqual(claimed1["kind"], "draft")

        # Claim next task again: Research task is queued but Research cube is OFF, so returns None
        claimed2 = workforce.claim_next(self.db_path)
        self.assertIsNone(claimed2)

        # Verify in DB that Research task is still 'queued'
        conn = workforce.connect(self.db_path)
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid_res,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "queued")

        # Turn ON Research cube
        cubes.turn_on("research", self.db_path)

        # Claim next task: Research task is now claimed!
        claimed3 = workforce.claim_next(self.db_path)
        self.assertIsNotNone(claimed3)
        self.assertEqual(claimed3["id"], tid_res)
        self.assertEqual(claimed3["kind"], "research")


class TestTelegramFlow(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.db_path = self.tmp.name
        self.tmp.close()
        workforce.connect(self.db_path)
        cubes.connect(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_parse_cubes_command(self):
        cmd, ids = telegram_inbox.parse_command("/cubes")
        self.assertEqual(cmd, "cubes")

        cmd2, ids2 = telegram_inbox.parse_command("cubes")
        self.assertEqual(cmd2, "cubes")

    def test_format_telegram_cubes_list(self):
        text, buttons = cubes.format_telegram_cubes_list(db_path=self.db_path)
        self.assertIn("Department Cubes", text)
        self.assertIn("Research Department", text)
        self.assertIsInstance(buttons, list)
        self.assertGreater(len(buttons), 0)

    def test_telegram_callback_toggle_and_delete(self):
        # Callback OFF
        reply = cubes.handle_telegram_callback("cb:cube:off:research", db_path=self.db_path)
        self.assertIn("OFF", reply)
        self.assertEqual(cubes.get_cube_state("research", self.db_path), "off")

        # Callback ON
        reply_on = cubes.handle_telegram_callback("cb:cube:on:research", db_path=self.db_path)
        self.assertIn("ON", reply_on)
        self.assertEqual(cubes.get_cube_state("research", self.db_path), "on")

        # Callback DEL (triggers Yes/No confirmation)
        conf = cubes.handle_telegram_callback("cb:cube:del:research", db_path=self.db_path)
        self.assertIsInstance(conf, dict)
        self.assertIn("Delete", conf["text"])
        self.assertEqual(len(conf["buttons"][0]), 2)
        self.assertEqual(conf["buttons"][0][0][1], "cb:cube:delyes:research")
        self.assertEqual(conf["buttons"][0][1][1], "cb:cube:delno:research")

        # Delete Cancel
        reply_no = cubes.handle_telegram_callback("cb:cube:delno:research", db_path=self.db_path)
        self.assertIn("cancelled", reply_no.lower())
        self.assertEqual(cubes.get_cube_state("research", self.db_path), "on")

        # Delete Confirm (Yes) -> Archives cube
        reply_yes = cubes.handle_telegram_callback("cb:cube:delyes:research", db_path=self.db_path)
        self.assertIn("deleted", reply_yes.lower())
        self.assertEqual(cubes.get_cube_state("research", self.db_path), "archived")


class TestPublicExportShape(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_public_export_shape(self):
        export = cubes.get_public_cubes_export(self.db_path)
        self.assertIsInstance(export, list)
        self.assertGreater(len(export), 0)

        out_file = os.path.join(os.path.dirname(self.db_path), "status.json")
        export_status.write_status(out_file, self.db_path)
        with open(out_file, encoding="utf-8") as f:
            status_json_data = json.load(f)
        self.assertIn("cubes", status_json_data)
        self.assertEqual(status_json_data["cubes"], export)

        for cube in export:
            # Check required top-level keys
            for key in ("id", "name", "role", "status", "enabled", "planned", "counts", "captain", "sub_rooms", "agents"):
                self.assertIn(key, cube, f"Cube missing key '{key}'")

            # Check counts shape
            counts = cube["counts"]
            for ckey in ("sub_rooms", "agents", "sub_agents", "kinds"):
                self.assertIn(ckey, counts)

            # Check captain shape
            captain = cube["captain"]
            for ckey in ("name", "role", "planned"):
                self.assertIn(ckey, captain)

            # Check sub_rooms shape
            for sr in cube["sub_rooms"]:
                self.assertIn("name", sr)

            # Check agents shape
            for ag in cube["agents"]:
                for akey in ("name", "role", "planned", "sub_agents"):
                    self.assertIn(akey, ag)
                for sub_ag in ag["sub_agents"]:
                    for skey in ("name", "role", "planned"):
                        self.assertIn(skey, sub_ag)

    def test_no_sensitive_data_in_export(self):
        status_json = json.dumps(export_status.build_status(self.db_path))
        # No money currency symbols in comments/export, no prompts, no confidential tokens
        self.assertNotIn("PROMPT", status_json)


if __name__ == "__main__":
    unittest.main()
