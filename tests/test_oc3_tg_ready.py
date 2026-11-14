"""Regression tests for tg_ready pack lookup fixes."""

import tempfile
import unittest
from pathlib import Path

import tg_ready


class TestZeroPaddedPackIds(unittest.TestCase):
    def test_zero_padded_pack_folder_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = Path(tmp) / "upload"
            pack = upload / "01-adhd-planner"
            pack.mkdir(parents=True)
            (pack / "listing.txt").write_text("Title: ADHD Planner\n", encoding="utf-8")
            self.assertEqual(tg_ready._find_target_pack(1, tmp), pack)

    def test_multiple_leading_zeros_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "002-focus"
            pack.mkdir()
            (pack / "listing.txt").write_text("Title: Focus\n", encoding="utf-8")
            self.assertEqual(tg_ready._find_target_pack(2, tmp), pack)
