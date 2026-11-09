#!/usr/bin/env python3
"""tests/test_t5b_reviews.py — Unit tests for review_request.py (BINNY-T5B)."""

from __future__ import annotations

import io
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

import review_request


class TestReviewRequest(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env in setUp with mock.patch.dict
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.test_dir = tempfile.mkdtemp(prefix="test_t5b_reviews_")
        self.products_dir = Path(self.test_dir) / "products"
        self.upload_dir = self.products_dir / "upload"
        self.marketing_dir = Path(self.test_dir) / "marketing"

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.marketing_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_pack(
        self,
        pack_name: str,
        title: str = "Daily Goal & Routine Planner",
        desc: str = "A structured daily planner to organize routines and goals.",
        what_you_get: str = "- 1 PDF Daily Routine Sheet\n- 1 PDF Weekly Goals Checklist",
        tags: str = "daily planner, routine, goals, printable, organization",
        create_files: list[str] | None = None,
    ) -> Path:
        pdir = self.upload_dir / pack_name
        pdir.mkdir(parents=True, exist_ok=True)

        content = (
            f"Title: {title}\n\n"
            f"Description: {desc}\n\n"
            f"What you get:\n{what_you_get}\n\n"
            f"Tags: {tags}\n"
        )
        (pdir / "listing.txt").write_text(content, encoding="utf-8")
        (pdir / "product.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)

        if create_files:
            for fname in create_files:
                (pdir / fname).write_text("sample content", encoding="utf-8")

        return pdir

    # 1. Output file path test
    def test_output_file_location(self):
        pack = self._create_pack("pack-01-planner")
        out_file = review_request.write_review_request(pack, self.marketing_dir)
        expected = self.marketing_dir / "pack-01-planner" / "review_request.md"
        self.assertEqual(out_file, expected)
        self.assertTrue(out_file.is_file())

    # 2. Pack folder is untouched
    def test_pack_folder_untouched(self):
        pack = self._create_pack("pack-02-untouched")
        before_files = sorted(f.name for f in pack.iterdir())
        review_request.write_review_request(pack, self.marketing_dir)
        after_files = sorted(f.name for f in pack.iterdir())
        self.assertEqual(before_files, after_files)

    # 3. Three templates are generated
    def test_exactly_three_templates_generated(self):
        pack = self._create_pack("pack-03-three")
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        self.assertEqual(len(templates), 3)

    # 4. English template character length <= 600
    def test_english_template_lengths_under_600(self):
        pack = self._create_pack(
            "pack-04-len-en",
            title="Super Comprehensive Academic Student Life & Habit Management System",
        )
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        for t in templates:
            self.assertLessEqual(
                len(t["english"]), 600, f"{t['name']} English exceeded 600 chars"
            )

    # 5. Tenglish template character length <= 600
    def test_tenglish_template_lengths_under_600(self):
        pack = self._create_pack(
            "pack-05-len-te",
            title="Super Comprehensive Academic Student Life & Habit Management System",
        )
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        for t in templates:
            self.assertLessEqual(
                len(t["tenglish"]), 600, f"{t['name']} Tenglish exceeded 600 chars"
            )

    # 6. Placeholder [BUYER NAME] present in all templates
    def test_buyer_name_placeholder_present(self):
        pack = self._create_pack("pack-06-buyer-name")
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        for t in templates:
            self.assertIn("[BUYER NAME]", t["english"])
            self.assertIn("[BUYER NAME]", t["tenglish"])

    # 7. Placeholder [STORE LINK] present in all templates
    def test_store_link_placeholder_present(self):
        pack = self._create_pack("pack-07-store-link")
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        for t in templates:
            self.assertIn("[STORE LINK]", t["english"])
            self.assertIn("[STORE LINK]", t["tenglish"])

    # 8. Polite thank you in all English versions
    def test_thank_you_in_english_templates(self):
        pack = self._create_pack("pack-08-thank-you-en")
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        for t in templates:
            self.assertTrue(
                re.search(r"\bthank\s+you\b", t["english"], re.IGNORECASE),
                f"Missing thank you in {t['id']}",
            )

    # 9. Polite thank you in all Tenglish versions
    def test_thank_you_in_tenglish_templates(self):
        pack = self._create_pack("pack-09-thank-you-te")
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        for t in templates:
            self.assertTrue(
                re.search(r"\bthanks\b", t["tenglish"], re.IGNORECASE),
                f"Missing thanks in {t['id']}",
            )

    # 10. Gentle review ask in all templates
    def test_gentle_review_ask_present(self):
        pack = self._create_pack("pack-10-review-ask")
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        for t in templates:
            self.assertIn("review", t["english"].lower())
            self.assertIn("review", t["tenglish"].lower())

    # 11. No incentives or discounts in templates or markdown
    def test_no_incentives_in_output(self):
        pack = self._create_pack("pack-11-no-incentives")
        out_file = review_request.write_review_request(pack, self.marketing_dir)
        content = out_file.read_text(encoding="utf-8")
        banned_incentives = [
            r"\bcoupon\b",
            r"\bdiscount\b",
            r"\bfreebie\b",
            r"\bcashback\b",
            r"\breward\b",
            r"\bgiveaway\b",
            r"%\s*off",
        ]
        for pat in banned_incentives:
            matches = re.findall(pat, content, re.IGNORECASE)
            self.assertEqual(
                matches, [], f"Found forbidden incentive pattern: {pat} in {matches}"
            )

    # 12. No pressure or fake urgency in templates or markdown
    def test_no_fake_urgency_in_output(self):
        pack = self._create_pack("pack-12-no-urgency")
        out_file = review_request.write_review_request(pack, self.marketing_dir)
        content = out_file.read_text(encoding="utf-8")
        banned_urgency = [
            r"\burpent\b",
            r"\bhurry\b",
            r"\blimited\s+time\b",
            r"\blast\s+chance\b",
            r"\bact\s+fast\b",
            r"\bimmediately\b",
            r"\bmust\s+review\b",
        ]
        for pat in banned_urgency:
            matches = re.findall(pat, content, re.IGNORECASE)
            self.assertEqual(
                matches, [], f"Found forbidden urgency pattern: {pat} in {matches}"
            )

    # 13. No money amounts or currency symbols
    def test_no_money_amounts_in_markdown(self):
        pack = self._create_pack("pack-13-no-money")
        out_file = review_request.write_review_request(pack, self.marketing_dir)
        content = out_file.read_text(encoding="utf-8")
        money_match = review_request.check_for_money(content)
        self.assertIsNone(money_match, f"Found money pattern in output: {money_match}")

    # 14. Check money sanitizer strips amounts from listing input
    def test_sanitizer_removes_money_from_listing(self):
        pack = self._create_pack(
            "pack-14-money-clean",
            title="Budget Planner priced at $25 USD",
            desc="Great value for ₹500 or 10 USD.",
            what_you_get="- 1 PDF Sheet Rs. 100",
        )
        info = review_request.parse_listing(pack)
        self.assertNotIn("$25", info["title"])
        self.assertNotIn("₹500", info["description"])
        self.assertNotIn("10 USD", info["description"])
        out_file = review_request.write_review_request(pack, self.marketing_dir)
        content = out_file.read_text(encoding="utf-8")
        self.assertIsNone(review_request.check_for_money(content))

    # 15. Check brand scrubbing
    def test_brand_scrubbing(self):
        pack = self._create_pack(
            "pack-15-brand-clean",
            title="Notion and Google Sheets Life Tracker",
            desc="Built for Excel and GoodNotes on iPad.",
        )
        info = review_request.parse_listing(pack)
        self.assertNotIn("Google Sheets", info["title"])
        self.assertNotIn("Notion", info["title"])
        self.assertNotIn("Excel", info["description"])
        self.assertNotIn("GoodNotes", info["description"])

    # 16. PDF detection affects usage advice
    def test_pdf_usage_guidance(self):
        pack = self._create_pack(
            "pack-16-pdf",
            desc="Printable PDF sheets",
            what_you_get="- 1 PDF File",
        )
        info = review_request.parse_listing(pack)
        self.assertTrue(info["has_pdf"])
        self.assertFalse(info["has_xlsx"])
        templates = review_request.generate_templates(info)
        # Should mention PDF / print in usage
        self.assertIn("PDF", templates[0]["english"])

    # 17. Spreadsheet detection affects usage advice
    def test_spreadsheet_usage_guidance(self):
        pack = self._create_pack(
            "pack-17-sheet",
            desc="Automated spreadsheet template",
            what_you_get="- 1 Spreadsheet Sheet",
            create_files=["data.xlsx"],
        )
        info = review_request.parse_listing(pack)
        self.assertTrue(info["has_xlsx"])
        templates = review_request.generate_templates(info)
        self.assertIn("spreadsheet", templates[0]["english"].lower())

    # 18. Both PDF and Spreadsheet detection
    def test_pdf_and_spreadsheet_guidance(self):
        pack = self._create_pack(
            "pack-18-both",
            desc="Printable PDF and spreadsheet tracker",
            what_you_get="- 1 PDF sheet\n- 1 spreadsheet template",
            create_files=["sheet.xlsx", "planner.pdf"],
        )
        info = review_request.parse_listing(pack)
        self.assertTrue(info["has_pdf"])
        self.assertTrue(info["has_xlsx"])
        templates = review_request.generate_templates(info)
        self.assertIn("spreadsheet", templates[0]["english"].lower())
        self.assertIn("pdf", templates[0]["english"].lower())

    # 19. Generic fallback when neither PDF nor XLSX specified
    def test_generic_fallback_guidance(self):
        pack = self._create_pack(
            "pack-19-generic",
            desc="Digital assets package",
            what_you_get="- Digital templates",
        )
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        self.assertIn("files", templates[0]["english"].lower())

    # 20. Atomic write ensures temp file is removed
    def test_atomic_write_leaves_no_temp_files(self):
        pack = self._create_pack("pack-20-atomic")
        review_request.write_review_request(pack, self.marketing_dir)
        pack_marketing = self.marketing_dir / "pack-20-atomic"
        temp_files = list(pack_marketing.glob("*.tmp"))
        self.assertEqual(temp_files, [])

    # 21. Deterministic generation (repeated calls produce identical content)
    def test_deterministic_output(self):
        pack = self._create_pack("pack-21-determ")
        out1 = review_request.write_review_request(pack, self.marketing_dir)
        content1 = out1.read_text(encoding="utf-8")
        out2 = review_request.write_review_request(pack, self.marketing_dir)
        content2 = out2.read_text(encoding="utf-8")
        self.assertEqual(content1, content2)

    # 22. Tenglish house style in owner guidance
    def test_tenglish_owner_guidance(self):
        pack = self._create_pack("pack-22-tenglish-guide")
        out_file = review_request.write_review_request(pack, self.marketing_dir)
        content = out_file.read_text(encoding="utf-8")
        # Check presence of house style keywords
        self.assertIn("Owner Guide (Tenglish House Style)", content)
        self.assertIn("pampali", content)
        self.assertIn("Niyamaalu", content)
        self.assertIn("cheyadu", content)

    # 23. Hand-sent explicit note present
    def test_hand_sent_explicit_note(self):
        pack = self._create_pack("pack-23-handsent")
        out_file = review_request.write_review_request(pack, self.marketing_dir)
        content = out_file.read_text(encoding="utf-8")
        self.assertIn("Never sends automatically", content)
        self.assertIn("hand tho", content)

    # 24. Discover ready packs finds valid packs
    def test_discover_ready_packs(self):
        self._create_pack("pack-24-a")
        self._create_pack("pack-24-b")
        # Empty folder without listing.txt should be ignored
        (self.upload_dir / "not-a-pack").mkdir()

        packs = review_request.discover_ready_packs(self.products_dir)
        names = [p.name for p in packs]
        self.assertIn("pack-24-a", names)
        self.assertIn("pack-24-b", names)
        self.assertNotIn("not-a-pack", names)

    # 25. Process all ready packs
    def test_process_all_ready_packs(self):
        self._create_pack("pack-25-a")
        self._create_pack("pack-25-b")
        written = review_request.process_all_ready_packs(
            self.products_dir, self.marketing_dir
        )
        self.assertEqual(len(written), 2)
        for w in written:
            self.assertTrue(w.is_file())

    # 26. CLI --pack argument with specific directory
    def test_cli_single_pack_dir(self):
        pack = self._create_pack("pack-26-cli")
        ret = review_request.main([
            "--pack", str(pack),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(ret, 0)
        expected = self.marketing_dir / "pack-26-cli" / "review_request.md"
        self.assertTrue(expected.is_file())

    # 27. CLI --pack argument with pack ID name
    def test_cli_pack_id(self):
        self._create_pack("pack-27-id")
        ret = review_request.main([
            "--pack", "pack-27-id",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(ret, 0)
        expected = self.marketing_dir / "pack-27-id" / "review_request.md"
        self.assertTrue(expected.is_file())

    # 28. CLI --all argument
    def test_cli_all(self):
        self._create_pack("pack-28-a")
        self._create_pack("pack-28-b")
        ret = review_request.main([
            "--all",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(ret, 0)
        self.assertTrue((self.marketing_dir / "pack-28-a" / "review_request.md").is_file())
        self.assertTrue((self.marketing_dir / "pack-28-b" / "review_request.md").is_file())

    # 29. Missing pack returns error exit code 1
    def test_cli_missing_pack(self):
        ret = review_request.main([
            "--pack", "non-existent-pack",
            "--products-dir", str(self.products_dir),
            "--marketing-dir", str(self.marketing_dir),
        ])
        self.assertEqual(ret, 1)

    # 30. Check raising ValueError if money pattern injected
    def test_money_pattern_exception(self):
        pack = self._create_pack("pack-30-inject")
        with mock.patch("review_request.build_markdown_document", return_value="Here is $50 price"):
            with self.assertRaises(ValueError) as ctx:
                review_request.write_review_request(pack, self.marketing_dir)
            self.assertIn("Prohibited money pattern", str(ctx.exception))

    # 31. Handling empty or minimal listing.txt
    def test_minimal_listing(self):
        pdir = self.upload_dir / "pack-31-minimal"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "listing.txt").write_text("", encoding="utf-8")
        out_file = review_request.write_review_request(pdir, self.marketing_dir)
        self.assertTrue(out_file.is_file())
        content = out_file.read_text(encoding="utf-8")
        self.assertIn("Pack 31 Minimal", content)

    # 32. Very long title truncation ensures char count stays within 600
    def test_very_long_title_truncation(self):
        super_long_title = "A " * 300
        pack = self._create_pack("pack-32-long", title=super_long_title)
        info = review_request.parse_listing(pack)
        templates = review_request.generate_templates(info)
        for t in templates:
            self.assertLessEqual(len(t["english"]), 600)
            self.assertLessEqual(len(t["tenglish"]), 600)

    # 33. Checklist items rendered with hand-tickable checkboxes
    def test_hand_tickable_checkboxes(self):
        pack = self._create_pack("pack-33-boxes")
        out_file = review_request.write_review_request(pack, self.marketing_dir)
        content = out_file.read_text(encoding="utf-8")
        self.assertIn("- [ ]", content)


if __name__ == "__main__":
    unittest.main()
