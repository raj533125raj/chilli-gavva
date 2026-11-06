"""Tests for upload packs made by the robot and /packs, /pack commands in Telegram."""
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import guardrails
import product_builder as pb
import telegram_inbox as ti
import truth_check
import upload_pack


PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
       b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


class JxPacksTests(unittest.TestCase):
    def setUp(self):
        env_clean = {k: None for k in os.environ if k.startswith("GITHUB_") or k in ("RUNNER_ENVIRONMENT",)}
        self.env_patcher = mock.patch.dict(os.environ, env_clean)
        self.env_patcher.start()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.db_path = str(self.root / "test_data.db")
        self.out_dir = str(self.root / "products")
        os.makedirs(self.out_dir, exist_ok=True)

        guardrails.connect(self.db_path)
        pb.connect(self.db_path)

        # Mock PRODUCTS_DIR to temp directory
        self.prod_dir_patch = mock.patch.object(pb, "PRODUCTS_DIR", self.out_dir)
        self.prod_dir_patch.start()

    def tearDown(self):
        self.prod_dir_patch.stop()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _create_ready_product_folder(self, approval_id=1, name="focus-planner"):
        folder_name = f"{approval_id}-{name}"
        folder_path = pathlib.Path(self.out_dir) / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)

        listing_md = (
            "# **Focus Planner**\n\n"
            "## Description\n"
            "Two printable pages: a Brain Dump page and a Daily Focus page.\n"
            "Print on A4 or US Letter. Use **a pen** and [read the guide](https://example.invalid/guide).\n\n"
            "## Tags (3)\n"
            "- focus\n"
            "- printable planner\n"
            "- calm routine\n\n"
            "## What you get\n"
            "- **A4 PDF**\n"
            "- `US Letter PDF`\n"
            "- README and personal use licence\n"
        )
        (folder_path / "listing.md").write_text(listing_md, encoding="utf-8")
        (folder_path / "README.md").write_text("# Focus Planner\nWrite with a pen.\n", encoding="utf-8")
        (folder_path / "LICENSE.txt").write_text(pb.LICENSE_TEXT, encoding="utf-8")

        pages = [
            {"title": "Brain Dump", "sections": [{"heading": "Thoughts", "type": "lines", "count": 20}]},
            {"title": "Daily Focus", "sections": [{"heading": "Priorities", "type": "lines", "count": 20}]},
        ]
        spec = {"name": "Focus Planner", "listing": {"title": "Focus Planner", "buyer": "adults"},
                "pages": pages}
        pb.build_planner_pdf(str(folder_path / "focus-A4.pdf"), spec, "A4")
        pb.build_planner_pdf(str(folder_path / "focus-Letter.pdf"), spec, "Letter")
        (folder_path / "cover.png").write_bytes(PNG)
        return folder_name, folder_path

    def test_pack_created_after_ready_build(self):
        folder_name, folder_path = self._create_ready_product_folder(approval_id=10)
        res = pb.truth_check_folder(10, folder_name, db_path=self.db_path, out_dir=self.out_dir)
        self.assertEqual(res["verdict"], "ready")

        upload_dir = pathlib.Path(self.out_dir) / "upload" / folder_name
        self.assertTrue(upload_dir.is_dir(), f"Upload pack directory not created at {upload_dir}")
        self.assertTrue((upload_dir / "product.zip").is_file())
        self.assertTrue((upload_dir / "listing.txt").is_file())
        self.assertTrue((upload_dir / "checklist.txt").is_file())
        self.assertTrue((upload_dir / "cover.png").is_file())

        listing_text = (upload_dir / "listing.txt").read_text(encoding="utf-8")
        self.assertIn("Title: Focus Planner", listing_text)
        self.assertIn("What you get:", listing_text)
        self.assertNotIn("$", listing_text)
        self.assertNotIn("USD", listing_text)

    def test_refused_build_no_pack_and_logs(self):
        # Create unready/bad folder
        folder_name = "20-broken-product"
        folder_path = pathlib.Path(self.out_dir) / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)
        (folder_path / "README.md").write_text("broken", encoding="utf-8")
        (folder_path / "listing.md").write_text("# broken\n", encoding="utf-8")

        stderr_buf = io.StringIO()
        with mock.patch("sys.stderr", stderr_buf):
            res = pb.truth_check_folder(20, folder_name, db_path=self.db_path, out_dir=self.out_dir)

        self.assertNotEqual(res["verdict"], "ready")
        upload_dir = pathlib.Path(self.out_dir) / "upload" / folder_name
        self.assertFalse(upload_dir.exists())

    def test_refusal_when_make_fails_logs_short_line(self):
        folder_name, folder_path = self._create_ready_product_folder(approval_id=30)
        stderr_buf = io.StringIO()
        with mock.patch("upload_pack.make", side_effect=ValueError("Test refusal from make()")), \
             mock.patch("sys.stderr", stderr_buf):
            res = pb.truth_check_folder(30, folder_name, db_path=self.db_path, out_dir=self.out_dir)

        self.assertEqual(res["verdict"], "ready")
        self.assertIn("Upload pack refused: Test refusal from make()", stderr_buf.getvalue())

    def test_telegram_packs_list(self):
        # Create two ready packs
        self._create_ready_product_folder(approval_id=1, name="first-planner")
        pb.truth_check_folder(1, "1-first-planner", db_path=self.db_path, out_dir=self.out_dir)

        self._create_ready_product_folder(approval_id=2, name="second-planner")
        pb.truth_check_folder(2, "2-second-planner", db_path=self.db_path, out_dir=self.out_dir)

        text = ti.handle_command("packs", [], db_path=self.db_path)
        self.assertIn("#1 Focus Planner", text)
        self.assertIn("#2 Focus Planner", text)

    def test_telegram_pack_text(self):
        self._create_ready_product_folder(approval_id=5, name="sample-pack")
        pb.truth_check_folder(5, "5-sample-pack", db_path=self.db_path, out_dir=self.out_dir)

        reply = ti.handle_command("pack", [5], db_path=self.db_path)
        self.assertIn("Title: Focus Planner", reply)
        self.assertIn("Description:", reply)
        self.assertIn("Tags:", reply)
        self.assertIn("What you get:", reply)
        self.assertLessEqual(len(reply), 3500)
        self.assertNotIn(".zip", reply.lower())
        self.assertNotIn("/", reply)
        self.assertNotIn("\\", reply)
        self.assertNotIn("$", reply)

    def test_non_owner_ignored(self):
        fake_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        owner_chat_id = "999888"
        attacker_chat_id = "111222"

        updates = [
            {"update_id": 100, "message": {"chat": {"id": attacker_chat_id}, "text": "/packs"}},
            {"update_id": 101, "message": {"chat": {"id": attacker_chat_id}, "text": "/pack 1"}},
        ]

        sent = []
        with mock.patch("telegram_inbox.fetch_updates", return_value=updates), \
             mock.patch("telegram_inbox.acknowledge_update"), \
             mock.patch("telegram_inbox.send_text", side_effect=lambda tok, chat, text, buttons=None: sent.append((chat, text))):
            handled = ti.process_updates(fake_token, owner_chat_id, db_path=self.db_path)

        self.assertEqual(handled, 0)
        self.assertEqual(sent, [])

    def test_no_money_in_pack_or_replies(self):
        self._create_ready_product_folder(approval_id=7, name="money-test")
        pb.truth_check_folder(7, "7-money-test", db_path=self.db_path, out_dir=self.out_dir)

        listing_file = pathlib.Path(self.out_dir) / "upload" / "7-money-test" / "listing.txt"
        self.assertTrue(listing_file.is_file())
        text = listing_file.read_text(encoding="utf-8")
        upload_pack._check_money(text, "test listing")

        # Inject money into listing.txt directly to ensure upload_pack_listing_text refuses it
        listing_file.write_text(text + "\nPrice: $25 USD\n", encoding="utf-8")
        reply = ti.handle_command("pack", [7], db_path=self.db_path)
        self.assertIn("owner must set price", reply)
        self.assertNotIn("$25", reply)

    def test_public_folders_untouched(self):
        web_dir = pathlib.Path("web")
        before_web_files = set(web_dir.rglob("*")) if web_dir.is_dir() else set()

        folder_name, folder_path = self._create_ready_product_folder(approval_id=8, name="public-test")
        pb.truth_check_folder(8, folder_name, db_path=self.db_path, out_dir=self.out_dir)

        after_web_files = set(web_dir.rglob("*")) if web_dir.is_dir() else set()
        self.assertEqual(before_web_files, after_web_files)


if __name__ == "__main__":
    unittest.main()
