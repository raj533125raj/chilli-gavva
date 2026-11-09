#!/usr/bin/env python3
"""tests/test_t4c_changelog.py — Unit tests for pack_changelog.py.

BINNY-T4C ROBOT: PACK VERSIONS AND CHANGELOG.
At least 30 unit tests covering:
- Discovering ready packs (upload directory, direct directory, empty, ignore non-packs)
- Snapshot computation (sha256 hashing, size, subdirectories/hidden files ignored)
- Comparison of snapshots (added, removed, modified, unchanged)
- Version parsing and bumping (initial 1.0, minor bumps, major bump on product.zip)
- Snapshot loading & atomic writing
- CHANGELOG.md generation, prepend order, and markdown structure
- Tenglish house style summary messages
- CLI execution (--pack, --all, --dry-run, --json, etc.)
- Strict checks against currency/money leakage
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import pack_changelog


class TestPackChangelog(unittest.TestCase):
    def setUp(self):
        # Clear GITHUB_* env in setUp with mock.patch.dict as required
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.test_dir = tempfile.mkdtemp(prefix="test_changelog_")
        self.products_dir = Path(self.test_dir) / "products"
        self.upload_dir = self.products_dir / "upload"
        self.versions_dir = self.products_dir / "versions"

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_pack(
        self,
        pack_name: str,
        files: dict[str, bytes | str] | None = None,
        in_upload: bool = True,
    ) -> Path:
        base = self.upload_dir if in_upload else self.products_dir
        pdir = base / pack_name
        pdir.mkdir(parents=True, exist_ok=True)

        if files is None:
            files = {
                "listing.txt": "Title: Sample Digital Planner\nTags: planner, focus\n",
                "product.zip": b"PK\x05\x06" + b"\x00" * 18,
                "cover.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 10,
            }

        for fname, content in files.items():
            fpath = pdir / fname
            if isinstance(content, str):
                fpath.write_text(content, encoding="utf-8")
            else:
                fpath.write_bytes(content)

        return pdir

    # 1. Discover ready packs - empty
    def test_01_discover_ready_packs_empty(self):
        packs = pack_changelog.discover_ready_packs(self.products_dir)
        self.assertEqual(len(packs), 0)

    # 2. Discover ready packs - finds valid pack with listing.txt
    def test_02_discover_ready_packs_single(self):
        self._create_pack("pack-alpha")
        packs = pack_changelog.discover_ready_packs(self.products_dir)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0].name, "pack-alpha")

    # 3. Discover ready packs - multiple packs sorted
    def test_03_discover_ready_packs_sorted(self):
        self._create_pack("pack-gamma")
        self._create_pack("pack-beta")
        packs = pack_changelog.discover_ready_packs(self.products_dir)
        self.assertEqual([p.name for p in packs], ["pack-beta", "pack-gamma"])

    # 4. Discover ready packs - ignores hidden and non-ready directories
    def test_04_discover_ready_packs_ignores_invalid(self):
        (self.upload_dir / ".hidden_pack").mkdir()
        (self.upload_dir / "empty_dir").mkdir()
        (self.upload_dir / "regular_file.txt").write_text("hello", encoding="utf-8")
        self._create_pack("valid-pack")
        packs = pack_changelog.discover_ready_packs(self.products_dir)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0].name, "valid-pack")

    # 5. Discover ready packs when products_dir has no upload subdirectory
    def test_05_discover_ready_packs_no_upload_subdir(self):
        custom_dir = Path(self.test_dir) / "custom_packs"
        custom_dir.mkdir()
        p = custom_dir / "direct-pack"
        p.mkdir()
        (p / "listing.txt").write_text("Title: Direct", encoding="utf-8")
        packs = pack_changelog.discover_ready_packs(custom_dir)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0].name, "direct-pack")

    # 6. sha256_file computes correct hash
    def test_06_sha256_file(self):
        test_file = Path(self.test_dir) / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")
        h = pack_changelog.sha256_file(test_file)
        self.assertEqual(h, "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9")

    # 7. snapshot_pack collects hashes and sizes deterministically
    def test_07_snapshot_pack_basic(self):
        pdir = self._create_pack("pack-snap")
        snap = pack_changelog.snapshot_pack(pdir)
        self.assertEqual(snap["pack_name"], "pack-snap")
        self.assertEqual(snap["files_count"], 3)
        self.assertIn("listing.txt", snap["files"])
        self.assertIn("product.zip", snap["files"])
        self.assertIn("cover.png", snap["files"])
        self.assertGreater(snap["files"]["product.zip"]["size"], 0)
        self.assertEqual(len(snap["files"]["listing.txt"]["sha256"]), 64)

    # 8. snapshot_pack ignores hidden files and subdirectories
    def test_08_snapshot_pack_ignores_hidden_and_subdirs(self):
        pdir = self._create_pack("pack-nested")
        (pdir / ".hidden.tmp").write_text("secret", encoding="utf-8")
        subdir = pdir / "subfolder"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("ignore", encoding="utf-8")

        snap = pack_changelog.snapshot_pack(pdir)
        self.assertEqual(snap["files_count"], 3)
        self.assertNotIn(".hidden.tmp", snap["files"])
        self.assertNotIn("subfolder", snap["files"])

    # 9. snapshot_pack raises FileNotFoundError on missing path
    def test_09_snapshot_pack_missing_dir(self):
        with self.assertRaises(FileNotFoundError):
            pack_changelog.snapshot_pack(Path(self.test_dir) / "non_existent")

    # 10. compare_snapshots identifies additions, removals, modifications, unchanged
    def test_10_compare_snapshots(self):
        old_files = {
            "file_kept.txt": {"size": 10, "sha256": "aaaa"},
            "file_modified.txt": {"size": 20, "sha256": "bbbb"},
            "file_removed.txt": {"size": 30, "sha256": "cccc"},
        }
        new_files = {
            "file_kept.txt": {"size": 10, "sha256": "aaaa"},
            "file_modified.txt": {"size": 25, "sha256": "bbbb_diff"},
            "file_added.txt": {"size": 40, "sha256": "dddd"},
        }
        diff = pack_changelog.compare_snapshots(old_files, new_files)
        self.assertEqual(diff["added"], ["file_added.txt"])
        self.assertEqual(diff["removed"], ["file_removed.txt"])
        self.assertEqual(diff["modified"], ["file_modified.txt"])
        self.assertEqual(diff["unchanged"], ["file_kept.txt"])

    # 11. parse_version handles standard and edge cases
    def test_11_parse_version(self):
        self.assertEqual(pack_changelog.parse_version("1.0"), (1, 0))
        self.assertEqual(pack_changelog.parse_version("2.14"), (2, 14))
        self.assertEqual(pack_changelog.parse_version("v3.5"), (3, 5))
        self.assertEqual(pack_changelog.parse_version("4"), (4, 0))
        self.assertEqual(pack_changelog.parse_version("invalid"), (1, 0))

    # 12. bump_version initial version is 1.0
    def test_12_bump_version_initial(self):
        diff = {"added": ["file.txt"], "removed": [], "modified": [], "unchanged": []}
        ver, changed = pack_changelog.bump_version(None, diff)
        self.assertEqual(ver, "1.0")
        self.assertTrue(changed)

    # 13. bump_version unchanged returns same version with changed=False
    def test_13_bump_version_unchanged(self):
        diff = {"added": [], "removed": [], "modified": [], "unchanged": ["file.txt"]}
        ver, changed = pack_changelog.bump_version("1.0", diff)
        self.assertEqual(ver, "1.0")
        self.assertFalse(changed)

    # 14. bump_version minor bump when non-product.zip files change
    def test_14_bump_version_minor(self):
        diff = {"added": ["extra.pdf"], "removed": [], "modified": ["listing.txt"], "unchanged": ["product.zip"]}
        ver, changed = pack_changelog.bump_version("1.0", diff)
        self.assertEqual(ver, "1.1")
        self.assertTrue(changed)

        ver2, changed2 = pack_changelog.bump_version("1.4", diff)
        self.assertEqual(ver2, "1.5")
        self.assertTrue(changed2)

    # 15. bump_version major bump when product.zip changes
    def test_15_bump_version_major_product_zip(self):
        diff = {"added": [], "removed": [], "modified": ["product.zip"], "unchanged": ["listing.txt"]}
        ver, changed = pack_changelog.bump_version("1.0", diff)
        self.assertEqual(ver, "2.0")
        self.assertTrue(changed)

        ver2, changed2 = pack_changelog.bump_version("1.5", diff)
        self.assertEqual(ver2, "2.0")
        self.assertTrue(changed2)

        ver3, changed3 = pack_changelog.bump_version("2.2", diff)
        self.assertEqual(ver3, "3.0")
        self.assertTrue(changed3)

    # 16. bump_version major bump when product.zip is added or removed
    def test_16_bump_version_major_product_zip_added_or_removed(self):
        diff_add = {"added": ["product.zip"], "removed": [], "modified": [], "unchanged": []}
        ver, changed = pack_changelog.bump_version("1.2", diff_add)
        self.assertEqual(ver, "2.0")

        diff_rem = {"added": [], "removed": ["product.zip"], "modified": [], "unchanged": []}
        ver_rem, changed_rem = pack_changelog.bump_version("2.1", diff_rem)
        self.assertEqual(ver_rem, "3.0")

    # 17. atomic_write_text and atomic_write_json write atomically without leaving temp files
    def test_17_atomic_writes(self):
        dest_txt = Path(self.test_dir) / "output" / "atomic.txt"
        pack_changelog.atomic_write_text(dest_txt, "safe content")
        self.assertEqual(dest_txt.read_text(encoding="utf-8"), "safe content")

        dest_json = Path(self.test_dir) / "output" / "atomic.json"
        pack_changelog.atomic_write_json(dest_json, {"k": 123})
        loaded = json.loads(dest_json.read_text(encoding="utf-8"))
        self.assertEqual(loaded, {"k": 123})

        # Ensure no leftover tmp files
        parent_files = [p.name for p in dest_json.parent.iterdir()]
        self.assertEqual(sorted(parent_files), ["atomic.json", "atomic.txt"])

    # 18. load_previous_snapshot missing or invalid file
    def test_18_load_previous_snapshot_resilient(self):
        self.assertIsNone(pack_changelog.load_previous_snapshot(Path(self.test_dir) / "none.json"))
        bad_json = Path(self.test_dir) / "bad.json"
        bad_json.write_text("{corrupt", encoding="utf-8")
        self.assertIsNone(pack_changelog.load_previous_snapshot(bad_json))

    # 19. format_tenglish_summary returns Telugu-English house style text
    def test_19_format_tenglish_summary_initial(self):
        summary = pack_changelog.format_tenglish_summary("habit-pack", "1.0", None, {}, is_initial=True)
        self.assertIn("habit-pack", summary)
        self.assertIn("v1.0", summary)
        self.assertIn("snapshot", summary)
        self.assertIn("ready ga unnay", summary)

    # 20. format_tenglish_summary minor bump vs major bump
    def test_20_format_tenglish_summary_bumps(self):
        diff_minor = {"added": [], "removed": [], "modified": ["cover.png"], "unchanged": []}
        msg_minor = pack_changelog.format_tenglish_summary("habit-pack", "1.1", "1.0", diff_minor, is_initial=False)
        self.assertIn("minor version bump", msg_minor)
        self.assertIn("v1.0 -> v1.1", msg_minor)

        diff_major = {"added": [], "removed": [], "modified": ["product.zip"], "unchanged": []}
        msg_major = pack_changelog.format_tenglish_summary("habit-pack", "2.0", "1.1", diff_major, is_initial=False)
        self.assertIn("major version bump", msg_major)
        self.assertIn("product.zip update ayindi", msg_major)

    # 21. format_tenglish_summary stable/unchanged
    def test_21_format_tenglish_summary_unchanged(self):
        diff_none = {"added": [], "removed": [], "modified": [], "unchanged": ["cover.png"]}
        msg = pack_changelog.format_tenglish_summary("habit-pack", "1.0", "1.0", diff_none, is_initial=False)
        self.assertIn("emi marledu", msg)
        self.assertIn("stable ga undi", msg)

    # 22. process_pack initial run creates 1.0 snapshot and CHANGELOG
    def test_22_process_pack_initial(self):
        pdir = self._create_pack("pack-init")
        res = pack_changelog.process_pack(pdir, versions_dir=self.versions_dir, snapshot_date="2025-02-01")

        self.assertEqual(res["version"], "1.0")
        self.assertTrue(res["is_initial"])
        self.assertTrue(res["changed"])

        # Check outside pack directory
        json_file = self.versions_dir / "pack-init.json"
        md_file = self.versions_dir / "pack-init-CHANGELOG.md"
        self.assertTrue(json_file.is_file())
        self.assertTrue(md_file.is_file())

        data = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], "1.0")
        self.assertEqual(data["files_count"], 3)
        self.assertEqual(len(data["history"]), 1)

        md_content = md_file.read_text(encoding="utf-8")
        self.assertIn("# Changelog - pack-init", md_content)
        self.assertIn("## [1.0] - 2025-02-01", md_content)
        self.assertIn("Initial pack release snapshot", md_content)

    # 23. process_pack unchanged run preserves version and does not duplicate changelog entry
    def test_23_process_pack_unchanged(self):
        pdir = self._create_pack("pack-stay")
        pack_changelog.process_pack(pdir, versions_dir=self.versions_dir, snapshot_date="2025-02-01")

        # Second run without touching files
        res2 = pack_changelog.process_pack(pdir, versions_dir=self.versions_dir, snapshot_date="2025-02-02")
        self.assertEqual(res2["version"], "1.0")
        self.assertFalse(res2["changed"])

        json_file = self.versions_dir / "pack-stay.json"
        data = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], "1.0")
        # History should not grow
        self.assertEqual(len(data["history"]), 1)

        # CHANGELOG should only contain [1.0]
        md_file = self.versions_dir / "pack-stay-CHANGELOG.md"
        md_content = md_file.read_text(encoding="utf-8")
        self.assertEqual(md_content.count("## [1.0]"), 1)
        self.assertNotIn("## [1.1]", md_content)

    # 24. process_pack minor change updates version to 1.1 and adds CHANGELOG entry
    def test_24_process_pack_minor_update(self):
        pdir = self._create_pack("pack-minor")
        pack_changelog.process_pack(pdir, versions_dir=self.versions_dir, snapshot_date="2025-02-01")

        # Update cover.png
        (pdir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nNEW_COVER_DATA")
        res = pack_changelog.process_pack(pdir, versions_dir=self.versions_dir, snapshot_date="2025-02-03")

        self.assertEqual(res["version"], "1.1")
        self.assertTrue(res["changed"])
        self.assertIn("cover.png", res["diff"]["modified"])

        md_file = self.versions_dir / "pack-minor-CHANGELOG.md"
        md_content = md_file.read_text(encoding="utf-8")
        # 1.1 should be prepended before 1.0
        pos_1_1 = md_content.find("## [1.1]")
        pos_1_0 = md_content.find("## [1.0]")
        self.assertNotEqual(pos_1_1, -1)
        self.assertNotEqual(pos_1_0, -1)
        self.assertLess(pos_1_1, pos_1_0)
        self.assertIn("### Changed", md_content)
        self.assertIn("`cover.png`", md_content)

    # 25. process_pack product.zip update bumps major version to 2.0
    def test_25_process_pack_major_update(self):
        pdir = self._create_pack("pack-major")
        pack_changelog.process_pack(pdir, versions_dir=self.versions_dir, snapshot_date="2025-02-01")

        # Minor bump first
        (pdir / "listing.txt").write_text("Title: Update 1", encoding="utf-8")
        pack_changelog.process_pack(pdir, versions_dir=self.versions_dir, snapshot_date="2025-02-02")

        # Now product.zip changes
        (pdir / "product.zip").write_bytes(b"PK\x05\x06NEW_ZIP_BYTES")
        res3 = pack_changelog.process_pack(pdir, versions_dir=self.versions_dir, snapshot_date="2025-02-03")

        self.assertEqual(res3["version"], "2.0")
        self.assertTrue(res3["changed"])

        md_file = self.versions_dir / "pack-major-CHANGELOG.md"
        md_content = md_file.read_text(encoding="utf-8")
        self.assertIn("## [2.0] - 2025-02-03", md_content)
        self.assertIn("Main product.zip marindi", md_content)

    # 26. process_pack with dry_run=True does not write files
    def test_26_process_pack_dry_run(self):
        pdir = self._create_pack("pack-dry")
        res = pack_changelog.process_pack(pdir, versions_dir=self.versions_dir, dry_run=True)
        self.assertEqual(res["version"], "1.0")
        self.assertTrue(res["dry_run"])

        json_file = self.versions_dir / "pack-dry.json"
        md_file = self.versions_dir / "pack-dry-CHANGELOG.md"
        self.assertFalse(json_file.exists())
        self.assertFalse(md_file.exists())

    # 27. process_all_ready_packs processes multiple packs
    def test_27_process_all_ready_packs(self):
        self._create_pack("pack-1")
        self._create_pack("pack-2")
        results = pack_changelog.process_all_ready_packs(
            products_dir=self.products_dir,
            versions_dir=self.versions_dir,
            snapshot_date="2025-02-05",
        )
        self.assertEqual(len(results), 2)
        pack_names = {r["pack_name"] for r in results}
        self.assertEqual(pack_names, {"pack-1", "pack-2"})
        self.assertTrue((self.versions_dir / "pack-1.json").is_file())
        self.assertTrue((self.versions_dir / "pack-2.json").is_file())

    # 28. CLI --pack argument execution
    def test_28_cli_pack_argument(self):
        self._create_pack("cli-pack")
        argv = [
            "pack_changelog.py",
            "--pack", "cli-pack",
            "--products-dir", str(self.products_dir),
            "--versions-dir", str(self.versions_dir),
            "--date", "2025-02-06",
            "--json",
        ]
        stdout = io.StringIO()
        with mock.patch("sys.argv", argv), mock.patch("sys.stdout", stdout):
            ret = pack_changelog.main()

        self.assertEqual(ret, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["pack_name"], "cli-pack")
        self.assertEqual(output[0]["version"], "1.0")

    # 29. CLI --all and --dry-run argument execution
    def test_29_cli_all_dry_run(self):
        self._create_pack("dry-1")
        self._create_pack("dry-2")
        argv = [
            "pack_changelog.py",
            "--all",
            "--products-dir", str(self.products_dir),
            "--versions-dir", str(self.versions_dir),
            "--dry-run",
        ]
        stdout = io.StringIO()
        with mock.patch("sys.argv", argv), mock.patch("sys.stdout", stdout):
            ret = pack_changelog.main()

        self.assertEqual(ret, 0)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertFalse((self.versions_dir / "dry-1.json").exists())

    # 30. CLI error when pack not found
    def test_30_cli_pack_not_found(self):
        argv = [
            "pack_changelog.py",
            "--pack", "non-existent-pack",
            "--products-dir", str(self.products_dir),
            "--versions-dir", str(self.versions_dir),
        ]
        stderr = io.StringIO()
        with mock.patch("sys.argv", argv), mock.patch("sys.stderr", stderr):
            ret = pack_changelog.main()

        self.assertEqual(ret, 1)
        self.assertIn("Pack not found", stderr.getvalue())

    # 31. Strict currency check: verify outputs and code have no currency amounts
    def test_31_no_money_amounts_in_outputs(self):
        pdir = self._create_pack("pack-money-check")
        res = pack_changelog.process_pack(pdir, versions_dir=self.versions_dir)
        self.assertIsNone(pack_changelog.check_for_money(res["tenglish_summary"]))

        json_path = Path(res["json_path"])
        changelog_path = Path(res["changelog_path"])

        import tests.test_r22_markdown_currency as mc
        # Check changelog text against r22 test's currency regex
        match_md = mc.CURRENCY_AMOUNT.search(changelog_path.read_text(encoding="utf-8"))
        self.assertIsNone(match_md)

        match_json = mc.CURRENCY_AMOUNT.search(json_path.read_text(encoding="utf-8"))
        self.assertIsNone(match_json)

    # 32. Files are written strictly outside pack folder
    def test_32_files_outside_pack_folder(self):
        pdir = self._create_pack("pack-clean")
        initial_files = set(os.listdir(pdir))

        pack_changelog.process_pack(pdir, versions_dir=self.versions_dir)

        # Confirm pack directory content was NOT modified
        current_files = set(os.listdir(pdir))
        self.assertEqual(initial_files, current_files)

        # Confirm files were written to versions_dir
        self.assertTrue((self.versions_dir / "pack-clean.json").is_file())
        self.assertTrue((self.versions_dir / "pack-clean-CHANGELOG.md").is_file())


if __name__ == "__main__":
    unittest.main()
