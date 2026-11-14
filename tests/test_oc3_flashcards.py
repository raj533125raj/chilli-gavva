"""Regression tests for the OC3-1 flashcards builder fixes."""

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import builder_flashcards


@unittest.skipUnless(builder_flashcards.HAS_PILLOW, "Pillow is required")
class TestOc3FlashcardsFixes(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_oc3_flashcards_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, True)

    def _build(self, spec):
        return builder_flashcards.build(spec, self.tmp_dir)

    def test_cover_is_a_real_pdf(self):
        self._build({"kind": "flashcards", "topic": "alphabet"})
        data = (Path(self.tmp_dir) / "cover.pdf").read_bytes()
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertIn(b"/Type /Page", data)
        self.assertIn(b"Flashcards - Alphabet", data)
        self.assertNotEqual(data[:8], b"\x89PNG\r\n\x1a\n")

    def test_output_files_live_in_out_dir_not_marketing(self):
        created = self._build({"kind": "flashcards", "topic": "alphabet"})
        for name in created:
            self.assertTrue((Path(self.tmp_dir) / name).is_file(), name)
        self.assertFalse((Path(self.tmp_dir) / "marketing" / "cover.pdf").exists())
        self.assertFalse((Path(self.tmp_dir) / "marketing" / "LICENSE.txt").exists())

    def test_back_page_is_separate_and_mirrored(self):
        created = self._build({"kind": "flashcards", "topic": "alphabet"})
        self.assertIn("flashcards_alphabet_page_1_back.png", created)
        self.assertNotIn("flashcards_alphabet_page_1_back.png", created[:0])
        manifest = json.loads(
            (Path(self.tmp_dir) / "flashcards_alphabet_page_1_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["back_page"], "flashcards_alphabet_page_1_back.png")
        self.assertEqual(manifest["duplex"], "flip_horizontal")

        fronts = [{"x": 10, "y": 20, "width": 100, "height": 50, "label": "Front: A"}]
        backs = builder_flashcards._duplex_back_elements(fronts, 500)
        self.assertEqual(backs[0]["x"], 390)
        self.assertEqual(backs[0]["y"], 20)
        self.assertEqual(backs[0]["label"], "Back: A")

    def test_io_is_imported_at_module_level(self):
        self.assertIs(builder_flashcards.io, io)
        png = builder_flashcards._build_pdf_page(20, 20, [])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_trace_worksheet_stays_inside_page(self):
        width_px = builder_flashcards.PRINT_SIZES["a4"]["width_px"]
        height_px = builder_flashcards.PRINT_SIZES["a4"]["height_px"]
        captured = []

        def capture(w, h, elements, margin_px=40):
            captured.extend(elements)
            return b"page"

        with mock.patch.object(builder_flashcards, "_build_pdf_page", side_effect=capture):
            builder_flashcards._build_worksheet_trace(
                "alphabet", builder_flashcards.ALPHABET, width_px, height_px,
                Path(self.tmp_dir), [],
            )
        self.assertEqual(len(captured), 10)
        for elem in captured:
            self.assertLessEqual(elem["y"] + elem["height"], height_px)
            self.assertLessEqual(elem["x"] + elem["width"], width_px)


if __name__ == "__main__":
    unittest.main()
