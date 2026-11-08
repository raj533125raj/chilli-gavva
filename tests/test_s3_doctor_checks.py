"""Unit tests for doctor.py reliability and ops environment checks."""

import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

import doctor


class TestDoctorIntegrityChecks(unittest.TestCase):
    def setUp(self):
        env_patcher = mock.patch.dict(
            os.environ,
            {k: "" for k in list(os.environ.keys()) if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT"},
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.products_dir = self.root / "exported_content" / "products"
        self.marketing_dir = self.root / "exported_content" / "marketing"
        self.locks_dir = self.root / ".scheduler_locks"
        self.plan_path = self.root / "exported_content" / "post_plan.json"
        self.keywords_path = self.marketing_dir / "keywords.json"

        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_upload_packs_folder_missing(self):
        missing_dir = self.root / "nonexistent"
        res = doctor.check_upload_packs_folder(str(missing_dir))
        self.assertEqual(res["status"], "warn")
        self.assertIn("not found", res["message"])
        self.assertIn("Upload folder create cheyandi", res["fix"])

    def test_upload_packs_folder_readable_ok(self):
        upload_dir = self.products_dir / "upload"
        upload_dir.mkdir(parents=True)
        res = doctor.check_upload_packs_folder(str(self.products_dir))
        self.assertEqual(res["status"], "ok")
        self.assertIn("readable", res["message"])
        self.assertIn("fix avasaram ledu", res["fix"])

    def test_marketing_folders_no_ready_packs_ok(self):
        res = doctor.check_marketing_folders_per_ready_pack(str(self.products_dir), str(self.marketing_dir))
        self.assertEqual(res["status"], "ok")
        self.assertIn("levu", res["message"])

    def test_marketing_folders_missing_carousel_and_pins_warns(self):
        upload_dir = self.products_dir / "upload" / "pack-alpha"
        upload_dir.mkdir(parents=True)
        (upload_dir / "product.zip").write_text("data")
        (upload_dir / "listing.txt").write_text("title: Alpha")

        res = doctor.check_marketing_folders_per_ready_pack(str(self.products_dir), str(self.marketing_dir))
        self.assertEqual(res["status"], "warn")
        self.assertIn("missing marketing folders", res["message"])
        self.assertIn("Run cheyandi: python3 carousel_maker.py", res["fix"])

    def test_marketing_folders_all_present_ok(self):
        upload_dir = self.products_dir / "upload" / "pack-alpha"
        upload_dir.mkdir(parents=True)
        (upload_dir / "product.zip").write_text("data")
        (upload_dir / "listing.txt").write_text("title: Alpha")

        car_dir = self.marketing_dir / "pack-alpha" / "carousel"
        car_dir.mkdir(parents=True)
        (car_dir / "slide_1.png").write_text("png")

        pins_dir = self.marketing_dir / "pack-alpha" / "pins"
        pins_dir.mkdir(parents=True)
        (pins_dir / "pin_1.png").write_text("png")

        res = doctor.check_marketing_folders_per_ready_pack(str(self.products_dir), str(self.marketing_dir))
        self.assertEqual(res["status"], "ok")
        self.assertIn("have carousel and pins", res["message"])
        self.assertIn("em cheyalsina avasaram ledu", res["fix"])

    def test_marketing_folders_partial_missing_pins_only(self):
        upload_dir = self.products_dir / "upload" / "pack-alpha"
        upload_dir.mkdir(parents=True)
        (upload_dir / "product.zip").write_text("data")
        (upload_dir / "listing.txt").write_text("title: Alpha")

        car_dir = self.marketing_dir / "pack-alpha" / "carousel"
        car_dir.mkdir(parents=True)
        (car_dir / "slide_1.png").write_text("png")

        res = doctor.check_marketing_folders_per_ready_pack(str(self.products_dir), str(self.marketing_dir))
        self.assertEqual(res["status"], "warn")
        self.assertIn("pins", res["message"])

    def test_post_plan_missing_warns(self):
        res = doctor.check_post_plan_valid_json(str(self.plan_path))
        self.assertEqual(res["status"], "warn")
        self.assertIn("Post plan file ledu", res["message"])
        self.assertIn("post_planner.py run chesi", res["fix"])

    def test_post_plan_valid_ok(self):
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text(json.dumps({"schedule": []}))
        res = doctor.check_post_plan_valid_json(str(self.plan_path))
        self.assertEqual(res["status"], "ok")
        self.assertIn("valid JSON", res["message"])

    def test_post_plan_corrupted_fails(self):
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text("{broken json syntax")
        res = doctor.check_post_plan_valid_json(str(self.plan_path))
        self.assertEqual(res["status"], "fail")
        self.assertIn("parse error", res["message"])
        self.assertIn("post_planner.py", res["fix"])

    def test_post_plan_non_dict_fails(self):
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text("[]")
        res = doctor.check_post_plan_valid_json(str(self.plan_path))
        self.assertEqual(res["status"], "fail")
        self.assertIn("must be a JSON object", res["message"])

    def test_keywords_json_missing_is_ok(self):
        res = doctor.check_keywords_json_valid_if_present(str(self.keywords_path))
        self.assertEqual(res["status"], "ok")
        self.assertIn("optional file", res["message"])

    def test_keywords_json_valid_is_ok(self):
        self.keywords_path.write_text(json.dumps([{"keyword": "budget planner"}]))
        res = doctor.check_keywords_json_valid_if_present(str(self.keywords_path))
        self.assertEqual(res["status"], "ok")
        self.assertIn("valid JSON", res["message"])

    def test_keywords_json_corrupted_fails(self):
        self.keywords_path.write_text("{not valid JSON!!")
        res = doctor.check_keywords_json_valid_if_present(str(self.keywords_path))
        self.assertEqual(res["status"], "fail")
        self.assertIn("decode error", res["message"])
        self.assertIn("Corrupt keywords.json delete chesi", res["fix"])

    def test_disk_usage_empty_folder_ok(self):
        exp_dir = self.root / "exported_content"
        res = doctor.check_exported_content_disk_usage(str(exp_dir))
        self.assertEqual(res["status"], "ok")
        self.assertIn("safe", res["message"])

    def test_disk_usage_missing_folder_ok(self):
        missing = self.root / "no_exported"
        res = doctor.check_exported_content_disk_usage(str(missing))
        self.assertEqual(res["status"], "ok")

    def test_disk_usage_under_limit_ok(self):
        f = self.root / "exported_content" / "sample.bin"
        f.write_bytes(b"x" * 1024)
        res = doctor.check_exported_content_disk_usage(str(self.root / "exported_content"))
        self.assertEqual(res["status"], "ok")

    def test_disk_usage_over_limit_warns(self):
        f = self.root / "exported_content" / "large.bin"
        f.write_bytes(b"x" * 100)
        # Test with tiny limit (50 bytes)
        res = doctor.check_exported_content_disk_usage(str(self.root / "exported_content"), limit_bytes=50)
        self.assertEqual(res["status"], "warn")
        self.assertIn("exceeds 2 GB limit", res["message"])
        self.assertIn("delete chesi disk space free cheyandi", res["fix"])

    def test_stale_locks_empty_ok(self):
        res = doctor.check_stale_state_locks(str(self.locks_dir))
        self.assertEqual(res["status"], "ok")
        self.assertIn("stale locks levu", res["message"])

    def test_stale_locks_fresh_lock_ok(self):
        lock = self.locks_dir / "worker.lock"
        lock.write_text("locked")
        res = doctor.check_stale_state_locks(str(self.locks_dir), max_age_seconds=7200.0)
        self.assertEqual(res["status"], "ok")

    def test_stale_locks_over_2h_warns(self):
        lock = self.locks_dir / "worker.lock"
        lock.write_text("locked")
        # Set mtime to 3 hours ago
        old_time = time.time() - 10800.0
        os.utime(str(lock), (old_time, old_time))

        res = doctor.check_stale_state_locks(str(self.locks_dir), max_age_seconds=7200.0)
        self.assertEqual(res["status"], "warn")
        self.assertIn("worker.lock", res["message"])
        self.assertIn("rm .scheduler_locks/*.lock", res["fix"])

    def test_stale_locks_missing_dir_ok(self):
        missing = self.root / "no_locks"
        res = doctor.check_stale_state_locks(str(missing))
        self.assertEqual(res["status"], "ok")

    def test_run_environment_checks_aggregation_all_ok(self):
        upload_dir = self.products_dir / "upload"
        upload_dir.mkdir(parents=True)
        self.plan_path.write_text("{}")

        res = doctor.run_environment_checks(
            products_dir=str(self.products_dir),
            marketing_dir=str(self.marketing_dir),
            plan_path=str(self.plan_path),
            keywords_path=str(self.keywords_path),
            exported_dir=str(self.root / "exported_content"),
            locks_dir=str(self.locks_dir),
        )
        self.assertEqual(res["overall_status"], "ok")
        self.assertEqual(len(res["checks"]), 6)

    def test_run_environment_checks_aggregation_warn(self):
        # post plan missing causes warn
        upload_dir = self.products_dir / "upload"
        upload_dir.mkdir(parents=True)

        res = doctor.run_environment_checks(
            products_dir=str(self.products_dir),
            marketing_dir=str(self.marketing_dir),
            plan_path=str(self.plan_path),
            keywords_path=str(self.keywords_path),
            exported_dir=str(self.root / "exported_content"),
            locks_dir=str(self.locks_dir),
        )
        self.assertEqual(res["overall_status"], "warn")

    def test_run_environment_checks_aggregation_fail(self):
        upload_dir = self.products_dir / "upload"
        upload_dir.mkdir(parents=True)
        self.plan_path.write_text("{corrupt")

        res = doctor.run_environment_checks(
            products_dir=str(self.products_dir),
            marketing_dir=str(self.marketing_dir),
            plan_path=str(self.plan_path),
            keywords_path=str(self.keywords_path),
            exported_dir=str(self.root / "exported_content"),
            locks_dir=str(self.locks_dir),
        )
        self.assertEqual(res["overall_status"], "fail")

    def test_check_responses_contain_tenglish_fix(self):
        checks = [
            doctor.check_upload_packs_folder(str(self.root / "missing")),
            doctor.check_marketing_folders_per_ready_pack(str(self.products_dir), str(self.marketing_dir)),
            doctor.check_post_plan_valid_json(str(self.root / "missing.json")),
            doctor.check_keywords_json_valid_if_present(str(self.root / "missing.json")),
            doctor.check_exported_content_disk_usage(str(self.root)),
            doctor.check_stale_state_locks(str(self.locks_dir)),
        ]
        for c in checks:
            self.assertIn("status", c)
            self.assertIn("message", c)
            self.assertIn("fix", c)
            self.assertIsInstance(c["fix"], str)
            self.assertGreater(len(c["fix"]), 5)


if __name__ == "__main__":
    unittest.main()
