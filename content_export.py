"""content_export.py — Export approved content to content/ folder as Markdown.

Reads the approvals table for approved items and writes each to
content/<date>_<slug>_id<N>.md. These files are git-committed, so GitHub Pages
or any static host can serve them.

Usage:
    python3 content_export.py          # export all pending approved items
    python3 content_export.py --list   # show what's been exported
    python3 content_export.py --all    # re-export everything (overwrite)
"""
import datetime
import os
import re
import sqlite3
import sys

import guardrails

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(BASE_DIR, "content")


def _slug(text, maxlen=40):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text[:maxlen].strip("-")


def _already_exported(db_path=None):
    """Return set of approval IDs already written to content/."""
    if not os.path.isdir(CONTENT_DIR):
        return set()
    ids = set()
    for fname in os.listdir(CONTENT_DIR):
        m = re.search(r"_id(\d+)\.md$", fname)
        if m:
            ids.add(int(m.group(1)))
    return ids


def export_approved(db_path=None, overwrite=False):
    """Write each approved item to content/<date>_<slug>_id<N>.md. Returns list of paths."""
    conn = guardrails.connect(db_path)
    rows = conn.execute("""
        SELECT id, title, kind, review, ts, payload
        FROM approvals
        WHERE status = 'approved'
        ORDER BY id
    """).fetchall()
    conn.close()

    already = _already_exported(db_path) if not overwrite else set()
    os.makedirs(CONTENT_DIR, exist_ok=True)

    exported = []
    for (aid, title, kind, review, ts, payload_json) in rows:
        if aid in already:
            continue

        date_str = (ts or "")[:10] or datetime.date.today().isoformat()
        slug = _slug(title or kind or "content")
        fname = f"{date_str}_{slug}_id{aid}.md"
        fpath = os.path.join(CONTENT_DIR, fname)

        # Parse payload for topic / body if stored
        topic = ""
        body = ""
        if payload_json:
            try:
                import json
                p = json.loads(payload_json)
                topic = p.get("topic", "")
                body = p.get("body", "") or p.get("draft", "") or p.get("content", "")
            except Exception:
                pass

        lines = [
            f"# {title or kind}",
            f"",
            f"**Date:** {date_str}  ",
            f"**Kind:** {kind}  ",
            f"**Approval ID:** {aid}",
            f"",
        ]
        if topic:
            lines += [f"**Topic:** {topic}", ""]
        if review:
            lines += ["## Review notes", "", review, ""]
        if body:
            lines += ["## Content", "", body, ""]

        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        exported.append(fpath)

    return exported


def list_exported():
    """Return sorted list of files in content/ with basic info."""
    if not os.path.isdir(CONTENT_DIR):
        return []
    files = []
    for fname in sorted(os.listdir(CONTENT_DIR)):
        if fname.endswith(".md"):
            fpath = os.path.join(CONTENT_DIR, fname)
            size = os.path.getsize(fpath)
            files.append({"file": fname, "bytes": size})
    return files


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Chilli Gavva content exporter")
    parser.add_argument("--list", action="store_true", help="List exported files")
    parser.add_argument("--all", action="store_true", help="Re-export everything")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    if args.list:
        files = list_exported()
        if not files:
            print("No files in content/ yet. Run: python3 content_export.py")
            return
        print(f"\nExported content ({len(files)} files):\n")
        for f in files:
            print(f"  {f['file']}  ({f['bytes']}B)")
        return

    paths = export_approved(db_path=args.db, overwrite=args.all)
    if not paths:
        print("Nothing new to export. Approve some items first via the dashboard.")
    else:
        print(f"Exported {len(paths)} file(s) to content/:")
        for p in paths:
            print(f"  {os.path.basename(p)}")
        print("\nTip: git add content/ && git commit -m 'content: export approved'")


if __name__ == "__main__":
    main()
