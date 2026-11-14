"""Regression tests for builder_eventplanner annotation compatibility."""

import typing
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import builder_eventplanner


class TestPython39Annotations(unittest.TestCase):
    def test_event_spec_uses_optional_dict(self):
        hints = typing.get_type_hints(builder_eventplanner.normalize_event_spec)
        self.assertEqual(hints["spec"], Optional[Dict[str, Any]])
        self.assertEqual(hints["return"], Dict[str, Any])

    def test_build_uses_typing_unions(self):
        hints = typing.get_type_hints(builder_eventplanner.build)
        self.assertEqual(hints["spec"], Optional[Dict[str, Any]])
        self.assertEqual(hints["out_dir"], Optional[Union[str, Path]])
        self.assertEqual(hints["return"], Union[List[str], Dict[str, Any]])


class TestCoverTitle(unittest.TestCase):
    @unittest.skipUnless(builder_eventplanner.HAS_PILLOW, "Pillow is required")
    def test_cover_title_trims_at_word_boundary(self):
        import tempfile
        from unittest import mock

        import store_kits

        title = "Ultimate Telugu Wedding Celebration Planner Mega Complete Bundle"
        spec = {"title": title}
        flat_title = store_kits.trim_to_word_boundary(title, 42)
        captured = []

        class FakeDraw:
            def text(self, xy, text, **kwargs):
                captured.append((xy, text, kwargs))

            def textbbox(self, xy, text, **kwargs):
                return (0, 0, len(text) * 8, 14)

            def rectangle(self, *args, **kwargs):
                pass

            def rounded_rectangle(self, *args, **kwargs):
                pass

            def line(self, *args, **kwargs):
                pass

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(builder_eventplanner.ImageDraw, "Draw", return_value=FakeDraw()):
            builder_eventplanner.render_cover_image(Path(tmp) / "cover.png", spec)

        main_titles = [text for xy, text, _ in captured if xy[1] == 145]
        self.assertEqual(main_titles, [flat_title])
        self.assertNotEqual(flat_title, title[:40] + "...")
        self.assertTrue(title.startswith(flat_title))
        self.assertEqual(title[len(flat_title)], " ")
