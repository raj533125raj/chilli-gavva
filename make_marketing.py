#!/usr/bin/env python3
"""BINNY-V5A: One command for all marketing files of ready upload packs.

Runs every generator for a pack in a safe order with per-step timeouts and a summary table:
Pack steps:
  - carousel_maker
  - pin_variants
  - cover_variants
  - reel_frames
  - blog_post
  - shorts_script
  - landing_page
  - seo_meta
  - review_request
  - launch_checklist

Global steps (when --all is specified):
  - pin_boards
  - sitemap_builder
  - catalog_sheet
  - gift_guide
  - keyword_bank
  - dup_check
  - image_optimize (dry-run unless --optimize is passed)
  - marketing_index

Skip a step cleanly when its module is missing; a failing step never stops the rest.
Writes exported_content/marketing/make_report.json atomically.
Supports --pack ID, --all, --only step1,step2, --skip step1,step2, --timeout SEC, --optimize.
No money amounts are written anywhere. Pack folders are untouched; idempotent runs.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PACKS_DIR = REPO_ROOT / "exported_content" / "products" / "upload"
DEFAULT_MARKETING_DIR = REPO_ROOT / "exported_content" / "marketing"
DEFAULT_PRODUCTS_DIR = REPO_ROOT / "exported_content" / "products"

MONEY_RE = re.compile(
    r"([\$₹]\s?\d[\d.,]*|\b\d+[\d.,]*\s?(?:USD|dollars?|cents?|INR|rupees?|₹)\b)",
    re.IGNORECASE,
)

DEFAULT_STEP_TIMEOUT = 120.0

PACK_STEPS = [
    "carousel_maker",
    "pin_variants",
    "cover_variants",
    "reel_frames",
    "blog_post",
    "shorts_script",
    "landing_page",
    "seo_meta",
    "review_request",
    "launch_checklist",
]

GLOBAL_STEPS = [
    "pin_boards",
    "sitemap_builder",
    "catalog_sheet",
    "gift_guide",
    "keyword_bank",
    "dup_check",
    "image_optimize",
    "marketing_index",
]

ALL_STEPS = PACK_STEPS + GLOBAL_STEPS

try:
    import product_qa_deep as _qa_deep
except Exception:
    _qa_deep = None


def sanitize_text(text: Any) -> str:
    """Wash currency or money amounts from messages."""
    if not isinstance(text, str):
        text = str(text)
    return MONEY_RE.sub("[amount]", text)


def atomic_write_json(path: Path | str, data: Dict[str, Any]) -> Path:
    """Atomically write JSON data to file with deterministic indentation."""
    out_path = Path(path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = out_path.parent / f".tmp_{out_path.name}_{os.getpid()}_{time.time()}"
    try:
        content = json.dumps(data, indent=2, sort_keys=True) + "\n"
        temp_file.write_text(content, encoding="utf-8")
        temp_file.replace(out_path)
    except Exception:
        if temp_file.is_file():
            temp_file.unlink(missing_ok=True)
        raise
    return out_path


def run_with_timeout(
    func: Callable[..., Any],
    args: tuple = (),
    kwargs: Optional[Dict[str, Any]] = None,
    timeout: float = DEFAULT_STEP_TIMEOUT,
) -> Tuple[bool, Any, Optional[str]]:
    """Run callable in a worker thread with timeout.

    Returns (success, result, error_message).
    """
    if kwargs is None:
        kwargs = {}
    res = [None]
    exc = [None]

    def worker():
        try:
            res[0] = func(*args, **kwargs)
        except BaseException as e:
            exc[0] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, None, f"timed out after {timeout:.1f}s"
    if exc[0] is not None:
        return False, None, sanitize_text(str(exc[0]))
    return True, res[0], None


def qa_verdict(pack_dir: Path | str) -> str:
    """Deep QA verdict for a pack folder, or 'skipped' when unavailable."""
    if _qa_deep is None:
        return "skipped"
    try:
        return str(_qa_deep.check_folder(str(pack_dir)).get("verdict", "skipped"))
    except Exception:
        return "skipped"


def report_row(pack_dir: Path | str, _marketing_dir: Optional[Path | str] = None) -> Dict[str, Any]:
    """Readiness dict for one pack folder."""
    pack_dir = Path(pack_dir)
    files = {
        name: (pack_dir / name).is_file()
        for name in ("product.zip", "listing.txt")
    }
    verdict = qa_verdict(pack_dir)
    return {
        "id": pack_dir.name,
        "files": files,
        "qa": verdict,
        "ready": files["product.zip"] and files["listing.txt"] and verdict == "ready",
    }


def select_ready_packs(packs_dir: Path | str, marketing_dir: Optional[Path | str] = None) -> List[Path]:
    """Ready pack folders under packs_dir, sorted by id."""
    root = Path(packs_dir)
    if not root.is_dir():
        return []
    return [
        pack
        for pack in sorted(p for p in root.iterdir() if p.is_dir())
        if report_row(pack)["ready"]
    ]


def load_module(name: str):
    """Import a generator module by name, returning module or None."""
    try:
        return __import__(name)
    except Exception:
        return None


def load_makers():
    """Import legacy maker modules; each stays None when unavailable."""
    carousel = load_module("carousel_maker")
    pins = load_module("pin_variants")
    return carousel, pins


def made_files(pack_marketing_dir: Path | str) -> List[str]:
    """Relative file names written for this pack (inside marketing only)."""
    pack_marketing_dir = Path(pack_marketing_dir)
    if not pack_marketing_dir.is_dir():
        return []
    return sorted(
        path.relative_to(pack_marketing_dir).as_posix()
        for path in pack_marketing_dir.rglob("*")
        if path.is_file() and path.name != "README.txt"
    )


def resolve_step_filter(
    only: Optional[str | List[str]] = None,
    skip: Optional[str | List[str]] = None,
) -> Tuple[Set[str], Set[str]]:
    """Parse --only and --skip comma-separated strings or lists."""
    def parse_items(val: Optional[str | List[str]]) -> Set[str]:
        if not val:
            return set()
        if isinstance(val, str):
            parts = [p.strip() for p in val.split(",") if p.strip()]
        else:
            parts = []
            for item in val:
                parts.extend(p.strip() for p in str(item).split(",") if p.strip())
        return set(parts)

    only_set = parse_items(only)
    skip_set = parse_items(skip)
    return only_set, skip_set


def is_step_enabled(step_name: str, only_set: Set[str], skip_set: Set[str]) -> bool:
    """Check if a step should be run based on only/skip filters."""
    if only_set and step_name not in only_set:
        return False
    if step_name in skip_set:
        return False
    return True


def run_pack_step(
    step_name: str,
    pack_dir: Path,
    marketing_dir: Path,
    timeout: float = DEFAULT_STEP_TIMEOUT,
    modules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a single pack generator step safely with timeout."""
    pack_dir = Path(pack_dir)
    marketing_dir = Path(marketing_dir)
    step_info = {
        "step": step_name,
        "status": "pending",
        "duration_sec": 0.0,
        "error": None,
        "output": None,
    }

    mod = modules.get(step_name) if modules else load_module(step_name)
    if mod is None:
        step_info["status"] = "skipped"
        step_info["error"] = "module missing"
        return step_info

    start_t = time.time()

    def invoke_step():
        if step_name == "carousel_maker":
            return mod.make_pack_carousel(pack_dir, marketing_dir=marketing_dir)
        elif step_name == "pin_variants":
            # pin_variants outputs to out_dir / "marketing" / <pack> / "pins"
            # or default "exported_content" / "marketing" / <pack> / "pins"
            res = mod.generate_pins_for_pack(pack_dir, out_dir=marketing_dir.parent)
            if hasattr(mod, "generate_social_variants"):
                mod.generate_social_variants(pack_dir, out_dir=marketing_dir.parent)
            return res
        elif step_name == "cover_variants":
            return mod.make_pack_covers(pack_dir, marketing_base=marketing_dir)
        elif step_name == "reel_frames":
            return mod.make_pack_reel(pack_dir, marketing_dir=marketing_dir)
        elif step_name == "blog_post":
            return mod.make_pack_blog(pack_dir, marketing_dir=marketing_dir)
        elif step_name == "shorts_script":
            return mod.generate_shorts_script_for_pack(pack_dir, marketing_dir=marketing_dir)
        elif step_name == "landing_page":
            return mod.build_landing_page(pack_dir, out_dir=marketing_dir)
        elif step_name == "seo_meta":
            return mod.generate_pack_seo_meta(pack_dir, marketing_dir=marketing_dir)
        elif step_name == "review_request":
            return mod.write_review_request(pack_dir, marketing_base_dir=marketing_dir)
        elif step_name == "launch_checklist":
            return mod.write_launch_checklist(pack_dir, marketing_dir=marketing_dir)
        else:
            raise ValueError(f"Unknown pack step: {step_name}")

    success, res, err = run_with_timeout(invoke_step, timeout=timeout)
    step_info["duration_sec"] = round(time.time() - start_t, 3)

    if success:
        step_info["status"] = "ok"
        step_info["output"] = str(res) if res is not None else None
    else:
        step_info["status"] = "failed"
        step_info["error"] = err
    return step_info


def process_pack(
    pack_dir: Path | str,
    marketing_dir: Path | str,
    makers: Optional[Any] = None,
    only: Optional[str | List[str]] = None,
    skip: Optional[str | List[str]] = None,
    timeout: float = DEFAULT_STEP_TIMEOUT,
    modules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run pack makers for one pack; collects results without raising.

    Maintains backwards compatibility with legacy 2-maker signature
    makers=(carousel_maker, pin_variants) while supporting all 10 generators.
    """
    pack_dir = Path(pack_dir)
    marketing_dir = Path(marketing_dir)
    pack_id = pack_dir.name
    only_set, skip_set = resolve_step_filter(only, skip)

    result: Dict[str, Any] = {
        "id": pack_id,
        "carousel": None,
        "pins": None,
        "covers": None,
        "reel": None,
        "blog": None,
        "shorts": None,
        "landing": None,
        "seo_meta": None,
        "review_request": None,
        "launch_checklist": None,
        "steps": {},
        "errors": [],
        "files": [],
        "ok": True,
    }

    # If makers tuple is passed (legacy interface), map it into modules dict
    active_modules: Dict[str, Any] = dict(modules) if modules else {}
    if makers is not None:
        if isinstance(makers, (tuple, list)):
            if len(makers) > 0 and makers[0] is not None:
                active_modules["carousel_maker"] = makers[0]
            elif len(makers) > 0 and makers[0] is None:
                active_modules["carousel_maker"] = None
            if len(makers) > 1 and makers[1] is not None:
                active_modules["pin_variants"] = makers[1]
            elif len(makers) > 1 and makers[1] is None:
                active_modules["pin_variants"] = None
        elif isinstance(makers, dict):
            active_modules.update(makers)

    for step in PACK_STEPS:
        if not is_step_enabled(step, only_set, skip_set):
            result["steps"][step] = {
                "step": step,
                "status": "skipped",
                "duration_sec": 0.0,
                "error": "filtered",
                "output": None,
            }
            continue

        step_res = run_pack_step(
            step, pack_dir, marketing_dir, timeout=timeout, modules=active_modules
        )
        result["steps"][step] = step_res

        # Map to legacy top-level keys
        if step == "carousel_maker":
            if step_res["status"] == "ok":
                result["carousel"] = step_res["output"]
            elif step_res["status"] == "skipped":
                result["errors"].append("carousel: module missing")
            else:
                result["errors"].append(f"carousel: {step_res['error']}")
        elif step == "pin_variants":
            if step_res["status"] == "ok":
                result["pins"] = step_res["output"]
            elif step_res["status"] == "skipped":
                result["errors"].append("pins: module missing")
            else:
                result["errors"].append(f"pins: {step_res['error']}")
        else:
            short_name = step.replace("_maker", "").replace("_variants", "").replace("_frames", "")
            if step_res["status"] == "ok":
                result[short_name] = step_res["output"]
            elif step_res["status"] == "failed":
                result["errors"].append(f"{step}: {step_res['error']}")

    result["files"] = made_files(marketing_dir / pack_id)
    result["ok"] = not result["errors"] and all(
        s.get("status") in ("ok", "skipped") for s in result["steps"].values()
    )
    return result


def write_readme(marketing_dir: Path | str, pack_id: str, result: Dict[str, Any]) -> Path:
    """README.txt at exported_content/marketing/<pack>/ — file names only."""
    lines = [f"Marketing files made for {pack_id} (by make_marketing.py):", ""]

    # Legacy two makers first
    for kind in ("carousel", "pins"):
        status = (
            "ok"
            if result.get(kind) is not None
            else next(
                (error for error in result.get("errors", []) if error.startswith(kind)),
                "not made",
            )
        )
        lines.append(f"{kind}: {status}")
        lines.extend(
            f"  {name}"
            for name in result.get("files", [])
            if name.startswith(kind + "/")
        )

    # Other generator outputs
    other_steps = [
        ("cover_variants", "covers"),
        ("reel_frames", "reel"),
        ("blog_post", "blog"),
        ("shorts_script", "shorts"),
        ("landing_page", "landing"),
        ("seo_meta", "seo_meta"),
        ("review_request", "review_request"),
        ("launch_checklist", "launch_checklist"),
    ]
    for step_name, label in other_steps:
        st = result.get("steps", {}).get(step_name, {})
        status = st.get("status", "not run")
        if status == "ok":
            lines.append(f"{label}: ok")
        elif status == "failed":
            lines.append(f"{label}: FAILED ({st.get('error')})")
        elif status == "skipped":
            lines.append(f"{label}: skipped ({st.get('error')})")

    if result.get("errors"):
        lines.append("")
        lines.extend(result["errors"])

    lines.append("")
    lines.append("Pack folder untouched. The owner reviews these files and posts by hand.")
    out_dir = Path(marketing_dir) / pack_id
    out_dir.mkdir(parents=True, exist_ok=True)
    readme = out_dir / "README.txt"
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return readme


def run_global_step(
    step_name: str,
    products_dir: Path,
    marketing_dir: Path,
    timeout: float = DEFAULT_STEP_TIMEOUT,
    optimize: bool = False,
    modules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a single global generator step safely with timeout."""
    products_dir = Path(products_dir)
    marketing_dir = Path(marketing_dir)
    step_info = {
        "step": step_name,
        "status": "pending",
        "duration_sec": 0.0,
        "error": None,
        "output": None,
    }

    mod = modules.get(step_name) if modules else load_module(step_name)
    if mod is None:
        step_info["status"] = "skipped"
        step_info["error"] = "module missing"
        return step_info

    start_t = time.time()

    def invoke_step():
        if step_name == "pin_boards":
            kw_path = marketing_dir / "keywords.json"
            out_json = marketing_dir / "boards.json"
            out_md = marketing_dir / "boards.md"
            return mod.write_board_plan(
                products_dir=products_dir,
                marketing_dir=marketing_dir,
                keywords_path=kw_path,
                output_json=out_json,
                output_md=out_md,
            )
        elif step_name == "sitemap_builder":
            site_dir = marketing_dir / "site"
            return mod.build_site_index(
                marketing_dir=marketing_dir,
                site_dir=site_dir,
            )
        elif step_name == "catalog_sheet":
            cat_dir = marketing_dir / "catalog"
            return mod.build_catalog(
                products_dir=products_dir,
                out_dir=cat_dir,
            )
        elif step_name == "gift_guide":
            col_dir = marketing_dir / "collections"
            return mod.generate_all_seasonal_collections(
                products_dir=products_dir,
                out_dir=col_dir,
            )
        elif step_name == "keyword_bank":
            kw_out = marketing_dir / "keywords.json"
            return mod.export_keyword_bank(
                products_dir=products_dir,
                output_path=kw_out,
            )
        elif step_name == "dup_check":
            return mod.check_duplicate_content(
                products_dir=products_dir,
                marketing_dir=marketing_dir,
                out_dir=marketing_dir,
            )
        elif step_name == "image_optimize":
            dry_run = not optimize
            return mod.optimize_marketing_folder(
                marketing_dir=marketing_dir,
                dry_run=dry_run,
            )
        elif step_name == "marketing_index":
            return mod.build_index(
                marketing_dir=marketing_dir,
            )
        else:
            raise ValueError(f"Unknown global step: {step_name}")

    success, res, err = run_with_timeout(invoke_step, timeout=timeout)
    step_info["duration_sec"] = round(time.time() - start_t, 3)

    if success:
        step_info["status"] = "ok"
        step_info["output"] = str(res) if res is not None else None
    else:
        step_info["status"] = "failed"
        step_info["error"] = err
    return step_info


def run_global_generators(
    products_dir: Path | str,
    marketing_dir: Path | str,
    only: Optional[str | List[str]] = None,
    skip: Optional[str | List[str]] = None,
    timeout: float = DEFAULT_STEP_TIMEOUT,
    optimize: bool = False,
    modules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run all 8 global generators in safe order."""
    products_dir = Path(products_dir)
    marketing_dir = Path(marketing_dir)
    only_set, skip_set = resolve_step_filter(only, skip)

    results: Dict[str, Any] = {}
    for step in GLOBAL_STEPS:
        if not is_step_enabled(step, only_set, skip_set):
            results[step] = {
                "step": step,
                "status": "skipped",
                "duration_sec": 0.0,
                "error": "filtered",
                "output": None,
            }
            continue

        step_res = run_global_step(
            step,
            products_dir=products_dir,
            marketing_dir=marketing_dir,
            timeout=timeout,
            optimize=optimize,
            modules=modules,
        )
        results[step] = step_res

    return results


def write_make_report(
    marketing_dir: Path | str,
    pack_results: List[Dict[str, Any]],
    global_results: Dict[str, Any],
    skipped_notes: List[str],
    elapsed_sec: float,
    optimize: bool = False,
) -> Path:
    """Write exported_content/marketing/make_report.json atomically."""
    marketing_dir = Path(marketing_dir)
    report_data = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "elapsed_sec": round(elapsed_sec, 3),
        "optimize": optimize,
        "total_packs": len(pack_results),
        "skipped_packs": skipped_notes,
        "packs": pack_results,
        "global_steps": global_results,
        "summary": {
            "packs_ok": sum(1 for p in pack_results if p.get("ok")),
            "packs_failed": sum(1 for p in pack_results if not p.get("ok")),
            "global_ok": sum(
                1 for s in global_results.values() if s.get("status") == "ok"
            ),
            "global_failed": sum(
                1 for s in global_results.values() if s.get("status") == "failed"
            ),
            "global_skipped": sum(
                1 for s in global_results.values() if s.get("status") == "skipped"
            ),
        },
    }
    report_path = marketing_dir / "make_report.json"
    return atomic_write_json(report_path, report_data)


def run(
    pack_id: Optional[str] = None,
    all_packs: bool = False,
    packs_dir: Optional[Path | str] = None,
    marketing_dir: Optional[Path | str] = None,
    products_dir: Optional[Path | str] = None,
    makers: Optional[Any] = None,
    only: Optional[str | List[str]] = None,
    skip: Optional[str | List[str]] = None,
    timeout: float = DEFAULT_STEP_TIMEOUT,
    optimize: bool = False,
    modules: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Path], List[str]]:
    """Drive the generators; returns (results, readmes, skipped notes)."""
    start_total = time.time()
    packs_dir = Path(packs_dir) if packs_dir else DEFAULT_PACKS_DIR
    marketing_dir = Path(marketing_dir) if marketing_dir else DEFAULT_MARKETING_DIR
    marketing_dir.mkdir(parents=True, exist_ok=True)

    if products_dir:
        resolved_products_dir = Path(products_dir)
    else:
        resolved_products_dir = packs_dir.parent if packs_dir.name == "upload" else packs_dir

    if pack_id:
        candidates = [packs_dir / pack_id]
    elif all_packs:
        candidates = (
            sorted(p for p in packs_dir.iterdir() if p.is_dir())
            if packs_dir.is_dir()
            else []
        )
    else:
        raise SystemExit("choose --pack ID or --all")

    results, readmes, skipped = [], [], []
    for pack in candidates:
        row = report_row(pack)
        if not pack.is_dir():
            skipped.append(f"{pack.name}: pack folder not found at {pack}")
            continue
        if not row["ready"]:
            missing = ", ".join(name for name, flag in row["files"].items() if not flag)
            skipped.append(f"{pack.name}: not ready ({missing}; qa {row['qa']})")
            continue

        result = process_pack(
            pack,
            marketing_dir=marketing_dir,
            makers=makers,
            only=only,
            skip=skip,
            timeout=timeout,
            modules=modules,
        )
        readmes.append(write_readme(marketing_dir, result["id"], result))
        results.append(result)

    # When --all is specified, run global generators
    global_results: Dict[str, Any] = {}
    if all_packs:
        global_results = run_global_generators(
            products_dir=resolved_products_dir,
            marketing_dir=marketing_dir,
            only=only,
            skip=skip,
            timeout=timeout,
            optimize=optimize,
            modules=modules,
        )

    # Write make_report.json atomically
    elapsed_total = time.time() - start_total
    write_make_report(
        marketing_dir=marketing_dir,
        pack_results=results,
        global_results=global_results,
        skipped_notes=skipped,
        elapsed_sec=elapsed_total,
        optimize=optimize,
    )

    return results, readmes, skipped


def render_table(
    results: List[Dict[str, Any]],
    skipped: List[str],
    global_results: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Format a clean summary table of pack and global runs in Tenglish style."""
    lines: List[str] = []
    for result in results:
        carousel_status = "ok" if result.get("carousel") else "FAILED"
        pins_status = "ok" if result.get("pins") else "FAILED"
        num_files = len(result.get("files", []))
        lines.append(
            f"{result['id']}: carousel {carousel_status} · pins {pins_status} · "
            f"{num_files} files · {result['id']}/README.txt"
        )
    lines.extend("skip " + note for note in skipped)

    if global_results:
        lines.append("Global generators:")
        for gname, ginfo in global_results.items():
            status = ginfo.get("status", "unknown")
            dur = ginfo.get("duration_sec", 0.0)
            err = f" ({ginfo.get('error')})" if ginfo.get("error") else ""
            sym = "✓" if status == "ok" else ("-" if status == "skipped" else "✗")
            lines.append(f"  {gname:18} : {sym} {status}{err} [{dur}s]")

    ok_count = sum(1 for r in results if r.get("ok"))
    err_count = sum(len(r.get("errors", [])) for r in results)
    if global_results:
        err_count += sum(1 for g in global_results.values() if g.get("status") == "failed")

    lines.append(
        f"Marketing: {ok_count} of {len(results)} packs fully made, "
        f"{len(skipped)} skipped, {err_count} errors"
    )
    return lines


def main(argv: Optional[List[str]] = None, makers: Optional[Any] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pack", help="one pack id under exported_content/products/upload")
    parser.add_argument("--all", action="store_true", help="every ready pack and global generators")
    parser.add_argument("--only", help="comma-separated generator step names to run only")
    parser.add_argument("--skip", help="comma-separated generator step names to skip")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_STEP_TIMEOUT,
        help=f"per-step timeout in seconds (default {DEFAULT_STEP_TIMEOUT})",
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="actually optimize and overwrite images (default is dry-run)",
    )
    parser.add_argument("--packs-dir", default=str(DEFAULT_PACKS_DIR))
    parser.add_argument("--marketing-dir", default=str(DEFAULT_MARKETING_DIR))
    parser.add_argument("--products-dir", default=None)

    args = parser.parse_args(argv)
    marketing_path = Path(args.marketing_dir)

    results, _readmes, skipped = run(
        pack_id=args.pack,
        all_packs=args.all,
        packs_dir=args.packs_dir,
        marketing_dir=marketing_path,
        products_dir=args.products_dir,
        makers=makers,
        only=args.only,
        skip=args.skip,
        timeout=args.timeout,
        optimize=args.optimize,
    )

    # Load report if written to get global_results
    global_results = None
    report_file = marketing_path / "make_report.json"
    if report_file.is_file():
        try:
            report_data = json.loads(report_file.read_text(encoding="utf-8"))
            global_results = report_data.get("global_steps")
        except Exception:
            pass

    for line in render_table(results, skipped, global_results=global_results):
        print(line)

    has_errors = (
        bool(skipped)
        or any(not r.get("ok") for r in results)
        or (
            global_results is not None
            and any(g.get("status") == "failed" for g in global_results.values())
        )
    )
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
