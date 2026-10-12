"""Turns drafts YOU approved into plain static web pages in site/.

  python3 site_builder.py

Only drafts with status 'approved' are built. Everything is HTML-escaped, there are no scripts,
no trackers, no external files, and no income promises are added. Building a page does NOT
publish it: you decide when and where to upload site/. Generated pages are git-ignored so a
public repository never leaks drafts.
"""
import datetime
import html
import os
import re
import sqlite3
import sys

import guardrails

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(BASE_DIR, "site")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>body{{font:18px/1.6 system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1.2rem;color:#1b1b1f}}
h1{{line-height:1.2}}footer{{margin-top:3rem;font-size:.8rem;color:#666}}</style></head>
<body><main>
{body}
</main><footer>Written with AI assistance and approved by the site owner. No income or results are promised.</footer>
</body></html>
"""


def slugify(text, fallback):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]
    return slug or fallback


def render_body(text):
    """Very small markdown subset: '# ' / '## ' headings, blank-line paragraphs. Everything escaped."""
    parts, title = [], None
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l.rstrip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith("# "):
            title = title or first[2:].strip()
            parts.append(f"<h1>{html.escape(first[2:].strip())}</h1>")
            lines = lines[1:]
        elif first.startswith("## "):
            parts.append(f"<h2>{html.escape(first[3:].strip())}</h2>")
            lines = lines[1:]
        if lines:
            parts.append("<p>" + "<br>\n".join(html.escape(l.strip()) for l in lines) + "</p>")
    return title, "\n".join(parts)


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS site_pages (approval_id INTEGER PRIMARY KEY, slug TEXT, built_ts TEXT)")
    return conn


def build(db_path=None, site_dir=None):
    site_dir = site_dir or SITE_DIR
    os.makedirs(site_dir, exist_ok=True)
    conn = connect(db_path)
    rows = conn.execute("SELECT a.id, a.title, a.payload FROM approvals a "
                        "WHERE a.kind = 'public_draft' AND a.status = 'approved' "
                        "AND a.id NOT IN (SELECT approval_id FROM site_pages) ORDER BY a.id").fetchall()
    built = []
    for approval_id, title, payload in rows:
        page_title, body = render_body(payload)
        slug = f"{approval_id}-{slugify(page_title or title, 'page')}"
        folder = os.path.join(site_dir, slug)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
            f.write(PAGE.format(title=html.escape(page_title or title), body=body))
        conn.execute("INSERT INTO site_pages (approval_id, slug, built_ts) VALUES (?, ?, ?)",
                     (approval_id, slug, datetime.datetime.now().isoformat(timespec="seconds")))
        built.append(slug)
    conn.commit()
    pages = conn.execute("SELECT slug FROM site_pages ORDER BY approval_id").fetchall()
    conn.close()
    if pages:
        items = "\n".join(f'<li><a href="{html.escape(s)}/">{html.escape(s)}</a></li>' for (s,) in pages)
        with open(os.path.join(site_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(PAGE.format(title="Pages", body=f"<h1>Pages</h1>\n<ul>\n{items}\n</ul>"))
    return built


if __name__ == "__main__":
    out = build()
    print(f"Built {len(out)} new page(s) in site/: {', '.join(out) or 'none (approve a draft first)'}")
    print("Review them, then upload site/ yourself. Nothing was published.")
