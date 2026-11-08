"""Tests for make_marketing.py: driving both makers for ready packs, with fakes."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_marketing


class _StubQA:
    def __init__(self, verdict="ready"):
        self.verdict = verdict

    def check_folder(self, folder):
        return {"verdict": self.verdict, "score": 90, "problems": [], "checked": []}


class FakeMakers:
    """Maker fakes mirroring the real modules' path contracts."""

    @staticmethod
    def fake_carousel(pack_dir, marketing_dir=None):
        target = Path(marketing_dir) / pack_dir.name / "carousel"
        target.mkdir(parents=True, exist_ok=True)
        (target / "slide-1.png").write_bytes(b"png")
        (target / "caption.txt").write_text("caption with no money", encoding="utf-8")
        return target

    @staticmethod
    def fake_pins(pack_dir, out_dir=None):
        target = Path(out_dir) / "marketing" / pack_dir.name / "pins"
        target.mkdir(parents=True, exist_ok=True)
        (target / "pin-a.png").write_bytes(b"png")
        return target


def fake_makers():
    return (types.SimpleNamespace(make_pack_carousel=FakeMakers.fake_carousel),
            types.SimpleNamespace(generate_pins_for_pack=FakeMakers.fake_pins))


class MakeMarketingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packs_root = self.root / "exported_content" / "products" / "upload"
        self.marketing_root = self.root / "exported_content" / "marketing"
        self.packs_root.mkdir(parents=True)
        self.qa = patch.object(make_marketing, "_qa_deep", _StubQA("ready"))
        self.qa.start()
        self.addCleanup(self.qa.stop)

    def ready_pack(self, name="pack-a"):
        import zipfile
        target = self.packs_root / name
        target.mkdir(exist_ok=True)
        with zipfile.ZipFile(target / "product.zip", "w") as archive:
            archive.writestr("LICENSE.txt", "personal use licence")
        (target / "listing.txt").write_text(
            "Title: Fake Maker Pack $9\n\nDescription:\nx\n\nTags: t\n", encoding="utf-8")
        return target

    def before_snapshot(self, pack):
        return sorted(p.name for p in pack.iterdir())

    def test_process_pack_runs_both_makers_and_collects_files(self):
        pack = self.ready_pack()
        result = make_marketing.process_pack(pack, self.marketing_root, fake_makers())
        self.assertTrue(result["ok"])
        self.assertIsNotNone(Path(result["carousel"]) / "slide-1.png")
        self.assertEqual(result["files"],
                         ["carousel/caption.txt", "carousel/slide-1.png", "pins/pin-a.png"])

    def test_readme_written_outside_pack_with_file_names_only(self):
        pack = self.ready_pack()
        result = make_marketing.process_pack(pack, self.marketing_root, fake_makers())
        readme = make_marketing.write_readme(self.marketing_root, result["id"], result)
        text = readme.read_text(encoding="utf-8")
        self.assertEqual(readme, self.marketing_root / "pack-a" / "README.txt")
        self.assertIn("carousel/slide-1.png", text)
        self.assertIn("pins/pin-a.png", text)
        self.assertNotIn("MB", text)
        self.assertNotIn("$", text)

    def test_readme_never_touches_the_pack_folder(self):
        pack = self.ready_pack()
        before = self.before_snapshot(pack)
        result = make_marketing.process_pack(pack, self.marketing_root, fake_makers())
        make_marketing.write_readme(self.marketing_root, result["id"], result)
        self.assertEqual(before, self.before_snapshot(pack))

    def test_one_failing_maker_continues_and_is_reported(self):
        def broken_pins(pack_dir, out_dir=None):
            raise RuntimeError("pins exploded")

        makers = (fake_makers()[0],
                  types.SimpleNamespace(generate_pins_for_pack=broken_pins))
        pack = self.ready_pack()
        result = make_marketing.process_pack(pack, self.marketing_root, makers)
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["carousel"])
        self.assertTrue(any(e.startswith("pins: pins exploded") for e in result["errors"]))
        text = make_marketing.write_readme(self.marketing_root, "pack-a", result).read_text(encoding="utf-8")
        self.assertIn("pins: pins exploded", text)
        self.assertIn("carousel/slide-1.png", text)

    def test_money_in_maker_error_is_washed(self):
        def crying_pins(pack_dir, out_dir=None):
            raise RuntimeError("cost $12.50 to fail")

        makers = (fake_makers()[0],
                  types.SimpleNamespace(generate_pins_for_pack=crying_pins))
        result = make_marketing.process_pack(self.ready_pack(), self.marketing_root, makers)
        self.assertFalse(result["ok"])
        self.assertTrue(any("$" not in e and "[amount]" in e for e in result["errors"]))

    def test_select_ready_packs_ready_rule(self):
        self.ready_pack("good-pack")
        bad = self.ready_pack("loose-pack")
        (bad / "listing.txt").unlink()
        packs = make_marketing.select_ready_packs(self.packs_root, self.marketing_root)
        self.assertEqual([p.name for p in packs], ["good-pack"])

    def test_select_ready_packs_qa_fix_is_skipped(self):
        self.ready_pack("paint-pack")
        with patch.object(make_marketing, "_qa_deep", _StubQA("fix")):
            self.assertEqual(
                make_marketing.select_ready_packs(self.packs_root), [])

    def test_run_all_processes_ready_packs_sorted(self):
        self.ready_pack("z-pack")
        self.ready_pack("a-pack")
        results, readmes, skipped = make_marketing.run(
            all_packs=True, packs_dir=self.packs_root,
            marketing_dir=self.marketing_root, makers=fake_makers())
        self.assertEqual([r["id"] for r in results], ["a-pack", "z-pack"])
        self.assertEqual(len(readmes), 2)
        self.assertEqual(skipped, [])
        for readme in readmes:
            self.assertTrue(readme.is_file())

    def test_run_skips_not_ready_but_reports(self):
        self.ready_pack("ok-pack")
        bad = self.packs_root / "bad-pack"
        bad.mkdir()
        results, _readmes, skipped = make_marketing.run(
            all_packs=True, packs_dir=self.packs_root,
            marketing_dir=self.marketing_root, makers=fake_makers())
        self.assertEqual([r["id"] for r in results], ["ok-pack"])
        self.assertEqual(len(skipped), 1)
        self.assertIn("bad-pack: not ready", skipped[0])

    def test_run_explicit_pack_when_not_ready_reports_without_making(self):
        self.packs_root / "not-ready-pack"
        (self.packs_root / "not-ready-pack").mkdir()
        results, _readmes, skipped = make_marketing.run(
            pack_id="not-ready-pack", packs_dir=self.packs_root,
            marketing_dir=self.marketing_root, makers=fake_makers())
        self.assertEqual(results, [])
        self.assertEqual(len(skipped), 1)

    def test_run_missing_pack_id_reports(self):
        _results, _readmes, skipped = make_marketing.run(
            pack_id="ghost-pack", packs_dir=self.packs_root,
            marketing_dir=self.marketing_root, makers=fake_makers())
        self.assertEqual(len(skipped), 1)
        self.assertIn("ghost-pack", skipped[0])

    def test_missing_modules_reported_and_flow_continues(self):
        pack = self.ready_pack()
        makers = (None, None)
        result = make_marketing.process_pack(pack, self.marketing_root, makers)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["errors"]), 2)
        readme = make_marketing.write_readme(self.marketing_root, "pack-a", result)
        text = readme.read_text(encoding="utf-8")
        self.assertIn("carousel: module missing", text)
        self.assertIn("pins: module missing", text)

    def test_idempotent_rerun_same_readme(self):
        self.ready_pack()
        first = make_marketing.run(all_packs=True, packs_dir=self.packs_root,
                                   marketing_dir=self.marketing_root,
                                   makers=fake_makers())
        text_first = (self.marketing_root / "pack-a" / "README.txt").read_text(encoding="utf-8")
        second = make_marketing.run(all_packs=True, packs_dir=self.packs_root,
                                    marketing_dir=self.marketing_root,
                                    makers=fake_makers())
        text_second = (self.marketing_root / "pack-a" / "README.txt").read_text(encoding="utf-8")
        self.assertEqual(text_first, text_second)
        self.assertEqual(first[0][0]["files"], second[0][0]["files"])

    def test_table_lines_and_last_line(self):
        self.ready_pack("table-pack")
        results, _readmes, _skipped = make_marketing.run(
            all_packs=True, packs_dir=self.packs_root,
            marketing_dir=self.marketing_root, makers=fake_makers())
        lines = make_marketing.render_table(results, [])
        self.assertTrue(lines[-1].startswith("Marketing: 1 of 1 packs fully made"))
        self.assertIn("carousel ok", lines[0])
        self.assertIn("pins ok", lines[0])

    def test_cli_exit_zero_when_all_good_one_when_failures(self):
        import io
        from contextlib import redirect_stdout
        self.ready_pack("cli-good")
        makers = (fake_makers()[0], None)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = make_marketing.main(["--pack", "cli-good",
                                        "--packs-dir", str(self.packs_root),
                                        "--marketing-dir", str(self.marketing_root)],
                                       makers=makers)
        self.assertEqual(code, 1)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = make_marketing.main(["--pack", "cli-good",
                                        "--packs-dir", str(self.packs_root),
                                        "--marketing-dir", str(self.marketing_root)],
                                       makers=fake_makers())
        self.assertEqual(code, 0)
        self.assertIn("cli-good:", buffer.getvalue())

    def test_cli_all_flags_exit_one_on_skip_notes(self):
        import io
        from contextlib import redirect_stdout
        self.ready_pack("all-good")
        (self.packs_root / "all-loose").mkdir()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = make_marketing.main(["--all",
                                        "--packs-dir", str(self.packs_root),
                                        "--marketing-dir", str(self.marketing_root)],
                                       makers=fake_makers())
        out = buffer.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("skip all-loose: not ready", out)
        self.assertTrue(any(line.startswith("skip ") for line in out.splitlines()))


if __name__ == "__main__":
    unittest.main()
