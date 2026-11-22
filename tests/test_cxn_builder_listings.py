"""Actual builder and upload outputs must satisfy the buyer listing gate."""
import tempfile
import unittest
from pathlib import Path

import listing_lint
import product_builder
import upload_pack
import store_kits
from tools import sell_proof


class BuilderListingTests(unittest.TestCase):
    def test_every_real_format_has_complete_listing_and_three_previews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, kind in enumerate(sell_proof.FORMAT_ORDER, 100):
                with self.subTest(kind=kind):
                    name = product_builder.build_product(sell_proof.spec_for(kind), index, out_dir=str(root))
                    source = root / name
                    result = product_builder.truth_check_folder(index, name, db_path=str(root / "proof.db"),
                        out_dir=str(root), notify_fn=lambda text: True)
                    self.assertEqual(result["verdict"], "ready", result)
                    pack = root / "upload" / name
                    self.assertTrue((pack / "product.zip").is_file())
                    copy = store_kits.gumroad_copy(pack, store_kits.parse_listing_file(pack / "listing.txt"))
                    self.assertEqual(copy["product_type"], kind, (kind, copy))
                    report = listing_lint.quality_report(pack)
                    self.assertEqual(report["fixes"], [], (kind, report))
                    self.assertGreaterEqual(len(list(pack.glob("preview-*.png"))), 3)
                    self.assertIn("How to use:", (pack / "listing.txt").read_text())
                    self.assertIn("License:", (pack / "listing.txt").read_text())

    def test_missing_buyer_details_still_fail(self):
        problems = [name for name,level,_ in listing_lint.quality_checks("Title: A short title") if level=="FAIL"]
        for required in ("title", "keyword", "description", "what you get", "format", "page count/size", "how to use", "license", "tags"):
            self.assertIn(required, problems)

    def test_listing_preserves_instructions_and_license(self):
        source = "# Topic Template\n\n## Description\nA useful description.\n\n## Tags\n- topic\n- template\n- workbook\n\n## What you get\n- guide.pdf\n\n## How to use\nOpen the PDF and print on A4.\n\n## License\nPersonal use only.\n"
        listing = upload_pack._listing(source)
        self.assertIn("- guide.pdf", listing)
        self.assertIn("How to use:\nOpen the PDF and print on A4.", listing)
        self.assertIn("License:\nPersonal use only.", listing)


if __name__ == "__main__":
    unittest.main()
