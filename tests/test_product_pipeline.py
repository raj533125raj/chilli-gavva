"""Offline delivery regressions: real files and encryption only in temp directories."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import checker
import cubes
import doctor
import guardrails
import launch_pack
import product_builder
import product_council
import product_pipeline as pipeline
import telegram_inbox
import workforce
from scripts import db_crypt
from test_launch_pack import fake_spec


DRAFT = ("PRODUCT NAME: Approved Fixture\nBUYER: adults\n"
         "WHAT'S INSIDE: Printable writing sections with notes.\n"
         "HOW TO USE: Print the PDF and write in the sections.\n"
         "LISTING DESCRIPTION: A simple structured printable for personal use.\n"
         "SEARCH TAGS: planner, printable, notes, personal, template\n")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = str(self.root / "robot.db")
        self.out = str(self.root / "products")
        self.sent = []
        self.prompts = []
        for patch in (mock.patch.dict(os.environ, {"DB_KEY": "fixture-key"}),
                      mock.patch("guardrails.is_killed", return_value=False),
                      mock.patch("rest_window.in_rest_window", return_value=False)):
            patch.start()
            self.addCleanup(patch.stop)

    def approved(self, review=None, council=False, niche="ADHD Planners Agent"):
        aid = guardrails.request_approval("public_draft", f"Draft: [{niche}] Approved Fixture",
                                         DRAFT, review, self.db)
        self.assertIn(f"#{aid}", telegram_inbox.handle_command("approve", [aid], self.db))
        if council:
            product_council.log_debate(aid, 1, "council_summary", "PASS", "Fixture", db_path=self.db)
        return aid

    def llm(self, prompt, size=None):
        self.prompts.append(prompt)
        if "Product Council Critic" in prompt:
            return json.dumps({"weaknesses": [], "verdict": "OK"})
        if "Buyer's Voice agent" in prompt:
            return json.dumps({"answer": "Clear structure", "conviction": 8})
        if "Judge agent on the Product Council" in prompt:
            return json.dumps({"verdict": "PASS", "reasons": ["Fixture passes"]})
        if "QA Checker" in prompt:
            return json.dumps({"score": 90, "verdict": "pass", "issues": []})
        return fake_spec(prompt, size)

    def run_pipeline(self, **kwargs):
        return pipeline.run_pending(self.db, self.out, self.llm,
                                    notify_fn=lambda text: self.sent.append(text) or True, **kwargs)

    def state(self, aid):
        with pipeline.connect(self.db) as conn:
            return conn.execute("SELECT status, folder, files FROM product_deliveries WHERE approval_id=?", (aid,)).fetchone()

    def test_approved_to_real_encrypted_pack_and_names_only_command(self):
        aid = self.approved()
        self.run_pipeline()
        status, folder, files = self.state(aid)
        self.assertEqual(status, "ready")
        self.assertEqual(len(self.prompts), 5)  # one council round, checker, spec
        self.assertEqual(product_builder.builds(self.db)[0]["status"], "built")
        archive = self.root / "products.tar.gz.enc"
        self.assertTrue(archive.read_bytes().startswith(b"Salted__"))
        restored = self.root / "restored"
        db_crypt.unpack_products(str(archive), str(restored))
        self.assertTrue(db_crypt.compare_product_trees(self.out, str(restored)))
        manifest = json.loads((restored / folder / "LAUNCH_PACK.json").read_text())
        self.assertEqual(manifest["truth_check_verdict"], "ready")
        self.assertEqual(sorted(manifest["files"]), sorted(json.loads(files)))
        self.assertEqual(len(self.sent), 1)
        self.assertIn(f"Product ready: #{aid}", self.sent[0])
        self.assertIn("— files ready to upload", self.sent[0])
        self.assertIn(f"/pack {aid}", self.sent[0])
        self.assertEqual(telegram_inbox.parse_command(f"/pack {aid}"), ("pack", [aid]))
        with mock.patch("builtins.open", side_effect=AssertionError("/pack must not open product files")):
            text = telegram_inbox.handle_command("pack", [aid], self.db)
        self.assertIn("listing.md", text)
        self.assertNotIn("structured writing sections", text)
        self.run_pipeline()
        self.assertEqual(len(self.prompts), 5)
        self.assertEqual(len(self.sent), 1)

    def test_encryption_outage_never_notifies_or_loses_approval_and_resumes_without_ai(self):
        aid = self.approved()
        with mock.patch.object(launch_pack, "encrypt_handoff", side_effect=OSError("private detail")):
            self.run_pipeline()
        self.assertEqual(self.state(aid)[0], "prepared")
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")
        self.assertEqual(self.sent, [])
        self.assertEqual(product_builder.builds(self.db)[0]["status"], "awaiting_encryption")
        self.assertIn("Private encryption failed", doctor.format_last_cases(db_path=self.db))
        self.assertNotIn("private detail", doctor.format_last_cases(db_path=self.db))
        with mock.patch.object(launch_pack, "build_launch_pack", side_effect=AssertionError("no rebuild")):
            self.run_pipeline()
        self.assertEqual(self.state(aid)[0], "ready")
        self.assertEqual(len(self.sent), 1)

    def test_split_workflow_confirms_encryption_then_inbox_retries_send(self):
        aid = self.approved()
        with mock.patch.dict(os.environ, {"DB_KEY": ""}):
            self.run_pipeline()
        self.assertEqual(self.state(aid)[0], "prepared")
        self.assertEqual(self.sent, [])
        archive = str(self.root / "products.tar.gz.enc")
        real_pack = db_crypt.pack_products
        with mock.patch.object(guardrails, "DB_PATH", self.db), \
             mock.patch.object(product_builder, "PRODUCTS_DIR", self.out), \
             mock.patch.object(db_crypt, "PRODUCTS_ENCRYPTED", archive), \
             mock.patch.object(db_crypt, "pack_products", side_effect=lambda: real_pack(self.out, archive)):
            self.assertEqual(db_crypt.main(["db_crypt.py", "pack-products"]), 0)
        pipeline.notify_ready(self.db, lambda text: False)
        pipeline.notify_ready(self.db, lambda text: self.sent.append(text) or True)
        pipeline.notify_ready(self.db, lambda text: self.sent.append(text) or True)
        self.assertEqual(len(self.sent), 1)

    def test_pack_cli_failure_goes_to_doctor_and_retries_without_rebuild(self):
        aid = self.approved()
        with mock.patch.dict(os.environ, {"DB_KEY": ""}):
            self.run_pipeline()
        with mock.patch.object(guardrails, "DB_PATH", self.db), \
             mock.patch.object(db_crypt, "pack_products", side_effect=db_crypt.CryptError("Fixture encryption failure")):
            self.assertEqual(db_crypt.main(["db_crypt.py", "pack-products"]), 1)
        self.assertEqual(self.state(aid)[0], "prepared")
        self.assertIn("Private encryption failed", doctor.format_last_cases(db_path=self.db))
        self.assertEqual(self.sent, [])
        self.run_pipeline()
        self.assertEqual(self.state(aid)[0], "ready")

    def test_existing_core_files_finish_without_regenerating_the_product(self):
        aid = self.approved(checker.format_review(90, []), council=True)
        spec, reason = product_builder.parse_spec(fake_spec(""), "ADHD Planners Agent")
        self.assertIsNone(reason)
        folder = product_builder.build_product(spec, aid, self.out)
        product_builder._record(self.db, aid, "built", folder)
        pdf = next((Path(self.out) / folder).glob("*-A4.pdf"))
        before = pdf.read_bytes()
        with mock.patch.object(product_builder, "build_product", side_effect=AssertionError("do not replace existing files")):
            self.run_pipeline()
        self.assertEqual(self.state(aid)[0], "ready")
        self.assertEqual(self.prompts, [])
        self.assertEqual(pdf.read_bytes(), before)

    def test_image_probe_keeps_incomplete_and_legacy_packs_in_backlog(self):
        aid = self.approved()
        folder = f"{aid}-partial"
        (Path(self.out) / folder).mkdir(parents=True)
        product_builder._record(self.db, aid, "needs_fix", folder)
        self.assertEqual(product_builder._todo(self.db, self.out)[0]["id"], aid)
        self.assertEqual(product_builder.builds(self.db)[0]["status"], "needs_fix")

    def test_worker_keeps_product_call_headroom_and_plan_does_not_round_builds_to_zero(self):
        self.approved()
        task = workforce.enqueue("research", {"topic": "Fixture"}, db_path=self.db)
        with mock.patch.object(guardrails, "daily_cap", return_value=5):
            plan = workforce.get_daily_pipeline_plan(self.db)
            self.assertEqual(plan["total_quotas"]["product_builds"], 1)
            result = workforce.run_once(db_path=self.db, llm=self.llm)
        self.assertEqual(result["ran"], 0)
        with workforce.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id=?", (task,)).fetchone()[0], "queued")
        self.assertEqual(self.prompts, [])

    def test_changed_files_before_encryption_confirmation_are_not_announced(self):
        aid = self.approved()
        with mock.patch.dict(os.environ, {"DB_KEY": ""}):
            self.run_pipeline()
        folder = self.state(aid)[1]
        (Path(self.out) / folder / "README.md").write_text("Changed fixture")
        archive = str(self.root / "products.tar.gz.enc")
        db_crypt.pack_products(self.out, archive)
        pipeline.mark_encrypted(self.db, self.out, archive)
        self.assertEqual(self.state(aid)[0], "retry")
        self.assertIn("files changed", doctor.format_last_cases(db_path=self.db))
        self.assertEqual(self.sent, [])

    def test_daily_attempts_shared_across_repeated_runs_then_continue_next_day(self):
        aids = [self.approved() for _ in range(3)]
        # No product/AI work is necessary to test the atomic daily reservation.
        with mock.patch.object(launch_pack, "build_launch_pack", side_effect=OSError):
            self.run_pipeline(day="2026-10-29")
            self.run_pipeline(day="2026-10-29")
            with pipeline.connect(self.db) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM product_delivery_attempts").fetchone()[0], 2)
            self.run_pipeline(day="2026-10-30", max_builds=1)
            with pipeline.connect(self.db) as conn:
                self.assertEqual(conn.execute("SELECT approval_id FROM product_delivery_attempts WHERE day='2026-10-30'").fetchone()[0], aids[2])

    def test_failures_are_isolated_and_approval_survives_build_exception(self):
        aid = self.approved()
        with mock.patch.object(launch_pack, "build_launch_pack", side_effect=RuntimeError("private draft")):
            self.run_pipeline()
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")
        self.assertEqual(self.state(aid)[0], "retry")
        self.assertIn("build: Stage failed (RuntimeError)", doctor.format_last_cases(db_path=self.db))
        self.assertNotIn("private draft", doctor.format_last_cases(db_path=self.db))

    def test_gates_stop_automatic_work(self):
        aid = self.approved()
        for patch in (mock.patch("guardrails.is_killed", return_value=True),
                      mock.patch("rest_window.in_rest_window", return_value=True),
                      mock.patch("guardrails.automation_allowed", return_value=False)):
            with patch:
                self.assertEqual(self.run_pipeline(), [])
        cubes.turn_off("product", self.db)
        self.assertEqual(self.run_pipeline(), [])
        self.assertEqual(self.prompts, [])
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")

    def test_pending_rejected_held_and_exhausted_ai_cap_do_not_build(self):
        for status in ("pending", "rejected"):
            aid = guardrails.request_approval("public_draft", "Draft: [ADHD Planners Agent] Fixture", DRAFT, db_path=self.db)
            if status == "rejected":
                guardrails.decide(aid, False, self.db)
        held = self.approved()
        with mock.patch.dict(product_builder.OWNER_HOLDS, {held: "Owner hold"}):
            self.assertEqual(self.run_pipeline(), [])
        with mock.patch("guardrails.can_call_llm", return_value=False):
            self.run_pipeline()
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.sent, [])

    def test_latest_council_failure_and_checker_reject_override_old_high_scores(self):
        aid = self.approved("score 100/100 | Checker: 90/100 | CHECKER: REJECT", council=True)
        self.assertFalse(launch_pack.is_eligible(aid, self.db))
        guardrails.set_review(aid, "score 100/100 | Checker: 40/100 | CHECKER: PASS", self.db)
        self.assertFalse(launch_pack.is_eligible(aid, self.db))
        guardrails.set_review(aid, checker.format_review(90, []), self.db)
        product_council.log_debate(aid, 1, "judge", "PASS", "Old", db_path=self.db)
        product_council.log_debate(aid, 2, "council_summary", "NEEDS YOU", "Later failure", db_path=self.db)
        self.assertFalse(launch_pack.is_eligible(aid, self.db))

    def test_approved_council_never_rewrites_owner_text(self):
        aid = self.approved()
        with mock.patch.object(product_council, "run_critic", return_value=({"verdict": "FLAGGED", "weaknesses": ["weak"]}, 0)), \
             mock.patch.object(product_council, "run_buyer", return_value=({"conviction": 2}, 0)), \
             mock.patch.object(product_council, "run_fixer", side_effect=AssertionError("owner text is immutable")), \
             mock.patch.object(product_council, "run_judge", return_value=({"verdict": "RETRY"}, 0)):
            result = product_council.run_council(aid, DRAFT, db_path=self.db, max_rounds=1)
        self.assertEqual(result["draft_text"], DRAFT)
        self.assertEqual(guardrails.get_approval(aid, self.db)["payload"], DRAFT)

    def test_approval_arriving_during_fixer_is_committed_and_judged_from_saved_text(self):
        aid = guardrails.request_approval("public_draft", "Draft: [ADHD Planners Agent] Approved Fixture", DRAFT, db_path=self.db)
        def approve_then_finish(*args, **kwargs):
            telegram_inbox.handle_command("approve", [aid], self.db)
            return "Unsaved fixture revision", 1
        with mock.patch.object(product_council, "run_critic", return_value=({"verdict": "FLAGGED", "weaknesses": ["weak"]}, 0)), \
             mock.patch.object(product_council, "run_buyer", return_value=({"conviction": 2}, 0)), \
             mock.patch.object(product_council, "run_fixer", side_effect=approve_then_finish), \
             mock.patch.object(product_council, "run_judge", return_value=({"verdict": "PASS"}, 0)) as judge:
            result = product_council.run_council(aid, DRAFT, db_path=self.db, max_rounds=1)
        self.assertEqual(result["draft_text"], DRAFT)
        self.assertEqual(judge.call_args.args[0], DRAFT)
        self.assertEqual(guardrails.get_approval(aid, self.db)["status"], "approved")
        self.assertEqual(guardrails.get_approval(aid, self.db)["payload"], DRAFT)

    def test_no_spec_no_fallback_product_and_missing_images_cannot_be_ready(self):
        aid = self.approved(checker.format_review(90, []), council=True)
        result = launch_pack.build_launch_pack(aid, self.db, self.out, lambda *a, **k: None)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(os.listdir(self.out), [])
        with mock.patch("mockups.write_mockups", return_value=[]):
            result = launch_pack.build_launch_pack(aid, self.db, self.out, fake_spec, max_fix_rounds=1)
        self.assertEqual(result["status"], "needs_fix")
        self.assertFalse((Path(self.out) / result["folder"] / "cover.png").exists())

    def test_one_missing_or_placeholder_image_blocks_an_otherwise_ready_pack(self):
        aid = self.approved(checker.format_review(90, []), council=True)
        result = launch_pack.build_launch_pack(aid, self.db, self.out, fake_spec)
        self.assertEqual(result["status"], "ready")
        folder = Path(self.out) / result["folder"]
        image = folder / "cover.png"
        image.unlink()
        spec, _ = product_builder.parse_spec(fake_spec(""), "ADHD Planners Agent")
        spec["listing"]["tags"] = launch_pack._ensure_13_tags(spec["listing"]["tags"], spec["niche"])
        quality, reasons = launch_pack.evaluate_pack_quality(str(folder), spec)
        self.assertLess(quality, launch_pack.QUALITY_BAR)
        self.assertTrue(any("Missing required listing images" in reason for reason in reasons))
        from PIL import Image
        Image.new("RGB", (1, 1)).save(image)
        quality, reasons = launch_pack.evaluate_pack_quality(str(folder), spec)
        self.assertLess(quality, launch_pack.QUALITY_BAR)
        self.assertTrue(any("placeholder" in reason for reason in reasons))

    def test_other_supported_formats_reach_truth_ready_with_real_files(self):
        from test_product_builder import SPECS
        for niche in ("Budget Sheets Agent", "Resume Bundle Agent", "Notion Templates Agent"):
            with self.subTest(niche=niche):
                aid = self.approved(checker.format_review(90, []), council=True, niche=niche)
                raw = json.loads(json.dumps(SPECS[niche]))
                if niche == "Notion Templates Agent":
                    raw["pages"][0]["markdown"] = "Instructions for the approved fixture. " * 25
                    raw["databases"][0]["rows"] = [["Sample", "Draft", "2026-10-31"]] * 4
                result = launch_pack.build_launch_pack(aid, self.db, self.out,
                                                       lambda *a, **k: json.dumps(raw))
                self.assertEqual(result["status"], "ready", result.get("reasons"))

    def test_cli_uses_delivery_pipeline(self):
        with mock.patch.object(pipeline, "run_pending", return_value=[]) as run, \
             mock.patch.object(product_builder, "rebuild_versions", return_value=[]), \
             mock.patch.object(product_builder, "build_bundles", return_value=[]), \
             mock.patch.object(product_builder, "add_missing_images", return_value=[]), \
             mock.patch.object(product_builder, "apply_owner_holds", return_value=[]), \
             mock.patch.object(product_builder, "check_unchecked", return_value=[]):
            product_builder.main(["--db", self.db])
        run.assert_called_once_with(db_path=self.db, max_builds=2)


if __name__ == "__main__":
    unittest.main()
