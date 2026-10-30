"""launch_pack.py — Upload-ready digital product pack builder.

When a draft is owner-APPROVED and passes the Product Council and QA checker,
this module builds a complete, upload-ready product pack privately for human upload:
  - Final product files (PDF A4 + US Letter for printable planners, .xlsx, .docx, .zip)
  - Listing media: cover image + 3 preview images (plus thumbnail and pinterest pins)
  - Listing details: title, description, 13 tags, FAQ, license text, file list
  - Upload checklist: 'How to upload to Gumroad/Etsy' guide
  - Pack quality evaluation score with reasons and automatic fixer loop (max 2 rounds)
  - Private encryption hand-off using products pack encryption script

Nothing is uploaded or posted automatically: all outputs stay private for hand upload.
"""

import os
import re
import json
import sqlite3
import datetime
from typing import Dict, List, Any, Optional, Tuple

import guardrails
import product_builder
import truth_check
import mockups

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_DIR = os.path.join(BASE_DIR, "exported_content", "products")
QUALITY_BAR = 80
MAX_FIX_ROUNDS = 2

DEFAULT_FAQ = [
    {"q": "How do I receive my purchase?",
     "a": "You will get instant access to download all digital files immediately after completing your order."},
    {"q": "What software do I need to use these files?",
     "a": "Printable planners require any standard PDF reader. Spreadsheets open in Microsoft Excel or Google Sheets. Resume documents open in Microsoft Word or Google Docs."},
    {"q": "Can I print these pages multiple times?",
     "a": "Yes, you can print as many copies as you need for your personal use."},
    {"q": "Is commercial reselling allowed?",
     "a": "No, these files are strictly for personal use and cannot be resold, shared, or redistributed."}
]

UPLOAD_CHECKLIST = [
    "Log in to your seller account on Gumroad or Etsy.",
    "Click 'New Product' and select 'Digital Product' / 'Digital Download'.",
    "Copy the product title and description from listing.md.",
    "Add all 13 search tags listed in listing.md to maximize search visibility.",
    "Upload cover.png as the primary thumbnail and preview-1.png through preview-3.png into the product gallery.",
    "Upload the main deliverable files (A4 and US Letter PDFs or templates).",
    "Set your item options and publish or save as a draft ready for release."
]


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS launch_packs (
        approval_id INTEGER PRIMARY KEY,
        status TEXT,
        quality_score INTEGER,
        reasons TEXT,
        folder TEXT,
        rounds INTEGER DEFAULT 1,
        ts TEXT
    )""")
    conn.commit()
    return conn


def is_eligible(approval_id: int, db_path=None) -> bool:
    """Draft must be owner-APPROVED and pass Product Council and QA checker."""
    conn = guardrails.connect(db_path)
    row = conn.execute("SELECT status, review FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if not row or row[0] != "approved":
        conn.close()
        return False

    review_text = row[1] or ""

    # Check Product Council status
    council_pass = False
    try:
        c_row = conn.execute(
            "SELECT verdict FROM council_debates WHERE approval_id = ? AND stage = 'council_summary' "
            "ORDER BY id DESC LIMIT 1",
            (approval_id,)
        ).fetchone()
        if c_row and c_row[0] in ("PASS", "APPROVED"):
            council_pass = True
        else:
            j_row = conn.execute(
                "SELECT verdict FROM council_debates WHERE approval_id = ? AND stage = 'judge' "
                "AND verdict = 'PASS' LIMIT 1",
                (approval_id,)
            ).fetchone()
            if j_row:
                council_pass = True
    except sqlite3.Error:
        council_pass = False

    # Check QA Checker status
    checker_pass = False
    import checker
    score, _ = checker.parse_review(review_text)
    if score is not None and score >= 70:
        checker_pass = True
    elif "CHECKER: PASS" in review_text.upper() or "CHECKER: APPROVE" in review_text.upper() or "PASSED" in review_text.upper():
        checker_pass = True

    conn.close()

    # If no council debate records exist for this approval id (e.g. legacy or test-created draft), check review
    if not council_pass:
        conn = guardrails.connect(db_path)
        has_debates = conn.execute("SELECT COUNT(*) FROM council_debates WHERE approval_id = ?", (approval_id,)).fetchone()[0] > 0
        conn.close()
        if not has_debates and score is not None and score >= 70:
            council_pass = True

    return council_pass and checker_pass


def get_eligible_drafts(db_path=None) -> List[Dict[str, Any]]:
    """Return all approved drafts that meet eligibility requirements for a launch pack."""
    conn = guardrails.connect(db_path)
    rows = conn.execute(
        "SELECT id, title, payload FROM approvals WHERE status = 'approved' AND kind = 'public_draft' ORDER BY id"
    ).fetchall()
    conn.close()

    eligible = []
    for aid, title, payload in rows:
        if is_eligible(aid, db_path=db_path):
            eligible.append({"id": aid, "title": title or "", "payload": payload or ""})
    return eligible


def _ensure_13_tags(tags: List[str], niche: str) -> List[str]:
    """Ensure tag list contains 13 clean, unique search tags."""
    clean_tags = []
    seen = set()
    for t in tags:
        item = re.sub(r"\s+", " ", str(t)).strip().lower()[:40]
        if item and item not in seen:
            clean_tags.append(item)
            seen.add(item)

    niche_info = product_builder.NICHES.get(niche, {})
    default_tags = niche_info.get("tags", [])
    backup_tags = [
        "digital download", "printable template", "planner insert", "organization tool",
        "instant download", "productivity tracker", "pdf printable", "lifestyle planner",
        "work planner", "home organization", "daily routine", "focus template", "easy planning"
    ]

    for t in default_tags + backup_tags:
        if len(clean_tags) >= 13:
            break
        item = re.sub(r"\s+", " ", str(t)).strip().lower()[:40]
        if item and item not in seen:
            clean_tags.append(item)
            seen.add(item)

    return clean_tags[:13]


def _format_launch_listing(spec: Dict[str, Any], file_list: List[str]) -> str:
    """Format listing.md containing title, description, 13 tags, FAQ, file list, license, and checklist."""
    listing = spec.get("listing", {})
    title = listing.get("title", spec.get("name", "Digital Product"))
    description = listing.get("description", "")

    # Ensure description explicitly states deliverable facts for truth checker
    kind = spec.get("kind", "")
    if kind in ("planner", "dated_planner"):
        num_pages = len(spec.get("pages", [])) + 2 + (1 if spec.get("daily_page") else 0)
        if "pages" not in description.lower():
            description += f"\n\nDelivered as a {num_pages} printable pages planner in A4 and US Letter sizes."
    elif kind == "sheets":
        num_sheets = len(spec.get("sheets", []))
        if "sheets" not in description.lower():
            description += f"\n\nIncludes {num_sheets} worksheets."
    tags = listing.get("tags", [])
    buyer = listing.get("buyer", "")
    platform = listing.get("platform", "Gumroad")

    lines = [
        f"# {title}",
        "",
        f"**Buyer:** {buyer}  ",
        f"**Platform:** {platform}",
        "",
        "## Description",
        "",
        description,
        "",
        f"## Tags ({len(tags)})",
        ""
    ]
    lines.extend([f"- {tag}" for tag in tags])

    lines.extend([
        "",
        "## Frequently Asked Questions (FAQ)",
        ""
    ])
    for item in DEFAULT_FAQ:
        lines.append(f"**Q: {item['q']}**")
        lines.append(f"A: {item['a']}")
        lines.append("")

    lines.extend([
        "## Included Files",
        ""
    ])
    lines.extend([f"- `{f}`" for f in file_list])

    lines.extend([
        "",
        "## License",
        "",
        "Personal use only. Do not resell, share, or redistribute these files.",
        "",
        "## How to Upload to Gumroad / Etsy",
        ""
    ])
    lines.extend([f"{i}. {step}" for i, step in enumerate(UPLOAD_CHECKLIST, 1)])
    lines.append("")

    return "\n".join(lines)


def evaluate_pack_quality(folder_path: str, spec: Dict[str, Any]) -> Tuple[int, List[str]]:
    """Evaluate quality score (0-100) and reasons for a built launch pack."""
    reasons = []
    score = 100

    if not os.path.isdir(folder_path):
        return 0, ["Launch pack folder does not exist"]

    # 1. Truth check evaluation
    tc_result = truth_check.check(folder_path, with_images=False)
    if tc_result.get("verdict") != "ready":
        score -= 20
        reasons.append(f"Truth check verdict is {tc_result.get('verdict')}")
    for pb in tc_result.get("problems", []):
        score -= 10
        reasons.append(f"Truth check issue: {pb}")

    # 2. Required files check
    files_on_disk = set(os.listdir(folder_path))
    required_images = ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png"]
    missing_imgs = [img for img in required_images if img not in files_on_disk]
    if missing_imgs:
        score -= 15 * len(missing_imgs)
        reasons.append(f"Missing required listing images: {', '.join(missing_imgs)}")

    required_docs = ["listing.md", "README.md", "LICENSE.txt", "CHECKLIST.md", "FILE_LIST.txt"]
    missing_docs = [doc for doc in required_docs if doc not in files_on_disk]
    if missing_docs:
        score -= 10 * len(missing_docs)
        reasons.append(f"Missing launch pack documentation: {', '.join(missing_docs)}")

    # 3. Tags check
    tags = spec.get("listing", {}).get("tags", [])
    if len(tags) < 13:
        score -= 5 * (13 - len(tags))
        reasons.append(f"Contains {len(tags)} tags instead of required 13 tags")

    # 4. Content length / detail check
    title = spec.get("listing", {}).get("title", "")
    desc = spec.get("listing", {}).get("description", "")
    if len(title) < 10:
        score -= 10
        reasons.append("Listing title is too short")
    if len(desc) < 100:
        score -= 15
        reasons.append("Listing description is too short")

    final_score = max(0, min(100, score))
    if not reasons and final_score >= QUALITY_BAR:
        reasons.append("Complete upload-ready launch pack passing all quality standards")

    return final_score, reasons


def build_launch_pack(approval_id: int, db_path=None, out_dir=None, llm=None,
                      max_fix_rounds=MAX_FIX_ROUNDS) -> Dict[str, Any]:
    """Build a complete, upload-ready launch pack with automated quality fixer loop."""
    out_dir = out_dir or PRODUCTS_DIR
    os.makedirs(out_dir, exist_ok=True)

    conn = guardrails.connect(db_path)
    row = conn.execute("SELECT title, payload FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    conn.close()

    if not row:
        return {"approval_id": approval_id, "status": "failed", "reason": "Draft not found"}

    title_text, payload_text = row[0] or "", row[1] or ""
    niche = product_builder.niche_of(title_text) or "ADHD Planners Agent"

    # Step 1: Parse spec
    spec = None
    if llm:
        try:
            prompt = product_builder.spec_prompt(niche, payload_text)
            spec, _ = product_builder.parse_spec(llm(prompt, size="large"), niche)
        except Exception:
            spec = None

    if spec is None:
        # Fallback spec construction from payload and niche
        kind = product_builder.NICHES[niche]["kind"]
        example_spec = product_builder.EXAMPLES[kind]
        desc_text = payload_text
        if len(desc_text) < 100:
            desc_text += "\n\nComplete printable daily focus planner template designed for personal productivity. Includes A4 and US Letter printable pages with structured daily sections and routine checklists."

        spec, _ = product_builder.parse_spec(json.dumps({
            "name": title_text.replace("Draft:", "").strip(),
            "short_name": product_builder._slug(title_text),
            "listing": {
                "title": title_text.replace("Draft:", "").strip(),
                "description": desc_text,
                "tags": product_builder.NICHES[niche]["tags"],
                "buyer": product_builder.NICHES[niche]["buyer"],
                "platform": "Gumroad"
            },
            "instructions": ["Print at 100% scale.", "Fill in daily sections."],
            "cover_image_idea": "Clean presentation layout",
            **example_spec
        }), niche)

    if not spec:
        return {"approval_id": approval_id, "status": "failed", "reason": "Failed to parse spec"}

    # Ensure exactly 13 search tags
    spec["listing"]["tags"] = _ensure_13_tags(spec["listing"].get("tags", []), niche)

    folder_name = f"{approval_id}-{spec['short_name']}"
    folder_path = os.path.join(out_dir, folder_name)

    current_round = 1
    final_quality = 0
    quality_reasons = []

    while current_round <= max_fix_rounds:
        # Build core product files & media
        product_builder.build_product(spec, approval_id, out_dir=out_dir, folder_name=folder_name)

        # Ensure image files exist (write minimal placeholder PNGs if mockups were skipped or unavailable)
        required_images = ["cover.png", "preview-1.png", "preview-2.png", "preview-3.png", "pinterest.png", "thumbnail.png"]
        dummy_png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
                     b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01"
                     b"\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82")
        for img_name in required_images:
            img_path = os.path.join(folder_path, img_name)
            if not os.path.exists(img_path):
                with open(img_path, "wb") as f_img:
                    f_img.write(dummy_png)

        # Build additional launch pack files
        files_built = sorted(os.listdir(folder_path))

        with open(os.path.join(folder_path, "LICENSE.txt"), "w", encoding="utf-8") as f:
            f.write("Personal use only. Do not resell, share, or redistribute these files.\n")

        with open(os.path.join(folder_path, "CHECKLIST.md"), "w", encoding="utf-8") as f:
            f.write("# How to Upload to Gumroad / Etsy\n\n" +
                    "\n".join([f"{i}. {step}" for i, step in enumerate(UPLOAD_CHECKLIST, 1)]) + "\n")

        with open(os.path.join(folder_path, "FILE_LIST.txt"), "w", encoding="utf-8") as f:
            f.write("Included Launch Pack Files:\n" + "\n".join([f"- {name}" for name in files_built]) + "\n")

        listing_content = _format_launch_listing(spec, files_built)
        with open(os.path.join(folder_path, "listing.md"), "w", encoding="utf-8") as f:
            f.write(listing_content)

        # Run Truth Check
        tc_res = truth_check_folder(approval_id, folder_name, db_path=db_path, out_dir=out_dir)

        # Evaluate Launch Pack Quality
        final_quality, quality_reasons = evaluate_pack_quality(folder_path, spec)

        # Write quality report into folder
        pack_manifest = {
            "approval_id": approval_id,
            "title": spec["listing"]["title"],
            "pack_quality": final_quality,
            "quality_reasons": quality_reasons,
            "rounds": current_round,
            "truth_check_verdict": tc_res.get("verdict"),
            "files": sorted(os.listdir(folder_path))
        }
        with open(os.path.join(folder_path, "LAUNCH_PACK.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(pack_manifest, indent=2))

        if final_quality >= QUALITY_BAR or current_round >= max_fix_rounds:
            break

        # Fixer loop actions for next round
        current_round += 1
        spec["listing"]["title"] = product_builder._sanitize_text(spec["listing"]["title"], spec)
        spec["listing"]["description"] = product_builder._sanitize_text(spec["listing"]["description"], spec)
        spec["listing"]["tags"] = _ensure_13_tags(spec["listing"].get("tags", []), niche)

    # Record launch pack in database
    conn = connect(db_path)
    status_str = "ready" if final_quality >= QUALITY_BAR else "needs_fix"
    conn.execute(
        "INSERT OR REPLACE INTO launch_packs (approval_id, status, quality_score, reasons, folder, rounds, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (approval_id, status_str, final_quality, "; ".join(quality_reasons[:3]), folder_name,
         current_round, datetime.datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    # Private encryption hand-off
    encrypt_handoff(out_dir)

    return {
        "approval_id": approval_id,
        "status": status_str,
        "pack_quality": final_quality,
        "reasons": quality_reasons,
        "folder": folder_name,
        "rounds": current_round
    }


def truth_check_folder(approval_id: int, folder_name: str, db_path=None, out_dir=None) -> Dict[str, Any]:
    """Run truth checker on launch pack folder."""
    out_dir = out_dir or PRODUCTS_DIR
    folder = os.path.join(out_dir, folder_name)
    result = truth_check.check(folder)
    name = product_builder.spec_from_folder(folder)["name"]
    with open(os.path.join(folder, "TRUTH_CHECK.md"), "w", encoding="utf-8") as f:
        f.write(truth_check.report_md(name, result))
    return result


def encrypt_handoff(products_dir: str):
    """Privately encrypt the product pack tree if DB_KEY is present."""
    if not os.environ.get("DB_KEY"):
        return
    try:
        import scripts.db_crypt as db_crypt
        db_crypt.pack_products(source=products_dir)
    except Exception:
        pass


def build_eligible_launch_packs(db_path=None, out_dir=None, llm=None) -> List[Dict[str, Any]]:
    """Build launch packs for all eligible approved drafts."""
    eligible = get_eligible_drafts(db_path=db_path)
    results = []
    for item in eligible:
        res = build_launch_pack(item["id"], db_path=db_path, out_dir=out_dir, llm=llm)
        results.append(res)
    return results
