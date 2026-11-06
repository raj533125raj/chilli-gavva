import pathlib
import re
import sys
import zipfile


def check_money_amount(text: str) -> bool:
    patterns = [
        r"\$\s*\d",
        r"\b\d+(?:\.\d+)?\s*(?:USD|INR)\b",
        r"(?:Rs\.?|₹)\s*\d",
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def check_listing_sections(text: str):
    has_title = "Title:" in text
    has_desc = "Description:" in text
    has_tags = "Tags:" in text
    has_wyg = "What you get" in text
    return has_title, has_desc, has_tags, has_wyg


def check_product_zip(zip_path: pathlib.Path):
    zip_opens = False
    files_count = 0
    has_pdf = False
    has_xlsx = False
    has_forbidden = False
    buyer_files_count = 0

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if zf.testzip() is None:
                zip_opens = True
                for info in zf.infolist():
                    if info.is_dir() or info.filename.endswith("/"):
                        continue

                    filename = info.filename
                    base_name = pathlib.Path(filename).name
                    base_lower = base_name.lower()

                    if base_name.startswith(".") or filename.startswith("__MACOSX/"):
                        continue

                    files_count += 1

                    if base_lower == "listing.md" or base_lower.endswith(
                        (".enc", ".db", ".json")
                    ):
                        has_forbidden = True
                    else:
                        buyer_files_count += 1

                    if base_lower.endswith(".pdf"):
                        has_pdf = True
                    if base_lower.endswith(".xlsx"):
                        has_xlsx = True
    except Exception:
        zip_opens = False

    return {
        "zip_opens": zip_opens,
        "files_count": files_count,
        "has_pdf": has_pdf,
        "has_xlsx": has_xlsx,
        "has_forbidden": has_forbidden,
        "buyer_files_count": buyer_files_count,
    }


def find_pack_folders(base_folder: pathlib.Path):
    if not base_folder.is_dir():
        return []

    direct_packs = [
        p
        for p in sorted(base_folder.iterdir())
        if p.is_dir()
        and (p / "product.zip").is_file()
        and (p / "listing.txt").is_file()
        and (p / "checklist.txt").is_file()
    ]
    if direct_packs:
        return direct_packs

    if (
        (base_folder / "product.zip").is_file()
        and (base_folder / "listing.txt").is_file()
        and (base_folder / "checklist.txt").is_file()
    ):
        return [base_folder]

    packs = []
    for p in sorted(base_folder.rglob("*")):
        if (
            p.is_dir()
            and (p / "product.zip").is_file()
            and (p / "listing.txt").is_file()
            and (p / "checklist.txt").is_file()
        ):
            if not any(parent in packs for parent in p.parents):
                packs.append(p)
    return packs


def check_pack(pack_dir: pathlib.Path) -> bool:
    zip_path = pack_dir / "product.zip"
    listing_path = pack_dir / "listing.txt"

    zip_info = check_product_zip(zip_path)

    listing_text = ""
    try:
        listing_text = listing_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass

    has_title, has_desc, has_tags, has_wyg = check_listing_sections(listing_text)
    money_found = check_money_amount(listing_text)

    ready = (
        zip_info["zip_opens"]
        and zip_info["buyer_files_count"] >= 1
        and not zip_info["has_forbidden"]
        and has_title
        and has_desc
        and has_tags
        and has_wyg
        and not money_found
    )

    has_pdf_str = "yes" if zip_info["has_pdf"] else "no"
    has_xlsx_str = "yes" if zip_info["has_xlsx"] else "no"
    has_wyg_str = "yes" if has_wyg else "no"
    money_str = "yes" if money_found else "no"
    ready_str = "yes" if ready else "no"

    row = (
        f"{pack_dir.name} | "
        f"{zip_info['files_count']} | "
        f"{has_pdf_str} | "
        f"{has_xlsx_str} | "
        f"{has_wyg_str} | "
        f"{money_str} | "
        f"{ready_str}"
    )
    print(row)
    return ready


def main():
    if len(sys.argv) < 2:
        print("Usage: check_upload_packs.py <folder>", file=sys.stderr)
        sys.exit(1)

    base_folder = pathlib.Path(sys.argv[1])
    if not base_folder.exists():
        print(f"Error: {base_folder} does not exist", file=sys.stderr)
        sys.exit(1)

    packs = find_pack_folders(base_folder)
    if not packs:
        sys.exit(1)

    all_ready = True
    for pack in packs:
        is_ready = check_pack(pack)
        if not is_ready:
            all_ready = False

    sys.exit(0 if all_ready else 1)


if __name__ == "__main__":
    main()
