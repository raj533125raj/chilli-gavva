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
import datetime
from typing import Dict, List, Any, Optional, Tuple

import guardrails
import product_builder
import truth_check

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
    import checker
    import product_council
    item = guardrails.get_approval(approval_id, db_path)
    summary = product_council.get_council_summary(approval_id, db_path)
    return bool(item and item["status"] == "approved" and item["kind"] == "public_draft"
                and approval_id not in product_builder.OWNER_HOLDS
                and summary and summary["verdict"] in ("PASS", "APPROVED")
                and checker.review_passes(item.get("review")))


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
    kind = product_builder.NICHES.get(niche, {}).get("kind", "")
    for t in tags:
        item = re.sub(r"\s+", " ", str(t)).strip().lower()[:40]
        item = product_builder._sanitize_text(item, {"kind": kind})
        if item and item not in seen:
            clean_tags.append(item)
            seen.add(item)

    niche_info = product_builder.NICHES.get(niche, {})
    default_tags = niche_info.get("tags", [])
    backup_tags = [
        "digital download", "organization template", "personal use", "downloadable template",
        "planning tool", "structured template", "simple layout", "everyday organization",
        "practical template", "easy planning", "organized workflow", "ready made template",
        "personal organization"
    ]

    for t in default_tags + backup_tags:
        if len(clean_tags) >= 13:
            break
        item = re.sub(r"\s+", " ", str(t)).strip().lower()[:40]
        item = product_builder._sanitize_text(item, {"kind": kind})
        if item and item not in seen:
            clean_tags.append(item)
            seen.add(item)

    return clean_tags[:13]


def _format_launch_listing(spec: Dict[str, Any], file_list: List[str], facts: Optional[Dict[str, Any]] = None) -> str:
    """Format listing.md containing title, description, 13 tags, FAQ, file list, license, and checklist."""
    listing = spec.get("listing", {})
    title = product_builder._sanitize_text(listing.get("title", spec.get("name", "Digital Product")), spec, facts=facts)
    description = product_builder._sanitize_text(listing.get("description", ""), spec, facts=facts)

    # Ensure description explicitly states deliverable facts for truth checker
    kind = spec.get("kind", "")
    if kind in ("planner", "dated_planner"):
        num_pages = facts["pages"] if facts and "pages" in facts else (len(spec.get("pages", [])) + 2 + (1 if spec.get("daily_page") else 0))
        description += f"\n\nIncludes {num_pages} printable pages in A4 and US Letter sizes."
    elif kind in ("sheets", "resume"):
        num_sheets = len(facts["sheets"]) if facts and "sheets" in facts else len(spec.get("sheets", []))
        description += f"\n\nIncludes {num_sheets} worksheets."
    elif kind == "notion" and facts:
        description += f"\n\nIncludes {len(facts['md']) + len(facts['csv'])} import pages and tables."
    if facts:
        # Evidence comes from the built files, giving the truth checker concrete
        # parts to verify even when the model's description is very general.
        parts = [f"{name} sheet" for name in facts["sheets"]
                 if "dashboard" not in name.lower() or facts["kind"] == "sheets"]
        parts += [os.path.splitext(name)[0].replace("-", " ").title() + " page"
                  for name in facts["md"]]
        parts += [os.path.splitext(name)[0].replace("-", " ").title() + " database"
                  for name in facts["csv"]]
        if parts:
            description += "\n\nVerified contents: " + "; ".join(parts) + "."
        if facts["formulas"]:
            description += "\n\nThe workbook includes formulas."
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
    invalid_imgs = []
    for name in required_images:
        if name in missing_imgs:
            continue
        try:
            from PIL import Image
            with Image.open(os.path.join(folder_path, name)) as img:
                if min(img.size) < 100:
                    raise ValueError("Placeholder image")
                img.verify()
        except Exception:
            invalid_imgs.append(name)
    if missing_imgs:
        score -= 15 * len(missing_imgs)
        reasons.append(f"Missing required listing images: {', '.join(missing_imgs)}")
    if invalid_imgs:
        reasons.append(f"Invalid or placeholder listing images: {', '.join(invalid_imgs)}")

    deliverables = [name for name in files_on_disk if name.endswith((".pdf", ".xlsx", ".docx", ".zip"))]
    if not deliverables:
        reasons.append("Missing product deliverable files")

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
    if missing_imgs or invalid_imgs or missing_docs or not deliverables:
        final_score = min(final_score, QUALITY_BAR - 1)
    if not reasons and final_score >= QUALITY_BAR:
        reasons.append("Complete upload-ready launch pack passing all quality standards")

    return final_score, reasons


def build_launch_pack(approval_id: int, db_path=None, out_dir=None, llm=None,
                      max_fix_rounds=MAX_FIX_ROUNDS, spec=None, existing_folder=None) -> Dict[str, Any]:
    """Build a complete, upload-ready launch pack with automated quality fixer loop."""
    out_dir = out_dir or PRODUCTS_DIR
    if not is_eligible(approval_id, db_path):
        return {"approval_id": approval_id, "status": "blocked",
                "reason": "Owner approval, council PASS and checker PASS are required"}
    os.makedirs(out_dir, exist_ok=True)

    conn = guardrails.connect(db_path)
    row = conn.execute("SELECT title, payload FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    conn.close()

    if not row:
        return {"approval_id": approval_id, "status": "failed", "reason": "Draft not found"}

    title_text, payload_text = row[0] or "", row[1] or ""
    niche = product_builder.niche_of(title_text)
    if not niche:
        return {"approval_id": approval_id, "status": "blocked", "reason": "Unsupported product niche"}

    if existing_folder:
        if os.path.basename(existing_folder) != existing_folder:
            raise ValueError("Invalid product folder")
        folder = os.path.join(out_dir, existing_folder)
        spec = product_builder.spec_from_folder(folder)
        with open(os.path.join(folder, "listing.md"), encoding="utf-8") as stream:
            listing = stream.read()
        description = re.search(r"(?s)## Description\s*\n(.*?)(?=\n## |\Z)", listing)
        title = listing.splitlines()[0].lstrip("# ")
        spec.update(short_name=existing_folder.split("-", 1)[-1], niche=niche)
        spec["listing"] = {"title": title, "description": description[1].strip() if description else "",
                           "tags": re.findall(r"(?m)^- (.+)$", listing.split("## Tags", 1)[-1].split("\n## ", 1)[0])
                           if "## Tags" in listing else [],
                           "buyer": product_builder.NICHES[niche]["buyer"],
                           "platform": "Gumroad"}

    # Step 1: Parse spec
    if spec is None:
        import workforce
        llm = llm or workforce.default_llm(db_path)
        try:
            prompt = product_builder.spec_prompt(niche, payload_text)
            spec, _ = product_builder.parse_spec(llm(prompt, size="large"), niche)
        except Exception:
            spec = None

    if not spec:
        return {"approval_id": approval_id, "status": "failed", "reason": "Failed to parse spec"}

    # Ensure exactly 13 search tags
    spec["listing"]["tags"] = _ensure_13_tags(spec["listing"].get("tags", []), niche)

    folder_name = existing_folder or f"{approval_id}-{spec['short_name']}"
    folder_path = os.path.join(out_dir, folder_name)

    current_round = 1
    final_quality = 0
    quality_reasons = []

    while current_round <= max_fix_rounds:
        # Build core product files & media
        if not existing_folder:
            product_builder.build_product(spec, approval_id, out_dir=out_dir, folder_name=folder_name)

        # Build additional launch pack files
        files_built = sorted(os.path.relpath(os.path.join(base, name), folder_path)
                             for base, _, names in os.walk(folder_path) for name in names)

        with open(os.path.join(folder_path, "LICENSE.txt"), "w", encoding="utf-8") as f:
            f.write("Personal use only. Do not resell, share, or redistribute these files.\n")

        with open(os.path.join(folder_path, "CHECKLIST.md"), "w", encoding="utf-8") as f:
            f.write("# How to Upload to Gumroad / Etsy\n\n" +
                    "\n".join([f"{i}. {step}" for i, step in enumerate(UPLOAD_CHECKLIST, 1)]) + "\n")

        files_built = sorted(set(files_built) | {"FILE_LIST.txt", "CHECKLIST.md", "TRUTH_CHECK.md", "LAUNCH_PACK.json"})
        with open(os.path.join(folder_path, "FILE_LIST.txt"), "w", encoding="utf-8") as f:
            f.write("Included Launch Pack Files:\n" + "\n".join([f"- {name}" for name in files_built]) + "\n")

        facts = truth_check.facts(folder_path)
        listing_content = _format_launch_listing(spec, files_built, facts=facts)
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
            "files": files_built
        }
        with open(os.path.join(folder_path, "LAUNCH_PACK.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(pack_manifest, indent=2))

        if final_quality >= QUALITY_BAR and tc_res.get("verdict") == "ready":
            break
        if current_round >= max_fix_rounds:
            break

        # Fixer loop actions for next round
        current_round += 1
        spec["listing"]["title"] = product_builder._sanitize_text(spec["listing"]["title"], spec, facts=facts)
        spec["listing"]["description"] = product_builder._sanitize_text(spec["listing"]["description"], spec, facts=facts)
        spec["listing"]["tags"] = _ensure_13_tags(spec["listing"].get("tags", []), niche)

    # Record launch pack in database
    conn = connect(db_path)
    status_str = "ready" if (final_quality >= QUALITY_BAR and tc_res.get("verdict") == "ready") else "needs_fix"
    conn.execute(
        "INSERT OR REPLACE INTO launch_packs (approval_id, status, quality_score, reasons, folder, rounds, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (approval_id, status_str, final_quality, "; ".join(quality_reasons[:3]), folder_name,
         current_round, datetime.datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

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


def encrypt_handoff(products_dir: str, destination=None):
    """Encrypt privately; callers must handle failure before announcing readiness."""
    from scripts import db_crypt
    if destination is None:
        destination = (os.path.join(BASE_DIR, db_crypt.PRODUCTS_ENCRYPTED)
                       if os.path.realpath(products_dir) == os.path.realpath(PRODUCTS_DIR)
                       else os.path.join(os.path.dirname(os.path.abspath(products_dir)),
                                         "products.tar.gz.enc"))
    db_crypt.pack_products(source=products_dir, dst=destination)
    return destination


def build_eligible_launch_packs(db_path=None, out_dir=None, llm=None) -> List[Dict[str, Any]]:
    """Build launch packs for all eligible approved drafts."""
    import product_pipeline
    return product_pipeline.run_pending(db_path=db_path, out_dir=out_dir, llm=llm)
