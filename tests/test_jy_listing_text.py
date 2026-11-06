"""Tests for cleaner listing text in upload packs (BINNY-JY ROBOT)."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import product_builder
import upload_pack


class JyListingTextTests(unittest.TestCase):
    def setUp(self):
        # Remove every inherited GITHUB_* key, including newly introduced CI keys.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
        self.environment = mock.patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_five_tags_expanded_up_to_13_valid_tags(self):
        listing_md = """# Minimalist Daily Task Manager Schedule Organizer Book

## Description
A simple task planner for personal use only.

## Tags (5)
- task manager
- daily routine
- habit tracker
- minimalist sheet
- focus log

## What you get
- planner.pdf
"""
        res = upload_pack._listing(listing_md)
        self.assertIn("Tags:", res)
        tags_line = [line for line in res.splitlines() if line.startswith("Tags:")][0]
        tags = [t.strip() for t in tags_line[len("Tags:"):].split(",")]
        self.assertGreater(len(tags), 5)
        self.assertLessEqual(len(tags), 13)
        self.assertEqual(len(tags), 13)
        # Check all tags valid
        for tag in tags:
            self.assertLessEqual(len(tag), 20)
            self.assertTrue(all(c.isalnum() or c.isspace() for c in tag))

    def test_long_tag_dropped(self):
        raw_tags = [
            "short tag",
            "this tag is way longer than twenty characters",
            "good tag",
        ]
        cleaned = upload_pack._clean_tags(raw_tags, "Short Title")
        self.assertIn("short tag", cleaned)
        self.assertIn("good tag", cleaned)
        self.assertNotIn("this tag is way longer than twenty characters", cleaned)
        for t in cleaned:
            self.assertLessEqual(len(t), 20)

    def test_duplicate_tag_dropped(self):
        raw_tags = [
            "Focus Planner",
            "focus planner",
            "FOCUS PLANNER",
            "daily routine",
        ]
        cleaned = upload_pack._clean_tags(raw_tags, "My Title")
        self.assertEqual(len([t for t in cleaned if t.casefold() == "focus planner"]), 1)

    def test_invalid_characters_in_tags_dropped(self):
        raw_tags = [
            "valid tag 123",
            "tag-with-hyphen",
            "tag_with_underscore",
            "tag with #hashtag",
            "another valid",
        ]
        cleaned = upload_pack._clean_tags(raw_tags, "Title")
        self.assertIn("valid tag 123", cleaned)
        self.assertIn("another valid", cleaned)
        self.assertNotIn("tag-with-hyphen", cleaned)
        self.assertNotIn("tag_with_underscore", cleaned)
        self.assertNotIn("tag with #hashtag", cleaned)

    def test_app_claim_replaced(self):
        desc = (
            "Compatible with Google Sheets, Numbers, LibreOffice, "
            "your preferred spreadsheet program, and any spreadsheet app."
        )
        cleaned = upload_pack._clean_description(desc)
        for forbidden in ("Google Sheets", "Numbers", "LibreOffice",
                          "your preferred spreadsheet program", "any spreadsheet app"):
            self.assertNotIn(forbidden.casefold(), cleaned.casefold())
        self.assertIn("an Excel (.xlsx) file", cleaned)

    def test_personal_use_line_added_once(self):
        # Case 1: no "personal use" in description -> line added once
        desc1 = "Simple spreadsheet tracker for home budgets."
        cleaned1 = upload_pack._clean_description(desc1)
        self.assertEqual(cleaned1.count("For personal use only. Do not resell or share the files."), 1)
        self.assertTrue(cleaned1.endswith("For personal use only. Do not resell or share the files."))

        # Case 2: already has "personal use" -> line not added
        desc2 = "Simple spreadsheet for personal use only."
        cleaned2 = upload_pack._clean_description(desc2)
        self.assertEqual(cleaned2.count("For personal use only. Do not resell or share the files."), 0)
        self.assertEqual(cleaned2, desc2)

    def test_money_still_refused(self):
        listing_md = """# Budget Tracker

## Description
Only $10 for personal use.

## Tags (5)
- budget
- tracker
- finance
- money
- excel

## What you get
- budget.xlsx
"""
        res = upload_pack._listing(listing_md)
        with self.assertRaisesRegex(ValueError, "Money amount found"):
            upload_pack._check_money(res, "listing")


if __name__ == "__main__":
    unittest.main()
