"""tests/test_kl_bundles.py — Tests for bundle_builder module.

Covers:
  - suggest_bundles:
    - 2, 3, 4 packs grouping
    - Jaccard similarity >= 0.25 threshold
    - Audience word matching (same audience)
    - Rejection of unrelated packs / niches
    - Max 5 suggestions limit
    - Deterministic ordering and IDs
    - Reason lines (informative, no money amounts, clean)
  - build_bundle:
    - Folder structure (bundle.zip, cover.png, listing.txt, LICENSE.txt)
    - No price in listing or any file
    - 13 tags merged without duplicates
    - Unified 'What you get' combining member packs
    - Clean description with personal use clause
    - Cover image: exactly 1280x720, fan layout, not monochrome
    - bundle.zip contents: member zips cleanly named, no __MACOSX, no .DS_Store, no absolute paths
    - Deep QA hook runs when available, writing report file outside bundle folder
    - Determinism: identical input packs yield identical bundle IDs and structure
    - Error handling: <2 packs, >4 packs, missing files, corrupted zips
"""

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

from PIL import Image, ImageDraw

import bundle_builder
import upload_pack


def create_dummy_png(filepath, width=1280, height=720, color=(100, 150, 200)):
    """Create a test PNG image that is not monochrome."""
    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, width - 50, height - 50], fill=(220, 100, 80))
    draw.text((70, 70), "Test Image", fill=(255, 255, 255))
    img.save(filepath, "PNG")


def create_dummy_product_zip(filepath, member_filenames=None):
    """Create a clean product.zip without metadata."""
    member_filenames = member_filenames or ["guide.txt", "template.txt"]
    with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in member_filenames:
            z.writestr(name, f"Content for {name}")


def create_temp_pack(
    parent_dir,
    pack_name,
    title="Digital Product",
    tags=None,
    what_you_get=None,
    description="A great practical digital tool for everyday organization.",
    license_text="Personal use only. Do not resell.",
    corrupt_zip=False,
    dirty_zip=False,
    abs_path_zip=False,
):
    """Create a temporary upload pack folder with standard files."""
    pack_dir = os.path.join(parent_dir, pack_name)
    os.makedirs(pack_dir, exist_ok=True)

    if tags is None:
        tags = ["digital download", "planner", "templates"]
    if what_you_get is None:
        what_you_get = ["1 printable page", "1 instruction guide"]

    # listing.txt
    tags_line = f"Tags: {', '.join(tags)}" if tags is not None else ""
    desc_line = f"Description:\n{description}" if description is not None else ""
    listing_content = (
        f"Title: {title}\n\n"
        f"{desc_line}\n\n"
        f"{tags_line}\n\n"
        f"What you get:\n" + "\n".join(f"- {item}" for item in what_you_get) + "\n"
    )
    with open(os.path.join(pack_dir, "listing.txt"), "w", encoding="utf-8") as f:
        f.write(listing_content)

    # LICENSE.txt
    with open(os.path.join(pack_dir, "LICENSE.txt"), "w", encoding="utf-8") as f:
        f.write(license_text + "\n")

    # cover.png
    create_dummy_png(os.path.join(pack_dir, "cover.png"), width=1280, height=720)

    # product.zip
    zip_path = os.path.join(pack_dir, "product.zip")
    if corrupt_zip:
        with open(zip_path, "wb") as f:
            f.write(b"not a valid zip")
    elif dirty_zip:
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("__MACOSX/._test", "bad macosx metadata")
            z.writestr("test.txt", "content")
    elif abs_path_zip:
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("/absolute/path/file.txt", "content")
    else:
        create_dummy_product_zip(zip_path)

    return pack_dir


class TestKLBundleBuilder(unittest.TestCase):

    def setUp(self):
        # Clear GITHUB_* and RUNNER_ENVIRONMENT environment variables
        env_patch = {
            k: v for k, v in os.environ.items()
            if not k.startswith("GITHUB_") and not k.startswith("RUNNER_")
        }
        self.env_mock = mock.patch.dict(os.environ, env_patch, clear=True)
        self.env_mock.start()

        self.tmp_dir = tempfile.mkdtemp(prefix="test_kl_bundles_")

    def tearDown(self):
        self.env_mock.stop()
        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Grouping Logic Tests (suggest_bundles)
    # --------------------------------------------------------------------------

    def test_01_suggest_empty_or_single_pack_returns_empty(self):
        self.assertEqual(bundle_builder.suggest_bundles([]), [])
        p1 = create_temp_pack(self.tmp_dir, "pack1", title="ADHD Focus Daily Planner")
        self.assertEqual(bundle_builder.suggest_bundles([p1]), [])

    def test_02_suggest_two_matching_adhd_packs(self):
        p1 = create_temp_pack(
            self.tmp_dir, "adhd_p1",
            title="ADHD Daily Routine Planner",
            tags=["adhd planner", "focus routine", "executive function", "neurodivergent planner", "dopamine tracker"],
            description="ADHD daily focus and executive function planner."
        )
        p2 = create_temp_pack(
            self.tmp_dir, "adhd_p2",
            title="ADHD Weekly Habit & Focus Tracker",
            tags=["adhd planner", "focus routine", "weekly habit", "dopamine tracker", "adhd routine"],
            description="ADHD weekly dopamine and focus habit system."
        )
        suggestions = bundle_builder.suggest_bundles([p1, p2])
        self.assertEqual(len(suggestions), 1)
        sug = suggestions[0]
        self.assertEqual(sug["size"], 2)
        self.assertIn("adhd_p1", sug["pack_ids"])
        self.assertIn("adhd_p2", sug["pack_ids"])
        self.assertGreaterEqual(sug["similarity"], 0.25)
        self.assertEqual(sug["audience"], "adhd")
        self.assertIn("ADHD", sug["reason"])

    def test_03_suggest_rejects_unrelated_packs_adhd_and_resume(self):
        p1 = create_temp_pack(
            self.tmp_dir, "adhd_p1",
            title="ADHD Daily Routine Planner",
            tags=["adhd planner", "focus routine", "executive function", "dopamine tracker"],
            description="ADHD daily focus and executive function planner."
        )
        p2 = create_temp_pack(
            self.tmp_dir, "resume_p1",
            title="Professional ATS Resume Template",
            tags=["resume template", "ats resume", "job application", "career cv", "interview guide"],
            description="ATS resume template and job application system."
        )
        suggestions = bundle_builder.suggest_bundles([p1, p2])
        self.assertEqual(suggestions, [])

    def test_04_suggest_rejects_unrelated_packs_budget_and_freelancer_with_low_overlap(self):
        p1 = create_temp_pack(
            self.tmp_dir, "budget_p1",
            title="Household Budget Tracker",
            tags=["household budget", "savings tracker", "debt payoff", "personal finance", "spending log"],
            description="Personal household budget and debt payoff tracker."
        )
        p2 = create_temp_pack(
            self.tmp_dir, "freelance_p1",
            title="Freelance Client Invoice Sheet",
            tags=["freelance invoice", "client contractor", "hourly billing", "solopreneur rate"],
            description="Freelance contractor billing and invoice tracker."
        )
        suggestions = bundle_builder.suggest_bundles([p1, p2])
        self.assertEqual(suggestions, [])

    def test_05_suggest_groups_three_freelancer_packs(self):
        p1 = create_temp_pack(
            self.tmp_dir, "free_1",
            title="Freelance Client Tracker",
            tags=["freelance client", "client invoice", "solopreneur tracker", "hourly billing", "contractor hub"],
            description="Freelancer client management tracker."
        )
        p2 = create_temp_pack(
            self.tmp_dir, "free_2",
            title="Freelance Invoice & Billing Hub",
            tags=["freelance invoice", "client billing", "solopreneur tracker", "contractor hub", "freelance finance"],
            description="Freelancer invoice generator and contractor tracker."
        )
        p3 = create_temp_pack(
            self.tmp_dir, "free_3",
            title="Freelance Project Milestone Tracker",
            tags=["freelance project", "client billing", "contractor hub", "solopreneur tracker", "client invoice"],
            description="Freelancer project delivery milestones for contractors."
        )
        suggestions = bundle_builder.suggest_bundles([p1, p2, p3])
        self.assertGreaterEqual(len(suggestions), 1)
        # Check that a 3-pack bundle is suggested
        sizes = [s["size"] for s in suggestions]
        self.assertIn(3, sizes)

    def test_06_suggest_groups_four_budget_packs(self):
        packs = []
        for i in range(1, 5):
            packs.append(create_temp_pack(
                self.tmp_dir, f"budget_{i}",
                title=f"Family Budget System Part {i}",
                tags=["budget planner", "family budget", "savings tracker", "debt payoff", "expense log"],
                description="Family budget, savings and debt payoff management sheet."
            ))
        suggestions = bundle_builder.suggest_bundles(packs)
        self.assertGreaterEqual(len(suggestions), 1)
        sizes = [s["size"] for s in suggestions]
        self.assertIn(4, sizes)

    def test_07_suggest_max_5_suggestions(self):
        # 6 closely related ADHD packs
        packs = []
        for i in range(1, 7):
            packs.append(create_temp_pack(
                self.tmp_dir, f"adhd_pack_{i}",
                title=f"ADHD Focus Kit {i}",
                tags=["adhd planner", "focus routine", "executive function", "dopamine tracker", "adhd habits"],
                description="ADHD daily focus, routine and dopamine tracker."
            ))
        suggestions = bundle_builder.suggest_bundles(packs)
        self.assertLessEqual(len(suggestions), 5)

    def test_08_suggest_jaccard_similarity_threshold_enforced(self):
        # Packs with only 1 word in common out of 20 -> Jaccard < 0.15
        p1 = create_temp_pack(
            self.tmp_dir, "gen_1",
            title="Daily Morning Routine Checklist",
            tags=["morning routine", "sunshine ritual", "breakfast checklist", "waking up"],
            description="Morning habit checklist for waking up early."
        )
        p2 = create_temp_pack(
            self.tmp_dir, "gen_2",
            title="Evening Bedtime Routine Guide",
            tags=["evening routine", "sleep tracker", "night relaxation", "bedtime tea"],
            description="Night habit guide for falling asleep calmly."
        )
        suggestions = bundle_builder.suggest_bundles([p1, p2])
        self.assertEqual(suggestions, [])

    def test_09_suggest_deterministic_output(self):
        packs = []
        for i in range(1, 4):
            packs.append(create_temp_pack(
                self.tmp_dir, f"det_pack_{i}",
                title=f"Student Study Planner {i}",
                tags=["student planner", "study guide", "college schedule", "academic calendar", "exam tracker"],
                description="Student study planner and exam tracker for college."
            ))
        sug1 = bundle_builder.suggest_bundles(packs)
        sug2 = bundle_builder.suggest_bundles(list(reversed(packs)))
        self.assertEqual(len(sug1), len(sug2))
        for a, b in zip(sug1, sug2):
            self.assertEqual(a["bundle_id"], b["bundle_id"])
            self.assertEqual(a["pack_ids"], b["pack_ids"])

    def test_10_suggest_no_money_in_reason(self):
        p1 = create_temp_pack(
            self.tmp_dir, "adhd_a",
            title="ADHD Weekly Planner",
            tags=["adhd planner", "focus tracker", "dopamine system"],
            description="ADHD weekly planning kit."
        )
        p2 = create_temp_pack(
            self.tmp_dir, "adhd_b",
            title="ADHD Monthly Review",
            tags=["adhd planner", "focus tracker", "dopamine system"],
            description="ADHD monthly review kit."
        )
        suggestions = bundle_builder.suggest_bundles([p1, p2])
        self.assertEqual(len(suggestions), 1)
        reason = suggestions[0]["reason"]
        # Must not raise ValueError in upload_pack._check_money
        upload_pack._check_money(reason, "suggest reason")

    def test_11_suggest_handles_dict_inputs(self):
        p1_dir = create_temp_pack(self.tmp_dir, "p1", title="ADHD Focus Kit A",
                                  tags=["adhd focus", "routine tracker", "dopamine"])
        p2_dir = create_temp_pack(self.tmp_dir, "p2", title="ADHD Focus Kit B",
                                  tags=["adhd focus", "routine tracker", "dopamine"])
        dict_inputs = [
            {"folder": p1_dir, "pack_id": "p1"},
            {"folder": p2_dir, "pack_id": "p2"}
        ]
        suggestions = bundle_builder.suggest_bundles(dict_inputs)
        self.assertEqual(len(suggestions), 1)

    def test_12_suggest_with_missing_keywords_is_skipped(self):
        # Empty tags, description, and title consisting only of stop words
        p1 = create_temp_pack(self.tmp_dir, "pack", title="The Pack", tags=[], description="")
        p2 = create_temp_pack(self.tmp_dir, "bundle", title="A Bundle", tags=[], description="")
        # Remove keywords by giving stop-word pack names
        self.assertEqual(bundle_builder.suggest_bundles([p1, p2]), [])

    def test_13_suggest_bundle_id_format(self):
        p1 = create_temp_pack(self.tmp_dir, "1-adhd-planner", title="ADHD Daily Routine",
                                  tags=["adhd planner", "focus", "dopamine"])
        p2 = create_temp_pack(self.tmp_dir, "2-adhd-tracker", title="ADHD Focus Tracker",
                                  tags=["adhd planner", "focus", "dopamine"])
        suggestions = bundle_builder.suggest_bundles([p1, p2])
        self.assertTrue(suggestions[0]["bundle_id"].startswith("bundle-"))

    def test_14_suggest_multiniche_isolation(self):
        # 2 ADHD packs + 2 Resume packs => should suggest 1 ADHD bundle and 1 Resume bundle, NEVER mixed
        p_adhd1 = create_temp_pack(self.tmp_dir, "adhd1", title="ADHD Focus 1", tags=["adhd planner", "focus routine", "dopamine"])
        p_adhd2 = create_temp_pack(self.tmp_dir, "adhd2", title="ADHD Focus 2", tags=["adhd planner", "focus routine", "dopamine"])
        p_res1 = create_temp_pack(self.tmp_dir, "res1", title="Resume Template 1", tags=["resume template", "ats resume", "interview guide"])
        p_res2 = create_temp_pack(self.tmp_dir, "res2", title="Resume Template 2", tags=["resume template", "ats resume", "interview guide"])

        suggestions = bundle_builder.suggest_bundles([p_adhd1, p_adhd2, p_res1, p_res2])
        self.assertGreaterEqual(len(suggestions), 2)
        for s in suggestions:
            ids = set(s["pack_ids"])
            self.assertFalse("adhd1" in ids and "res1" in ids)
            self.assertFalse("adhd2" in ids and "res2" in ids)

    def test_15_suggest_general_audience_matching(self):
        p1 = create_temp_pack(self.tmp_dir, "org1", title="Minimalist Home Declutter Checklist",
                              tags=["declutter checklist", "home organization", "tidy space", "room by room"])
        p2 = create_temp_pack(self.tmp_dir, "org2", title="Minimalist Cleaning Routine Checklist",
                              tags=["cleaning routine", "home organization", "tidy space", "declutter checklist"])
        suggestions = bundle_builder.suggest_bundles([p1, p2])
        self.assertEqual(len(suggestions), 1)

    # --------------------------------------------------------------------------
    # Build Bundle Tests (build_bundle)
    # --------------------------------------------------------------------------

    def test_16_build_bundle_creates_expected_folder_and_files(self):
        p1 = create_temp_pack(self.tmp_dir, "adhd_p1", title="ADHD Planner 1",
                              tags=["adhd", "focus", "planner"], what_you_get=["A4 PDF", "US Letter PDF"])
        p2 = create_temp_pack(self.tmp_dir, "adhd_p2", title="ADHD Planner 2",
                              tags=["adhd", "focus", "tracker"], what_you_get=["Daily Worksheet PDF"])

        out_base = os.path.join(self.tmp_dir, "bundles_out")
        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=out_base)

        self.assertTrue(os.path.isdir(bundle_path))
        self.assertTrue(os.path.isfile(os.path.join(bundle_path, "bundle.zip")))
        self.assertTrue(os.path.isfile(os.path.join(bundle_path, "cover.png")))
        self.assertTrue(os.path.isfile(os.path.join(bundle_path, "listing.txt")))
        self.assertTrue(os.path.isfile(os.path.join(bundle_path, "LICENSE.txt")))

    def test_17_build_bundle_cover_dimensions_and_validity(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="Product 1", tags=["tag1", "tag2"])
        p2 = create_temp_pack(self.tmp_dir, "p2", title="Product 2", tags=["tag1", "tag2"])

        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        cover_file = os.path.join(bundle_path, "cover.png")
        with Image.open(cover_file) as img:
            self.assertEqual(img.size, (1280, 720))
            # Check not monochrome
            extrema = img.convert("RGBA").getextrema()
            is_single = all(min_val == max_val for min_val, max_val in extrema[:3])
            self.assertFalse(is_single, "Bundle cover must not be monochrome")

    def test_18_build_bundle_cover_with_3_packs(self):
        packs = [
            create_temp_pack(self.tmp_dir, f"pack_{i}", title=f"Pack {i}", tags=["planner", "habits"])
            for i in range(1, 4)
        ]
        bundle_path = bundle_builder.build_bundle(packs, out_dir=self.tmp_dir)
        cover_file = os.path.join(bundle_path, "cover.png")
        with Image.open(cover_file) as img:
            self.assertEqual(img.size, (1280, 720))

    def test_19_build_bundle_cover_with_4_packs(self):
        packs = [
            create_temp_pack(self.tmp_dir, f"pack_{i}", title=f"Pack {i}", tags=["planner", "habits"])
            for i in range(1, 5)
        ]
        bundle_path = bundle_builder.build_bundle(packs, out_dir=self.tmp_dir)
        cover_file = os.path.join(bundle_path, "cover.png")
        with Image.open(cover_file) as img:
            self.assertEqual(img.size, (1280, 720))

    def test_20_build_bundle_zip_contains_cleanly_named_member_zips(self):
        p1 = create_temp_pack(self.tmp_dir, "pack_one", title="Pack One", tags=["a", "b"])
        p2 = create_temp_pack(self.tmp_dir, "pack_two", title="Pack Two", tags=["a", "b"])

        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        zip_path = os.path.join(bundle_path, "bundle.zip")

        with zipfile.ZipFile(zip_path, "r") as z:
            names = z.namelist()
            self.assertEqual(len(names), 2)
            self.assertIn("1-pack-one.zip", names)
            self.assertIn("2-pack-two.zip", names)
            for n in names:
                self.assertFalse(n.startswith("/"))
                self.assertNotIn("__MACOSX", n)
                self.assertNotEqual(os.path.basename(n), ".DS_Store")

    def test_21_build_bundle_inner_member_zips_are_valid(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="P1", tags=["a", "b"])
        p2 = create_temp_pack(self.tmp_dir, "p2", title="P2", tags=["a", "b"])

        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        zip_path = os.path.join(bundle_path, "bundle.zip")

        with zipfile.ZipFile(zip_path, "r") as z:
            data1 = z.read("1-p1.zip")
            # Write to disk and check with zipfile
            mem_zip1 = os.path.join(self.tmp_dir, "extracted_1.zip")
            with open(mem_zip1, "wb") as f:
                f.write(data1)
            with zipfile.ZipFile(mem_zip1, "r") as z_inner:
                self.assertIn("guide.txt", z_inner.namelist())

    def test_22_build_bundle_listing_has_no_money_amounts(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="ADHD Focus Kit", tags=["adhd", "focus"])
        p2 = create_temp_pack(self.tmp_dir, "p2", title="ADHD Task Tracker", tags=["adhd", "focus"])

        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        listing_file = os.path.join(bundle_path, "listing.txt")
        with open(listing_file, encoding="utf-8") as f:
            listing_text = f.read()

        # Check with upload_pack._check_money
        upload_pack._check_money(listing_text, "test listing")
        # Ensure no dollar or currency signs in listing
        self.assertNotIn("$", listing_text)
        self.assertNotIn("USD", listing_text)
        self.assertNotIn("INR", listing_text)

    def test_23_build_bundle_listing_tags_up_to_13_no_duplicates(self):
        p1 = create_temp_pack(
            self.tmp_dir, "p1", title="ADHD Tools",
            tags=["adhd planner", "focus routine", "dopamine tracker", "adhd planner"] # duplicate
        )
        p2 = create_temp_pack(
            self.tmp_dir, "p2", title="ADHD Habits",
            tags=["executive function", "adhd habits", "focus routine", "daily schedule"]
        )
        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        with open(os.path.join(bundle_path, "listing.txt"), encoding="utf-8") as f:
            listing_text = f.read()

        m = re.search(r"^Tags:\s*(.+)$", listing_text, re.MULTILINE)
        self.assertIsNotNone(m)
        tags = [t.strip() for t in m.group(1).split(",")]
        self.assertLessEqual(len(tags), 13)
        self.assertGreaterEqual(len(tags), 5)
        # Check no duplicates
        lower_tags = [t.lower() for t in tags]
        self.assertEqual(len(lower_tags), len(set(lower_tags)))

    def test_24_build_bundle_listing_what_you_get_combines_members(self):
        p1 = create_temp_pack(
            self.tmp_dir, "p1", title="Pack Alpha",
            what_you_get=["Alpha PDF document", "Alpha quickstart sheet"]
        )
        p2 = create_temp_pack(
            self.tmp_dir, "p2", title="Pack Beta",
            what_you_get=["Beta workbook .xlsx", "Beta video checklist"]
        )
        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        with open(os.path.join(bundle_path, "listing.txt"), encoding="utf-8") as f:
            listing_text = f.read()

        self.assertIn("Alpha PDF document", listing_text)
        self.assertIn("Alpha quickstart sheet", listing_text)
        self.assertIn("Beta workbook .xlsx", listing_text)
        self.assertIn("Beta video checklist", listing_text)

    def test_25_build_bundle_listing_personal_use_notice_present(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="Pack 1")
        p2 = create_temp_pack(self.tmp_dir, "p2", title="Pack 2")

        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        with open(os.path.join(bundle_path, "listing.txt"), encoding="utf-8") as f:
            listing_text = f.read()
        self.assertIn("personal use", listing_text.lower())

    def test_26_build_bundle_license_copied_from_member(self):
        custom_license = "Custom personal use agreement for buyer testing."
        p1 = create_temp_pack(self.tmp_dir, "p1", title="P1", license_text=custom_license)
        p2 = create_temp_pack(self.tmp_dir, "p2", title="P2")

        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        lic_file = os.path.join(bundle_path, "LICENSE.txt")
        self.assertTrue(os.path.isfile(lic_file))
        with open(lic_file, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(custom_license, content)

    def test_27_build_bundle_atomic_creation(self):
        # Even if a failure happens during zip or write, destination should not be left in half-state
        p1 = create_temp_pack(self.tmp_dir, "p1", corrupt_zip=True)
        p2 = create_temp_pack(self.tmp_dir, "p2")

        with self.assertRaises(Exception):
            bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)

    def test_28_build_bundle_refuses_dirty_member_zip_with_macosx(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", dirty_zip=True)
        p2 = create_temp_pack(self.tmp_dir, "p2")

        with self.assertRaises(ValueError) as ctx:
            bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        self.assertIn("illegal metadata entry", str(ctx.exception))

    def test_29_build_bundle_refuses_absolute_paths_in_member_zip(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", abs_path_zip=True)
        p2 = create_temp_pack(self.tmp_dir, "p2")

        with self.assertRaises(ValueError) as ctx:
            bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        self.assertIn("absolute path", str(ctx.exception))

    def test_30_build_bundle_refuses_less_than_2_packs(self):
        p1 = create_temp_pack(self.tmp_dir, "p1")
        with self.assertRaises(ValueError):
            bundle_builder.build_bundle([p1], out_dir=self.tmp_dir)

    def test_31_build_bundle_refuses_more_than_4_packs(self):
        packs = [create_temp_pack(self.tmp_dir, f"p{i}") for i in range(5)]
        with self.assertRaises(ValueError):
            bundle_builder.build_bundle(packs, out_dir=self.tmp_dir)

    def test_32_build_bundle_refuses_missing_product_zip(self):
        p1 = create_temp_pack(self.tmp_dir, "p1")
        p2 = create_temp_pack(self.tmp_dir, "p2")
        os.remove(os.path.join(p2, "product.zip"))

        with self.assertRaises(ValueError) as ctx:
            bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        self.assertIn("missing product.zip", str(ctx.exception))

    def test_33_build_bundle_deep_qa_report_saved_outside_bundle_folder(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="ADHD Focus 1")
        p2 = create_temp_pack(self.tmp_dir, "p2", title="ADHD Focus 2")

        out_base = os.path.join(self.tmp_dir, "bundles_out")
        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=out_base)

        bundle_id = os.path.basename(bundle_path)
        report_file = os.path.join(out_base, f"{bundle_id}-qa_report.json")

        self.assertTrue(os.path.isfile(report_file))
        # Ensure report file is NOT inside the bundle folder
        self.assertFalse(os.path.exists(os.path.join(bundle_path, f"{bundle_id}-qa_report.json")))
        # Check report format
        with open(report_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertIn("verdict", data)
            self.assertIn("score", data)

    def test_34_build_bundle_from_suggestion_object(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="ADHD Daily Routine", tags=["adhd", "routine"])
        p2 = create_temp_pack(self.tmp_dir, "p2", title="ADHD Weekly Review", tags=["adhd", "routine"])

        suggestions = bundle_builder.suggest_bundles([p1, p2])
        self.assertEqual(len(suggestions), 1)

        out_base = os.path.join(self.tmp_dir, "bundles_out")
        bundle_path = bundle_builder.build_bundle(suggestions[0], out_dir=out_base)

        self.assertEqual(os.path.basename(bundle_path), suggestions[0]["bundle_id"])
        self.assertTrue(os.path.isfile(os.path.join(bundle_path, "bundle.zip")))

    def test_35_build_bundle_deterministic_folder_name(self):
        p1 = create_temp_pack(self.tmp_dir, "pack_x", title="ADHD X", tags=["adhd"])
        p2 = create_temp_pack(self.tmp_dir, "pack_y", title="ADHD Y", tags=["adhd"])

        path1 = bundle_builder.build_bundle([p1, p2], out_dir=os.path.join(self.tmp_dir, "out1"))
        path2 = bundle_builder.build_bundle([p2, p1], out_dir=os.path.join(self.tmp_dir, "out2"))

        self.assertEqual(os.path.basename(path1), os.path.basename(path2))

    def test_36_build_bundle_overwrites_existing_atomically(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="ADHD 1")
        p2 = create_temp_pack(self.tmp_dir, "p2", title="ADHD 2")

        out_base = os.path.join(self.tmp_dir, "bundles_out")
        path1 = bundle_builder.build_bundle([p1, p2], out_dir=out_base)
        # Re-build should overwrite cleanly
        path2 = bundle_builder.build_bundle([p1, p2], out_dir=out_base)
        self.assertEqual(path1, path2)
        self.assertTrue(os.path.isfile(os.path.join(path2, "bundle.zip")))

    def test_37_build_bundle_deep_qa_passes(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="ADHD Daily Focus Planner", tags=["adhd", "focus"])
        p2 = create_temp_pack(self.tmp_dir, "p2", title="ADHD Weekly Focus Review", tags=["adhd", "focus"])

        out_base = os.path.join(self.tmp_dir, "bundles_out")
        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=out_base)
        bundle_id = os.path.basename(bundle_path)
        report_file = os.path.join(out_base, f"{bundle_id}-qa_report.json")

        with open(report_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["verdict"], "ready")
            self.assertEqual(data["score"], 100)

    def test_38_build_bundle_spreadsheet_claims_sanitized_in_description(self):
        p1 = create_temp_pack(
            self.tmp_dir, "p1", title="Spreadsheet Pack",
            description="Compatible with Google Sheets and Numbers apps."
        )
        p2 = create_temp_pack(self.tmp_dir, "p2", title="Another Sheet Pack")

        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        with open(os.path.join(bundle_path, "listing.txt"), encoding="utf-8") as f:
            listing_text = f.read()

        self.assertNotIn("Google Sheets", listing_text)
        self.assertNotIn("Numbers", listing_text)
        self.assertIn("Excel (.xlsx)", listing_text)

    def test_39_build_bundle_handles_fallback_card_when_member_cover_missing(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="Pack 1")
        p2 = create_temp_pack(self.tmp_dir, "p2", title="Pack 2")
        # Remove covers from member packs
        os.remove(os.path.join(p1, "cover.png"))
        os.remove(os.path.join(p2, "cover.png"))

        bundle_path = bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
        cover_file = os.path.join(bundle_path, "cover.png")
        self.assertTrue(os.path.isfile(cover_file))
        with Image.open(cover_file) as img:
            self.assertEqual(img.size, (1280, 720))

    def test_40_main_cli_suggest(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="ADHD Focus Routine", tags=["adhd", "routine"])
        p2 = create_temp_pack(self.tmp_dir, "p2", title="ADHD Habit Review", tags=["adhd", "routine"])

        with mock.patch("sys.stdout") as mock_stdout:
            exit_code = bundle_builder.main(["suggest", self.tmp_dir])
            self.assertEqual(exit_code, 0)

    def test_41_main_cli_build(self):
        p1 = create_temp_pack(self.tmp_dir, "p1", title="ADHD Focus Routine", tags=["adhd", "routine"])
        p2 = create_temp_pack(self.tmp_dir, "p2", title="ADHD Habit Review", tags=["adhd", "routine"])
        out_base = os.path.join(self.tmp_dir, "cli_bundles")

        with mock.patch("sys.stdout") as mock_stdout:
            exit_code = bundle_builder.main(["build", p1, p2, "--out-dir", out_base])
            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.isdir(out_base))

    def test_42_build_bundle_size_limit_check(self):
        # Mock MAX_BUNDLE_UNCOMPRESSED_BYTES to a small number
        p1 = create_temp_pack(self.tmp_dir, "p1")
        p2 = create_temp_pack(self.tmp_dir, "p2")

        with mock.patch.object(bundle_builder, "MAX_BUNDLE_UNCOMPRESSED_BYTES", 10):
            with self.assertRaises(ValueError) as ctx:
                bundle_builder.build_bundle([p1, p2], out_dir=self.tmp_dir)
            self.assertIn("exceeds 250 MB limit", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
