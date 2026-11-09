#!/usr/bin/env python3
"""sitemap_builder.py — Generates sitemap.xml, robots.txt, and index.html for landing pages.

Scans exported_content/marketing/*/landing/index.html (without importing landing_page.py)
and builds:
  - exported_content/marketing/site/sitemap.xml (standard valid sitemap with <urlset>, <url>, <loc>, <lastmod>)
  - exported_content/marketing/site/robots.txt (allowing crawlers and referencing Sitemap: <base_url>/sitemap.xml)
  - exported_content/marketing/site/index.html (clean directory linking each landing page with title & description)

Rules & constraints:
  - Base URL configurable via --base-url (default: https://example.com placeholder)
  - Scans whatever exists in marketing directory
  - Never import landing_page.py
  - Outputs strictly outside private pack folders into exported_content/marketing/site/
  - Atomic writes and deterministic ordering (alphabetical by pack name / slug)
  - Valid XML (escaped entities, parseable by xml.etree.ElementTree)
  - Valid, cleanly-escaped HTML
  - Owner-facing text in Tenglish house style (Telugu sentence words + English nouns)
  - No money amounts or currency symbols anywhere
  - No brand logos or unauthorized names
  - Nothing auto-posts, publishes, or emails
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

# Default fallback URL
DEFAULT_BASE_URL = "https://example.com"
DEFAULT_MARKETING_DIR = "exported_content/marketing"
DEFAULT_SITE_DIR = "exported_content/marketing/site"

XML_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"


class MetaTagParser(HTMLParser):
    """Safe HTMLParser to extract <title> and <meta name="..." content="...">."""

    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: List[str] = []
        self.title: str = ""
        self.description: str = ""
        self.og_title: str = ""
        self.og_description: str = ""
        self.canonical: str = ""

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag_lower == "title":
            self.in_title = True
        elif tag_lower == "meta":
            name = attr_dict.get("name", "").lower()
            prop = attr_dict.get("property", "").lower()
            content = attr_dict.get("content", "").strip()

            if name == "description" and not self.description:
                self.description = content
            elif prop == "og:title" and not self.og_title:
                self.og_title = content
            elif prop == "og:description" and not self.og_description:
                self.og_description = content
        elif tag_lower == "link":
            rel = attr_dict.get("rel", "").lower()
            href = attr_dict.get("href", "").strip()
            if rel == "canonical" and not self.canonical:
                self.canonical = href

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False
            if not self.title:
                self.title = "".join(self.title_parts).strip()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


def parse_page_metadata(html_content: str, fallback_title: str = "") -> Dict[str, str]:
    """Extract title and description from HTML page text."""
    parser = MetaTagParser()
    try:
        parser.feed(html_content)
    except Exception:
        pass

    final_title = parser.title or parser.og_title or fallback_title
    final_desc = parser.description or parser.og_description or ""

    # Clean whitespace
    final_title = re.sub(r"\s+", " ", final_title).strip()
    final_desc = re.sub(r"\s+", " ", final_desc).strip()

    return {
        "title": final_title,
        "description": final_desc,
        "canonical": parser.canonical,
    }


def scan_landing_pages(marketing_dir: str | Path) -> List[Dict[str, Any]]:
    """Scan marketing_dir/*/landing/index.html deterministically.

    Returns list of dicts:
      [
        {
          "pack_slug": "pack-name",
          "file_path": Path(...),
          "rel_url": "pack-name/landing/",
          "title": "...",
          "description": "...",
          "mtime_iso": "YYYY-MM-DD",
        },
        ...
      ]
    Sorted alphabetically by pack_slug.
    """
    mkt_path = Path(marketing_dir)
    if not mkt_path.is_dir():
        return []

    entries: List[Dict[str, Any]] = []

    # Iterate over child directories
    try:
        children = sorted([p for p in mkt_path.iterdir() if p.is_dir()], key=lambda p: p.name)
    except Exception:
        return []

    for child in children:
        # Avoid the site/ output directory itself if inside marketing
        if child.name == "site":
            continue
        landing_index = child / "landing" / "index.html"
        if landing_index.is_file():
            try:
                content = landing_index.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""

            fallback_name = child.name.replace("-", " ").replace("_", " ").title()
            meta = parse_page_metadata(content, fallback_title=fallback_name)

            # File last modified date for lastmod in sitemap
            try:
                mtime = landing_index.stat().st_mtime
                dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                mtime_iso = dt.strftime("%Y-%m-%d")
            except Exception:
                mtime_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            entries.append({
                "pack_slug": child.name,
                "file_path": landing_index,
                "rel_url": f"{child.name}/landing/",
                "title": meta["title"] or fallback_name,
                "description": meta["description"],
                "mtime_iso": mtime_iso,
            })

    # Sort deterministically by pack_slug
    entries.sort(key=lambda x: x["pack_slug"])
    return entries


def clean_base_url(base_url: str) -> str:
    """Normalize base URL ensuring protocol and no trailing slash."""
    url = (base_url or "").strip()
    if not url:
        return DEFAULT_BASE_URL
    # Ensure scheme
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
    return url.rstrip("/")


def generate_sitemap_xml(entries: List[Dict[str, Any]], base_url: str) -> str:
    """Generate standard valid sitemap.xml content deterministically."""
    clean_base = clean_base_url(base_url)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<urlset xmlns="{XML_NAMESPACE}">',
    ]

    # Include root index if desired
    root_loc = html.escape(clean_base + "/", quote=True)
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines.append("  <url>")
    lines.append(f"    <loc>{root_loc}</loc>")
    lines.append(f"    <lastmod>{today_iso}</lastmod>")
    lines.append("    <changefreq>weekly</changefreq>")
    lines.append("    <priority>1.0</priority>")
    lines.append("  </url>")

    for entry in entries:
        full_url = f"{clean_base}/{entry['rel_url']}"
        loc_escaped = html.escape(full_url, quote=True)
        lastmod = entry.get("mtime_iso") or today_iso

        lines.append("  <url>")
        lines.append(f"    <loc>{loc_escaped}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("    <changefreq>monthly</changefreq>")
        lines.append("    <priority>0.8</priority>")
        lines.append("  </url>")

    lines.append("</urlset>\n")
    return "\n".join(lines)


def generate_robots_txt(base_url: str) -> str:
    """Generate robots.txt allowing search engines and pointing to sitemap.xml."""
    clean_base = clean_base_url(base_url)
    sitemap_url = f"{clean_base}/sitemap.xml"
    content = (
        "User-agent: *\n"
        "Allow: /\n\n"
        f"Sitemap: {sitemap_url}\n"
    )
    return content


def generate_index_html(entries: List[Dict[str, Any]], base_url: str) -> str:
    """Generate clean, accessible index.html directory of landing pages."""
    clean_base = clean_base_url(base_url)

    cards_html = []
    if not entries:
        cards_html.append("    <p class=\"empty-msg\">Landing pages emi levu. Drafts approve chesi landing pages build cheyandi.</p>")
    else:
        for entry in entries:
            slug = html.escape(entry["pack_slug"])
            title = html.escape(entry["title"])
            desc = html.escape(entry["description"]) if entry["description"] else "Mee personal printable template and planning toolkit."
            url = f"../{entry['rel_url']}"  # Relative from site/ folder to pack/landing/

            card = (
                f'    <article class="card">\n'
                f'      <h2><a href="{html.escape(url)}">{title}</a></h2>\n'
                f'      <p class="desc">{desc}</p>\n'
                f'      <div class="meta">\n'
                f'        <span class="badge">{slug}</span>\n'
                f'        <a class="view-link" href="{html.escape(url)}">View Landing Page &rarr;</a>\n'
                f'      </div>\n'
                f'    </article>'
            )
            cards_html.append(card)

    cards_joined = "\n".join(cards_html)

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Products Directory — Digital Planners &amp; Templates</title>
  <meta name="description" content="Browse our curated collection of printable templates, organizers, and productivity toolkits.">
  <style>
    :root {{
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #0f172a;
      --muted: #475569;
      --border: #e2e8f0;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --tag-bg: #f1f5f9;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      line-height: 1.6;
      background-color: var(--bg);
      color: var(--text);
      padding: 2rem 1.5rem;
    }}
    .container {{
      max-width: 900px;
      margin: 0 auto;
    }}
    header {{
      margin-bottom: 2.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.5rem;
    }}
    h1 {{
      font-size: 2rem;
      font-weight: 700;
      color: var(--text);
      letter-spacing: -0.025em;
      margin-bottom: 0.5rem;
    }}
    .subtitle {{
      color: var(--muted);
      font-size: 1.1rem;
    }}
    .grid {{
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.5rem;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }}
    .card:hover {{
      border-color: #cbd5e1;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }}
    .card h2 {{
      font-size: 1.25rem;
      margin-bottom: 0.5rem;
    }}
    .card h2 a {{
      color: var(--primary);
      text-decoration: none;
    }}
    .card h2 a:hover {{
      text-decoration: underline;
      color: var(--primary-hover);
    }}
    .card .desc {{
      color: var(--muted);
      font-size: 0.95rem;
      margin-bottom: 1rem;
    }}
    .meta {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.85rem;
    }}
    .badge {{
      background: var(--tag-bg);
      color: var(--muted);
      padding: 0.2rem 0.6rem;
      border-radius: 4px;
      font-family: monospace;
    }}
    .view-link {{
      color: var(--primary);
      text-decoration: none;
      font-weight: 500;
    }}
    .view-link:hover {{
      text-decoration: underline;
    }}
    .empty-msg {{
      color: var(--muted);
      padding: 2rem;
      background: var(--card-bg);
      border-radius: 8px;
      text-align: center;
      border: 1px dashed var(--border);
    }}
    footer {{
      margin-top: 3.5rem;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border);
      font-size: 0.85rem;
      color: var(--muted);
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Products Directory</h1>
      <p class="subtitle">Explore our personal planning templates, printables, and digital guides.</p>
    </header>
    <main class="grid">
{cards_joined}
    </main>
    <footer>
      <p>For personal use only. Designed with care.</p>
    </footer>
  </div>
</body>
</html>
"""
    return page


def atomic_write(target_path: Path, content: str) -> None:
    """Write string content atomically via a temporary file in the same directory."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = target_path.parent
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        temp_file = Path(tf.name)
        tf.write(content)
        tf.flush()
        os.fsync(tf.fileno())

    os.replace(temp_file, target_path)


def format_tenglish_summary(count: int, site_dir: Path) -> str:
    """Format owner notification in Tenglish house style."""
    if count == 0:
        return f"Landing pages emi levu, site folder empty ga undi ({site_dir})."
    elif count == 1:
        return f"1 landing page sitemap, robots.txt mariyu index ready ayyindi ({site_dir}) ✓."
    else:
        return f"{count} landing pages sitemap, robots.txt mariyu index ready ayyindi ({site_dir}) ✓."


def build_site_index(
    marketing_dir: str | Path = DEFAULT_MARKETING_DIR,
    site_dir: str | Path = DEFAULT_SITE_DIR,
    base_url: str = DEFAULT_BASE_URL,
) -> Dict[str, Any]:
    """Scan landing pages and build sitemap.xml, robots.txt, and index.html."""
    mkt_path = Path(marketing_dir)
    out_path = Path(site_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    entries = scan_landing_pages(mkt_path)

    # 1. Generate sitemap.xml
    sitemap_content = generate_sitemap_xml(entries, base_url)
    sitemap_path = out_path / "sitemap.xml"
    atomic_write(sitemap_path, sitemap_content)

    # 2. Generate robots.txt
    robots_content = generate_robots_txt(base_url)
    robots_path = out_path / "robots.txt"
    atomic_write(robots_path, robots_content)

    # 3. Generate index.html
    index_content = generate_index_html(entries, base_url)
    index_path = out_path / "index.html"
    atomic_write(index_path, index_content)

    tenglish_summary = format_tenglish_summary(len(entries), out_path)

    return {
        "status": "ok",
        "count": len(entries),
        "base_url": clean_base_url(base_url),
        "marketing_dir": str(mkt_path),
        "site_dir": str(out_path),
        "sitemap_path": str(sitemap_path),
        "robots_path": str(robots_path),
        "index_path": str(index_path),
        "tenglish_summary": tenglish_summary,
        "pages": [
            {
                "pack_slug": e["pack_slug"],
                "title": e["title"],
                "rel_url": e["rel_url"],
            }
            for e in entries
        ],
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build sitemap.xml, robots.txt, and landing pages index.html."
    )
    parser.add_argument(
        "--marketing-dir",
        default=DEFAULT_MARKETING_DIR,
        help=f"Marketing directory to scan (default: {DEFAULT_MARKETING_DIR}).",
    )
    parser.add_argument(
        "--site-dir",
        default=DEFAULT_SITE_DIR,
        help=f"Target output directory (default: {DEFAULT_SITE_DIR}).",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base website URL for sitemap.xml and robots.txt (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console output.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    res = build_site_index(
        marketing_dir=args.marketing_dir,
        site_dir=args.site_dir,
        base_url=args.base_url,
    )

    if not args.quiet:
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(res["tenglish_summary"])
            print(f"Base URL: {res['base_url']}")
            print(f"Files written in: {res['site_dir']}")
            print(f"  - {res['sitemap_path']}")
            print(f"  - {res['robots_path']}")
            print(f"  - {res['index_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
