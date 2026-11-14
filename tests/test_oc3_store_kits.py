"""Regression tests for store_kits fixes from the OC3 review."""

import tempfile
import typing
import unittest
from pathlib import Path
from typing import Union

import store_kits


class TestPython39Annotations(unittest.TestCase):
    def test_build_and_discovery_use_typing_union(self):
        hints = typing.get_type_hints(store_kits.build)
        self.assertEqual(hints["pack_dir"], Union[Path, str])
        hints = typing.get_type_hints(store_kits.discover_ready_packs)
        self.assertEqual(hints["products_dir"], Union[Path, str])


class TestMoneyRefusal(unittest.TestCase):
    def test_generate_kit_text_refuses_money_instead_of_sanitizing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "1-planner"
            pack.mkdir()
            info = {
                "title": "Focus Planner",
                "description": "Only $10 for this planner.",
                "what_you_get": "A4 PDF",
                "tags": "planner, focus",
            }
            with self.assertRaises(ValueError) as caught:
                store_kits.generate_kit_text("gumroad", pack, info)
        self.assertIn("forbidden money", str(caught.exception))
        self.assertNotIn("[PRICE SET BY OWNER]", str(caught.exception))


class TestFlagGraphemes(unittest.TestCase):
    def test_regional_indicator_pair_is_one_grapheme(self):
        flag = "\U0001F1EE\U0001F1F3"
        self.assertEqual(store_kits.grapheme_clusters(flag), [flag])
        self.assertEqual(store_kits.count_graphemes(flag), 1)
        self.assertEqual(store_kits.grapheme_clusters("A" + flag + "B"), ["A", flag, "B"])

    def test_trim_does_not_split_a_flag(self):
        flag = "\U0001F1EE\U0001F1F3"
        other = "\U0001F1FA\U0001F1F8"
        self.assertEqual(store_kits.trim_to_word_boundary(flag + other, 1), flag)
