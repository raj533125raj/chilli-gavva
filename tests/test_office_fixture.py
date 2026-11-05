"""tests/test_office_fixture.py — Unit tests for synthetic office fixture generator.

Tests:
  - Small, medium, full sizes validate against office_protocol.py
  - Counts match expected sizes:
      small: 6 teams, 30 agents (24 main + 6 captains)
      medium: 15 teams, 400 agents (250 main + 15 captains + 135 sub agents)
      full: 40 teams, 3500 main agents, 6000 total agents (3500 main + 40 captains + 2460 sub agents)
  - Deterministic generation with the same seed
  - "synthetic": True present in every record
  - Generation of full size executes under 10 seconds
  - Guard against writing to web/data
"""

import datetime
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import office_protocol
from scripts import make_office_fixture


class TestOfficeFixture(unittest.TestCase):
    def setUp(self):
        # Clear GitHub Actions and runner environment variables
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "GITHUB_ACTIONS": "",
                "RUNNER_ENVIRONMENT": "",
                "GITHUB_RUN_ID": "",
                "CHILLI_TEST": "1",
            },
            clear=False,
        )
        self.env_patcher.start()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _verify_synthetic_flag(self, obj, path="root"):
        """Recursively ensure every dict has 'synthetic': True."""
        if isinstance(obj, dict):
            self.assertIn(
                "synthetic",
                obj,
                f"Missing 'synthetic' key at path {path}: keys={list(obj.keys())}",
            )
            self.assertTrue(
                obj["synthetic"],
                f"'synthetic' flag is not True at path {path}",
            )
            for k, v in obj.items():
                self._verify_synthetic_flag(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, elem in enumerate(obj):
                self._verify_synthetic_flag(elem, f"{path}[{i}]")

    def test_small_size_validates_and_counts_match(self):
        snap = make_office_fixture.generate_office_fixture(size="small", seed=100, validate=True)

        # office_protocol validation (should pass without error)
        val = office_protocol.validate_snapshot(snap)
        self.assertEqual(val["version"], office_protocol.PROTOCOL_VERSION)

        teams = snap["teams"]
        self.assertEqual(len(teams), 6)

        captains = [t["captain"] for t in teams if t.get("captain")]
        main_agents = [a for t in teams for a in t["agents"]]
        sub_agents = [a for t in teams for r in t["sub_rooms"] for a in r["agents"]]

        self.assertEqual(len(captains), 6)
        self.assertEqual(len(main_agents), 24)
        self.assertEqual(len(sub_agents), 0)
        self.assertEqual(len(captains) + len(main_agents) + len(sub_agents), 30)
        self.assertEqual(snap["counters"]["agents"], 30)

        self._verify_synthetic_flag(snap)

    def test_medium_size_validates_and_counts_match(self):
        snap = make_office_fixture.generate_office_fixture(size="medium", seed=200, validate=True)

        val = office_protocol.validate_snapshot(snap)
        self.assertEqual(val["version"], office_protocol.PROTOCOL_VERSION)

        teams = snap["teams"]
        self.assertEqual(len(teams), 15)

        captains = [t["captain"] for t in teams if t.get("captain")]
        main_agents = [a for t in teams for a in t["agents"]]
        sub_agents = [a for t in teams for r in t["sub_rooms"] for a in r["agents"]]

        self.assertEqual(len(captains), 15)
        self.assertEqual(len(main_agents), 250)
        self.assertEqual(len(sub_agents), 135)
        self.assertEqual(len(captains) + len(main_agents) + len(sub_agents), 400)
        self.assertEqual(snap["counters"]["agents"], 400)

        self._verify_synthetic_flag(snap)

    def test_full_size_validates_and_counts_match(self):
        snap = make_office_fixture.generate_office_fixture(size="full", seed=42, validate=True)

        val = office_protocol.validate_snapshot(snap)
        self.assertEqual(val["version"], office_protocol.PROTOCOL_VERSION)

        teams = snap["teams"]
        self.assertEqual(len(teams), 40)

        captains = [t["captain"] for t in teams if t.get("captain")]
        main_agents = [a for t in teams for a in t["agents"]]
        sub_agents = [a for t in teams for r in t["sub_rooms"] for a in r["agents"]]

        self.assertEqual(len(captains), 40)
        self.assertEqual(len(main_agents), 3500)
        self.assertEqual(len(sub_agents), 2460)
        self.assertEqual(len(captains) + len(main_agents) + len(sub_agents), 6000)
        self.assertEqual(snap["counters"]["agents"], 6000)

        # Ensure all honest statuses are represented across the agents
        all_agents = captains + main_agents + sub_agents
        statuses = set(a["status"] for a in all_agents)
        self.assertEqual(statuses, set(office_protocol.HONEST_STATUSES))

        # Ensure mix of levels
        levels = set(a["level"] for a in all_agents)
        self.assertEqual(levels, {"captain", "main", "sub"})

        # Ensure next shift times are present for off-duty agents
        off_duty = [a for a in all_agents if a["status"] == "off duty with next shift"]
        self.assertGreater(len(off_duty), 0)
        for a in off_duty:
            self.assertIn("next_shift", a)

        # Ensure approvals mix around 24h
        approvals = snap["approvals"]
        self.assertGreaterEqual(len(approvals), 40)
        waited = [a["waited_hours"] for a in approvals]
        self.assertTrue(any(w < 24.0 for w in waited))
        self.assertTrue(any(w >= 24.0 for w in waited))

        self._verify_synthetic_flag(snap)

    def test_deterministic_generation_with_same_seed(self):
        s1 = make_office_fixture.generate_office_fixture(size="medium", seed=999)
        s2 = make_office_fixture.generate_office_fixture(size="medium", seed=999)

        json1 = json.dumps(s1, sort_keys=True)
        json2 = json.dumps(s2, sort_keys=True)
        self.assertEqual(json1, json2)

        # Different seed produces different output
        s3 = make_office_fixture.generate_office_fixture(size="medium", seed=1000)
        json3 = json.dumps(s3, sort_keys=True)
        self.assertNotEqual(json1, json3)

    def test_full_size_generation_under_10_seconds(self):
        t0 = time.time()
        snap = make_office_fixture.generate_office_fixture(size="full", seed=42, validate=True)
        elapsed = time.time() - t0

        self.assertLess(
            elapsed,
            10.0,
            f"Full size generation and validation took {elapsed:.2f}s, exceeding 10.0s threshold",
        )
        self.assertEqual(len(snap["teams"]), 40)

    def test_prohibit_writing_to_web_data(self):
        snap = make_office_fixture.generate_office_fixture(size="small", seed=1)
        with self.assertRaises(ValueError) as ctx:
            make_office_fixture.write_fixture_file(snap, "web/data/test.json")
        self.assertIn("web/data", str(ctx.exception))

    def test_write_to_temp_directory_succeeds(self):
        snap = make_office_fixture.generate_office_fixture(size="small", seed=1)
        target = os.path.join(self.temp_dir.name, "output_small.json")
        written = make_office_fixture.write_fixture_file(snap, target)

        self.assertTrue(os.path.exists(written))
        with open(written, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["version"], office_protocol.PROTOCOL_VERSION)
        self.assertEqual(len(data["teams"]), 6)


if __name__ == "__main__":
    unittest.main()
