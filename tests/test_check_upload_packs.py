import pathlib
import subprocess
import sys
import zipfile


def make_pack(
    pack_dir: pathlib.Path,
    zip_files: dict,
    listing_content: str,
    checklist_content: str = "ok",
):
    pack_dir.mkdir(parents=True, exist_ok=True)
    zip_path = pack_dir / "product.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for fname, data in zip_files.items():
            zf.writestr(fname, data)

    (pack_dir / "listing.txt").write_text(listing_content, encoding="utf-8")
    (pack_dir / "checklist.txt").write_text(checklist_content, encoding="utf-8")


def run_checker(target_folder: pathlib.Path):
    script = pathlib.Path("scripts/check_upload_packs.py").resolve()
    return subprocess.run(
        [sys.executable, str(script), str(target_folder)],
        capture_output=True,
        text=True,
    )


def test_good_pack_ready(tmp_path):
    listing = (
        "Title: Daily Planner\n"
        "Description: Beautiful daily planner for productivity.\n"
        "Tags: planner, daily, schedule\n"
        "What you get: High quality printable PDF file.\n"
    )
    make_pack(
        tmp_path / "good_pack",
        {"planner.pdf": b"%PDF-1.4 mock pdf content"},
        listing,
    )

    result = run_checker(tmp_path)
    assert result.returncode == 0
    assert "good_pack | 1 | yes | no | yes | no | yes" in result.stdout.strip()


def test_money_in_listing_not_ready(tmp_path):
    listing = (
        "Title: Daily Planner\n"
        "Description: Price is $10 for the entire bundle.\n"
        "Tags: planner, daily, schedule\n"
        "What you get: High quality printable PDF file.\n"
    )
    make_pack(
        tmp_path / "money_pack",
        {"planner.pdf": b"%PDF-1.4 mock pdf content"},
        listing,
    )

    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "money_pack | 1 | yes | no | yes | yes | no" in result.stdout.strip()


def test_zip_with_listing_md_not_ready(tmp_path):
    listing = (
        "Title: Daily Planner\n"
        "Description: Beautiful daily planner.\n"
        "Tags: planner, daily, schedule\n"
        "What you get: High quality printable PDF file.\n"
    )
    make_pack(
        tmp_path / "listing_md_pack",
        {
            "planner.pdf": b"%PDF-1.4 mock pdf content",
            "listing.md": b"# Listing details",
        },
        listing,
    )

    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "listing_md_pack | 2 | yes | no | yes | no | no" in result.stdout.strip()


def test_missing_what_you_get_not_ready(tmp_path):
    listing = (
        "Title: Daily Planner\n"
        "Description: Beautiful daily planner.\n"
        "Tags: planner, daily, schedule\n"
    )
    make_pack(
        tmp_path / "missing_wyg_pack",
        {"planner.pdf": b"%PDF-1.4 mock pdf content"},
        listing,
    )

    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "missing_wyg_pack | 1 | yes | no | no | no | no" in result.stdout.strip()


def test_xlsx_and_money_variations(tmp_path):
    listing_inr = (
        "Title: Budget Spreadsheet\n"
        "Description: Cost: 500 INR\n"
        "Tags: finance, excel\n"
        "What you get: Excel sheet.\n"
    )
    make_pack(
        tmp_path / "xlsx_pack",
        {"tracker.xlsx": b"mock xlsx content"},
        listing_inr,
    )

    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "xlsx_pack | 1 | no | yes | yes | yes | no" in result.stdout.strip()


# unittest adapter: the robot's CI runs `python -m unittest`, so run the pytest-style tests here too.
import tempfile
import unittest


class CheckUploadPacksTests(unittest.TestCase):
    pass


def _make_case(fn):
    def case(self):
        with tempfile.TemporaryDirectory() as d:
            fn(pathlib.Path(d))
    return case


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        setattr(CheckUploadPacksTests, _name, _make_case(_fn))
