"""tg_reports.py — Telegram plugin for robot reports and summaries.

Commands provided:
  - /ops: ops_report text summary
  - /dashboard: ops_dashboard html sent as a document
  - /seo N: seo_score summary + top 3 fixes
  - /lint N: listing_lint PASS/WARN/FAIL counts + first 5 lines
  - /faq N: faq_builder markdown as text or document if long
  - /dup: dup_check pairs over 0.5
  - /keywords: top 20 from keywords.json
  - /calendar: calendar_export .ics as a document
  - /versions N: pack_changelog latest version + changes
  - /ideas: idea_scoring of the latest scout ideas if available
  - /boards: pin_boards summary

All actions are owner-only. Owner-facing text is written in Tenglish house style.
Strict privacy scrubbing: no system paths leaked, no money amounts, text <= 3500 chars.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Tuple

COMMANDS = [
    {
        "cmd": "/ops",
        "words": ["ops", "ops report", "operations report", "ops vivaralu"],
        "help_en": "Show weekly operations summary (runs, failures, retries, providers).",
        "help_te": "Weekly ops summary chupisthundi (runs, failures, retries, providers).",
    },
    {
        "cmd": "/dashboard",
        "words": ["dashboard", "ops dashboard", "dashboard doc", "dashboard pampu"],
        "help_en": "Send the self-contained HTML ops dashboard as a document.",
        "help_te": "HTML ops dashboard file document ga pampisthundi.",
    },
    {
        "cmd": "/seo",
        "words": ["seo", "seo score", "seo check", "seo vivaralu"],
        "help_en": "Show 0-100 SEO score, summary, and top 3 fixes for pack N.",
        "help_te": "Pack N kosam 0-100 SEO score mariyu top 3 fixes chupisthundi.",
    },
    {
        "cmd": "/lint",
        "words": ["lint", "listing lint", "lint check", "listing check"],
        "help_en": "Check listing format rules (PASS/WARN/FAIL) and first 5 problem lines for pack N.",
        "help_te": "Pack N listing format rules (PASS/WARN/FAIL) mariyu modati 5 lines chupisthundi.",
    },
    {
        "cmd": "/faq",
        "words": ["faq", "buyer faq", "faq text", "faq pampu"],
        "help_en": "Show buyer FAQ markdown for pack N (or send as document if long).",
        "help_te": "Pack N kosam buyer FAQ markdown text leda document ga pampisthundi.",
    },
    {
        "cmd": "/dup",
        "words": ["dup", "dup check", "duplicates", "duplicate check", "saripolchu"],
        "help_en": "Check cross-pack duplicate content (similarity > 0.5) and common openings.",
        "help_te": "Packs madhya duplicate content (similarity > 0.5) unda ani check chesthundi.",
    },
    {
        "cmd": "/keywords",
        "words": ["keywords", "top keywords", "keywords list", "mukhyamaina padalu"],
        "help_en": "List top 20 keywords from the keyword bank.",
        "help_te": "Keyword bank nunchi top 20 keywords chupisthundi.",
    },
    {
        "cmd": "/calendar",
        "words": ["calendar", "calendar export", "ics file", "calendar pampu"],
        "help_en": "Export and send the post plan .ics calendar file as a document.",
        "help_te": "Post plan calendar .ics file document ga pampisthundi.",
    },
    {
        "cmd": "/versions",
        "words": ["versions", "pack version", "changelog", "version vivaralu"],
        "help_en": "Show latest semantic version and file changes for pack N.",
        "help_te": "Pack N latest version mariyu file changes vivaralu chupisthundi.",
    },
    {
        "cmd": "/ideas",
        "words": ["ideas", "scout ideas", "idea score", "ideas scoring"],
        "help_en": "Score and rank latest scout product ideas on 0-100 scale.",
        "help_te": "Latest scout ideas ni 0-100 scale meeda score chesi chupisthundi.",
    },
    {
        "cmd": "/boards",
        "words": ["boards", "pinterest boards", "board plan", "boards summary"],
        "help_en": "Show Pinterest board plan summary, board names, and pin counts.",
        "help_te": "Pinterest board plan vivaralu, perulu mariyu pin counts chupisthundi.",
    },
]

MAX_MESSAGE_CHARS = 3500

CURRENCY_PATTERN = re.compile(
    r"[$₹€£¥₩₽]\s*[+-]?\d[\d,.]*|\b(?:USD|INR|EUR|GBP|JPY|CAD|AUD|NZD|CHF|CNY|RMB|HKD|SGD|AED|SAR|KRW|RUB|BRL|MXN|ZAR|SEK|NOK|DKK|Rs\.?|dollars|rupees|cents|euros|pounds|yen)\s*[:=]?\s*[+-]?\d[\d,.]*|\b[+-]?\d[\d,.]*\s*(?:(?:USD|INR|EUR|GBP|JPY|CAD|AUD|NZD|CHF|CNY|RMB|HKD|SGD|AED|SAR|KRW|RUB|BRL|MXN|ZAR|SEK|NOK|DKK|Rs\.?|dollars|rupees|cents|euros|pounds|yen)\b|[$₹€£¥₩₽])",
    re.IGNORECASE,
)


def scrub_outgoing(text: str) -> str:
    """Scrub sensitive paths, money amounts, secrets, and enforce <= 3500 characters."""
    if not text:
        return ""

    # Path scrubbing: replace system paths with exported_content relative paths or basename
    def _path_repl(m: re.Match) -> str:
        full = m.group(0)
        idx = full.find("exported_content/")
        if idx != -1:
            return full[idx:]
        return full.rstrip("/").split("/")[-1]

    text = re.sub(r"(?<![\w\.\-])/(?:[\w\.\-]+/)+[\w\.\-]+", _path_repl, text)

    # Currency / money amounts
    text = CURRENCY_PATTERN.sub("[hidden]", text)

    try:
        import public_export

        text = public_export.scrub_text(text)
    except Exception:
        pass

    text = text.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        suffix = "\n\n...(cut, message length limit)"
        text = text[: MAX_MESSAGE_CHARS - len(suffix)] + suffix
    return text


def find_pack_dir(aid: int | str, products_dir: Optional[str | Path] = None) -> Optional[Path]:
    """Locate ready pack directory matching aid number or name."""
    try:
        import product_builder

        base = Path(products_dir or product_builder.PRODUCTS_DIR)
    except Exception:
        base = Path(products_dir or "exported_content/products")

    search_dirs = []
    if (base / "upload").is_dir():
        search_dirs.append(base / "upload")
    if base.is_dir():
        search_dirs.append(base)

    # Local fallback
    local_base = Path("exported_content/products")
    if local_base.is_dir() and local_base not in search_dirs:
        if (local_base / "upload").is_dir():
            search_dirs.append(local_base / "upload")
        search_dirs.append(local_base)

    aid_str = str(aid).strip().lstrip("#")

    for sdir in search_dirs:
        try:
            entries = sorted(sdir.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                if not p.is_dir() or p.name == "upload":
                    continue
                # Match exact folder name or aid prefix (e.g. 1 or 01 or 01-daily-planner)
                p_prefix = p.name.split("-")[0].lstrip("0") or "0"
                target_prefix = aid_str.split("-")[0].lstrip("0") or "0"
                if (
                    p.name == aid_str
                    or p_prefix == target_prefix
                    or re.match(rf"^0*{aid_str}(?:-.*)?$", p.name)
                ):
                    if (p / "product.zip").is_file() and (p / "listing.txt").is_file():
                        return p
            except OSError:
                continue

    return None


def extract_pack_id(text: str) -> Optional[str]:
    """Extract pack identifier or number from text like '/seo 1' or 'seo pack 2'."""
    m = re.search(r"(?:pack\s+|#)?(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    tokens = text.strip().split()
    if len(tokens) >= 2:
        candidate = tokens[1].strip().lstrip("#")
        if candidate:
            return candidate
    return None


# --- Command Handlers ---

def handle_ops(text: str, ctx: dict) -> dict:
    """Handle /ops command — formatted ops report summary."""
    try:
        import ops_report
    except ImportError:
        return {"text": scrub_outgoing("Ops report module ledu. ops_report.py verify cheyandi.")}

    db_path = ctx.get("db_path")
    try:
        summary = ops_report.generate_ops_summary(db_path=db_path)
        out = ops_report.format_ops_text(summary)
        if not out.strip():
            out = "Ops summary lo emi data ledu."
        return {"text": scrub_outgoing(out)}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Ops report create cheyadam lo error vachindi: {exc}")}


def handle_dashboard(text: str, ctx: dict) -> dict:
    """Handle /dashboard command — send self-contained ops dashboard HTML."""
    try:
        import ops_dashboard
    except ImportError:
        return {"text": scrub_outgoing("Ops dashboard module ledu. ops_dashboard.py verify cheyandi.")}

    db_path = ctx.get("db_path")
    send_document = ctx.get("send_document")
    if not send_document:
        return {"text": scrub_outgoing("Document send capability ledu.")}

    out_file = Path("exported_content/ops/index.html")
    try:
        if not out_file.is_file():
            out_file.parent.mkdir(parents=True, exist_ok=True)
            ops_dashboard.render_dashboard_to_file(str(out_file), db_path=db_path)

        if not out_file.is_file():
            return {"text": scrub_outgoing("Ops dashboard file generate kaledu.")}

        caption = scrub_outgoing("Private ops dashboard HTML document (no secrets, counts only).")
        send_document(str(out_file), caption=caption)
        return {"handled": True}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Ops dashboard send cheyadam lo error vachindi: {exc}")}


def handle_seo(text: str, ctx: dict) -> dict:
    """Handle /seo N command — 0-100 SEO score, Tenglish summary, and top 3 fixes."""
    pack_id = extract_pack_id(text)
    if not pack_id:
        return {"text": scrub_outgoing("E pack kosam SEO score kavali? Send /seo <pack_number> (e.g. /seo 1).")}

    try:
        import seo_score
    except ImportError:
        return {"text": scrub_outgoing("SEO score module ledu. seo_score.py verify cheyandi.")}

    pdir = find_pack_dir(pack_id)
    if not pdir:
        return {"text": scrub_outgoing(f"Pack #{pack_id} ready folder dorakaledu. /packs to check ready packs.")}

    try:
        res = seo_score.score_pack_folder(pdir)
        score = res.get("score", 0)
        t_summary = res.get("tenglish_summary", "")
        checks = res.get("checks", {})

        failed_checks = []
        for check_name, c_info in checks.items():
            if c_info.get("score", 0) < c_info.get("max", 0):
                failed_checks.append((check_name, c_info))

        lines = [
            f"📊 SEO Score for Pack #{pack_id}: {score}/100",
            f"Summary: {t_summary}",
        ]

        if failed_checks:
            lines.append("\nTop fixes needed:")
            for i, (name, info) in enumerate(failed_checks[:3], start=1):
                reason = info.get("reason", "Needs improvement")
                lines.append(f"{i}. {name.replace('_', ' ').capitalize()}: {reason}")
        else:
            lines.append("\nAnni SEO checks pass ayyayi! Ready to list.")

        return {"text": scrub_outgoing("\n".join(lines))}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Pack #{pack_id} SEO scoring lo error vachindi: {exc}")}


def handle_lint(text: str, ctx: dict) -> dict:
    """Handle /lint N command — listing lint PASS/WARN/FAIL counts and first 5 lines."""
    pack_id = extract_pack_id(text)
    if not pack_id:
        return {"text": scrub_outgoing("E pack kosam lint check kavali? Send /lint <pack_number> (e.g. /lint 1).")}

    try:
        import listing_lint
    except ImportError:
        return {"text": scrub_outgoing("Listing lint module ledu. listing_lint.py verify cheyandi.")}

    pdir = find_pack_dir(pack_id)
    if not pdir:
        return {"text": scrub_outgoing(f"Pack #{pack_id} ready folder dorakaledu. /packs to check ready packs.")}

    try:
        res = listing_lint.lint_folder(pdir)
        if isinstance(res, tuple):
            results = res[0]
        else:
            results = res

        pass_count = sum(1 for _, status, _ in results if status == "PASS")
        warn_count = sum(1 for _, status, _ in results if status == "WARN")
        fail_count = sum(1 for _, status, _ in results if status == "FAIL")

        lines = [
            f"🔍 Listing Lint for Pack #{pack_id}:",
            f"PASS: {pass_count} | WARN: {warn_count} | FAIL: {fail_count}",
            "\nDetails (first 5 checks):",
        ]

        for rule, status, msg in results[:5]:
            icon = "✅" if status == "PASS" else ("⚠️" if status == "WARN" else "❌")
            lines.append(f"{icon} {rule}: {status} — {msg}")

        return {"text": scrub_outgoing("\n".join(lines))}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Pack #{pack_id} listing lint lo error vachindi: {exc}")}


def handle_faq(text: str, ctx: dict) -> dict:
    """Handle /faq N command — buyer FAQ markdown as text or document."""
    pack_id = extract_pack_id(text)
    if not pack_id:
        return {"text": scrub_outgoing("E pack kosam FAQ kavali? Send /faq <pack_number> (e.g. /faq 1).")}

    try:
        import faq_builder
    except ImportError:
        return {"text": scrub_outgoing("FAQ builder module ledu. faq_builder.py verify cheyandi.")}

    pdir = find_pack_dir(pack_id)
    if not pdir:
        return {"text": scrub_outgoing(f"Pack #{pack_id} ready folder dorakaledu. /packs to check ready packs.")}

    try:
        faq_text = faq_builder.build_faq(pdir)
        scrubbed = scrub_outgoing(faq_text)

        if len(scrubbed) <= 3000:
            return {"text": scrubbed}

        send_document = ctx.get("send_document")
        if send_document:
            with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as tf:
                tf.write(scrubbed)
                temp_path = tf.name
            try:
                caption = scrub_outgoing(f"Pack #{pack_id} Buyer FAQ Markdown document.")
                send_document(temp_path, caption=caption)
                return {"handled": True}
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        return {"text": scrubbed}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Pack #{pack_id} FAQ create cheyadam lo error vachindi: {exc}")}


def handle_dup(text: str, ctx: dict) -> dict:
    """Handle /dup command — duplicate content check reporting pairs over 0.5 similarity."""
    try:
        import dup_check
    except ImportError:
        return {"text": scrub_outgoing("Duplicate check module ledu. dup_check.py verify cheyandi.")}

    report_path = Path("exported_content/marketing/dup_report.json")
    report = None
    try:
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if not report:
            report = dup_check.check_duplicate_content()

        summary_tenglish = report.get("summary_tenglish", "Duplicate check summary ledu.")
        total_packs = report.get("total_packs", 0)
        flagged = report.get("flagged_pairs", [])

        pairs_over_half = [p for p in flagged if p.get("max_similarity", 0) > 0.5]

        lines = [
            f"👯 Duplicate Content Report ({total_packs} packs):",
            f"Summary: {summary_tenglish}",
        ]

        if pairs_over_half:
            lines.append(f"\nPairs with similarity > 0.5 ({len(pairs_over_half)}):")
            for p in pairs_over_half[:5]:
                pa = p.get("pack_a", "")
                pb = p.get("pack_b", "")
                sim = p.get("max_similarity", 0.0)
                reasons = p.get("reasons", [])
                reason_str = ", ".join(reasons) if reasons else "high text similarity"
                lines.append(f"• {pa} vs {pb}: {int(sim*100)}% ({reason_str})")
        else:
            lines.append("\n0.5 kante ekkuva duplicate unna pairs levu ✓.")

        return {"text": scrub_outgoing("\n".join(lines))}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Duplicate check run cheyadam lo error vachindi: {exc}")}


def handle_keywords(text: str, ctx: dict) -> dict:
    """Handle /keywords command — top 20 keywords from keywords.json."""
    kw_paths = [
        Path("exported_content/marketing/keywords.json"),
        Path("keywords.json"),
    ]
    data = None
    for kw_path in kw_paths:
        if kw_path.is_file():
            try:
                data = json.loads(kw_path.read_text(encoding="utf-8"))
                break
            except Exception:
                continue

    if data is None:
        try:
            import keyword_bank

            data = keyword_bank.build_keyword_bank()
        except Exception:
            return {"text": scrub_outgoing("Keywords data file ledu. keywords.json verify cheyandi.")}

    raw_list = []
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        raw_list = data.get("keywords") or data.get("terms") or list(data.keys())

    formatted_kws = []
    for item in raw_list:
        if isinstance(item, dict):
            term = item.get("term") or item.get("keyword") or item.get("name") or ""
            score = item.get("score") if item.get("score") is not None else item.get("pack_count")
            if term:
                formatted_kws.append(f"{term} (score {score})" if score is not None else term)
        elif isinstance(item, str) and item.strip():
            formatted_kws.append(item.strip())

    if not formatted_kws:
        return {"text": scrub_outgoing("Keywords list lo emi padalu dorakaledu.")}

    top_20 = formatted_kws[:20]
    lines = [
        f"🔑 Top {len(top_20)} Keywords from Keyword Bank:",
        "",
    ]
    for i, kw in enumerate(top_20, start=1):
        lines.append(f"{i}. {kw}")

    return {"text": scrub_outgoing("\n".join(lines))}


def handle_calendar(text: str, ctx: dict) -> dict:
    """Handle /calendar command — send calendar .ics file as a document."""
    try:
        import calendar_export
    except ImportError:
        return {"text": scrub_outgoing("Calendar export module ledu. calendar_export.py verify cheyandi.")}

    send_document = ctx.get("send_document")
    if not send_document:
        return {"text": scrub_outgoing("Document send capability ledu.")}

    ics_path = Path("exported_content/marketing/post_plan.ics")
    try:
        if not ics_path.is_file():
            ics_path.parent.mkdir(parents=True, exist_ok=True)
            calendar_export.export_calendar(ics_path=str(ics_path))

        if not ics_path.is_file():
            return {"text": scrub_outgoing("Post plan calendar file (.ics) generate kaledu.")}

        caption = scrub_outgoing("Post plan .ics calendar file (manual posting schedule, RFC 5545).")
        send_document(str(ics_path), caption=caption)
        return {"handled": True}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Calendar export send cheyadam lo error vachindi: {exc}")}


def handle_versions(text: str, ctx: dict) -> dict:
    """Handle /versions N command — latest version and file changes."""
    pack_id = extract_pack_id(text)
    if not pack_id:
        return {"text": scrub_outgoing("E pack kosam versions chudali? Send /versions <pack_number> (e.g. /versions 1).")}

    try:
        import pack_changelog
    except ImportError:
        return {"text": scrub_outgoing("Pack changelog module ledu. pack_changelog.py verify cheyandi.")}

    pdir = find_pack_dir(pack_id)
    if not pdir:
        return {"text": scrub_outgoing(f"Pack #{pack_id} ready folder dorakaledu. /packs to check ready packs.")}

    try:
        res = pack_changelog.process_pack(pdir, dry_run=True)
        version = res.get("version", "1.0")
        prev_version = res.get("previous_version")
        t_summary = res.get("tenglish_summary", "")
        diff = res.get("diff", {})

        lines = [
            f"📦 Version Report for {pdir.name}:",
            f"Current Version: v{version}" + (f" (was v{prev_version})" if prev_version else " (initial)"),
            f"Summary: {t_summary}",
        ]

        added = diff.get("added", [])
        modified = diff.get("modified", [])
        removed = diff.get("removed", [])

        if added or modified or removed:
            lines.append("\nChanges:")
            if added:
                lines.append(f"• Added: {', '.join(added)}")
            if modified:
                lines.append(f"• Modified: {', '.join(modified)}")
            if removed:
                lines.append(f"• Removed: {', '.join(removed)}")
        else:
            lines.append("\nFiles lo emi marpulu levu (clean & stable).")

        return {"text": scrub_outgoing("\n".join(lines))}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Pack #{pack_id} version check lo error vachindi: {exc}")}


def handle_ideas(text: str, ctx: dict) -> dict:
    """Handle /ideas command — offline scoring of latest scout ideas."""
    try:
        import idea_scoring
    except ImportError:
        return {"text": scrub_outgoing("Idea scoring module ledu. idea_scoring.py verify cheyandi.")}

    db_path = ctx.get("db_path")
    ideas: List[Dict[str, Any]] = []
    if db_path and os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            t_check = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scout_ideas'").fetchone()
            if t_check:
                rows = cursor.execute("SELECT id, category, idea, niche FROM scout_ideas ORDER BY id DESC LIMIT 5").fetchall()
                for r in rows:
                    cat = r["category"] or ""
                    idea_text = r["idea"] or ""
                    niche = r["niche"] or ""
                    ideas.append({
                        "title": idea_text,
                        "niche": niche,
                        "tags": [cat.lower()] if cat else [],
                    })
            conn.close()
        except Exception:
            pass

    if not ideas:
        ideas_file = Path("scout_ideas.json")
        if ideas_file.is_file():
            try:
                ideas = json.loads(ideas_file.read_text(encoding="utf-8"))
            except Exception:
                pass

    if not ideas:
        return {"text": scrub_outgoing("Scout ideas database lo emi levu. Scout inka ideas search cheyaledu.")}

    try:
        scored_list = idea_scoring.score_ideas_list(ideas)
        lines = [
            f"💡 Scout Ideas Scoring ({len(scored_list)} ideas):",
            "",
        ]
        for i, s in enumerate(scored_list, start=1):
            title = s.get("title", "")
            score = s.get("total_score", 0)
            summary = s.get("tenglish_summary", "")
            lines.append(f"{i}. {title} — Score: {score}/100")
            lines.append(f"   Reason: {summary}")

        return {"text": scrub_outgoing("\n".join(lines))}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Idea scoring run cheyadam lo error vachindi: {exc}")}


def handle_boards(text: str, ctx: dict) -> dict:
    """Handle /boards command — Pinterest board plan summary."""
    try:
        import pin_boards
    except ImportError:
        return {"text": scrub_outgoing("Pin boards module ledu. pin_boards.py verify cheyandi.")}

    boards_json_path = Path("exported_content/marketing/boards.json")
    plan = None
    try:
        if boards_json_path.is_file():
            try:
                plan = json.loads(boards_json_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        if not plan:
            plan = pin_boards.build_board_plan()

        b_count = plan.get("boards_count", len(plan.get("boards", [])))
        unique_pins = plan.get("total_unique_pins", 0)
        t_summary = plan.get("tenglish_summary", "")
        boards = plan.get("boards", [])

        lines = [
            f"📌 Pinterest Boards Plan ({b_count} boards, {unique_pins} unique pins):",
            f"Summary: {t_summary}",
            "\nBoards list:",
        ]

        for b in boards:
            name = b.get("name", "")
            pins_in_board = len(b.get("pins", []))
            lines.append(f"• {name} ({pins_in_board} pins mapped)")

        return {"text": scrub_outgoing("\n".join(lines))}
    except Exception as exc:
        return {"text": scrub_outgoing(f"Board plan summary create cheyadam lo error vachindi: {exc}")}


# --- Main Handler Router ---

def handle(text: str, ctx: dict) -> Optional[dict]:
    """Plugin entrypoint conforming to Telegram plugin interface."""
    if not text or not isinstance(text, str):
        return None

    cleaned = text.strip()
    lower = cleaned.lower()
    first_token = lower.split()[0] if lower.split() else ""

    # Check multi-word command phrases first to prevent false matching
    if (
        first_token in ("/dashboard", "dashboard")
        or lower.startswith("ops dashboard")
        or lower.startswith("dashboard doc")
        or lower.startswith("dashboard pampu")
    ):
        return handle_dashboard(cleaned, ctx)

    if (
        first_token in ("/ops", "ops")
        or lower.startswith("ops report")
        or lower.startswith("operations report")
        or lower.startswith("ops vivaralu")
    ):
        return handle_ops(cleaned, ctx)

    if (
        first_token in ("/seo", "seo")
        or lower.startswith("seo score")
        or lower.startswith("seo check")
        or lower.startswith("seo vivaralu")
    ):
        return handle_seo(cleaned, ctx)

    if (
        first_token in ("/lint", "lint")
        or lower.startswith("listing lint")
        or lower.startswith("lint check")
        or lower.startswith("listing check")
    ):
        return handle_lint(cleaned, ctx)

    if (
        first_token in ("/faq", "faq")
        or lower.startswith("buyer faq")
        or lower.startswith("faq text")
        or lower.startswith("faq pampu")
    ):
        return handle_faq(cleaned, ctx)

    if (
        first_token in ("/dup", "dup")
        or lower.startswith("dup check")
        or lower.startswith("duplicate check")
        or lower.startswith("duplicates")
        or lower.startswith("saripolchu")
    ):
        return handle_dup(cleaned, ctx)

    if (
        first_token in ("/keywords", "keywords")
        or lower.startswith("top keywords")
        or lower.startswith("keywords list")
        or lower.startswith("mukhyamaina padalu")
    ):
        return handle_keywords(cleaned, ctx)

    if (
        first_token in ("/calendar", "calendar")
        or lower.startswith("calendar export")
        or lower.startswith("ics file")
        or lower.startswith("calendar pampu")
    ):
        return handle_calendar(cleaned, ctx)

    if (
        first_token in ("/versions", "versions")
        or lower.startswith("pack version")
        or lower.startswith("changelog")
        or lower.startswith("version vivaralu")
    ):
        return handle_versions(cleaned, ctx)

    if (
        first_token in ("/ideas", "ideas")
        or lower.startswith("scout ideas")
        or lower.startswith("idea score")
        or lower.startswith("ideas scoring")
    ):
        return handle_ideas(cleaned, ctx)

    if (
        first_token in ("/boards", "boards")
        or lower.startswith("pinterest boards")
        or lower.startswith("board plan")
        or lower.startswith("boards summary")
    ):
        return handle_boards(cleaned, ctx)

    return None
