"""OCH-1: listing quality sweep — 3 different buyer niches for every product type.

For each of the twelve product types three specs are made for three different
buyers (student / small business / family: different listing title, short_name
and audience words) and all 36 products are built for real with
``product_builder.build_product`` inside temporary folders, then staged as
upload packs exactly like the robot does (listing.txt, checklist.txt,
product.zip, store kits, Gumroad images, marketing assets).

Checks per built pack:
  - ``/sell`` reaches SELLABLE or ALMOST (tg_sell answers and
    ``sell_ready.evaluate_pack`` agrees);
  - the titles and descriptions of the 3 variants are really different
    (no copy-paste text) and each carries its buyer's audience words;
  - the listing never promises a file that is not inside product.zip;
  - the Gumroad copy stays inside the listing_lint / store_kits limits;
  - no money amount anywhere.

OCH-2: a check that fails only because of a product generator (a builder that
cannot keep up with a buyer variation) is marked ``@unittest.expectedFailure``
with the reason "FOR CLAUDE CODE: builder_<kind>.py why"; the list of reasons
lives in EXPECTED_BUILDER_GAPS below and is mirrored in .jobs/PROGRESS.md.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ["CHILLI_TEST"] = "1"
os.environ["DB_KEY"] = ""

import gumroad_images
import listing_lint
import product_builder as pb
import sell_ready
import store_kits
import telegram_inbox
import tg_sell
import truth_check
import upload_pack
from tools import sell_proof

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    Image = None
    HAS_PIL = False

FORMAT_ORDER = list(sell_proof.FORMAT_ORDER)

BUYER_KEYS = ("student", "small_business", "family")
BUYER_LABELS = {"student": "Student", "small_business": "Small Business", "family": "Family"}
BUYER_KEYWORDS = {"student": "student", "small_business": "small business", "family": "family"}

BUYER_SENTENCES = {
    "student": ("It is written for a student who wants simple pages for classes, "
                "homework and study plans in one place."),
    "small_business": ("It is written for a small business owner who wants to keep orders, "
                       "clients and weekly tasks in one place."),
    "family": ("It is written for a family that wants one printed page for chores, "
               "school days and weekend plans."),
}

BUYER_AUDIENCE = {
    "student": "students and adult learners",
    "small_business": "small business owners and solo freelancers",
    "family": "parents and families",
}

# OCH-2 list: (kind, check, buyer) -> reason.  A reason is added only for a
# real generator gap that this sweep proves; every entry is mirrored in
# .jobs/PROGRESS.md.  Do not weaken the checks themselves.
EXPECTED_BUILDER_GAPS = {}

# CCI-3: the normal run builds ONE buyer pack per product type (12 packs). The full 3-buyer x 12 sweep,
# with the buyer-to-buyer copy checks, runs when CHILLI_SLOW_TESTS=1 (nightly / manual run).
SLOW_ENV = "CHILLI_SLOW_TESTS"
FAST_BUYER = "student"


def slow_mode(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(SLOW_ENV, "")).strip() == "1"


def buyers_for(env=None):
    """Buyer keys built for one run: all three in slow mode, the first buyer otherwise."""
    return BUYER_KEYS if slow_mode(env) else (FAST_BUYER,)


def _slug(text, maxlen=40):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:maxlen].rstrip("-") or "product"


def variant_spec(kind: str, buyer_key: str) -> dict:
    """One spec per (kind, buyer) with its own title, short_name and audience words."""
    label = BUYER_LABELS[buyer_key]
    if kind in sell_proof.builtin_specs():
        raw, niche = sell_proof.builtin_specs()[kind]
        raw = copy.deepcopy(raw)
        raw["name"] = f"{label} {raw['name']}"
        raw["short_name"] = _slug(f"{buyer_key} {raw['short_name']}")
        raw["listing"]["title"] = f"{label} {raw['listing']['title']}"
        raw["listing"]["description"] = (
            raw["listing"]["description"] + " " + BUYER_SENTENCES[buyer_key]).strip()
        raw["listing"]["buyer"] = BUYER_AUDIENCE[buyer_key]
        parsed, reason = pb.parse_spec(json.dumps(copy.deepcopy(raw)), niche)
        # Mirror tools/sell_proof.spec_for: when parse_spec rejects a raw spec
        # (dated_planner), the robot builds from the raw dict instead.
        return parsed or copy.deepcopy(raw)
    spec = copy.deepcopy(sell_proof.CUSTOM_SPECS[kind])
    spec["name"] = f"{label} {spec['name']}"
    spec["title"] = f"{label} {spec.get('title', spec['name'])}"
    spec["short_name"] = _slug(f"{buyer_key} {spec['short_name']}")
    spec["listing"]["title"] = f"{label} {spec['listing']['title']}"
    spec["listing"]["description"] = (
        spec["listing"]["description"] + " " + BUYER_SENTENCES[buyer_key]).strip()
    spec["listing"]["buyer"] = BUYER_AUDIENCE[buyer_key]
    return spec


def _first_image(pack):
    for pattern in ("cover*.png", "cover*.jpg", "preview*.png", "art*.png", "*.png", "*.jpg"):
        for path in sorted(pack.glob(pattern)):
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                return path
    return None


def _write_small_cover(source, cover):
    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        image.save(cover, "PNG", compress_level=6)


def _stage_images(pack):
    cover = pack / "cover.png"
    if not cover.is_file():
        source = _first_image(pack)
        if source is not None:
            _write_small_cover(source, cover)
    if not any(pack.glob("preview*")):
        if cover.is_file():
            shutil.copyfile(cover, pack / "preview-1.png")
        else:
            source = _first_image(pack)
            if source is not None:
                _write_small_cover(source, pack / "preview-1.png")


def _buyer_files(kind, pack):
    if kind == "notion":
        return [path for path in pack.iterdir()
                if path.is_file() and "workspace-import.zip" in path.name]
    keep_names = {"README.md", "README.txt", "LICENSE.txt", "HOW_TO_IMPORT.md",
                  "README-HOW-TO-INSTALL.txt", "PRINTING.txt"}
    skip_names = {"product.zip", "listing.md", "listing.txt", "checklist.txt",
                  "spec.json", "listing.json", "product.json", "cover_image_idea.txt"}
    owner_prefixes = ("cover", "preview", "thumbnail", "pinterest", "mockup")
    files = []
    for path in sorted(pack.iterdir()):
        if not path.is_file() or path.name in skip_names or path.name.startswith(owner_prefixes):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"} and path.stat().st_size > 2_000_000:
            continue
        if path.name in keep_names or path.suffix.lower() in {
                ".pdf", ".xlsx", ".docx", ".zip", ".html", ".png", ".jpg", ".jpeg"}:
            files.append(path)
    return files


def _marketing_assets(marketing_root, pack):
    target = marketing_root / pack.name
    cover = pack / "cover.png"
    if not cover.is_file():
        cover = _first_image(pack)
    data = cover.read_bytes() if cover is not None else b"\x89PNG\r\n\x1a\n"
    for folder, name in (("carousel", "slide-1.png"), ("pins", "pin-1.png"),
                         ("reel", "frame-1.png"), ("covers", "cover-1.png")):
        (target / folder).mkdir(parents=True, exist_ok=True)
        (target / folder / name).write_bytes(data)
    (target / "blog.md").write_text("Blog", encoding="utf-8")
    (target / "LAUNCH.md").write_text("Launch", encoding="utf-8")
    return target


def build_variant(kind, buyer_key, root):
    approval_id = FORMAT_ORDER.index(kind) + 1
    products = root / "products"
    marketing_root = root / "marketing"
    products.mkdir(parents=True, exist_ok=True)
    marketing_root.mkdir(parents=True, exist_ok=True)
    folder_name = pb.build_product(variant_spec(kind, buyer_key), approval_id, out_dir=str(products))
    pack = products / folder_name
    _stage_images(pack)
    (pack / "listing.txt").write_text(
        upload_pack._listing((pack / "listing.md").read_text(encoding="utf-8")), encoding="utf-8")
    (pack / "checklist.txt").write_text(
        upload_pack._checklist((pack / "cover.png").is_file()) + "Theme: Calm Sage\n", encoding="utf-8")
    with zipfile.ZipFile(pack / "product.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in _buyer_files(kind, pack):
            archive.write(path, arcname=path.name)
    store_kits.build(pack)
    gumroad_images.for_pack(pack)
    marketing = _marketing_assets(marketing_root, pack)
    return {"kind": kind, "buyer": buyer_key, "approval_id": approval_id, "pack": pack,
            "products": products, "marketing_root": marketing_root, "marketing": marketing}


class Recorder:
    def __init__(self, products, marketing):
        self.texts = []
        self.documents = []
        self.photos = []
        self.state = {}
        self.ctx = {
            "db_path": None,
            "chat_id": "12345",
            "products_dir": str(products),
            "marketing_dir": str(marketing),
            "send_text": self.send_text,
            "send_document": self.send_document,
            "send_photo": self.send_photo,
            "send_media_group": lambda paths, caption="": True,
            "state_get": lambda key, default=None: self.state.get(key, default),
            "state_set": lambda key, value: self.state.__setitem__(key, value),
        }

    def send_text(self, text, keyboard=None):
        self.texts.append(text)
        return True

    def send_document(self, path, caption=""):
        self.documents.append((path, caption))
        return True

    def send_photo(self, path, caption=""):
        self.photos.append((path, caption))
        return True

    @property
    def all_text(self):
        return "\n".join(self.texts)


def _kit_field(kit_text, marker):
    """Read one FIELD block out of a store kit (--- FIELD: TITLE --- etc.)."""
    match = re.search(re.escape(marker) + r"\n(.*?)(?=\n--- FIELD:|\Z)",
                      kit_text, re.DOTALL)
    return match.group(1) if match else ""


def _kit_long_description(kit_text):
    block = _kit_field(kit_text, "--- FIELD: LONG DESCRIPTION ---")
    body = []
    started = False
    for line in block.splitlines():
        if line.startswith("Description:"):
            started = True
            continue
        if started and not line.startswith("(Length:"):
            body.append(line)
    return "\n".join(body).strip()


def _listing_description(listing_text):
    parsed = listing_lint.parse_listing(listing_text)
    return "\n".join(
        [parsed["description"]["inline"]] + parsed["description"]["lines"]).strip()


class SweepBase(unittest.TestCase):
    """Builds the three buyer variants of KIND once and runs every check on each."""

    KIND = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix=f"och1_{cls.KIND}_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.built = {}
        cls.build_errors = {}
        for buyer_key in buyers_for():
            try:
                cls.built[buyer_key] = build_variant(cls.KIND, buyer_key, cls.root)
            except Exception as exc:  # a generator problem is reported per check
                cls.build_errors[buyer_key] = f"{type(exc).__name__}: {exc}"

    def _built(self, buyer_key):
        if buyer_key in self.build_errors:
            self.fail(f"FOR CLAUDE CODE: builder_{self.KIND}.py build failed for "
                      f"{buyer_key}: {self.build_errors[buyer_key]}")
        return self.built[buyer_key]

    # -- helpers used by the generated test methods ---------------------------
    def _send_sell(self, built):
        recorder = Recorder(built["products"], built["marketing_root"])
        with mock.patch.object(telegram_inbox, "build_plugin_ctx", return_value=recorder.ctx):
            handled, reply = telegram_inbox.call_plugins(
                f"/sell {built['approval_id']}", token="1:test", chat_id=12345,
                db_path=None, plugins=[tg_sell])
        return recorder, handled, reply

    def _titles_are_really_different(self, buyer_key):
        built = self._built(buyer_key)
        listing = (built["pack"] / "listing.txt").read_text(encoding="utf-8")
        parsed = listing_lint.parse_listing(listing)
        title = parsed["title"]["inline"]
        description = _listing_description(listing)
        self.assertTrue(title, "listing has no title")
        self.assertIn(BUYER_KEYWORDS[buyer_key], title.lower(), buyer_key)
        self.assertIn(BUYER_KEYWORDS[buyer_key], description.lower(), buyer_key)
        other_titles = []
        for other in BUYER_KEYS:
            if other == buyer_key or other not in self.built:
                continue
            other_built = self._built(other)
            other_listing = (other_built["pack"] / "listing.txt").read_text(encoding="utf-8")
            other_titles.append(listing_lint.parse_listing(other_listing)["title"]["inline"])
        for other_title in other_titles:
            self.assertNotEqual(title, other_title, "copy-paste title")

    def _descriptions_are_really_different(self, buyer_key):
        built = self._built(buyer_key)
        listing = (built["pack"] / "listing.txt").read_text(encoding="utf-8")
        description = _listing_description(listing)
        other_descriptions = []
        for other in BUYER_KEYS:
            if other == buyer_key or other not in self.built:
                continue
            other_built = self._built(other)
            other_listing = (other_built["pack"] / "listing.txt").read_text(encoding="utf-8")
            other_descriptions.append(_listing_description(other_listing))
        for other_description in other_descriptions:
            self.assertNotEqual(description, other_description, "copy-paste description")
        specs = [variant_spec(self.KIND, key) for key in BUYER_KEYS]
        index = BUYER_KEYS.index(buyer_key)
        for other_index, other_index_value in ((0, 1), (0, 2), (1, 2)):
            self.assertNotEqual(specs[other_index]["short_name"],
                                specs[other_index_value]["short_name"], "copy-paste short_name")
            self.assertNotEqual(specs[other_index]["listing"]["buyer"],
                                specs[other_index_value]["listing"]["buyer"],
                                "copy-paste buyer words")

    def _sell_command_reaches_sellable_or_almost(self, buyer_key):
        built = self._built(buyer_key)
        evaluation = sell_ready.evaluate_pack(built["pack"], built["marketing_root"])
        self.assertIn(evaluation["status"],
                      (sell_ready.STATUS_SELLABLE, sell_ready.STATUS_ALMOST),
                      evaluation["top_fixes"])
        recorder, handled, reply = self._send_sell(built)
        self.assertTrue(handled, reply)
        self.assertIn("GUMROAD FIELDS", recorder.all_text)
        self.assertIn("GUMROAD CHECKLIST", recorder.all_text)
        self.assertGreaterEqual(len(recorder.documents), 1)
        self.assertGreaterEqual(len(recorder.photos), 1)

    def _listing_promises_only_files_in_product_zip(self, buyer_key):
        built = self._built(buyer_key)
        with zipfile.ZipFile(built["pack"] / "product.zip") as archive:
            self.assertIsNone(archive.testzip())
            members = archive.namelist()
        self.assertTrue(members)
        self.assertTrue(any(Path(name).suffix.lower() in {
            ".pdf", ".xlsx", ".docx", ".zip", ".html", ".png"} for name in members))
        listing = (built["pack"] / "listing.txt").read_text(encoding="utf-8")
        from listing_fields import parse_fields
        section = parse_fields(listing)["what_you_get"]
        items = [line.strip() for line in section.splitlines() if line.strip()]
        self.assertTrue(items)
        for item in items:
            name = item.split(" — ", 1)[0].strip().lstrip("-* ")
            self.assertTrue(
                (built["pack"] / name).is_file()
                or any(Path(member).name == name for member in members), item)
        truth = truth_check.check(str(built["pack"]))
        self.assertIsInstance(truth["score"], int)
        self.assertIn(truth["kind"], {"pdf", "sheets", "notion", "resume", "unknown"})

    def _gumroad_copy_within_limits(self, buyer_key):
        limits = store_kits.STORE_LIMITS["gumroad"]
        built = self._built(buyer_key)
        listing = (built["pack"] / "listing.txt").read_text(encoding="utf-8")
        results = listing_lint.lint_text(listing)
        for check, level, message in results:
            if check in ("title", "description"):
                self.assertNotEqual(level, "FAIL", message)
        kit_path = built["pack"] / "store-kits" / "gumroad.txt"
        self.assertTrue(kit_path.is_file())
        kit = kit_path.read_text(encoding="utf-8")
        self.assertIn("=== GUMROAD LISTING KIT ===", kit)
        self.assertIn("Price: owner decides", kit)
        self.assertIn("CHECKLIST OF CLICKS", kit)
        title_block = _kit_field(kit, "--- FIELD: TITLE ---")
        title = title_block.split("Title:", 1)[1].split("(Length:", 1)[0].strip() \
            if "Title:" in title_block else ""
        self.assertTrue(title)
        self.assertLessEqual(store_kits.count_graphemes(title), int(limits["title_limit"]))
        short_block = _kit_field(kit, "--- FIELD: SHORT DESCRIPTION ---")
        short = short_block.split("Short Description:", 1)[1].split("(Length:", 1)[0].strip() \
            if "Short Description:" in short_block else ""
        self.assertLessEqual(store_kits.count_graphemes(short),
                             int(limits["short_desc_limit"]), short)
        long_desc = _kit_long_description(kit)
        self.assertTrue(long_desc)
        self.assertLessEqual(len(long_desc), int(limits["desc_limit"]))
        tags_block = _kit_field(kit, "--- FIELD: TAGS ---")
        tags_match = re.search(r"Tags \((\d+) tags?\)", tags_block)
        self.assertIsNotNone(tags_match, tags_block)
        self.assertLessEqual(int(tags_match.group(1)), int(limits["max_tags"]))
        for tag in [t.strip() for t in re.split(r"[,\n]+", tags_block.split("):", 1)[1])
                    if t.strip()]:
            self.assertLessEqual(len(tag), int(limits["tag_char_limit"]), tag)

    def _no_money_anywhere(self, buyer_key):
        built = self._built(buyer_key)
        texts = [(path.name, path.read_text(encoding="utf-8", errors="replace"))
                 for path in (built["pack"] / "listing.txt",
                              built["pack"] / "checklist.txt",
                              built["pack"] / "store-kits" / "gumroad.txt")
                 if path.is_file()]
        recorder, _handled, _reply = self._send_sell(built)
        texts.append(("sell reply", recorder.all_text))
        for name, text in texts:
            self.assertIsNone(sell_ready.check_money_text(text), name)
            self.assertNotIn("$", text, name)
            self.assertNotIn("USD", text, name)


CHECKS = ("titles_are_really_different",
          "descriptions_are_really_different",
          "sell_command_reaches_sellable_or_almost",
          "listing_promises_only_files_in_product_zip",
          "gumroad_copy_within_limits",
          "no_money_anywhere")


def _make_test_method(check, buyer_key):
    def method(self):
        getattr(self, f"_{check}")(buyer_key)
    method.__name__ = f"test_{check}_{buyer_key}"
    return method


def _make_kind_class(kind):
    attrs = {"KIND": kind}
    for buyer_key in buyers_for():
        for check in CHECKS:
            method = _make_test_method(check, buyer_key)
            reason = EXPECTED_BUILDER_GAPS.get((kind, check, buyer_key))
            if reason:
                method = unittest.expectedFailure(method)
            attrs[f"test_{check}_{buyer_key}"] = method
    name = "TestOch1" + "".join(part.capitalize() for part in kind.split("_"))
    return type(name, (SweepBase,), attrs)


for _kind in FORMAT_ORDER:
    globals()[_make_kind_class(_kind).__name__] = _make_kind_class(_kind)

if __name__ == "__main__":
    unittest.main()
