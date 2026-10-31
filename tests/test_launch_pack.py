"""Unit tests for launch_pack.py.

Tests:
  - Eligibility checks (owner approval + Product Council + QA Checker)
  - Full launch pack build from a fake approved draft using temp dirs
  - Generation of required media, listing.md with 13 tags, FAQ, checklist, file list
  - Truth check integration and pack quality scoring
  - Automated quality fixer loop (max 2 rounds)
  - Private encryption hand-off
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import guardrails
import launch_pack
import mockups
import product_council


def fake_spec(prompt, size=None):
    return json.dumps({
        "name": "Approved Fixture", "short_name": "approved-fixture",
        "listing": {"title": "Approved Fixture Planner",
                    "description": "A printable planner for personal use. Includes structured writing sections with room for notes. Print the supplied pages and write by hand.",
                    "tags": ["planner", "printable", "notes", "personal", "template"],
                    "buyer": "adults"},
        "instructions": ["Print the supplied PDF pages.", "Write in the sections."],
        "pages": [{"title": "Notes", "sections": [{"heading": "Notes", "type": "lines", "count": 8}]}]
    })


class TestLaunchPack(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_agent.db")
        self.out_dir = os.path.join(self.tmp_dir, "products")
        os.makedirs(self.out_dir, exist_ok=True)
        patch = mock.patch("workforce.default_llm", return_value=fake_spec)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def _create_approved_draft(self, title="Draft: [ADHD Planners Agent] Daily Focus System",
                               payload="Comprehensive daily focus planner for neurodivergent adults. Includes 4 printable pages in A4 and US Letter sizes.",
                               status="approved", review="score 85/100 | Checker: 85/100 | CHECKER: PASS"):
        conn = guardrails.connect(self.db_path)
        cur = conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status, review) VALUES (?, 'public_draft', ?, ?, ?, ?)",
            ("2026-10-24T12:00:00", title, payload, status, review)
        )
        approval_id = cur.lastrowid
        conn.commit()
        conn.close()

        # Record passing Product Council debate log
        product_council.log_debate(
            approval_id=approval_id,
            round_num=1,
            stage="council_summary",
            verdict="PASS",
            reasons="High buyer conviction and clear value",
            conviction=8,
            top_fix="Refined structure",
            db_path=self.db_path
        )
        return approval_id

    def test_eligibility_filtering(self):
        aid_valid = self._create_approved_draft()

        # Unapproved draft
        conn = guardrails.connect(self.db_path)
        cur = conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status, review) VALUES (?, 'public_draft', ?, ?, 'pending', ?)",
            ("2026-10-24T12:00:00", "Draft: [ADHD Planners Agent] Unapproved", "Payload", "score 50/100")
        )
        aid_pending = cur.lastrowid
        conn.commit()
        conn.close()

        self.assertTrue(launch_pack.is_eligible(aid_valid, db_path=self.db_path))
        self.assertFalse(launch_pack.is_eligible(aid_pending, db_path=self.db_path))

        eligible_drafts = launch_pack.get_eligible_drafts(db_path=self.db_path)
        self.assertEqual(len(eligible_drafts), 1)
        self.assertEqual(eligible_drafts[0]["id"], aid_valid)

    @unittest.skipUnless(mockups.available(), "needs Pillow and poppler (free image tools)")
    def test_build_full_launch_pack(self):
        aid = self._create_approved_draft()
        res = launch_pack.build_launch_pack(aid, db_path=self.db_path, out_dir=self.out_dir)

        self.assertEqual(res["approval_id"], aid)
        self.assertEqual(res["status"], "ready")
        self.assertGreaterEqual(res["pack_quality"], 80)

        folder_path = os.path.join(self.out_dir, res["folder"])
        self.assertTrue(os.path.isdir(folder_path))

        # Verify output files on disk
        files = os.listdir(folder_path)
        self.assertIn("cover.png", files)
        self.assertIn("preview-1.png", files)
        self.assertIn("preview-2.png", files)
        self.assertIn("preview-3.png", files)
        self.assertIn("listing.md", files)
        self.assertIn("README.md", files)
        self.assertIn("LICENSE.txt", files)
        self.assertIn("CHECKLIST.md", files)
        self.assertIn("FILE_LIST.txt", files)
        self.assertIn("LAUNCH_PACK.json", files)
        self.assertIn("TRUTH_CHECK.md", files)

        # Inspect listing.md for required sections
        with open(os.path.join(folder_path, "listing.md"), encoding="utf-8") as f:
            listing_text = f.read()

        self.assertIn("## Description", listing_text)
        self.assertIn("## Tags (13)", listing_text)
        self.assertIn("## Frequently Asked Questions (FAQ)", listing_text)
        self.assertIn("## License", listing_text)
        self.assertIn("## Included Files", listing_text)
        self.assertIn("## How to Upload to Gumroad / Etsy", listing_text)

        # Inspect LAUNCH_PACK.json manifest
        with open(os.path.join(folder_path, "LAUNCH_PACK.json"), encoding="utf-8") as f:
            manifest = json.load(f)

        self.assertEqual(manifest["approval_id"], aid)
        self.assertIn("pack_quality", manifest)
        self.assertIn("quality_reasons", manifest)

    @unittest.skipUnless(mockups.available(), "needs Pillow and poppler (free image tools)")
    def test_quality_evaluation_and_fixer_loop(self):
        aid = self._create_approved_draft()
        res = launch_pack.build_launch_pack(aid, db_path=self.db_path, out_dir=self.out_dir, max_fix_rounds=2)

        self.assertLessEqual(res["rounds"], 2)

        # Check recorded status in database
        conn = launch_pack.connect(self.db_path)
        row = conn.execute("SELECT status, quality_score, rounds FROM launch_packs WHERE approval_id = ?", (aid,)).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], res["status"])
        self.assertEqual(row[1], res["pack_quality"])

    @unittest.skipUnless(mockups.available(), "needs Pillow and poppler (free image tools)")
    def test_encryption_handoff(self):
        aid = self._create_approved_draft()

        from scripts import db_crypt
        with mock.patch.dict(os.environ, {"DB_KEY": "fixture-encryption-key"}):
            res = launch_pack.build_launch_pack(aid, db_path=self.db_path, out_dir=self.out_dir)
            self.assertEqual(res["approval_id"], aid)
            archive = launch_pack.encrypt_handoff(self.out_dir)
            self.assertEqual(archive, os.path.join(self.tmp_dir, "products.tar.gz.enc"))
            restored = os.path.join(self.tmp_dir, "restored")
            db_crypt.unpack_products(archive, restored)
            self.assertTrue(db_crypt.compare_product_trees(self.out_dir, restored))


if __name__ == "__main__":
    unittest.main()
