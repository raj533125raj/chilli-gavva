#!/usr/bin/env python3
"""CC1-1: marketing files made automatically for every READY pack.

For one pack folder this module runs the REAL makers (through make_marketing):

  pins      make_marketing.process_pack(only=pin_variants)  -> pins/ and posts/ (PNG + json)
  captions  pin_variants caption functions                  -> captions.txt (English + Tenglish)
  faq       faq_builder.build_faq                           -> FAQ.md
  landing   make_marketing.process_pack(only=landing_page)  -> landing/index.html
  calendar  post_planner.build_plan + calendar_export       -> calendar/post_plan.(json|ics|csv)

Everything is written to the pack's PRIVATE marketing folder
    exported_content/products/marketing/<pack folder>/
which is git-ignored (exported_content/products/ is in .gitignore) and travels only inside the
encrypted products archive. A folder inside the repo that is not git-ignored is refused.

manifest.json lists every file with its sha256. A second run rebuilds only the parts whose
sources (listing, images, file names, licence, maker code) changed or whose files are missing
or damaged; when nothing changed nothing is written at all.

The pack folder itself is never modified: makers read a temporary copy.
Nothing is posted, published or sent anywhere. The owner posts by hand.
A failing maker is logged and never stops the other makers or the product build.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import guardrails
import make_marketing

REPO_ROOT = Path(__file__).resolve().parent
MARKETING_FOLDER = "marketing"   # pin_variants writes to <out>/marketing/<pack>/pins, so the name is fixed
MANIFEST_NAME = "manifest.json"
FORMAT_VERSION = 1
PLAN_DAYS = 30
BACKFILL_LIMIT = 3
STEP_TIMEOUT = 180.0

PARTS = ("pins", "captions", "faq", "landing", "calendar")
# Output paths of each part, relative to the pack's marketing folder ("x/" = whole folder).
PART_OUTPUTS = {
    "pins": ("pins/", "posts/"),
    "captions": ("captions.txt",),
    "faq": ("FAQ.md",),
    "landing": ("landing/",),
    "calendar": ("calendar/",),
}
# Which source facts of the pack feed each part (see source_hashes).
PART_SOURCES = {
    "pins": ("listing", "images"),
    "captions": ("listing",),
    "faq": ("names", "licence"),
    "landing": ("listing", "images", "names", "licence"),
    "calendar": ("listing", "images"),
}
# Maker modules behind each part: a code change in a maker rebuilds its part.
PART_MODULES = {
    "pins": ("pin_variants",),
    "captions": ("pin_variants", "post_planner"),
    "faq": ("faq_builder",),
    "landing": ("landing_page", "faq_builder"),
    "calendar": ("post_planner", "calendar_export"),
}
PART_LABELS = {"pins": "Pins", "captions": "captions", "faq": "FAQ",
               "landing": "landing page", "calendar": "plan"}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
CAPTION_LAYOUTS = ("hero mockup", "3-page fan", "checklist teaser", "before/after",
                   "quote card", "what's inside grid")

log = logging.getLogger("marketing_auto")


# ---- small helpers ---------------------------------------------------------
def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha_json(value: Any) -> str:
    return _sha_bytes(json.dumps(value, sort_keys=True).encode("utf-8"))


def clean_error(error: Any) -> str:
    """Short, money-free, secret-free reason for the manifest, the DB and the log."""
    text = make_marketing.sanitize_text(" ".join(str(error or "").split()))
    try:
        import secret_guard
        text = secret_guard.scrub_text(text)
    except Exception:
        pass
    return text[:200]


def approval_id_of(pack_name: str) -> Optional[int]:
    """Leading number of a pack folder name ('12-budget-sheet' -> 12)."""
    match = re.match(r"^(\d+)(?:-|$)", str(pack_name))
    return int(match.group(1)) if match else None


def _today(now: Optional[datetime.datetime] = None) -> datetime.date:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.date() if isinstance(now, datetime.datetime) else now


# ---- where the files go (private only) ------------------------------------
def products_dir_of(pack_dir: Path | str) -> Path:
    """Products root of a pack folder (also for packs under <products>/upload/)."""
    parent = Path(pack_dir).resolve().parent
    return parent.parent if parent.name == "upload" else parent


def marketing_root(products_dir: Path | str) -> Path:
    return Path(products_dir).resolve() / MARKETING_FOLDER


def pack_marketing_dir(pack_dir: Path | str, products_dir: Optional[Path | str] = None) -> Path:
    """<products>/marketing/<pack folder name>/ — the pack's private marketing folder."""
    pack_dir = Path(pack_dir).resolve()
    return marketing_root(products_dir or products_dir_of(pack_dir)) / pack_dir.name


def gitignore_dirs(gitignore: Optional[Path] = None) -> List[str]:
    """Plain folder/file patterns from .gitignore (globs and '!' lines are not used here)."""
    path = Path(gitignore) if gitignore else REPO_ROOT / ".gitignore"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "!")) or any(ch in line for ch in "*?["):
            continue
        found.append(line.strip("/"))
    return found


def is_private_dir(path: Path | str, repo_root: Optional[Path] = None,
                   gitignore: Optional[Path] = None) -> bool:
    """True when files written under `path` can never be committed to the public repo.

    Outside the repo -> private. Inside the repo -> only when .gitignore covers the folder.
    """
    root = Path(repo_root).resolve() if repo_root else REPO_ROOT
    target = Path(path).resolve()
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError:
        return True
    ignore_file = gitignore or (root / ".gitignore")
    return any(relative == pattern or relative.startswith(pattern + "/")
               for pattern in gitignore_dirs(ignore_file))


# ---- pack sources and change detection -------------------------------------
def _top_files(pack_dir: Path) -> List[Path]:
    return sorted(p for p in pack_dir.iterdir() if p.is_file() and not p.is_symlink())


def listing_path(pack_dir: Path) -> Optional[Path]:
    for name in ("listing.txt", "listing.md"):
        if (pack_dir / name).is_file():
            return pack_dir / name
    return None


def source_hashes(pack_dir: Path | str) -> Dict[str, str]:
    """Hashes of the pack facts the makers read: listing, images, file names, licence."""
    pack_dir = Path(pack_dir)
    files = _top_files(pack_dir)
    listing = listing_path(pack_dir)
    images = [(p.name, _sha_file(p)) for p in files if p.suffix.lower() in IMAGE_SUFFIXES]
    names: List[Any] = [p.name for p in files]
    archive = pack_dir / "product.zip"
    if archive.is_file():
        try:
            with zipfile.ZipFile(archive) as bundle:
                names.append(sorted(bundle.namelist()))
        except Exception:
            names.append(_sha_file(archive))
    licence = pack_dir / "LICENSE.txt"
    return {
        "listing": (listing.name + ":" + _sha_file(listing)) if listing else "",
        "images": _sha_json(images),
        "names": _sha_json(names),
        "licence": _sha_file(licence) if licence.is_file() else "",
    }


def module_signature(module: Any) -> str:
    """sha256 of a maker's source file ('' for modules without a file, e.g. test fakes)."""
    path = getattr(module, "__file__", None)
    try:
        return _sha_file(Path(path)) if path and os.path.isfile(path) else ""
    except OSError:
        return ""


def resolve_modules(modules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every maker module by name (None when it cannot be imported). `modules` overrides."""
    wanted = sorted({name for names in PART_MODULES.values() for name in names})
    given = dict(modules or {})
    return {name: (given[name] if name in given else make_marketing.load_module(name))
            for name in wanted}


def part_fingerprint(part: str, sources: Dict[str, str], modules: Dict[str, Any]) -> str:
    return _sha_json([FORMAT_VERSION, part,
                      [sources.get(key, "") for key in PART_SOURCES[part]],
                      [module_signature(modules.get(name)) for name in PART_MODULES[part]]])


def part_files(marketing_dir: Path, part: str) -> Dict[str, str]:
    """{relative path: sha256} of the files a part owns right now."""
    found: Dict[str, str] = {}
    for output in PART_OUTPUTS[part]:
        target = marketing_dir / output.rstrip("/")
        if output.endswith("/"):
            if target.is_dir():
                for path in sorted(target.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        found[path.relative_to(marketing_dir).as_posix()] = _sha_file(path)
        elif target.is_file():
            found[output] = _sha_file(target)
    return found


def remove_part_files(marketing_dir: Path, part: str) -> None:
    for output in PART_OUTPUTS[part]:
        target = marketing_dir / output.rstrip("/")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists() or target.is_symlink():
            try:
                target.unlink()
            except OSError:
                pass


def load_manifest(marketing_dir: Path | str) -> Dict[str, Any]:
    path = Path(marketing_dir) / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("parts"), dict) else {}


def needs_build(part: str, entry: Optional[Dict[str, Any]], fingerprint: str,
                marketing_dir: Path) -> Tuple[bool, str]:
    """(rebuild?, reason). A part is kept only when sources AND files are exactly as recorded."""
    if not isinstance(entry, dict):
        return True, "new"
    if entry.get("status") != "ok":
        return True, "last run failed"
    if entry.get("fingerprint") != fingerprint:
        return True, "sources changed"
    recorded = entry.get("files")
    if not isinstance(recorded, dict) or not recorded:
        return True, "no files recorded"
    if part_files(marketing_dir, part) != recorded:
        return True, "files missing or changed"
    return False, "unchanged"


# ---- a private, read-only view of the pack for the makers ------------------
def _listing_txt_from_md(text: str) -> str:
    """listing.txt body (Title/Description/Tags/What you get) from a robot-built listing.md."""
    try:
        import upload_pack
        return upload_pack._listing(text)
    except Exception:
        pass
    title, sections, current = "", {}, None
    for line in text.splitlines():
        heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            label = re.sub(r"\s*\(\d+\)$", "", heading.group(2)).strip()
            if len(heading.group(1)) == 1 and not title:
                title, current = label, None
            else:
                current = label.lower()
                sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    description = " ".join(line.strip() for line in sections.get("description", []) if line.strip())
    tags = [line.strip().lstrip("-* ").strip() for line in sections.get("tags", []) if line.strip()]
    items = [line.strip().lstrip("-* ").strip() for line in sections.get("what you get", [])
             if line.strip()]
    return (f"Title: {title}\n\nDescription:\n{description}\n\nTags: {', '.join(tags)}"
            "\n\nWhat you get:\n" + "\n".join(items) + "\n")


def make_view(pack_dir: Path, stage_root: Path) -> Path:
    """Copy the pack's top-level files to stage_root/<pack name>/ (the real pack is untouched).

    Robot-built folders carry listing.md; the makers read listing.txt, so the view gets one.
    """
    view = stage_root / pack_dir.name
    view.mkdir(parents=True)
    for path in _top_files(pack_dir):
        shutil.copy2(path, view / path.name)
    if not (view / "listing.txt").is_file() and (view / "listing.md").is_file():
        text = (view / "listing.md").read_text(encoding="utf-8-sig", errors="replace")
        (view / "listing.txt").write_text(_listing_txt_from_md(text), encoding="utf-8")
    return view


# ---- the five makers --------------------------------------------------------
def _step_through_make_marketing(step: str, view: Path, root: Path, modules: Dict[str, Any],
                                 timeout: float) -> Optional[str]:
    """Run one make_marketing pack step; returns None on success or the error text."""
    result = make_marketing.process_pack(view, root, only=[step], timeout=timeout, modules=modules)
    info = result["steps"].get(step, {})
    if info.get("status") == "ok":
        return None
    return info.get("error") or f"{step} {info.get('status', 'not run')}"


def _call(func, timeout: float, *args, **kwargs) -> Tuple[Any, Optional[str]]:
    ok, value, error = make_marketing.run_with_timeout(func, args=args, kwargs=kwargs, timeout=timeout)
    return (value, None) if ok else (None, error or "failed")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".tmp_{path.name}_{os.getpid()}"
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _make_pins(view, root, out, modules, timeout, now) -> Tuple[Optional[str], Dict[str, Any]]:
    if modules.get("pin_variants") is None:
        return "pin_variants module missing", {}
    error = _step_through_make_marketing("pin_variants", view, root, modules, timeout)
    pins = len(list((out / "pins").glob("*.png"))) if (out / "pins").is_dir() else 0
    posts = len(list((out / "posts").glob("*.png"))) if (out / "posts").is_dir() else 0
    if not error and not pins:
        error = "no Pin images were written (Pillow missing?)"
    return error, {"pins": pins, "posts": posts}


def captions_text(view: Path, modules: Dict[str, Any]) -> str:
    """English + Tenglish caption for each Pin layout, made by pin_variants' own functions."""
    pins = modules.get("pin_variants")
    if pins is None:
        raise RuntimeError("pin_variants module missing")
    info = pins.extract_pack_info(view)
    base_title = info["raw_title"]
    titles = list(pins.generate_ab_titles(base_title))
    while len(titles) < len(CAPTION_LAYOUTS):
        titles.append(f"{base_title} Template {len(titles) + 1}")
    hashtags: List[str] = []
    planner = modules.get("post_planner")
    if planner is not None:
        try:
            hashtags = list(planner.clean_hashtags(list(info.get("tags") or []), 8))
        except Exception:
            hashtags = []
    lines = [f"Captions: {base_title}",
             "Copy-paste chesi meere post cheyandi. Robot edi post cheyadu.", ""]
    for index, layout in enumerate(CAPTION_LAYOUTS):
        alt, english, tenglish = pins.generate_image_captions_and_alt(
            info["pack_name"], titles[index], layout, "pin", info.get("description") or "")
        lines += [f"== Pin {index + 1}: {layout} ==",
                  f"Title: {titles[index]}",
                  f"English: {english}",
                  f"Tenglish: {tenglish}",
                  f"Alt text: {alt}"]
        if hashtags:
            lines.append("Hashtags: " + " ".join("#" + tag for tag in hashtags))
        lines.append("")
    return make_marketing.sanitize_text("\n".join(lines).rstrip() + "\n")


def _make_captions(view, root, out, modules, timeout, now) -> Tuple[Optional[str], Dict[str, Any]]:
    text, error = _call(captions_text, timeout, view, modules)
    if error:
        return error, {}
    _write_text(out / "captions.txt", text)
    return None, {"captions": len(CAPTION_LAYOUTS)}


def _make_faq(view, root, out, modules, timeout, now) -> Tuple[Optional[str], Dict[str, Any]]:
    builder = modules.get("faq_builder")
    if builder is None:
        return "faq_builder module missing", {}
    text, error = _call(builder.build_faq, timeout, view)
    if error:
        return error, {}
    if not str(text or "").strip():
        return "FAQ maker returned no text", {}
    text = make_marketing.sanitize_text(str(text))
    _write_text(out / "FAQ.md", text)
    return None, {"questions": sum(1 for line in text.splitlines() if line.startswith("## "))}


def _make_landing(view, root, out, modules, timeout, now) -> Tuple[Optional[str], Dict[str, Any]]:
    if modules.get("landing_page") is None:
        return "landing_page module missing", {}
    error = _step_through_make_marketing("landing_page", view, root, modules, timeout)
    if not error and not (out / "landing" / "index.html").is_file():
        error = "landing page file was not written"
    return error, {}


def _make_calendar(view, root, out, modules, timeout, now) -> Tuple[Optional[str], Dict[str, Any]]:
    planner, exporter = modules.get("post_planner"), modules.get("calendar_export")
    if planner is None or exporter is None:
        return "post_planner / calendar_export module missing", {}
    start = _today(now)
    # view.parent holds only this pack, so all 30 days belong to it.
    plan, error = _call(planner.build_plan, timeout, products_dir=view.parent, start_date=start,
                        days=PLAN_DAYS, marketing_dir=root)
    if error:
        return error, {}
    if not isinstance(plan, list) or not plan:
        return "post planner made an empty plan", {}
    target = out / "calendar"
    target.mkdir(parents=True, exist_ok=True)
    _write_text(target / "post_plan.json", json.dumps(plan, indent=2, sort_keys=True) + "\n")
    _, error = _call(exporter.export_calendar, timeout, plan=plan,
                     ics_path=str(target / "post_plan.ics"), csv_path=str(target / "post_plan.csv"))
    if error:
        return error, {}
    days = len({str(item.get("date")) for item in plan if isinstance(item, dict)})
    return None, {"days": days, "entries": len(plan), "start_date": start.isoformat()}


MAKERS = {"pins": _make_pins, "captions": _make_captions, "faq": _make_faq,
          "landing": _make_landing, "calendar": _make_calendar}


# ---- the owner line ----------------------------------------------------------
def summary_line(parts: Dict[str, Dict[str, Any]]) -> str:
    """Tenglish line for the READY message, built only from parts that really exist.

    All parts ok   -> "Marketing files ready: 11 Pins, 5 Posts, 30 rojula plan, FAQ, landing page, captions"
    Some parts ok  -> "Marketing files konni ready: ... (inka raledu: Pins)"
    Nothing ok     -> "" (nothing is claimed).
    """
    def ok(part):
        return isinstance(parts.get(part), dict) and parts[part].get("status") == "ok"

    def meta(part, key):
        try:
            return int((parts[part].get("meta") or {}).get(key) or 0)
        except (TypeError, ValueError):
            return 0

    made: List[str] = []
    if ok("pins"):
        made.append(f"{meta('pins', 'pins')} Pins")
        if meta("pins", "posts"):
            made.append(f"{meta('pins', 'posts')} Posts")
    if ok("calendar"):
        made.append(f"{meta('calendar', 'days') or PLAN_DAYS} rojula plan")
    if ok("faq"):
        made.append("FAQ")
    if ok("landing"):
        made.append("landing page")
    if ok("captions"):
        made.append("captions")
    if not made:
        return ""
    missing = [PART_LABELS[part] for part in PARTS if not ok(part)]
    if missing:
        return ("Marketing files konni ready: " + ", ".join(made)
                + " (inka raledu: " + ", ".join(missing) + ")")
    return "Marketing files ready: " + ", ".join(made)


def overall_status(parts: Dict[str, Dict[str, Any]]) -> str:
    good = sum(1 for part in PARTS if (parts.get(part) or {}).get("status") == "ok")
    return "ready" if good == len(PARTS) else ("partial" if good else "failed")


# ---- database ----------------------------------------------------------------
def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS marketing_ready (
        pack TEXT PRIMARY KEY, approval_id INTEGER, status TEXT, line TEXT DEFAULT '',
        parts TEXT DEFAULT '{}', files INTEGER DEFAULT 0, pack_digest TEXT DEFAULT '',
        error TEXT DEFAULT '', day TEXT DEFAULT '', ts TEXT DEFAULT '')""")
    conn.commit()
    return conn


_COLUMNS = ("pack", "approval_id", "status", "line", "parts", "files", "pack_digest", "error", "day", "ts")


def record(result: Dict[str, Any], db_path=None, pack_digest: str = "",
           now: Optional[datetime.datetime] = None) -> None:
    """Write 'marketing ready' (or partial / failed) for one pack into the robot database."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    parts = {part: (result.get("parts", {}).get(part) or {}).get("status", "missing") for part in PARTS}
    errors = "; ".join(f"{part}: {info.get('error')}" for part, info in result.get("parts", {}).items()
                       if info.get("error")) or (result.get("error") or "")
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO marketing_ready "
            "(pack, approval_id, status, line, parts, files, pack_digest, error, day, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (result["pack"], approval_id_of(result["pack"]), result.get("status", "failed"),
             result.get("line", ""), json.dumps(parts, sort_keys=True), int(result.get("files", 0)),
             pack_digest or "", clean_error(errors), _today(now).isoformat(),
             now.isoformat(timespec="seconds")))
        conn.commit()
    finally:
        conn.close()


def status(pack_name: str, db_path=None) -> Optional[Dict[str, Any]]:
    """The recorded marketing state of one pack folder name, or None."""
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT " + ", ".join(_COLUMNS) + " FROM marketing_ready WHERE pack=?",
                           (str(pack_name),)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = dict(zip(_COLUMNS, row))
    try:
        data["parts"] = json.loads(data["parts"] or "{}")
    except ValueError:
        data["parts"] = {}
    return data


def all_status(db_path=None) -> List[Dict[str, Any]]:
    conn = connect(db_path)
    try:
        names = [row[0] for row in conn.execute("SELECT pack FROM marketing_ready ORDER BY approval_id, pack")]
    finally:
        conn.close()
    return [item for item in (status(name, db_path) for name in names) if item]


# ---- build one pack ------------------------------------------------------------
def build_for_pack(pack_dir: Path | str, db_path=None, *, products_dir: Optional[Path | str] = None,
                   force: bool = False, modules: Optional[Dict[str, Any]] = None,
                   now: Optional[datetime.datetime] = None, timeout: float = STEP_TIMEOUT,
                   save: bool = True, pack_digest: str = "") -> Dict[str, Any]:
    """Make (or refresh) all marketing files of one pack. Never raises.

    Returns {"pack", "status" ready|partial|failed, "line", "parts", "built" [parts rebuilt],
             "files" count, "changed" bool, "marketing_dir", "manifest", "error"}.
    """
    pack_path = Path(pack_dir)
    result: Dict[str, Any] = {"pack": pack_path.name, "status": "failed", "line": "", "parts": {},
                              "built": [], "files": 0, "changed": False, "marketing_dir": "",
                              "manifest": "", "error": ""}
    try:
        if pack_path.is_symlink() or not pack_path.is_dir():
            raise ValueError("pack folder not found")
        pack_path = pack_path.resolve()
        result["pack"] = pack_path.name
        if listing_path(pack_path) is None:
            raise ValueError("pack has no listing.txt or listing.md")
        root = marketing_root(products_dir or products_dir_of(pack_path))
        out = root / pack_path.name
        if (pack_path.name == MARKETING_FOLDER or pack_path.parent.name == MARKETING_FOLDER
                or (pack_path / MANIFEST_NAME).is_file()):
            raise ValueError("a marketing folder is not a pack")
        if not is_private_dir(out):
            raise ValueError("marketing folder is not private (not git-ignored); nothing written")
        result["marketing_dir"] = str(out)
        result["manifest"] = str(out / MANIFEST_NAME)
        _build(pack_path, root, out, result, force, resolve_modules(modules), now, timeout)
    except Exception as error:   # never stop the product build
        result["error"] = clean_error(f"{type(error).__name__}: {error}")
        log.warning("marketing_auto: %s: %s", result["pack"], result["error"])
    if save:
        try:
            record(result, db_path, pack_digest=pack_digest, now=now)
        except Exception as error:
            log.warning("marketing_auto: %s: database note failed: %s", result["pack"], clean_error(error))
    return result


def _build(pack_path: Path, root: Path, out: Path, result: Dict[str, Any], force: bool,
           modules: Dict[str, Any], now, timeout: float) -> None:
    old = load_manifest(out)
    old_parts = old.get("parts", {}) if old else {}
    sources = source_hashes(pack_path)
    plan: Dict[str, Tuple[bool, str, str]] = {}
    for part in PARTS:
        fingerprint = part_fingerprint(part, sources, modules)
        rebuild, reason = needs_build(part, old_parts.get(part), fingerprint, out)
        plan[part] = (rebuild or force, "forced" if force and not rebuild else reason, fingerprint)
    if plan["pins"][0] and not plan["calendar"][0]:
        plan["calendar"] = (True, "Pins changed", plan["calendar"][2])   # the plan names Pin files

    parts: Dict[str, Dict[str, Any]] = {}
    stage: Optional[str] = None
    try:
        for part in PARTS:
            rebuild, reason, fingerprint = plan[part]
            if not rebuild:
                parts[part] = dict(old_parts[part])
                continue
            if stage is None:
                stage = tempfile.mkdtemp(prefix="marketing_auto_")
                view = make_view(pack_path, Path(stage))
                out.mkdir(parents=True, exist_ok=True)
            remove_part_files(out, part)
            try:
                error, meta = MAKERS[part](view, root, out, modules, timeout, now)
            except Exception as exc:
                error, meta = f"{type(exc).__name__}: {exc}", {}
            files = part_files(out, part)
            if not error and not files:
                error = "maker wrote no files"
            entry = {"status": "failed" if error else "ok", "fingerprint": fingerprint,
                     "files": files, "meta": meta or {}, "reason": reason}
            if error:
                entry["error"] = clean_error(error)
                log.warning("marketing_auto: %s: %s failed: %s", pack_path.name, part, entry["error"])
            parts[part] = entry
            result["built"].append(part)
    finally:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)

    line = summary_line(parts)
    all_files: Dict[str, str] = {}
    for part in PARTS:
        all_files.update(parts[part].get("files") or {})
    manifest = {"version": FORMAT_VERSION, "pack": pack_path.name, "status": overall_status(parts),
                "line": line, "parts": parts, "files": all_files,
                "note": "Private marketing files. The owner reviews and posts by hand."}
    comparable = {key: value for key, value in old.items() if key != "updated_at"} if old else None
    if comparable != manifest:
        manifest_out = dict(manifest)
        manifest_out["updated_at"] = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="seconds")
        out.mkdir(parents=True, exist_ok=True)
        make_marketing.atomic_write_json(out / MANIFEST_NAME, manifest_out)
        result["changed"] = True
    result.update(status=manifest["status"], line=line, files=len(all_files),
                  parts={part: {"status": parts[part]["status"], "built": part in result["built"],
                                "error": parts[part].get("error", ""), "meta": parts[part].get("meta", {}),
                                "files": len(parts[part].get("files") or {})} for part in PARTS})


def run_for_pack(pack_dir: Path | str, db_path=None, **options: Any) -> str:
    """Make the marketing files of one pack and return the Tenglish line for the READY message.

    Example: "Marketing files ready: 11 Pins, 5 Posts, 30 rojula plan, FAQ, landing page, captions".
    Returns "" when nothing could be made (the reason is logged and saved in the database).
    """
    return build_for_pack(pack_dir, db_path, **options).get("line", "")


# ---- READY packs: new ones first, then backfill (max 3 per run) ----------------
def ready_packs(db_path=None) -> List[Dict[str, Any]]:
    """READY, owner-approved packs from the delivery table, unsent READY messages first."""
    conn = guardrails.connect(db_path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_deliveries'").fetchone():
            return []
        rows = conn.execute(
            "SELECT d.approval_id, d.folder, d.digest, d.notified FROM product_deliveries d "
            "JOIN approvals a ON a.id = d.approval_id "
            "WHERE d.status='ready' AND a.status='approved' AND COALESCE(d.folder,'') != '' "
            "ORDER BY d.notified, d.approval_id").fetchall()
    finally:
        conn.close()
    return [dict(zip(("id", "folder", "digest", "notified"), row)) for row in rows]


def _wants_build(item: Dict[str, Any], row: Optional[Dict[str, Any]], out: Path, today: str) -> bool:
    if row is None or row.get("pack_digest") != (item.get("digest") or ""):
        return True
    if row.get("status") == "ready":
        return not (out / MANIFEST_NAME).is_file()
    return row.get("day") != today      # failed / partial: one retry per day, no hammering


def _pack_matches_receipt(pack_dir: Path, digest: str) -> bool:
    """Only the exact files that were encrypted and announced as READY get marketing."""
    if not digest:
        return False
    try:
        import product_pipeline
        return product_pipeline._inventory(str(pack_dir))[1] == digest
    except Exception:
        return False


def run_ready(db_path=None, products_dir: Optional[Path | str] = None, limit: int = BACKFILL_LIMIT,
              modules: Optional[Dict[str, Any]] = None,
              now: Optional[datetime.datetime] = None) -> Dict[int, str]:
    """Hook for product_pipeline.notify_ready. Never raises.

    Builds marketing for at most `limit` READY packs that still need it (packs whose READY message
    is not sent yet go first, older packs are the backfill) and returns {approval id: Tenglish line}
    for every READY pack that has marketing files.
    """
    lines: Dict[int, str] = {}
    try:
        import product_builder
        base = Path(products_dir or product_builder.PRODUCTS_DIR)
        holds = getattr(product_builder, "OWNER_HOLDS", {})
        today = _today(now).isoformat()
        built = 0
        for item in ready_packs(db_path):
            aid, folder = item["id"], str(item["folder"])
            if aid in holds or os.sep in folder or "/" in folder or folder in (".", "..", MARKETING_FOLDER):
                continue
            pack_dir = base / folder
            row = status(folder, db_path)
            if (built < max(0, int(limit)) and _wants_build(item, row, marketing_root(base) / folder, today)
                    and pack_dir.is_dir() and not pack_dir.is_symlink()
                    and _pack_matches_receipt(pack_dir, item.get("digest") or "")):
                built += 1
                result = build_for_pack(pack_dir, db_path, products_dir=base, modules=modules, now=now,
                                        pack_digest=item.get("digest") or "")
                row = {"line": result.get("line", ""), "pack_digest": item.get("digest") or ""}
            if row and row.get("line") and row.get("pack_digest") == (item.get("digest") or ""):
                lines[aid] = row["line"]
    except Exception as error:
        log.warning("marketing_auto: READY hook failed: %s", clean_error(f"{type(error).__name__}: {error}"))
    return lines


# ---- command line ---------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Marketing files for READY packs (private folder only).")
    parser.add_argument("--pack", help="one pack folder")
    parser.add_argument("--backfill", action="store_true",
                        help=f"READY packs without marketing files (max {BACKFILL_LIMIT} per run)")
    parser.add_argument("--status", action="store_true", help="show what the database says")
    parser.add_argument("--force", action="store_true", help="rebuild every part")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    if args.pack:
        result = build_for_pack(args.pack, args.db, force=args.force)
        print(result["line"] or f"Marketing files raledu: {result['error'] or result['status']}")
        for part in PARTS:
            info = result["parts"].get(part) or {}
            print(f"  {part:9} {info.get('status', 'not run'):7} "
                  f"{'rebuilt' if info.get('built') else 'kept':7} {info.get('error', '')}")
        return 0 if result["status"] == "ready" else 1
    if args.backfill:
        lines = run_ready(args.db)
        for aid in sorted(lines):
            print(f"#{aid}: {lines[aid]}")
        if not lines:
            print("Marketing files unna READY packs levu.")
        return 0
    if args.status:
        rows = all_status(args.db)
        for row in rows:
            print(f"{row['pack']}: {row['status']} — {row['line'] or row['error']}")
        if not rows:
            print("Inka marketing files record kaaledu.")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
