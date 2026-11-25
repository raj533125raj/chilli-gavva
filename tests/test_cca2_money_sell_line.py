"""CCA-2: the four money-sheet formats go through the whole sell line like every other format.

Real path in temp folders (no network, nothing posted): build -> truth check -> upload pack -> store kit +
Gumroad images -> /sell SELLABLE -> carousel + 5 Pins -> listing gate -> buyer check score -> free sample ->
visual QA 0 flags. The three-buyer sweep (tests.test_och_niche_sweep) and the all-format sample / listing
tests pick the formats up from tools/sell_proof.FORMAT_ORDER.
"""
import datetime as dt
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

try:
    from PIL import Image
except ImportError:
    Image = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from tools import sell_proof  # noqa: E402

KINDS = ["debt_payoff", "couples_budget", "freelance_budget", "sinking_funds"]
NOW = dt.datetime(2027, 1, 4, 12, 0, tzinfo=dt.timezone.utc)


@unittest.skipUnless(Image is not None, "needs Pillow")
class MoneySellLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.rows = {kind: sell_proof.prove(kind, cls.root) for kind in KINDS}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def products(self, kind):
        return self.root / kind / "products"

    def test_registered_in_the_sell_proof_and_sellable(self):
        for kind in KINDS:
            with self.subTest(kind):
                self.assertIn(kind, sell_proof.FORMAT_ORDER)
                row = self.rows[kind]
                self.assertEqual(row["status"], "SELLABLE", row)
                self.assertEqual(row["truth"], "ready 100", row)
                self.assertEqual(row["zip"], "ok", row)
                self.assertTrue(row["sell_message"], row)

    def test_upload_kit_has_the_workbook_and_no_amounts(self):
        import sell_ready
        import store_kits
        for kind in KINDS:
            with self.subTest(kind):
                pack = Path(self.rows[kind]["pack"])
                with zipfile.ZipFile(pack / "product.zip") as archive:
                    names = archive.namelist()
                self.assertIn(f"{kind}.xlsx", names)
                self.assertIn(f"{kind}-csv-tabs.zip", names)
                with zipfile.ZipFile(pack / "product.zip") as archive, \
                        zipfile.ZipFile(__import__("io").BytesIO(archive.read(f"{kind}-csv-tabs.zip"))) as inner:
                    self.assertIn("Summary.csv", inner.namelist())
                info = store_kits.parse_listing_file(pack / "listing.txt")
                self.assertEqual(store_kits.gumroad_product_type(pack, info), kind)
                self.assertIsNone(sell_ready.check_money_text((pack / "listing.txt").read_text()))
                self.assertTrue((pack / "store-kits").is_dir())

    def test_carousel_and_pins(self):
        import carousel_maker
        import marketing_auto
        import teams_report
        from tests.listing_fixtures import save_store_link
        for kind in KINDS:
            with self.subTest(kind):
                pack, products = Path(self.rows[kind]["pack"]), self.products(kind)
                db = self.root / kind / "proof.db"
                save_store_link(pack, db)
                marketing_auto.build_for_pack(pack, str(db), products_dir=products)
                out = products / "marketing" / pack.name
                self.assertEqual(carousel_maker.check_ready_carousel(out / "carousel"), [])
                found = teams_report.scan_marketing(products)[pack.name]
                self.assertEqual(found["carousel"]["count"], 1)
                self.assertEqual(found["pins"]["count"], 5)

    def test_listing_gate_and_buyer_check_score(self):
        import buyer_check
        import listing_lint
        import selltest_listings
        for kind in KINDS:
            with self.subTest(kind):
                pack = Path(self.rows[kind]["pack"])
                copy = selltest_listings.make(pack, kind)
                copy["B"] = buyer_check.TITLES[kind]
                self.assertIn(copy["keyword"].casefold(), copy["B"].casefold())
                listing = selltest_listings.render(copy, "B")
                fails = [name for name, level, _ in listing_lint.quality_checks(listing, pack, copy["keyword"])
                         if level == "FAIL"]
                self.assertEqual(fails, [])
                image, page = buyer_check.inside_image(pack)
                self.assertNotIn("instruction", page.lower())
                import io
                buffer = io.BytesIO()
                image.save(buffer, "PNG")
                staged = self.root / kind / "gallery-first.png"
                staged.write_bytes(buffer.getvalue())
                result = buyer_check.evaluate(pack, kind, listing, staged, buffer.getvalue())
                # Every buyer rule passes except the free-sample link, which needs the owner's /listed link.
                self.assertEqual(result["score"], 83, result)
                self.assertEqual([k for k, ok in result["checks"].items() if not ok], ["sample"])

    def test_free_sample(self):
        import free_sample
        import guardrails
        import listing_lint
        import product_builder as pb
        import selltest
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            products, db, packs = root / "products", str(root / "s.db"), []
            for kind in KINDS:
                aid = guardrails.request_approval("public_draft", "Draft: " + kind, "Product", db_path=db)
                guardrails.decide(aid, True, db_path=db)
                folder = pb.build_product(sell_proof.spec_for(kind), aid, str(products))
                pb._record(db, aid, "built", folder)
                truth = pb.truth_check_folder(aid, folder, db, str(products), lambda text: True)
                self.assertEqual(truth["verdict"], "ready", truth)
                packs.append({"id": aid, "title": sell_proof.spec_for(kind)["listing"]["title"], "folder": folder,
                              "format": kind, "listing_score": 100, "ready_score": 95})
            with mock.patch.object(selltest, "candidates", return_value=packs):
                selltest.start(db, products, NOW)
                for pack in packs:
                    with self.subTest(pack["format"]):
                        run = selltest.active(db)
                        if pack["id"] not in {p["id"] for p in run["packs"]}:
                            selltest.swap(run["packs"][-1]["id"], pack["id"], db, products, NOW)
                        with mock.patch.object(free_sample.shutil, "which", return_value=None):
                            folder = free_sample.build(pack["id"], products, db)
                        self.assertTrue(listing_lint.quality_report(folder)["ok"])
                        with zipfile.ZipFile(folder / "product.zip") as archive:
                            self.assertTrue(archive.read("sample.pdf").startswith(b"%PDF"))

    def test_visual_qa_zero_flags(self):
        import visual_qa
        for kind in KINDS:
            with self.subTest(kind):
                products = self.products(kind)
                folder = next(p for p in products.iterdir() if p.is_dir() and p.name[0].isdigit())
                report = visual_qa.check_pack(folder, self.root / "qa" / kind, kind)
                self.assertEqual(report["flags"], [])
                self.assertGreaterEqual(report["previews"], 3)


if __name__ == "__main__":
    unittest.main()
