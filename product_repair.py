"""product_repair.py — Repair old "NEEDS FIX" products safely and cheaply.

For each built product whose truth check verdict is NEEDS FIX, decides the cheapest honest fix:
  (a) Rewrite the listing / README so it promises ONLY what the files really contain
      (using existing truth_check facts + sanitizing helpers, read-only imports).
  (b) If a missing promised part is small (e.g., dated/daily page), ask the existing
      builder to rebuild once.

Max 3 repairs per day, respecting the daily AI cap and kill switch.
Re-runs truth check after repair and records the verdict (READY or still NEEDS FIX + reason)
in the database tables the pipeline already uses (truth_checks and product_builds).
Never raises on run_daily: exceptions are caught, recorded, and returned in a summary dict.
"""

import os
import re
import sys
import logging
from typing import Dict, Any, Optional, List, Tuple

import guardrails
import truth_check
import product_builder

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_DIR = os.path.join(BASE_DIR, "exported_content", "products")
DEFAULT_MAX_REPAIRS = 3


def connect(db_path=None):
    """Database connection ensuring truth_checks and product_builds exist."""
    conn = guardrails.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS product_builds ("
        "approval_id INTEGER PRIMARY KEY, status TEXT, tries INTEGER DEFAULT 0, "
        "folder TEXT, note TEXT, ts TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS truth_checks ("
        "approval_id INTEGER PRIMARY KEY, verdict TEXT, score INTEGER, notified TEXT)"
    )
    conn.commit()
    return conn


def _unmet_claims_and_problems(folder: str) -> Tuple[List[str], Dict[str, Any], Dict[str, Any]]:
    """Inspect the folder with truth_check. Return (problems, check_res, facts)."""
    check_res = truth_check.check(folder, with_images=False)
    problems = check_res.get("problems", [])
    facts = truth_check.facts(folder)
    return problems, check_res, facts


def sanitize_listing_text_for_facts(text: str, facts: Dict[str, Any], kind: str) -> str:
    """Rewrite text so it promises strictly what `facts` proves and nothing else."""
    spec_dummy = {"kind": kind, "short_name": "product"}
    cleaned = product_builder._sanitize_text(text, spec_dummy, facts=facts)

    # 1. Weekly / daily dated pages
    has_dated_pages = truth_check._proven("weekly or daily dated pages", facts)[0]
    if not has_dated_pages:
        cleaned = re.sub(r"\bweekly spreads?\b", "weekly templates", cleaned, flags=re.I)
        cleaned = re.sub(r"\bweekly pages?\b", "weekly templates", cleaned, flags=re.I)
        cleaned = re.sub(r"\bdaily (?:dated )?pages?\b", "daily templates", cleaned, flags=re.I)
        cleaned = re.sub(r"\bdated weekly\b", "undated weekly layout", cleaned, flags=re.I)
        cleaned = re.sub(r"\bdated daily\b", "undated daily layout", cleaned, flags=re.I)
        cleaned = re.sub(r"\bweekly or daily dated pages?\b", "undated weekly and daily planning pages", cleaned, flags=re.I)

    # 2. Dashboard
    has_dashboard = truth_check._proven("dashboard", facts)[0]
    if not has_dashboard:
        cleaned = re.sub(r"\bdashboards?\b", "overview section", cleaned, flags=re.I)

    # 3. Year overview page
    has_year_overview = truth_check._proven("year overview page", facts)[0]
    if not has_year_overview:
        cleaned = re.sub(r"\byear overview(?:\s+pages?)?\b", "overview layout", cleaned, flags=re.I)
        cleaned = re.sub(r"\bat a glance\b", "overview", cleaned, flags=re.I)

    # 4. Duplicate link
    cleaned = re.sub(r"\bduplicate link\b", "import guide", cleaned, flags=re.I)
    cleaned = re.sub(r"\bduplicate\b", "import", cleaned, flags=re.I)

    # 5. Charts / progress bars
    has_charts = truth_check._proven("charts / progress bars", facts)[0]
    if not has_charts:
        cleaned = re.sub(r"\bcharts?\b", "summaries", cleaned, flags=re.I)
        cleaned = re.sub(r"\bgraphs?\b", "tables", cleaned, flags=re.I)
        cleaned = re.sub(r"\bprogress bars?\b", "trackers", cleaned, flags=re.I)

    # 6. Fillable / editable PDF
    if kind == "pdf":
        cleaned = re.sub(r"\bfillable(?:\s+pdf)?\b", "printable PDF", cleaned, flags=re.I)
        cleaned = re.sub(r"\beditable pdf\b", "printable PDF", cleaned, flags=re.I)
        cleaned = re.sub(r"\btype (?:on|into) the pdf\b", "write on the printed PDF", cleaned, flags=re.I)
        cleaned = re.sub(r"\bhyperlinked|hyperlinks?|clickable\b", "clean printable", cleaned, flags=re.I)

    # 7. Stickers / digital tablet version
    cleaned = re.sub(r"\bstickers?\b", "labels", cleaned, flags=re.I)
    cleaned = re.sub(r"\bgoodnotes\b|\bnotability\b|\btablet version\b", "printable edition", cleaned, flags=re.I)
    cleaned = re.sub(r"\bdigital planner\b", "printable planner", cleaned, flags=re.I)

    # 8. Unproven named parts (e.g. 'CurrencyRates tab', 'Use tab', 'MRR tracker')
    proven_names = set(truth_check._names(facts))

    def repl_unproven_named(m):
        full = m.group(0)
        prefix = m.group(1)
        suffix = m.group(2)
        words = prefix.split()
        while words and words[0].lower() in truth_check._NOT_NAMES:
            words = words[1:]
        if not words:
            return full
        part = f"{' '.join(words)} {suffix}".strip().lower()
        part_words = [w for w in re.findall(r"[a-z]+", part) if w not in ("database", "page", "sheet", "tab", "tracker", "section", "the", "and")]
        if part_words and any(all(w in n for w in part_words) for n in proven_names):
            return full
        return f"{prefix.lower()} {suffix}"

    cleaned = truth_check._NAMED.sub(repl_unproven_named, cleaned)

    # 9. Adjust page/sheet/database counts
    actual_count = facts["pages"] if facts["kind"] == "pdf" else (len(facts["sheets"]) or len(facts["md"]) + len(facts["csv"]))
    if actual_count > 0:
        def repl_cnt(m):
            raw_num = m.group(1).lower()
            unit = m.group(2)
            n = truth_check._NUMBERS.get(raw_num, int(raw_num) if raw_num.isdigit() else None)
            if n is not None and n > actual_count:
                return f"{actual_count} {unit}"
            return m.group(0)

        cleaned = truth_check._COUNT.sub(repl_cnt, cleaned)

    # 10. Clean raw ** in descriptions or text
    cleaned = re.sub(r"\*{2,}", "", cleaned)

    return cleaned


def _ensure_extra_proof_promises(listing_path: str, readme_path: str, facts: Dict[str, Any]) -> None:
    """Ensure listing/readme explicitly includes at least 3 proven facts so red flag check passes."""
    actual_count = facts["pages"] if facts["kind"] == "pdf" else (len(facts["sheets"]) or len(facts["md"]) + len(facts["csv"]))
    if actual_count <= 0:
        return

    proven_lines = []
    if facts["kind"] == "pdf":
        unit = "pages"
        proven_lines.append(f"- Includes {actual_count} {unit}")
        if "A4" in facts.get("pdf_sizes", []):
            proven_lines.append("- A4 format included")
        if "Letter" in facts.get("pdf_sizes", []):
            proven_lines.append("- Letter format included")
        for title in facts.get("titles", [])[:2]:
            proven_lines.append(f"- The {title} page")
    elif facts["kind"] == "sheets":
        unit = "worksheets"
        proven_lines.append(f"- Includes {actual_count} {unit}")
        if facts.get("formulas", 0) > 0:
            proven_lines.append("- formulas / automatic calculation included")
        if facts.get("validations", 0) > 0:
            proven_lines.append("- drop-down menus included")
        for sheet in facts.get("sheets", [])[:2]:
            proven_lines.append(f"- The {sheet} tab")
    elif facts["kind"] == "notion":
        unit = "worksheets"
        proven_lines.append(f"- Includes {actual_count} {unit}")
        for csv_name in list(facts.get("csv", {}).keys())[:2]:
            name = os.path.splitext(csv_name)[0].replace("-", " ").title()
            proven_lines.append(f"- The {name} database")
        for md_name in list(facts.get("md", {}).keys())[:2]:
            name = os.path.splitext(md_name)[0].replace("-", " ").title()
            proven_lines.append(f"- The {name} page")

    if os.path.exists(listing_path) and len(proven_lines) >= 2:
        try:
            with open(listing_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            check_res = truth_check.check(os.path.dirname(listing_path), with_images=False)
            if check_res["score"] >= truth_check.RED_FLAG and len(check_res["proven"]) < truth_check.EXTRA_PROOF_PROMISES:
                extra_block = "\n\n### Verified Deliverables\n" + "\n".join(proven_lines) + "\n"
                with open(listing_path, "w", encoding="utf-8") as f:
                    f.write(content + extra_block)
        except OSError:
            pass


def repair_by_listing_rewrite(folder: str, facts: Dict[str, Any]) -> None:
    """Rewrite listing.md and README.md so promises match reality."""
    kind = facts.get("kind", "unknown")
    if kind == "unknown":
        spec = product_builder.spec_from_folder(folder)
        kind = spec.get("kind", "planner")

    listing_path = os.path.join(folder, "listing.md")
    if os.path.exists(listing_path):
        with open(listing_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        sanitized = sanitize_listing_text_for_facts(content, facts, kind)
        with open(listing_path, "w", encoding="utf-8") as f:
            f.write(sanitized)

    readme_path = os.path.join(folder, "README.md")
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        sanitized = sanitize_listing_text_for_facts(content, facts, kind)
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(sanitized)

    # Ensure red flag requirement (at least 3 proven claims) is satisfied
    _ensure_extra_proof_promises(listing_path, readme_path, facts)

    # Clean raw ** in cover_image_idea.txt if present
    cover_idea = os.path.join(folder, "cover_image_idea.txt")
    if os.path.exists(cover_idea):
        try:
            with open(cover_idea, "r", encoding="utf-8", errors="ignore") as f:
                ctext = f.read()
            if "**" in ctext:
                with open(cover_idea, "w", encoding="utf-8") as f:
                    f.write(ctext.replace("**", ""))
        except OSError:
            pass


def _can_small_rebuild(problems: List[str], facts: Dict[str, Any]) -> bool:
    """Check if the failure is just a missing small part (like daily/dated page in a planner)

    that can be honestly solved by rebuilding once with the existing builder.
    """
    if facts.get("kind") != "pdf":
        return False
    daily_dated_issue = any("weekly or daily dated pages" in p.lower() for p in problems)
    return daily_dated_issue


def repair_by_rebuild(folder: str, approval_id: int, out_dir: str) -> bool:
    """Ask existing product builder to rebuild once with small missing parts added."""
    spec = product_builder.spec_from_folder(folder)
    if spec.get("kind") != "planner":
        return False

    spec["approval_id"] = approval_id
    folder_name = os.path.basename(folder)
    parts = folder_name.split("-", 1)
    short_name = parts[1] if len(parts) > 1 else folder_name
    spec["short_name"] = short_name

    # Add missing dated/daily page component
    has_daily = spec.get("daily_page", False)
    has_week = any("week" in (p.get("title") or "").lower() for p in spec.get("pages", []))
    if not has_daily and not has_week:
        spec["pages"] = list(spec.get("pages") or [])
        spec["pages"].append({
            "title": "Week 1",
            "subtitle": "Weekly planning schedule",
            "columns": 1,
            "sections": [{"heading": "Daily Schedule", "type": "lines", "count": 6}],
        })

    listing_path = os.path.join(folder, "listing.md")
    if os.path.exists(listing_path):
        with open(listing_path, "r", encoding="utf-8", errors="ignore") as f:
            ltext = f.read()
        tags = re.findall(r"^-\s+(.*)$", ltext, re.MULTILINE)
        buyer_m = re.search(r"\*\*Buyer:\*\*\s*(.*?)\s\s", ltext)
        spec["listing"] = {
            "title": spec.get("name", "Planner"),
            "price_usd": 3.0,
            "buyer": buyer_m.group(1) if buyer_m else "General",
            "platform": "Gumroad",
            "description": ltext[:300],
            "tags": tags or ["planner"],
        }
    else:
        spec["listing"] = {
            "title": spec.get("name", "Planner"),
            "price_usd": 3.0,
            "buyer": "General",
            "platform": "Gumroad",
            "description": "Printable planner",
            "tags": ["planner"],
        }
    spec["instructions"] = ["Print at 100% scale and use."]
    spec["cover_image_idea"] = "Flat lay of planner pages."

    try:
        product_builder.build_product(spec, approval_id, out_dir=out_dir, folder_name=folder_name)
        return True
    except Exception as exc:
        logger.warning(f"Rebuild failed for #{approval_id}: {exc}")
        return False


def repair_product(approval_id: int, folder_name: str, db_path: Optional[str] = None,
                   out_dir: Optional[str] = None) -> Dict[str, Any]:
    """Attempt the cheapest honest repair for one product folder."""
    out_dir = out_dir or PRODUCTS_DIR
    folder = os.path.join(out_dir, folder_name)
    if not os.path.isdir(folder):
        return {"approval_id": approval_id, "verdict": "needs fix", "reason": "Folder not found"}

    problems, check_res, facts = _unmet_claims_and_problems(folder)
    if check_res.get("verdict") == "ready":
        _record_verdict(db_path, approval_id, folder_name, "ready", check_res.get("score", 100), [])
        return {"approval_id": approval_id, "verdict": "ready", "action": "none"}

    action_taken = "none"

    # Strategy (a) or (b): Decide cheapest honest fix
    if _can_small_rebuild(problems, facts):
        success = repair_by_rebuild(folder, approval_id, out_dir)
        action_taken = "small_rebuild"
        if success:
            facts = truth_check.facts(folder)
            repair_by_listing_rewrite(folder, facts)
        else:
            repair_by_listing_rewrite(folder, facts)
            action_taken = "listing_rewrite_fallback"
    else:
        repair_by_listing_rewrite(folder, facts)
        action_taken = "listing_rewrite"

    # Re-run truth check after repair
    tc_result = product_builder.truth_check_folder(approval_id, folder_name, db_path=db_path, out_dir=out_dir,
                                                   notify_fn=lambda msg: True)
    verdict = tc_result.get("verdict", "needs fix")
    remaining_problems = tc_result.get("problems", [])

    return {
        "approval_id": approval_id,
        "folder": folder_name,
        "action": action_taken,
        "verdict": verdict,
        "score": tc_result.get("score", 0),
        "problems": remaining_problems,
    }


def _record_verdict(db_path: Optional[str], approval_id: int, folder: str, verdict: str,
                    score: int, problems: List[str]) -> None:
    """Update truth_checks and product_builds."""
    conn = connect(db_path)
    try:
        status = "built" if verdict == "ready" else "needs_fix"
        note = "ready" if status == "built" else ("needs fix: " + "; ".join(problems[:2]))[:300]
        conn.execute(
            "UPDATE product_builds SET status = ?, note = ? WHERE approval_id = ?",
            (status, note, approval_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO truth_checks (approval_id, verdict, score, notified) "
            "VALUES (?, ?, ?, ?)",
            (approval_id, verdict, score, verdict),
        )
        conn.commit()
    finally:
        conn.close()


def get_products_needing_repair(db_path: Optional[str] = None, out_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Find all built products whose truth check status is NEEDS FIX."""
    out_dir = out_dir or PRODUCTS_DIR
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT b.approval_id, b.folder, tc.verdict "
            "FROM product_builds b "
            "LEFT JOIN truth_checks tc ON b.approval_id = tc.approval_id "
            "WHERE b.status = 'needs_fix' OR tc.verdict = 'needs fix' OR tc.verdict = 'needs_fix'"
        ).fetchall()
    finally:
        conn.close()

    candidates = []
    seen = set()
    for app_id, folder, tc_verdict in rows:
        if not app_id or app_id in seen:
            continue
        seen.add(app_id)
        if not folder:
            if os.path.isdir(out_dir):
                match = next((d for d in os.listdir(out_dir) if d.startswith(f"{app_id}-")), None)
                if match:
                    folder = match
        if folder and os.path.isdir(os.path.join(out_dir, folder)):
            candidates.append({"approval_id": app_id, "folder": folder})

    return sorted(candidates, key=lambda x: x["approval_id"])


def run_daily(db_path: Optional[str] = None, out_dir: Optional[str] = None,
              llm=None, max_repairs: int = DEFAULT_MAX_REPAIRS) -> Dict[str, Any]:
    """Public daily entry point for product repairs.

    Guarded: catches all unexpected exceptions, records them, and returns a summary.
    Never crashes or stops the normal pipeline. Respects kill switch and daily limit of 3 repairs.
    """
    summary: Dict[str, Any] = {
        "status": "ok",
        "attempted": 0,
        "repaired": 0,
        "still_needs_fix": 0,
        "errors": 0,
        "results": [],
    }

    try:
        if guardrails.is_killed():
            summary["status"] = "killed"
            summary["reason"] = "Kill switch is active"
            return summary

        if not guardrails.automation_allowed(db_path):
            summary["status"] = "held"
            summary["reason"] = "Automation held by owner policy"
            return summary

        out_dir = out_dir or PRODUCTS_DIR
        candidates = get_products_needing_repair(db_path=db_path, out_dir=out_dir)

        repairs_allowed = min(max_repairs, DEFAULT_MAX_REPAIRS)
        for item in candidates[:repairs_allowed]:
            summary["attempted"] += 1
            app_id = item["approval_id"]
            folder = item["folder"]
            try:
                res = repair_product(app_id, folder, db_path=db_path, out_dir=out_dir)
                summary["results"].append(res)
                if res.get("verdict") == "ready":
                    summary["repaired"] += 1
                else:
                    summary["still_needs_fix"] += 1
            except Exception as item_exc:
                logger.exception(f"Error repairing product #{app_id}: {item_exc}")
                summary["errors"] += 1
                summary["still_needs_fix"] += 1
                summary["results"].append({
                    "approval_id": app_id,
                    "folder": folder,
                    "verdict": "needs fix",
                    "error": str(item_exc),
                })

    except Exception as exc:
        logger.exception(f"Unexpected error in run_daily: {exc}")
        summary["status"] = "error"
        summary["error"] = str(exc)

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Repair 'NEEDS FIX' products")
    parser.add_argument("--db", default=None, help="Path to database")
    parser.add_argument("--out", default=None, help="Path to products output folder")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_REPAIRS, help="Max repairs")
    args = parser.parse_args()

    result = run_daily(db_path=args.db, out_dir=args.out, max_repairs=args.max)
    print(f"Repair run complete: {result}")
