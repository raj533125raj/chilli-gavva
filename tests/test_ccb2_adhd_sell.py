"""CCB-2: the three ADHD planner editions sell like every other format: truth check 100, /sell SELLABLE,
upload kit, Pins + carousel, buyer check score shown, visual QA 0 flags (when poppler is installed)."""
import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from tools import sell_proof  # noqa: E402

EDITIONS = {"energy": "Energy-Based Daily Planner Printable",
            "time_blindness": "Time-Blindness Routine Planner Printable",
            "focus": "Focus and Brain-Dump Sheets Printable"}


def edition_spec(edition):
    spec = copy.deepcopy(sell_proof.CUSTOM_SPECS["adhd_planner"])
    spec.update(edition=edition, name=EDITIONS[edition], title=EDITIONS[edition],
                short_name=edition.replace("_", "-") + "-planner")
    spec["listing"]["title"] = EDITIONS[edition]
    return spec


@unittest.skipUnless(Image is not None, "needs Pillow")
class AdhdSellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.rows = {e: sell_proof.prove("adhd_planner", cls.root, edition_spec(e), e) for e in EDITIONS}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_edition_is_sellable_with_its_own_pages(self):
        import json
        for edition, row in self.rows.items():
            with self.subTest(edition):
                self.assertEqual((row["status"], row["truth"], row["zip"], row["sell_message"]),
                                 ("SELLABLE", "ready 100", "ok", True), row)
                folder = Path(row["pack"]).parent.parent / Path(row["pack"]).name
                manifest = json.loads((folder / "manifest.json").read_text())
                self.assertEqual(manifest["edition"], edition)
                self.assertGreaterEqual(manifest["pages_per_pdf"], 24)

    def test_buyer_check_score_and_pins_carousel(self):
        import buyer_check
        import carousel_maker
        import marketing_auto
        import selltest_listings
        import teams_report
        from tests.listing_fixtures import save_store_link
        scores = {}
        for edition, row in self.rows.items():
            with self.subTest(edition):
                pack = Path(row["pack"])
                copy_ = selltest_listings.make(pack, "adhd_planner")
                copy_["B"] = buyer_check.TITLES["adhd_planner"]
                listing = selltest_listings.render(copy_, "B")
                image, page = buyer_check.inside_image(pack)
                buffer = io.BytesIO()
                image.save(buffer, "PNG")
                first = self.root / edition / "gallery-first.png"
                first.write_bytes(buffer.getvalue())
                result = buyer_check.evaluate(pack, "adhd_planner", listing, first, buffer.getvalue())
                scores[edition] = result["score"]
                self.assertEqual([k for k, ok in result["checks"].items() if not ok], ["sample"], result)
                products = self.root / edition / "products"
                db = self.root / edition / "proof.db"
                save_store_link(pack, db)
                marketing_auto.build_for_pack(pack, str(db), products_dir=products)
                self.assertEqual(carousel_maker.check_ready_carousel(products / "marketing" / pack.name / "carousel"), [])
                self.assertEqual(teams_report.scan_marketing(products)[pack.name]["pins"]["count"], 5)
        self.assertEqual(set(scores.values()), {83})
        print("\nBuyer check scores (free-sample link pending owner /listed):", scores)

    @unittest.skipUnless(__import__("visual_qa").available(), "needs poppler (pdftoppm, pdftotext)")
    def test_visual_qa_zero_flags(self):
        import visual_qa
        for edition, row in self.rows.items():
            with self.subTest(edition):
                folder = Path(row["pack"]).parent.parent / Path(row["pack"]).name
                report = visual_qa.check_pack(folder, self.root / "qa" / edition, "adhd_planner")
                self.assertEqual(report["flags"], [])


if __name__ == "__main__":
    unittest.main()
