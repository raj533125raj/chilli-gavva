"""Regression tests for tg_kits annotation compatibility fixes."""

import typing
import unittest
from pathlib import Path
from typing import Dict, Optional, Union

import tg_kits


class TestPython39Annotations(unittest.TestCase):
    def test_find_ready_pack_dir_uses_typing_union(self):
        hints = typing.get_type_hints(tg_kits.find_ready_pack_dir)
        self.assertEqual(hints["aid"], Union[int, str])
        self.assertEqual(hints["return"], Optional[Path])

    def test_handle_uses_optional_dict(self):
        hints = typing.get_type_hints(tg_kits.handle)
        self.assertEqual(hints["ctx"], dict)
        self.assertEqual(hints["return"], Optional[dict])


class TestSafetyBeforeBuild(unittest.TestCase):
    def test_unsafe_pack_is_rejected_before_building_kits(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            products = root / "products"
            products.mkdir()
            unsafe = root / "outside"
            unsafe.mkdir()
            ctx = {
                "products_dir": str(products),
                "marketing_dir": str(root / "marketing"),
                "send_text": lambda text, keyboard=None: None,
                "send_document": lambda path, caption="": None,
            }
            with mock.patch.object(tg_kits, "find_ready_pack_dir", return_value=unsafe), \
                 mock.patch.object(tg_kits.store_kits, "build") as build_mock:
                result = tg_kits.handle("/kit 1 gumroad", ctx)
        build_mock.assert_not_called()
        self.assertIn("path safe kaadu", result["text"])
