"""Unit tests for cube packs system (cube_packs/ directory and loader in cubes.py).

Verifies:
- valid pack loads
- broken pack skipped with clear reason and robot doesn't crash
- duplicate id rejected
- secret-looking values rejected
- planned pack never schedules or claims tasks
- existing 6 cubes unchanged
- GITHUB_ACTIONS / RUNNER_ENVIRONMENT / GITHUB_RUN_ID cleared in setUp
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import cubes
import workforce


class TestCubePacks(unittest.TestCase):

    def setUp(self):
        # IMPORTANT: clear GITHUB_ACTIONS / RUNNER_ENVIRONMENT / GITHUB_RUN_ID in setUp
        self.env_patch = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
        }, clear=False)
        self.env_patch.start()

        # Temporary working directory for packs & test database
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_agent_data.db")
        self.packs_dir = os.path.join(self.test_dir, "cube_packs")
        os.makedirs(self.packs_dir, exist_ok=True)
        cubes.connect(self.db_path)
        workforce.connect(self.db_path)

    def tearDown(self):
        self.env_patch.stop()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_existing_6_cubes_unchanged(self):
        base_cubes = cubes.load_registry(packs_dir=self.packs_dir)
        cube_ids = [c["id"] for c in base_cubes]
        expected_ids = ["executive", "research", "content", "qa", "product", "marketing"]
        for expected in expected_ids:
            self.assertIn(expected, cube_ids, f"Base cube '{expected}' missing from registry")
        self.assertEqual(len(cube_ids), 6)

    def test_valid_pack_loads(self):
        # Create a valid pack in temp packs dir
        pack_folder = os.path.join(self.packs_dir, "newsletter-team")
        os.makedirs(pack_folder, exist_ok=True)
        pack_data = {
            "id": "newsletter-team",
            "name": "Newsletter Team",
            "colour": "#10b981",
            "status": "planned",
            "captain": "Newsletter Captain",
            "sub_rooms": [
                {
                    "name": "Story Curation",
                    "main_agent": "Curator Agent",
                    "sub_agents": ["Link Checker"]
                },
                {
                    "name": "Email Layout Studio",
                    "main_agent": "Email Layout Worker",
                    "sub_agents": ["Headline Tester"]
                }
            ],
            "tools_allowed": ["email_draft_tool"],
            "schedule_hints": {"frequency": "weekly"}
        }
        with open(os.path.join(pack_folder, "cube.json"), "w", encoding="utf-8") as f:
            json.dump(pack_data, f)

        # Optional recipe
        recipes_folder = os.path.join(pack_folder, "recipes")
        os.makedirs(recipes_folder, exist_ok=True)
        with open(os.path.join(recipes_folder, "weekly_digest.yaml"), "w", encoding="utf-8") as f:
            f.write("name: weekly_digest\nsteps: []\n")

        all_cubes = cubes.load_registry(packs_dir=self.packs_dir)
        pack_ids = [c["id"] for c in all_cubes]
        self.assertIn("newsletter-team", pack_ids)

        c = next(c for c in all_cubes if c["id"] == "newsletter-team")
        self.assertEqual(c["name"], "Newsletter Team")
        self.assertEqual(c["captain"], "Newsletter Captain")
        self.assertEqual(c["status"], "planned")
        self.assertTrue(c["planned"])
        self.assertIn("Story Curation", c["sub_rooms"])
        self.assertIn("Curator Agent", c["agents"])
        self.assertIn("Link Checker", c["sub_agents"])

    def test_broken_pack_skipped_with_reason(self):
        # Create a malformed pack (missing captain and sub_rooms)
        broken_folder = os.path.join(self.packs_dir, "broken-pack")
        os.makedirs(broken_folder, exist_ok=True)
        with open(os.path.join(broken_folder, "cube.json"), "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        # Create another broken pack (missing required fields)
        broken_folder2 = os.path.join(self.packs_dir, "missing-fields")
        os.makedirs(broken_folder2, exist_ok=True)
        with open(os.path.join(broken_folder2, "cube.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "missing-fields", "name": "Broken"}, f)

        # Loader should not crash, should simply skip both broken packs
        with self.assertLogs("cubes", level="WARNING") as log:
            all_cubes = cubes.load_registry(packs_dir=self.packs_dir)

        cube_ids = [c["id"] for c in all_cubes]
        self.assertNotIn("broken-pack", cube_ids)
        self.assertNotIn("missing-fields", cube_ids)
        # Check logs contain reasons
        log_text = "\n".join(log.output)
        self.assertIn("broken-pack", log_text)
        self.assertIn("missing-fields", log_text)

    def test_duplicate_id_rejected(self):
        # Attempt to create a pack with an existing base ID (e.g., 'research')
        dup_folder = os.path.join(self.packs_dir, "research")
        os.makedirs(dup_folder, exist_ok=True)
        pack_data = {
            "id": "research",
            "name": "Duplicate Research",
            "colour": "#000000",
            "status": "planned",
            "captain": "Fake Captain",
            "sub_rooms": [{"name": "Room 1", "main_agent": "Agent 1", "sub_agents": []}],
            "tools_allowed": []
        }
        with open(os.path.join(dup_folder, "cube.json"), "w", encoding="utf-8") as f:
            json.dump(pack_data, f)

        with self.assertLogs("cubes", level="WARNING") as log:
            all_cubes = cubes.load_registry(packs_dir=self.packs_dir)

        # Original research department preserved
        research_cubes = [c for c in all_cubes if c["id"] == "research"]
        self.assertEqual(len(research_cubes), 1)
        self.assertEqual(research_cubes[0]["name"], "Research Department")
        self.assertIn("duplicate cube id 'research'", "\n".join(log.output))

    def test_secret_looking_value_rejected(self):
        bad_pack = os.path.join(self.packs_dir, "leaky-pack")
        os.makedirs(bad_pack, exist_ok=True)
        pack_data = {
            "id": "leaky-pack",
            "name": "Leaky Pack",
            "colour": "#112233",
            "status": "planned",
            "captain": "Leaky Captain",
            "sub_rooms": [
                {
                    "name": "Leak Room",
                    "main_agent": "Leak Agent",
                    "sub_agents": []
                }
            ],
            "tools_allowed": ["api_key" + "=test_secret_value_12345"],
        }
        with open(os.path.join(bad_pack, "cube.json"), "w", encoding="utf-8") as f:
            json.dump(pack_data, f)

        with self.assertLogs("cubes", level="WARNING") as log:
            all_cubes = cubes.load_registry(packs_dir=self.packs_dir)

        self.assertNotIn("leaky-pack", [c["id"] for c in all_cubes])
        self.assertIn("leaky-pack", "\n".join(log.output))

    def test_planned_pack_never_schedules(self):
        # A pack in planned state must never be enabled for kind execution or agent execution
        pack_folder = os.path.join(self.packs_dir, "drafting-squad")
        os.makedirs(pack_folder, exist_ok=True)
        pack_data = {
            "id": "drafting-squad",
            "name": "Drafting Squad",
            "colour": "#445566",
            "status": "planned",
            "captain": "Squad Captain",
            "kinds": ["squad_task"],
            "sub_rooms": [
                {
                    "name": "Squad Room",
                    "main_agent": "Squad Worker",
                    "sub_agents": []
                }
            ],
            "tools_allowed": []
        }
        with open(os.path.join(pack_folder, "cube.json"), "w", encoding="utf-8") as f:
            json.dump(pack_data, f)

        # Mock CUBE_PACKS_DIR in cubes module to point to our test packs dir
        with mock.patch.object(cubes, "CUBE_PACKS_DIR", self.packs_dir):
            cube_info = cubes.get_cube("drafting-squad", db_path=self.db_path)
            self.assertIsNotNone(cube_info)
            self.assertFalse(cube_info["enabled"])
            self.assertTrue(cube_info["planned"])

            # Verify is_kind_enabled is False
            self.assertFalse(cubes.is_kind_enabled("squad_task", db_path=self.db_path))

            # Verify is_agent_enabled is False
            self.assertFalse(cubes.is_agent_enabled("Squad Worker", db_path=self.db_path))
            self.assertFalse(cubes.is_agent_enabled("Squad Captain", db_path=self.db_path))

            # Even if someone attempts to turn it on in DB while planned is True, is_kind_enabled stays False
            cubes.turn_on("drafting-squad", db_path=self.db_path)
            self.assertFalse(cubes.is_kind_enabled("squad_task", db_path=self.db_path))
            self.assertFalse(cubes.is_agent_enabled("Squad Worker", db_path=self.db_path))

    def test_example_packs_in_repo(self):
        # Test that repository's actual cube_packs/ has pinterest-team and instagram-team
        all_cubes = cubes.load_registry()
        cube_map = {c["id"]: c for c in all_cubes}

        self.assertIn("pinterest-team", cube_map)
        self.assertIn("instagram-team", cube_map)

        pin = cube_map["pinterest-team"]
        self.assertEqual(pin["status"], "planned")
        self.assertTrue(pin["planned"])
        self.assertEqual(len(pin["sub_rooms"]), 2)
        self.assertEqual(pin["captain"], "Pinterest Captain")

        insta = cube_map["instagram-team"]
        self.assertEqual(insta["status"], "planned")
        self.assertTrue(insta["planned"])
        self.assertEqual(len(insta["sub_rooms"]), 2)
        self.assertEqual(insta["captain"], "Instagram Captain")

        # Verify public export contains them as planned
        export = cubes.get_public_cubes_export(self.db_path)
        export_map = {c["id"]: c for c in export}
        self.assertIn("pinterest-team", export_map)
        self.assertIn("instagram-team", export_map)
        self.assertTrue(export_map["pinterest-team"]["planned"])
        self.assertFalse(export_map["pinterest-team"]["enabled"])
        self.assertTrue(export_map["instagram-team"]["planned"])
        self.assertFalse(export_map["instagram-team"]["enabled"])


if __name__ == "__main__":
    unittest.main()
