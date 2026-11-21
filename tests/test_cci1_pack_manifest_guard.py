"""CCI-1: a product pack may hold its own manifest.json (builder_habit writes one).

marketing_auto used to refuse any folder with a manifest.json as "a marketing folder", so habit tracker packs
never got their Pins / carousel. Only a marketing folder (a manifest with a "parts" dict) is refused now.
"""
import json
import tempfile
import unittest
from pathlib import Path

import marketing_auto


def _pack(root: Path, manifest: dict) -> Path:
    pack = root / "products" / "8-habit-tracker"
    pack.mkdir(parents=True)
    (pack / "listing.txt").write_text("Title: Habit Tracker\n", encoding="utf-8")
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack


class PackManifestGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _error(self, pack: Path) -> str:
        result = marketing_auto.build_for_pack(pack, str(self.root / "agent_data.db"),
                                               products_dir=self.root / "products", save=False)
        return result.get("error", "")

    def test_product_manifest_is_a_pack_not_a_marketing_folder(self):
        pack = _pack(self.root, {"product": "habit tracker", "files": ["listing.txt"]})
        self.assertNotIn("not a pack", self._error(pack))

    def test_marketing_folder_with_parts_manifest_is_still_refused(self):
        pack = _pack(self.root, {"version": 1, "parts": {"pins": {"status": "ready"}}})
        self.assertIn("not a pack", self._error(pack))

    def test_marketing_folder_name_is_still_refused(self):
        folder = self.root / "products" / marketing_auto.MARKETING_FOLDER / "x"
        folder.mkdir(parents=True)
        (folder / "listing.txt").write_text("Title: X\n", encoding="utf-8")
        self.assertIn("not a pack", self._error(folder))


if __name__ == "__main__":
    unittest.main()
