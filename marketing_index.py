#!/usr/bin/env python3
"""marketing_index.py — One page to browse all marketing files.

Scans exported_content/marketing/ and generates a self-contained, responsive
exported_content/marketing/index.html to browse all generated marketing assets.

Features:
  - One section per pack with relative links and thumbnails for covers, pins,
    carousel slides, and reel frames.
  - Links to blog.md, LAUNCH.md, shorts.md, landing page, and review requests.
  - Status row for each pack showing ready/missing for every asset type.
  - Global overview table of all packs and readiness counts.
  - Works gracefully with any subset of assets present (or none).
  - Pure self-contained HTML with inline CSS, zero external fonts or CDN calls.
  - HTML-escapes all pack names, paths, and dynamic contents.
  - Atomic file writes via temporary file and replace.
  - Deterministic outputs (re-runs with identical inputs yield identical HTML).
  - Tenglish house style owner-facing guidance without money amounts or brand logos.
"""

from __future__ import annotations

import argparse
import html
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

GLOBAL_NON_PACK_DIRS = {
    "catalog",
    "collections",
    "newsletter",
    "site",
    "ops",
    "shared",
    "common",
    "bundles",
}

STATUS_KEYS = (
    ("covers", "Covers"),
    ("pins", "Pins"),
    ("carousel", "Carousel"),
    ("reel", "Reel"),
    ("blog", "Blog"),
    ("launch", "Launch Checklist"),
    ("shorts", "Shorts"),
    ("landing", "Landing Page"),
    ("reviews", "Review Requests"),
)


def _safe_rel(target: Path, base: Path) -> str:
    """Return normalized POSIX relative path from base to target."""
    try:
        return target.relative_to(base).as_posix()
    except ValueError:
        return os.path.relpath(str(target), str(base)).replace("\\", "/")


def _find_images(directory: Path, base: Path) -> List[str]:
    """Find and return deterministically sorted relative paths to images in directory."""
    if not directory.is_dir():
        return []
    images = [
        _safe_rel(p, base)
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(images)


def _find_covers(pack_dir: Path, base: Path) -> List[str]:
    """Locate cover images in covers/ subdir or pack root."""
    results: List[str] = []
    covers_subdir = pack_dir / "covers"
    if covers_subdir.is_dir():
        results.extend(_find_images(covers_subdir, base))
    # Also check if pack root has direct cover image files
    for p in pack_dir.iterdir():
        if p.is_file() and p.stem.lower().startswith("cover") and p.suffix.lower() in IMAGE_EXTENSIONS:
            rel = _safe_rel(p, base)
            if rel not in results:
                results.append(rel)
    return sorted(results)


def _find_pins(pack_dir: Path, base: Path) -> List[str]:
    """Locate pin images in pins/ subdir or pack root."""
    pins_subdir = pack_dir / "pins"
    if pins_subdir.is_dir():
        return _find_images(pins_subdir, base)
    results = [
        _safe_rel(p, base)
        for p in pack_dir.iterdir()
        if p.is_file() and p.stem.lower().startswith("pin") and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(results)


def _find_carousel(pack_dir: Path, base: Path) -> List[str]:
    """Locate carousel slide images in carousel/ subdir."""
    carousel_subdir = pack_dir / "carousel"
    if carousel_subdir.is_dir():
        return _find_images(carousel_subdir, base)
    results = [
        _safe_rel(p, base)
        for p in pack_dir.iterdir()
        if p.is_file() and p.stem.lower().startswith("slide") and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(results)


def _find_reel(pack_dir: Path, base: Path) -> List[str]:
    """Locate reel frame images in reel/ subdir."""
    reel_subdir = pack_dir / "reel"
    if reel_subdir.is_dir():
        return _find_images(reel_subdir, base)
    results = [
        _safe_rel(p, base)
        for p in pack_dir.iterdir()
        if p.is_file() and p.stem.lower().startswith("frame") and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(results)


def _find_blog(pack_dir: Path, base: Path) -> Optional[str]:
    """Locate blog.md in pack folder or blog/ subdir."""
    candidates = [
        pack_dir / "blog.md",
        pack_dir / "blog" / "blog.md",
        pack_dir / "blog" / "index.md",
    ]
    for c in candidates:
        if c.is_file():
            return _safe_rel(c, base)
    return None


def _find_launch(pack_dir: Path, base: Path) -> Optional[str]:
    """Locate LAUNCH.md checklist in pack folder."""
    candidates = [
        pack_dir / "LAUNCH.md",
        pack_dir / "launch.md",
        pack_dir / "launch_checklist.md",
        pack_dir / "LAUNCH_CHECKLIST.md",
    ]
    for c in candidates:
        if c.is_file():
            return _safe_rel(c, base)
    return None


def _find_shorts(pack_dir: Path, base: Path) -> Optional[str]:
    """Locate shorts.md script in reel/ or pack folder."""
    candidates = [
        pack_dir / "reel" / "shorts.md",
        pack_dir / "shorts.md",
        pack_dir / "shorts" / "shorts.md",
    ]
    for c in candidates:
        if c.is_file():
            return _safe_rel(c, base)
    return None


def _find_landing(pack_dir: Path, base: Path) -> Optional[str]:
    """Locate landing page HTML in landing/ subdir or pack folder."""
    candidates = [
        pack_dir / "landing" / "index.html",
        pack_dir / "landing.html",
        pack_dir / "landing" / "landing.html",
        pack_dir / "landing" / "index.htm",
    ]
    for c in candidates:
        if c.is_file():
            return _safe_rel(c, base)
    return None


def _find_reviews(pack_dir: Path, base: Path) -> Optional[str]:
    """Locate review requests template/file in pack folder or reviews/ subdir."""
    candidates = [
        pack_dir / "review_request.md",
        pack_dir / "review_requests.md",
        pack_dir / "review-request.md",
        pack_dir / "review-requests.md",
        pack_dir / "review_request.txt",
        pack_dir / "review_requests.txt",
        pack_dir / "review-request.txt",
        pack_dir / "review-requests.txt",
        pack_dir / "review.md",
        pack_dir / "reviews.md",
        pack_dir / "review.txt",
        pack_dir / "reviews.txt",
        pack_dir / "reviews" / "review_request.md",
        pack_dir / "reviews" / "review_requests.md",
        pack_dir / "reviews" / "review_request.txt",
        pack_dir / "reviews" / "review.md",
        pack_dir / "reviews" / "index.html",
        pack_dir / "review_requests" / "index.html",
    ]
    for c in candidates:
        if c.is_file():
            return _safe_rel(c, base)
    return None


def _find_other_files(pack_dir: Path, base: Path, known_rel_files: Iterable[str]) -> List[str]:
    """Collect other non-image files under pack folder (e.g. caption.txt, alt.txt, seo_meta.json)."""
    known_set = set(known_rel_files)
    others: List[str] = []
    for root, _, files in os.walk(str(pack_dir)):
        for f in files:
            p = Path(root) / f
            rel = _safe_rel(p, base)
            if rel in known_set:
                continue
            if p.suffix.lower() in IMAGE_EXTENSIONS:
                continue
            # Ignore hidden files
            if f.startswith("."):
                continue
            others.append(rel)
    return sorted(others)


def scan_pack(pack_dir: Path, marketing_dir: Path) -> Dict[str, Any]:
    """Scan a single pack folder and return its structured marketing asset summary."""
    covers = _find_covers(pack_dir, marketing_dir)
    pins = _find_pins(pack_dir, marketing_dir)
    carousel = _find_carousel(pack_dir, marketing_dir)
    reel = _find_reel(pack_dir, marketing_dir)

    blog = _find_blog(pack_dir, marketing_dir)
    launch = _find_launch(pack_dir, marketing_dir)
    shorts = _find_shorts(pack_dir, marketing_dir)
    landing = _find_landing(pack_dir, marketing_dir)
    reviews = _find_reviews(pack_dir, marketing_dir)

    known_links: List[str] = []
    for item in (blog, launch, shorts, landing, reviews):
        if item:
            known_links.append(item)

    other_files = _find_other_files(pack_dir, marketing_dir, known_links)

    status = {
        "covers": "ready" if bool(covers) else "missing",
        "pins": "ready" if bool(pins) else "missing",
        "carousel": "ready" if bool(carousel) else "missing",
        "reel": "ready" if bool(reel) else "missing",
        "blog": "ready" if bool(blog) else "missing",
        "launch": "ready" if bool(launch) else "missing",
        "shorts": "ready" if bool(shorts) else "missing",
        "landing": "ready" if bool(landing) else "missing",
        "reviews": "ready" if bool(reviews) else "missing",
    }

    ready_count = sum(1 for val in status.values() if val == "ready")
    total_count = len(status)

    return {
        "id": pack_dir.name,
        "covers": covers,
        "pins": pins,
        "carousel": carousel,
        "reel": reel,
        "blog": blog,
        "launch": launch,
        "shorts": shorts,
        "landing": landing,
        "reviews": reviews,
        "other_files": other_files,
        "status": status,
        "ready_count": ready_count,
        "total_count": total_count,
        "is_complete": ready_count == total_count,
    }


def scan_marketing(
    marketing_dir: Path | str,
    exclude_dirs: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Scan marketing_dir for pack folders and return deterministically sorted asset metadata."""
    m_dir = Path(marketing_dir)
    if not m_dir.is_dir():
        return []

    excludes = set(GLOBAL_NON_PACK_DIRS)
    if exclude_dirs:
        excludes.update(exclude_dirs)

    pack_dirs = [
        p for p in m_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in excludes
    ]
    pack_dirs.sort(key=lambda p: p.name)

    results: List[Dict[str, Any]] = []
    for pdir in pack_dirs:
        results.append(scan_pack(pdir, m_dir))
    return results


def _render_thumbnail_grid(label: str, images: Sequence[str], empty_hint: str) -> str:
    """Render a thumbnail grid with relative links or a missing notice."""
    e_label = html.escape(label)
    if not images:
        return (
            f'<div class="asset-group empty">'
            f'<h4 class="asset-title">{e_label} <span class="badge missing">missing</span></h4>'
            f'<p class="empty-note">{html.escape(empty_hint)}</p>'
            f'</div>'
        )

    cards = []
    for rel_path in images:
        filename = os.path.basename(rel_path)
        e_path = html.escape(rel_path)
        e_name = html.escape(filename)
        card = (
            f'<a href="{e_path}" class="thumb-card" target="_blank" rel="noopener">'
            f'<img src="{e_path}" alt="{e_name}" class="thumb-img" loading="lazy" />'
            f'<span class="thumb-name">{e_name}</span>'
            f'</a>'
        )
        cards.append(card)

    return (
        f'<div class="asset-group">'
        f'<h4 class="asset-title">{e_label} <span class="badge ready">ready ({len(images)})</span></h4>'
        f'<div class="thumb-grid">' + "".join(cards) + '</div>'
        f'</div>'
    )


def _render_status_row(status: Dict[str, str]) -> str:
    """Render the status row badges (ready vs missing) for one pack."""
    items = []
    for key, label in STATUS_KEYS:
        val = status.get(key, "missing")
        e_label = html.escape(label)
        cls = "ready" if val == "ready" else "missing"
        items.append(
            f'<span class="status-cell {cls}">'
            f'<span class="badge {cls}">{cls}</span> {e_label}'
            f'</span>'
        )
    return '<div class="status-row">' + "".join(items) + '</div>'


def _render_links_section(pack: Dict[str, Any]) -> str:
    """Render links to text assets and landing page."""
    links_data = [
        ("Blog Article", "blog.md", pack.get("blog"), "blog.md inka tayaru cheyaledu"),
        ("Launch Checklist", "LAUNCH.md", pack.get("launch"), "LAUNCH.md file ledhu"),
        ("YouTube Shorts", "shorts.md", pack.get("shorts"), "shorts.md script ledhu"),
        ("Landing Page", "Landing Page", pack.get("landing"), "Landing page build avvaledu"),
        ("Review Requests", "Review Requests", pack.get("reviews"), "Review requests file inka ledhu"),
    ]

    items = []
    for title, short_label, rel_path, missing_hint in links_data:
        e_title = html.escape(title)
        e_short = html.escape(short_label)
        if rel_path:
            e_path = html.escape(rel_path)
            items.append(
                f'<li class="doc-link-item doc-ready">'
                f'<a href="{e_path}" target="_blank" rel="noopener" class="doc-link">'
                f'<span class="doc-icon">&#128196;</span> '
                f'<strong>{e_title}</strong>: <code>{e_short}</code>'
                f'</a>'
                f'</li>'
            )
        else:
            items.append(
                f'<li class="doc-link-item doc-missing">'
                f'<span class="doc-icon">&#9888;</span> '
                f'<span class="doc-muted"><strong>{e_title}</strong>: {html.escape(missing_hint)} (missing)</span>'
                f'</li>'
            )

    others = pack.get("other_files", [])
    other_html = ""
    if others:
        other_items = []
        for o_path in others:
            e_path = html.escape(o_path)
            e_name = html.escape(os.path.basename(o_path))
            other_items.append(
                f'<a href="{e_path}" target="_blank" rel="noopener" class="other-file-pill">'
                f'<code>{e_name}</code>'
                f'</a>'
            )
        other_html = (
            f'<div class="other-files-box">'
            f'<span class="other-files-label">Inka unna files:</span> '
            f'<div class="other-files-list">' + "".join(other_items) + '</div>'
            f'</div>'
        )

    return (
        f'<div class="docs-block">'
        f'<h4 class="asset-title">Links mariyu documents</h4>'
        f'<ul class="doc-link-list">' + "".join(items) + '</ul>'
        f'{other_html}'
        f'</div>'
    )


def _render_pack_section(pack: Dict[str, Any]) -> str:
    """Render the full section for a single pack."""
    pack_id = pack["id"]
    e_id = html.escape(pack_id)
    ready_count = pack["ready_count"]
    total_count = pack["total_count"]
    status_summary = f"{ready_count}/{total_count} ready"
    summary_badge = "ready" if pack["is_complete"] else ("partial" if ready_count > 0 else "missing")

    status_row_html = _render_status_row(pack["status"])
    covers_html = _render_thumbnail_grid("Cover Designs", pack["covers"], "Covers levu (cover images missing)")
    pins_html = _render_thumbnail_grid("Pinterest Pins", pack["pins"], "Pins levu (pin images missing)")
    carousel_html = _render_thumbnail_grid("Instagram Carousel Slides", pack["carousel"], "Carousel slides levu")
    reel_html = _render_thumbnail_grid("Reel Frames", pack["reel"], "Reel frames levu")
    links_html = _render_links_section(pack)

    return (
        f'<section class="pack-section" id="pack-{e_id}">'
        f'<header class="pack-header">'
        f'<div class="pack-title-group">'
        f'<h3 class="pack-id">{e_id}</h3>'
        f'<span class="pack-count-badge {summary_badge}">{html.escape(status_summary)}</span>'
        f'</div>'
        f'<p class="pack-hint">Mee {e_id} marketing files kinda thumbnails mariyu links lo chudavachu.</p>'
        f'</header>'
        f'<div class="status-container">'
        f'<h4 class="status-heading">Status row (asset readiness):</h4>'
        f'{status_row_html}'
        f'</div>'
        f'<div class="assets-grid-container">'
        f'{covers_html}'
        f'{pins_html}'
        f'{carousel_html}'
        f'{reel_html}'
        f'</div>'
        f'{links_html}'
        f'</section>'
    )


def _render_overview_table(packs: Sequence[Dict[str, Any]]) -> str:
    """Render top overview summary table of all packs and readiness status."""
    if not packs:
        return (
            '<div class="empty-state">'
            '<p class="empty-state-title">Packs levu (no pack folders found)</p>'
            '<p class="empty-state-hint">exported_content/marketing/ lo kotha pack marketing assets build aina taruvatha malli run cheyandi.</p>'
            '</div>'
        )

    rows = []
    for pack in packs:
        p_id = pack["id"]
        e_id = html.escape(p_id)
        status = pack["status"]
        cells = []
        for key, _ in STATUS_KEYS:
            st = status.get(key, "missing")
            mark = "&#10003;" if st == "ready" else "&#10007;"
            cells.append(f'<td class="text-center status-{st}">{mark}</td>')

        r_count = pack["ready_count"]
        t_count = pack["total_count"]
        badge_cls = "ready" if pack["is_complete"] else ("partial" if r_count > 0 else "missing")
        row = (
            f'<tr>'
            f'<td><a href="#pack-{e_id}" class="table-pack-link"><strong>{e_id}</strong></a></td>'
            + "".join(cells) +
            f'<td class="text-center"><span class="badge {badge_cls}">{r_count}/{t_count}</span></td>'
            f'</tr>'
        )
        rows.append(row)

    header_cells = "".join(f'<th class="text-center">{html.escape(label)}</th>' for _, label in STATUS_KEYS)

    return (
        f'<div class="overview-box">'
        f'<h3 class="overview-title">Mee packs marketing overview</h3>'
        f'<div class="table-responsive">'
        f'<table class="overview-table">'
        f'<thead>'
        f'<tr>'
        f'<th>Pack ID</th>'
        f'{header_cells}'
        f'<th class="text-center">Total Ready</th>'
        f'</tr>'
        f'</thead>'
        f'<tbody>'
        + "".join(rows) +
        f'</tbody>'
        f'</table>'
        f'</div>'
        f'</div>'
    )


def render_html(
    packs_data: Sequence[Dict[str, Any]],
    title: str = "Chilli Gavva Marketing Hub",
) -> str:
    """Render self-contained, responsive HTML page with inline styling."""
    e_title = html.escape(title)
    packs_count = len(packs_data)
    total_ready_items = sum(p["ready_count"] for p in packs_data)
    total_items = sum(p["total_count"] for p in packs_data)

    overview_html = _render_overview_table(packs_data)
    pack_sections = "".join(_render_pack_section(p) for p in packs_data)

    css = """
    *, *::before, *::after { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #f1f5f9;
      color: #0f172a;
      line-height: 1.5;
    }
    header.site-header {
      background: #0f172a;
      color: #f8fafc;
      padding: 24px 32px;
      border-bottom: 3px solid #38bdf8;
    }
    .header-content {
      max-width: 1280px;
      margin: 0 auto;
    }
    .site-title {
      margin: 0 0 6px 0;
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.5px;
    }
    .site-tagline {
      margin: 0;
      font-size: 14px;
      color: #94a3b8;
    }
    .stats-summary {
      display: flex;
      gap: 16px;
      margin-top: 14px;
      flex-wrap: wrap;
    }
    .stat-pill {
      background: #1e293b;
      padding: 4px 12px;
      border-radius: 9999px;
      font-size: 13px;
      color: #cbd5e1;
      border: 1px solid #334155;
    }
    .stat-pill strong {
      color: #38bdf8;
    }
    main.container {
      max-width: 1280px;
      margin: 28px auto;
      padding: 0 20px;
    }
    .overview-box {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 32px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .overview-title {
      margin: 0 0 14px 0;
      font-size: 18px;
      font-weight: 600;
      color: #1e293b;
    }
    .table-responsive {
      overflow-x: auto;
    }
    table.overview-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    table.overview-table th, table.overview-table td {
      padding: 10px 12px;
      border: 1px solid #e2e8f0;
    }
    table.overview-table th {
      background: #f8fafc;
      color: #475569;
      font-weight: 600;
    }
    table.overview-table td.text-center, table.overview-table th.text-center {
      text-align: center;
    }
    .table-pack-link {
      color: #0284c7;
      text-decoration: none;
    }
    .table-pack-link:hover {
      text-decoration: underline;
    }
    .status-ready {
      color: #166534;
      background: #f0fdf4;
      font-weight: 700;
    }
    .status-missing {
      color: #991b1b;
      background: #fef2f2;
    }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 9999px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.3px;
    }
    .badge.ready {
      background: #dcfce7;
      color: #166534;
      border: 1px solid #bbf7d0;
    }
    .badge.missing {
      background: #fee2e2;
      color: #991b1b;
      border: 1px solid #fecaca;
    }
    .badge.partial {
      background: #fef3c7;
      color: #92400e;
      border: 1px solid #fde68a;
    }
    .pack-section {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 32px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .pack-header {
      margin-bottom: 16px;
      padding-bottom: 12px;
      border-bottom: 1px solid #f1f5f9;
    }
    .pack-title-group {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .pack-id {
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      color: #0f172a;
    }
    .pack-count-badge {
      padding: 3px 10px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 600;
    }
    .pack-hint {
      margin: 4px 0 0 0;
      font-size: 13px;
      color: #64748b;
    }
    .status-container {
      margin-bottom: 20px;
    }
    .status-heading {
      margin: 0 0 8px 0;
      font-size: 13px;
      text-transform: uppercase;
      color: #64748b;
      letter-spacing: 0.5px;
    }
    .status-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .status-cell {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 12px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
    }
    .status-cell.ready {
      background: #f0fdf4;
      border-color: #dcfce7;
      color: #166534;
    }
    .status-cell.missing {
      background: #fef2f2;
      border-color: #fee2e2;
      color: #991b1b;
    }
    .asset-group {
      margin-top: 16px;
      margin-bottom: 20px;
    }
    .asset-group.empty {
      opacity: 0.75;
    }
    .asset-title {
      margin: 0 0 10px 0;
      font-size: 15px;
      font-weight: 600;
      color: #334155;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .empty-note {
      margin: 0;
      font-size: 13px;
      color: #94a3b8;
      font-style: italic;
    }
    .thumb-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
    }
    .thumb-card {
      display: flex;
      flex-direction: column;
      align-items: center;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 8px;
      text-decoration: none;
      color: inherit;
      width: 160px;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .thumb-card:hover {
      transform: translateY(-2px);
      box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
      border-color: #cbd5e1;
    }
    .thumb-img {
      width: 144px;
      height: 144px;
      object-fit: contain;
      background: #ffffff;
      border-radius: 4px;
      display: block;
    }
    .thumb-name {
      margin-top: 6px;
      font-size: 11px;
      text-align: center;
      word-break: break-all;
      color: #475569;
    }
    .docs-block {
      margin-top: 24px;
      padding-top: 18px;
      border-top: 1px dashed #e2e8f0;
    }
    .doc-link-list {
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }
    .doc-link-item {
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 13px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid #e2e8f0;
      background: #f8fafc;
    }
    .doc-link-item.doc-ready {
      background: #ffffff;
      border-color: #cbd5e1;
    }
    .doc-link-item.doc-missing {
      background: #fafafa;
      color: #94a3b8;
      border-style: dashed;
    }
    .doc-link {
      color: #0284c7;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .doc-link:hover {
      text-decoration: underline;
    }
    .doc-link code {
      background: #f1f5f9;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 12px;
      color: #0f172a;
    }
    .doc-muted {
      color: #64748b;
    }
    .other-files-box {
      margin-top: 12px;
      font-size: 12px;
      color: #64748b;
    }
    .other-files-label {
      font-weight: 600;
      margin-right: 6px;
    }
    .other-files-list {
      display: inline-flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 4px;
    }
    .other-file-pill {
      background: #f1f5f9;
      border: 1px solid #e2e8f0;
      padding: 2px 8px;
      border-radius: 4px;
      text-decoration: none;
      color: #334155;
    }
    .other-file-pill:hover {
      background: #e2e8f0;
    }
    footer.site-footer {
      text-align: center;
      padding: 32px 20px;
      font-size: 13px;
      color: #64748b;
      border-top: 1px solid #e2e8f0;
      background: #ffffff;
      margin-top: 48px;
    }
    .empty-state {
      background: #ffffff;
      border: 2px dashed #cbd5e1;
      border-radius: 12px;
      padding: 40px 20px;
      text-align: center;
      margin: 20px 0;
    }
    .empty-state-title {
      font-size: 18px;
      font-weight: 600;
      color: #475569;
      margin: 0 0 6px 0;
    }
    .empty-state-hint {
      font-size: 14px;
      color: #94a3b8;
      margin: 0;
    }
    """

    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="en">\n'
        f'<head>\n'
        f'  <meta charset="utf-8" />\n'
        f'  <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        f'  <title>{e_title}</title>\n'
        f'  <style>{css}</style>\n'
        f'</head>\n'
        f'<body>\n'
        f'  <header class="site-header">\n'
        f'    <div class="header-content">\n'
        f'      <h1 class="site-title">{e_title}</h1>\n'
        f'      <p class="site-tagline">Mee marketing assets anni okka page lo chusukondi. Pack marketing files review cheyandi.</p>\n'
        f'      <div class="stats-summary">\n'
        f'        <span class="stat-pill">Packs: <strong>{packs_count}</strong></span>\n'
        f'        <span class="stat-pill">Ready assets: <strong>{total_ready_items}/{total_items}</strong></span>\n'
        f'        <span class="stat-pill">Posting: <strong>Manual review only (auto-post ledhu)</strong></span>\n'
        f'      </div>\n'
        f'    </div>\n'
        f'  </header>\n'
        f'  <main class="container">\n'
        f'{overview_html}\n'
        f'{pack_sections}\n'
        f'  </main>\n'
        f'  <footer class="site-footer">\n'
        f'    <p>Chilli Gavva Marketing Hub &bull; Owner review kosam tayaru chesina marketing files. Automatic posting ledhu.</p>\n'
        f'  </footer>\n'
        f'</body>\n'
        f'</html>\n'
    )


def atomic_write(target_path: Path | str, content: str) -> Path:
    """Atomically write text content to target_path using UTF-8."""
    dest = Path(target_path).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = dest.parent

    # Create temporary file in same directory to ensure atomic replace across filesystems
    fd, tmp_path_str = tempfile.mkstemp(prefix=".marketing_idx_", suffix=".tmp", dir=temp_dir)
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
    return dest


def build_index(
    marketing_dir: Path | str = "exported_content/marketing",
    output_path: Optional[Path | str] = None,
    title: str = "Chilli Gavva Marketing Hub",
    exclude_dirs: Optional[Iterable[str]] = None,
) -> Path:
    """Scan marketing_dir, generate self-contained index HTML, and write atomically."""
    m_dir = Path(marketing_dir)
    if output_path is None:
        out_file = m_dir / "index.html"
    else:
        out_file = Path(output_path)

    packs_data = scan_marketing(m_dir, exclude_dirs=exclude_dirs)
    html_content = render_html(packs_data, title=title)
    return atomic_write(out_file, html_content)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for marketing_index."""
    parser = argparse.ArgumentParser(
        description="Scan exported_content/marketing/ and write index.html to browse marketing files."
    )
    parser.add_argument(
        "--marketing-dir",
        default="exported_content/marketing",
        help="Marketing directory containing pack folders (default: exported_content/marketing).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML path (default: <marketing-dir>/index.html).",
    )
    parser.add_argument(
        "--title",
        default="Chilli Gavva Marketing Hub",
        help="Title displayed on the generated HTML page.",
    )
    args = parser.parse_args(argv)

    out = build_index(
        marketing_dir=args.marketing_dir,
        output_path=args.output,
        title=args.title,
    )
    print(f"Marketing index generated: {out} (Mee marketing files ready ga unnayyi)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
