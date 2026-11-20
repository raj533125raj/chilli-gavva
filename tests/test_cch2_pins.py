"""CCH-2: Pinterest output is ready to post for every SELLABLE pack.

5 Pins of 1000 x 1500 in the pack theme (no logos, no money), title <= 100, description <= 500, board name and a
link placeholder; the checker fails on a wrong size, empty text or text running off the image. marketing_auto
makes them only for SELLABLE packs (the /sell judge) and keeps them in the private marketing folder.
"""
import copy
import datetime
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import pin_ready

NOW = datetime.datetime(2026, 11, 20, 9, 0, tzinfo=datetime.timezone.utc)
LISTING = """Title: Calm Weekly Planner Printable

Description:
A calm weekly planner that keeps your week on one page. It has a priorities box, a habit row and space for notes.
Print it as often as you need for your own routine.

Tags: weekly planner, printable, routine, habit tracker

What you get:
LICENSE.txt - plain text
README.md - Markdown
calm-weekly-A4.pdf - PDF, page count: 4, A4
calm-weekly-Letter.pdf - PDF, page count: 4, US Letter
"""


def make_pack(root: Path, listing: str = LISTING, colour=(150, 70, 60)) -> Path:
    pack = root / "products" / "upload" / "9001-calm-weekly"
    pack.mkdir(parents=True)
    (pack / "listing.txt").write_text(listing, encoding="utf-8")
    Image.new("RGB", (1200, 1600), colour).save(pack / "cover.png")
    page = Image.new("RGB", (1200, 1600), (250, 250, 245))
    page.paste(Image.new("RGB", (900, 200), colour), (150, 150))
    page.save(pack / "preview-1.png")
    return pack


def tree_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


@unittest.skipUnless(HAS_PIL, "Pins need Pillow")
class MakePinsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pack = make_pack(self.root)
        self.out = self.root / "marketing" / "9001-calm-weekly" / "pinterest"

    def test_five_ready_pins(self):
        before = tree_hash(self.pack)
        data = pin_ready.make_pins(self.pack, self.out, now=NOW)
        self.assertEqual(data["check"], {"ok": True, "problems": []})
        self.assertEqual(tree_hash(self.pack), before)          # the pack is only read
        self.assertEqual(sorted(p.name for p in self.out.glob("*.png")), [f"pin-{i}.png" for i in range(1, 6)])
        saved = json.loads((self.out / "pins.json").read_text())
        self.assertEqual(saved["made_at"], "2026-11-20T09:00:00")
        self.assertEqual(len(saved["pins"]), 5)
        designs = set()
        for pin in saved["pins"]:
            with Image.open(self.out / pin["file"]) as im:
                self.assertEqual(im.size, (1000, 1500))
            self.assertTrue(0 < len(pin["title"]) <= 100, pin["title"])
            self.assertTrue(0 < len(pin["description"]) <= 500, pin["description"])
            self.assertEqual(pin["link"], "[STORE LINK]")
            self.assertTrue(pin["board"])
            self.assertTrue(pin["alt"])
            self.assertNotRegex(json.dumps(pin), r"[$₹€£]")
            designs.add(pin["design"])
        self.assertEqual(len(designs), 5)
        self.assertEqual(saved["pins"][0]["board"], "Weekly Overview Planners & Schedule Sheets")
        self.assertIn("Calm Weekly Planner Printable", (self.out / "PINS.txt").read_text())
        self.assertIn("[STORE LINK]", (self.out / "PINS.txt").read_text())
        self.assertIn("Calm weekly A4 (PDF, 4 pages)", saved["pins"][1]["description"])

    def test_pins_use_the_pack_theme(self):
        data = pin_ready.make_pins(self.pack, self.out, now=NOW)
        accent = pin_ready.rgb_of(data["theme"]["accent"])
        self.assertLess(sum(abs(a - b) for a, b in zip(accent, (150, 70, 60))), 60)   # colour from the cover
        with Image.open(self.out / "pin-1.png") as im:
            band = im.convert("RGB").getpixel((20, 20))
        self.assertEqual(band, accent)

    def test_long_title_and_long_word_still_fit(self):
        long_title = ("Ultra Organised Household Routine And Weekly Cleaning Planner Printable Pack With "
                      "Supercalifragilisticexpialidociousroutinechecklistsheets Inside For Busy Homes")
        pack = make_pack(self.root / "b", LISTING.replace("Calm Weekly Planner Printable", long_title))
        data = pin_ready.make_pins(pack, self.root / "b" / "pinterest", now=NOW)
        self.assertEqual(data["check"]["problems"], [])
        self.assertTrue(all(len(p["title"]) <= 100 for p in data["pins"]))

    def test_brand_and_money_words_are_removed(self):
        listing = LISTING.replace("Calm Weekly Planner Printable", "Calm Weekly Notion Planner for Canva USD 5")
        pack = make_pack(self.root / "c", listing)
        data = pin_ready.make_pins(pack, self.root / "c" / "pinterest", now=NOW)
        self.assertEqual(data["check"]["problems"], [])
        text = json.dumps(data["pins"]).lower()
        for word in ("notion", "canva", "usd"):
            self.assertNotIn(word, text)

    def test_folder_is_replaced_not_mixed(self):
        self.out.mkdir(parents=True)
        (self.out / "old-pin.png").write_bytes(b"x")
        pin_ready.make_pins(self.pack, self.out, now=NOW)
        self.assertFalse((self.out / "old-pin.png").exists())
        self.assertFalse([p for p in self.out.parent.iterdir() if p.name.startswith(".pins")])


@unittest.skipUnless(HAS_PIL, "the checker opens the images")
class CheckerTests(unittest.TestCase):
    """The checker fails on wrong size, empty text, too long text, text off the image and more."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.good = root / "good"
        pin_ready.make_pins(make_pack(root), cls.good, now=NOW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def broken(self, change):
        folder = Path(tempfile.mkdtemp(dir=self.tmp.name))
        for p in self.good.iterdir():
            (folder / p.name).write_bytes(p.read_bytes())
        data = json.loads((folder / "pins.json").read_text())
        change(data, folder)
        (folder / "pins.json").write_text(json.dumps(data))
        return pin_ready.check_pins(folder)

    def test_good_pins_pass(self):
        self.assertEqual(pin_ready.check_pins(self.good), [])

    def test_wrong_size(self):
        def change(data, folder):
            Image.new("RGB", (1000, 1000), (255, 255, 255)).save(folder / "pin-2.png")
        self.assertIn("Pin 2: size 1000x1000 (need 1000x1500)", self.broken(change))

    def test_empty_texts(self):
        def change(data, folder):
            data["pins"][0]["title"] = " "
            data["pins"][1]["description"] = ""
            data["pins"][2]["board"] = ""
        problems = self.broken(change)
        for want in ("Pin 1 title: empty", "Pin 2 description: empty", "Pin 3 board: empty"):
            self.assertIn(want, problems)

    def test_too_long_texts(self):
        def change(data, folder):
            data["pins"][0]["title"] = "word " * 30
            data["pins"][1]["description"] = "word " * 120
        problems = self.broken(change)
        self.assertTrue(any(p.startswith("Pin 1 title: 150 chars") for p in problems), problems)
        self.assertTrue(any(p.startswith("Pin 2 description: 600 chars") for p in problems), problems)

    def test_text_running_off_the_image(self):
        def change(data, folder):
            block = data["pins"][0]["layout"][0]
            block["x"] = 900                                  # the title now starts near the right edge
        problems = self.broken(change)
        self.assertTrue(any("text runs off the image" in p for p in problems), problems)

    def test_line_too_wide_for_its_box(self):
        def change(data, folder):
            block = data["pins"][1]["layout"][0]
            block["lines"] = [" ".join(block["lines"])]       # all words on one line
            block["align"] = "left"
        problems = self.broken(change)
        self.assertTrue(any("line wider than its box" in p or "runs off" in p for p in problems), problems)

    def test_words_missing_from_the_image(self):
        def change(data, folder):
            data["pins"][3]["layout"][0]["lines"] = data["pins"][3]["layout"][0]["lines"][:1]
        problems = self.broken(change)
        self.assertTrue(any("words missing" in p for p in problems), problems)

    def test_missing_link_money_brand_contrast_file_count(self):
        def change(data, folder):
            data["pins"][0]["link"] = ""
            data["pins"][1]["description"] += " Only USD 5 today."
            data["pins"][2]["title"] = "Planner for Canva"
            data["pins"][3]["layout"][0]["fill"] = data["pins"][3]["layout"][0]["bg"]
            (folder / "pin-5.png").unlink()
        problems = self.broken(change)
        for want in ("Pin 1: link placeholder missing", "Pin 2 description: money words", "Pin 3 title: brand name",
                     "Pin 5: image file missing"):
            self.assertIn(want, problems)
        self.assertTrue(any("text colour too weak" in p for p in problems), problems)
        self.assertEqual(self.broken(lambda d, f: d["pins"].pop()), ["need 5 Pins, found 4"])

    def test_missing_json(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(pin_ready.check_pins(empty), ["pins.json missing or broken"])


@unittest.skipUnless(HAS_PIL, "Pins need Pillow")
class MarketingAutoTests(unittest.TestCase):
    """marketing_auto makes the Pins only for SELLABLE packs, lists them in the manifest, reruns cheaply."""

    def setUp(self):
        import marketing_auto
        self.ma = marketing_auto
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pack = make_pack(self.root)
        self.products = self.root / "products"
        self.db = str(self.root / "agent_data.db")
        p = mock.patch.object(marketing_auto, "is_private_dir", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def build(self, verdict=(True, "SELLABLE 90")):
        with mock.patch.object(self.ma, "sellable", return_value=verdict) as judge:
            result = self.ma.build_for_pack(self.pack, self.db, products_dir=self.products, now=NOW)
        return result, judge.call_count

    def test_sellable_pack_gets_ready_pins_in_manifest(self):
        result, calls = self.build()
        self.assertEqual(calls, 1)
        self.assertEqual(result["social"]["pinterest"]["status"], "ok", result["social"])
        out = self.products / "marketing" / self.pack.name
        self.assertEqual(pin_ready.check_pins(out / "pinterest"), [])
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertEqual(manifest["social"]["pinterest"]["status"], "ok")
        for i in range(1, 6):
            self.assertIn(f"pinterest/pin-{i}.png", manifest["files"])
        self.assertEqual(sorted(manifest["files"]),
                         sorted(p.relative_to(out).as_posix() for p in out.rglob("*")
                                if p.is_file() and p.name != "manifest.json"))
        # a second run: nothing judged, nothing written
        stamp = (out / "manifest.json").read_text()
        result, calls = self.build()
        self.assertEqual(calls, 0)
        self.assertFalse(result["social"]["pinterest"]["built"])
        self.assertEqual((out / "manifest.json").read_text(), stamp)
        # a damaged Pin is noticed by the checker and made again
        (out / "pinterest" / "pin-3.png").unlink()
        result, calls = self.build()
        self.assertTrue(result["social"]["pinterest"]["built"])
        self.assertTrue((out / "pinterest" / "pin-3.png").is_file())

    def test_not_sellable_pack_gets_no_pins_and_is_judged_again_only_after_a_change(self):
        result, calls = self.build((False, "ALMOST 70"))
        self.assertEqual(result["social"]["pinterest"]["status"], "skipped")
        self.assertIn("ALMOST 70", result["social"]["pinterest"]["reason"])
        out = self.products / "marketing" / self.pack.name
        self.assertFalse((out / "pinterest").exists())
        self.assertFalse(self.ma.social_due(self.pack, out, self.products, "2026-11-20"))
        _, calls = self.build((False, "ALMOST 70"))
        self.assertEqual(calls, 0)                       # nothing changed: not judged again
        (self.pack / "checklist.txt").write_text("fixed")   # the pack changed
        self.assertTrue(self.ma.social_due(self.pack, out, self.products, "2026-11-20"))
        result, calls = self.build()
        self.assertEqual(calls, 1)
        self.assertEqual(result["social"]["pinterest"]["status"], "ok")

    def test_real_judge_says_not_sellable_without_upload_files(self):
        (self.pack / "listing.txt").rename(self.pack / "listing.md.txt")
        ok, why = self.ma.sellable(self.pack, self.products / "marketing", self.products)
        self.assertFalse(ok)
        self.assertIn("no upload pack", why)


@unittest.skipUnless(HAS_PIL, "the sell proof needs Pillow")
class SellProofTests(unittest.TestCase):
    """The robot's real path (tools/sell_proof.py): a SELLABLE pack gets 5 ready Pins; /teams counts them."""

    def test_sellable_planner_gets_ready_pins(self):
        import sell_proof
        import teams_report
        with tempfile.TemporaryDirectory() as tmp:
            row = sell_proof.prove("planner", Path(tmp))
            self.assertEqual(row["status"], "SELLABLE", row)
            products = Path(tmp) / "planner" / "products"
            folder = products / "marketing" / Path(row["pack"]).name / "pinterest"
            self.assertEqual(pin_ready.check_pins(folder), [])
            stats = teams_report.scan_marketing(products)
            self.assertEqual(stats[Path(row["pack"]).name]["pins"]["count"], 5)


if __name__ == "__main__":
    unittest.main()
