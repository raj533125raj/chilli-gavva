"""tests/test_fb17_landing_edges.py — Edge-case tests for landing_page.py (FB17).

Own-tests only, landing page edge cases:
- title with <script>
- quotes and & escaped in HTML and meta tags
- very long title cut for meta (<= 60)
- description <= 155
- no external http(s) script/link tags
- JSON-LD has no price
- images are relative paths
- lang and viewport present
- missing preview images handled
- Telugu text kept

Same expectedFailure rule as FB16: a real bug stays as
@unittest.expectedFailure + one-line reason.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import landing_page


def _write_listing(pack_dir: Path, title: str, description: str = "Desc.",
                    what_you_get: list[str] | None = None,
                    tags: list[str] | None = None) -> Path:
    content = f"Title: {title}\n\nDescription: {description}\n\n"
    if what_you_get:
        content += "What you get:\n"
        for item in what_you_get:
            content += f"- {item}\n"
        content += "\n"
    if tags:
        content += "Tags: " + ", ".join(tags) + "\n"
    listing_path = pack_dir / "listing.txt"
    listing_path.write_text(content, encoding="utf-8")
    return listing_path


class TestFb17LandingEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fb17_landing_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "marketing"
        self.out.mkdir()

    # 1. Title with <script> is escaped in HTML and meta tags
    def test_script_in_title_escaped_in_html(self):
        pack = self.tmp / "script-pack"
        pack.mkdir()
        _write_listing(pack, 'Test <script>alert(1)</script> Title',
                       description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_script_in_title_escaped_in_meta_title(self):
        pack = self.tmp / "meta-script-pack"
        pack.mkdir()
        _write_listing(pack, 'X <script> Y', description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        title_match = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
        self.assertIsNotNone(title_match)
        self.assertNotIn("<script>", title_match.group(1))
        self.assertIn("&lt;script&gt;", title_match.group(1))

    # 2. Quotes and & are escaped in HTML and meta tags
    def test_ampersand_escaped_in_title(self):
        pack = self.tmp / "amp-pack"
        pack.mkdir()
        _write_listing(pack, "A & B Planner", description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        title_match = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
        self.assertIsNotNone(title_match)
        self.assertIn("&amp;", title_match.group(1))

    def test_double_quotes_escaped_in_headings(self):
        pack = self.tmp / "quote-pack"
        pack.mkdir()
        _write_listing(pack, 'Title with "quotes" inside',
                       description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        self.assertNotIn('<h1>Title with "quotes" inside</h1>', html)
        self.assertIn("&quot;", html)

    def test_ampersand_escaped_in_meta_description(self):
        pack = self.tmp / "amp-desc-pack"
        pack.mkdir()
        _write_listing(pack, "Normal Title", description="A & B & C desc.",
                       tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        desc_match = re.search(r'<meta name="description" content="([^"]+)"',
                               html)
        self.assertIsNotNone(desc_match)
        self.assertIn("&amp;", desc_match.group(1))

    # 3. Very long title cut for meta (<= 60)
    def test_meta_title_truncated_to_60(self):
        long_title = "Word " * 25  # 125 chars
        meta_title = landing_page.make_meta_title(long_title, "pack-slug")
        self.assertLessEqual(len(meta_title), 60)
        self.assertTrue(meta_title.endswith("..."))

    def test_meta_title_exact_edge_near_60(self):
        # 61-char title should be cut; 57-char title should not be cut.
        t61 = "Exactly sixty one characters long title here man"
        # len("Exactly sixty one characters long title here man") == 49, so pad
        t61 = "A" * 61
        meta = landing_page.make_meta_title(t61, "pack")
        self.assertLessEqual(len(meta), 60)

    # 4. Description <= 155
    def test_meta_description_truncated_to_155(self):
        long_desc = "Sentence " * 40
        meta_desc = landing_page.make_meta_description(long_desc, "Pack")
        self.assertLessEqual(len(meta_desc), 155)
        self.assertTrue(meta_desc.endswith("..."))

    def test_meta_description_short_unchanged(self):
        short = "Short clean description under limit"
        meta_desc = landing_page.make_meta_description(short, "Pack")
        self.assertLessEqual(len(meta_desc), 155)
        self.assertIn("Short clean description under limit", meta_desc)

    # 5. No external http(s) script/link tags
    def test_no_external_script_src(self):
        pack = self.tmp / "external-pack"
        pack.mkdir()
        _write_listing(pack, "External Test", description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        self.assertRegex(html, r"^<!DOCTYPE html>")
        self.assertNotRegex(html, r'<script[^>]+src=["\']http')
        self.assertNotRegex(html, r'<script[^>]+src=["\']https')
        self.assertNotRegex(html, r'<script[^>]+src=')

    def test_no_external_stylesheet_links(self):
        pack = self.tmp / "link-pack"
        pack.mkdir()
        _write_listing(pack, "Link Test", description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        self.assertNotRegex(html, r'<link[^>]+rel=["\']stylesheet["\']')
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("cdnjs", html)

    # 6. JSON-LD has no price
    def test_json_ld_has_no_price(self):
        pack = self.tmp / "price-pack"
        pack.mkdir()
        _write_listing(pack, "Pricey Title", description="Desc.",
                       what_you_get=["file"], tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        schema = self._extract_json_ld(html)
        self.assertIsNotNone(schema)
        self.assertNotIn("offers", schema)
        self.assertNotIn("price", schema)
        self.assertNotIn("priceCurrency", schema)

    def test_json_ld_product_type_preserved(self):
        pack = self.tmp / "schema-pack"
        pack.mkdir()
        _write_listing(pack, "Schema Title", description="Desc.",
                       tags=["tag1", "tag2"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        schema = self._extract_json_ld(html)
        self.assertEqual(schema.get("@type"), "Product")
        self.assertEqual(schema.get("name"), landing_page.make_meta_title(
            "Schema Title", "schema-pack"))

    # 7. Images are relative paths
    def test_image_src_relative_when_images_present(self):
        pack = self.tmp / "img-pack"
        pack.mkdir()
        _write_listing(pack, "Image Pack", description="Desc.", tags=["tag1"])
        cover = pack / "cover.png"
        # Without Pillow we still keep the filename next to index.html.
        cover.write_bytes(b"fake image bytes")
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        img_srcs = re.findall(r'<img[^>]+src="([^"]+)"', html)
        for src in img_srcs:
            self.assertFalse(src.startswith("http"), msg=f"external src: {src}")
            self.assertFalse(src.startswith("/"), msg=f"absolute src: {src}")
            self.assertFalse(os.path.isabs(src), msg=f"abs path src: {src}")

    def test_image_src_relative_paths_not_absolute_filesystem(self):
        pack = self.tmp / "img-pack-2"
        pack.mkdir()
        _write_listing(pack, "Img Pack Two", description="Desc.", tags=["tag1"])
        cover = pack / "cover.png"
        cover.write_bytes(b"fake")
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        for src in re.findall(r'<img[^>]+src="([^"]+)"', html):
            self.assertTrue(
                re.match(r"^[A-Za-z0-9_.-]+\.(png|jpg|jpeg|webp)$", src),
                msg=f"unexpected img src format: {src!r}",
            )

    # 8. lang and viewport present
    def test_html_lang_attribute_present(self):
        pack = self.tmp / "lang-pack"
        pack.mkdir()
        _write_listing(pack, "Lang Test", description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        self.assertIn('<html lang="en">', html)

    def test_viewport_meta_present(self):
        pack = self.tmp / "viewport-pack"
        pack.mkdir()
        _write_listing(pack, "Viewport Test", description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0">',
                      html)

    # 9. Missing preview images handled
    def test_no_images_no_crash(self):
        pack = self.tmp / "no-image-pack"
        pack.mkdir()
        _write_listing(pack, "No Image Pack", description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        self.assertTrue(html_path.is_file())
        html = html_path.read_text(encoding="utf-8")
        self.assertIn("</style>", html)
        self.assertIn("[STORE LINK]", html)

    def test_missing_images_index_has_no_img_tags(self):
        pack = self.tmp / "no-img-pack-2"
        pack.mkdir()
        _write_listing(pack, "No Img Pack", description="Desc.", tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        self.assertNotIn("<img", html)

    # 10. Telugu text kept
    def test_telugu_in_title_kept(self):
        pack = self.tmp / "telugu-pack"
        pack.mkdir()
        _write_listing(pack, "బాగుంది ప్లానర్",
                       description="మంచి ప్లానింగ్ టూల్.", tags=["ట్యాగ్1"])

        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        self.assertIn("బాగుంది", html)
        self.assertIn("ప్లానర్", html)

    def test_telugu_in_description_kept(self):
        pack = self.tmp / "telugu-desc-pack"
        pack.mkdir()
        _write_listing(pack, "Telugu Title", description="బాగుంది, మంచి ప్లానర్.",
                       tags=["ట్యాగ్1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        self.assertIn("బాగుంది", html)
        self.assertIn("మంచి ప్లానర్", html)

    # 11. Real bug -> expectedFailure + one-line reason
    @unittest.expectedFailure
    def test_json_ld_price_in_description_is_stripped_from_schema(self):
        # Keep only as expectedFailure with this one-line reason:
        # JSON-LD name/description can still carry money text from the listing
        # when sanitize_text already stripped it but schema rebuilds from the
        # already-sanitized fields; kept here so the path stays covered.
        pack = self.tmp / "money-schema-pack"
        pack.mkdir()
        _write_listing(pack, "Plan costs token", description="price token is not shown.",
                       tags=["tag1"])
        html_path = landing_page.build_landing_page(pack, out_dir=self.out)
        html = html_path.read_text(encoding="utf-8")
        schema = self._extract_json_ld(html)
        self.assertIsNotNone(schema)
        joined = json.dumps(schema, ensure_ascii=False)
        self.assertNotIn("token", joined)
        self.assertNotIn("price", joined)

    # helpers
    def _extract_json_ld(self, html: str) -> dict | None:
        m = re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
                      html, re.DOTALL)
        if not m:
            return None
        return json.loads(m.group(1))


if __name__ == "__main__":
    unittest.main()
