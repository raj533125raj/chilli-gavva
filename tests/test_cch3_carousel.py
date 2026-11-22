"""CCH-3: Instagram output is ready to post for every SELLABLE pack.

One carousel of 5-8 slides at 1080 x 1350 in the pack theme, caption <= 2,200 chars with <= 30 hashtags and an
alt text per slide; the checker (same style as the Pin checker) fails on a wrong size, empty or long text, too
many hashtags and text running off the slide. marketing_auto makes it only for SELLABLE packs.
"""
import datetime
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

import carousel_maker

NOW = datetime.datetime(2026, 11, 20, 9, 0, tzinfo=datetime.timezone.utc)
LISTING = """Title: Calm Weekly Planner Printable

Description:
A calm weekly planner that keeps your week on one page. It has a priorities box, a habit row and space for notes.

Tags: weekly planner, printable, routine, habit tracker, Canva, USD planner

What you get:
LICENSE.txt - plain text
calm-weekly-A4.pdf - PDF, page count: 4, A4
calm-weekly-Letter.pdf - PDF, page count: 4, US Letter
"""


def make_pack(root: Path, listing: str = LISTING, previews: int = 3) -> Path:
    pack = root / "products" / "upload" / "9001-calm-weekly"
    pack.mkdir(parents=True)
    (pack / "listing.txt").write_text(listing, encoding="utf-8")
    Image.new("RGB", (1200, 1600), (40, 90, 150)).save(pack / "cover.png")
    for i in range(1, previews + 1):
        page = Image.new("RGB", (1600, 1200), (250, 250, 245))
        page.paste(Image.new("RGB", (300 + 100 * i, 200), (40, 90, 150)), (100, 100))
        page.save(pack / f"preview-{i}.png")
    return pack


@unittest.skipUnless(HAS_PIL, "the carousel needs Pillow")
class MakeCarouselTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make(self, **kw):
        pack = make_pack(self.root, **kw)
        out = self.root / "marketing" / pack.name / "carousel"
        return carousel_maker.make_ready_carousel(pack, out, now=NOW), out

    def test_ready_carousel(self):
        data, out = self.make()
        self.assertEqual(data["check"], {"ok": True, "problems": []})
        slides = data["slides"]
        self.assertTrue(5 <= len(slides) <= 8, len(slides))
        self.assertEqual([s["design"] for s in slides],
                         ["cover", "inside", "page", "page", "page", "steps", "quote", "cta"])
        for s in slides:
            with Image.open(out / s["file"]) as im:
                self.assertEqual(im.size, (1080, 1350))
            self.assertTrue(0 < len(s["alt"]) <= 100, s["alt"])
        caption = (out / "caption.txt").read_text().strip()
        self.assertEqual(caption, data["caption"])
        self.assertLessEqual(len(caption), 2200)
        tags = [w for w in caption.split() if w.startswith("#")]
        self.assertTrue(0 < len(tags) <= 30)
        self.assertIn("#weeklyplanner", tags)
        self.assertNotIn("#canva", tags)
        self.assertNotIn("#usdplanner", tags)
        self.assertNotRegex(caption.lower(), r"[$₹€£]|\busd\b|canva")
        self.assertIn("Calm weekly A4 (PDF, 4 pages)", caption)
        self.assertIn("Link in bio", caption)
        self.assertEqual(len((out / "alt.txt").read_text().strip().splitlines()), len(slides))
        saved = json.loads((out / "carousel.json").read_text())
        self.assertEqual(saved["made_at"], "2026-11-20T09:00:00")
        self.assertTrue(saved["check"]["ok"])

    def test_slide_count_follows_the_real_pages(self):
        data, _ = self.make(previews=1)
        self.assertEqual(len(data["slides"]), 6)            # one real preview page: no repeated pages
        self.assertTrue(data["check"]["ok"], data["check"])

    def test_same_preview_twice_is_shown_once(self):
        pack = make_pack(self.root, previews=2)
        (pack / "preview-3.png").write_bytes((pack / "preview-1.png").read_bytes())
        data = carousel_maker.make_ready_carousel(pack, self.root / "o" / "carousel", now=NOW)
        self.assertEqual(sum(1 for s in data["slides"] if s["design"] == "page"), 2)

    def test_long_description_keeps_caption_under_limit(self):
        long_desc = " ".join(["This planner helps you plan a calm week with clear boxes."] * 60)
        listing = LISTING.replace("A calm weekly planner that keeps your week on one page.", long_desc)
        tags = ", ".join(f"topic{i} words" for i in range(60))
        listing = listing.replace("weekly planner, printable, routine, habit tracker, Canva, USD planner", tags)
        data, _ = self.make(listing=listing)
        self.assertTrue(data["check"]["ok"], data["check"])
        self.assertLessEqual(len(data["caption"]), 2200)
        self.assertLessEqual(len([w for w in data["caption"].split() if w.startswith("#")]), 30)

    def test_theme_comes_from_the_pack_cover(self):
        data, out = self.make()
        with Image.open(out / "slide-1.png") as im:
            band = im.convert("RGB").getpixel((20, 20))
        self.assertEqual("#%02x%02x%02x" % band, data["theme"]["accent"])


@unittest.skipUnless(HAS_PIL, "the checker opens the images")
class CheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.good = root / "good"
        carousel_maker.make_ready_carousel(make_pack(root), cls.good, now=NOW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def broken(self, change):
        folder = Path(tempfile.mkdtemp(dir=self.tmp.name))
        for p in self.good.iterdir():
            (folder / p.name).write_bytes(p.read_bytes())
        data = json.loads((folder / "carousel.json").read_text())
        change(data, folder)
        (folder / "carousel.json").write_text(json.dumps(data))
        return carousel_maker.check_ready_carousel(folder)

    def test_good_passes(self):
        self.assertEqual(carousel_maker.check_ready_carousel(self.good), [])

    def test_wrong_size(self):
        def change(data, folder):
            Image.new("RGB", (1080, 1080), (255, 255, 255)).save(folder / "slide-2.png")
        self.assertIn("Slide 2: Image 1080x1350 ga tayaru cheyandi.", self.broken(change))

    def test_slide_count(self):
        self.assertEqual(self.broken(lambda d, f: d.update(slides=d["slides"][:4])), ["Carousel lo 5-8 slides tayaru cheyandi."])
        self.assertEqual(self.broken(lambda d, f: d.update(slides=d["slides"] + d["slides"][:1])),
                         ["Carousel lo 5-8 slides tayaru cheyandi."])

    def test_empty_and_long_texts(self):
        def change(data, folder):
            data["slides"][0]["alt"] = ""
            data["slides"][1]["alt"] = "word " * 120
            data["caption"] = ""
        problems = self.broken(change)
        self.assertIn("Slide 1: Alt text 1-500 characters lo rayandi.", problems)
        self.assertTrue(any(p.startswith("Slide 2: Alt text 1-500 characters") for p in problems), problems)
        self.assertIn("Caption 1-2200 characters lo rayandi.", problems)

    def test_caption_too_long_and_too_many_hashtags(self):
        def change(data, folder):
            data["caption"] = "words " * 400 +" ".join(f"#tag{i}" for i in range(31))
            (folder / "caption.txt").write_text(data["caption"])
        problems = self.broken(change)
        self.assertTrue(any(p == "Caption 1-2200 characters lo rayandi." for p in problems), problems)
        self.assertIn("Caption lo 20 kanna ekkuva hashtags unnayi; 20 lopala pettandi.", problems)

    def test_caption_file_must_match(self):
        def change(data, folder):
            (folder / "caption.txt").write_text("something else")
        self.assertIn("caption.txt lo check chesina ade Caption save cheyandi.", self.broken(change))

    def test_text_running_off_the_slide(self):
        def change(data, folder):
            data["slides"][2]["layout"][0]["y"] = 1330
        problems = self.broken(change)
        self.assertTrue(any(p.startswith("Slide 3: text runs off the image") for p in problems), problems)

    def test_money_brand_and_missing_alt_lines(self):
        def change(data, folder):
            data["caption"] += " Only USD 5 on Canva"
            (folder / "caption.txt").write_text(data["caption"])
            (folder / "alt.txt").write_text("Slide 1: x\n")
        problems = self.broken(change)
        self.assertIn("Caption lo brand/money text teeseyandi.", problems)
        self.assertIn("Caption lo brand/money text teeseyandi.", problems)
        self.assertTrue(any(p.startswith("alt.txt lo prathi Slide") for p in problems), problems)


@unittest.skipUnless(HAS_PIL, "the carousel needs Pillow")
class MarketingAutoTests(unittest.TestCase):
    def setUp(self):
        import marketing_auto
        self.ma = marketing_auto
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pack = make_pack(self.root)
        self.products = self.root / "products"
        from tests.listing_fixtures import save_store_link
        save_store_link(self.pack, self.root / "db.sqlite")
        p = mock.patch.object(marketing_auto, "is_private_dir", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def build(self, verdict):
        with mock.patch.object(self.ma, "sellable", return_value=verdict):
            return self.ma.build_for_pack(self.pack, str(self.root / "db.sqlite"), products_dir=self.products, now=NOW)

    def test_sellable_pack_gets_carousel_and_pins(self):
        result = self.build((True, "SELLABLE 90"))
        self.assertEqual(result["social"]["carousel"]["status"], "ok", result["social"])
        self.assertEqual(result["social"]["pinterest"]["status"], "ok", result["social"])
        out = self.products / "marketing" / self.pack.name
        self.assertEqual(carousel_maker.check_ready_carousel(out / "carousel"), [])
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertIn("carousel/slide-1.png", manifest["files"])
        self.assertIn("carousel/caption.txt", manifest["files"])

    def test_not_sellable_pack_gets_no_carousel(self):
        result = self.build((False, "NOT YET 40"))
        self.assertEqual(result["social"]["carousel"]["status"], "skipped")
        self.assertFalse((self.products / "marketing" / self.pack.name / "carousel").exists())


@unittest.skipUnless(HAS_PIL, "the sell proof needs Pillow")
class SellProofTests(unittest.TestCase):
    """The robot's real path: a SELLABLE pack gets a ready carousel, /sell credits it, /teams counts it."""

    def test_sellable_pack_gets_ready_carousel(self):
        import sell_proof
        import sell_ready
        import teams_report
        with tempfile.TemporaryDirectory() as tmp:
            row = sell_proof.prove("wall_art", Path(tmp))
            self.assertEqual(row["status"], "SELLABLE", row)
            products = Path(tmp) / "wall_art" / "products"
            name = Path(row["pack"]).name
            self.assertEqual(carousel_maker.check_ready_carousel(products / "marketing" / name / "carousel"), [])
            evaluation = sell_ready.evaluate_pack(Path(row["pack"]), products / "marketing")
            self.assertTrue(evaluation["checks"]["marketing"]["has_carousel"])
            found = teams_report.scan_marketing(products)[name]
            self.assertEqual(found["carousel"]["count"], 1)
            from tests.listing_fixtures import save_store_link
            import marketing_auto
            db = Path(tmp) / "wall_art" / "proof.db"
            save_store_link(Path(row["pack"]), db)
            marketing_auto.build_for_pack(Path(row["pack"]), str(db), products_dir=products)
            found = teams_report.scan_marketing(products)[name]
            self.assertEqual(found["pins"]["count"], 5)


if __name__ == "__main__":
    unittest.main()
