"""owner_assets.py - Private Owner Asset Storage and Retrieval (BINNY-W4B).

Allows the owner to store private branding assets (logo, face icons, product photos)
in the private SQLite database table `owner_assets` — never in the public git repo.

Features:
- Format verification: PNG, JPG/JPEG, WEBP magic bytes accepted. SVG strictly refused.
- Size limit: <= 2 MB (2 * 1024 * 1024 bytes).
- SHA-256 deduplication and verification.
- Asset kinds: 'logo', 'icon', 'photo' (default: 'photo').
- Helpers:
    * init_db(db_path)
    * detect_image_format(data: bytes) -> tuple[str | None, str | None]
    * validate_asset_bytes(data: bytes, filename: str = "") -> tuple[bool, str, dict]
    * store_asset(db_path, data, filename="", kind="photo", caption="", tags="") -> dict
    * list_assets(db_path, kind=None) -> list[dict]
    * get_asset(db_path, asset_id) -> dict | None
    * get_asset_by_sha256(db_path, sha256_hash) -> dict | None
    * get_logo(db_path=None) -> dict | None
    * delete_asset(db_path, asset_id) -> bool
    * count_assets(db_path, kind=None) -> int
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

MAX_ASSET_BYTES = 2 * 1024 * 1024  # 2 MB limit
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_data.db")

# Magic bytes
PNG_HEADER = b"\x89PNG\r\n\x1a\n"
JPG_SOI = b"\xff\xd8\xff"
RIFF_HEADER = b"RIFF"
WEBP_HEADER = b"WEBP"


def get_db_path(db_path: Optional[str] = None) -> str:
    """Resolve database path, falling back to guardrails or default."""
    if db_path:
        return db_path
    try:
        import guardrails
        return getattr(guardrails, "DB_PATH", DEFAULT_DB_PATH)
    except Exception:
        return DEFAULT_DB_PATH


def init_db(db_path: Optional[str] = None) -> None:
    """Create the owner_assets table if it does not exist."""
    path = get_db_path(db_path)
    con = sqlite3.connect(path)
    try:
        with con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS owner_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sha256 TEXT UNIQUE NOT NULL,
                    filename TEXT,
                    kind TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    caption TEXT,
                    tags TEXT,
                    data BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
    finally:
        con.close()


def is_svg(data: bytes, filename: str = "") -> bool:
    """Check if file data or name indicates an SVG file."""
    if filename.lower().endswith(".svg"):
        return True
    # Strip leading whitespace/BOM for check
    sample = data[:1024].lstrip()
    # Check UTF-8/ASCII BOM if present
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:].lstrip()
    sample_lower = sample.lower()
    if b"<svg" in sample_lower:
        return True
    if b"<!doctype svg" in sample_lower:
        return True
    if b"xmlns=\"http://www.w3.org/2000/svg\"" in sample_lower:
        return True
    if b"xmlns='http://www.w3.org/2000/svg'" in sample_lower:
        return True
    return False


def detect_image_format(data: bytes) -> Tuple[Optional[str], Optional[str]]:
    """Detect image format from magic bytes.

    Returns (fmt, mime_type) e.g. ('PNG', 'image/png') or (None, None).
    Supported: PNG, JPEG, WEBP.
    """
    if len(data) < 8:
        return None, None

    if data.startswith(PNG_HEADER):
        return "PNG", "image/png"

    if data.startswith(JPG_SOI):
        return "JPEG", "image/jpeg"

    # WebP check: starts with 'RIFF' and at offset 8 has 'WEBP'
    if len(data) >= 12 and data[:4] == RIFF_HEADER and data[8:12] == WEBP_HEADER:
        return "WEBP", "image/webp"

    return None, None


def validate_asset_bytes(data: bytes, filename: str = "") -> Tuple[bool, str, Dict[str, Any]]:
    """Validate asset bytes.

    Checks:
    - Must not be empty.
    - SVG is strictly refused.
    - Size <= 2 MB.
    - Magic bytes must match PNG, JPEG, or WEBP.

    Returns (ok, error_message, info_dict).
    """
    if not data:
        return False, "File empty ga undi.", {}

    if is_svg(data, filename):
        return False, "SVG files support ledu. PNG, JPG leda WEBP pampandi.", {}

    size = len(data)
    if size > MAX_ASSET_BYTES:
        mb = round(size / (1024 * 1024), 2)
        return False, f"File size peddaga undi ({mb} MB). Max limit 2 MB.", {}

    fmt, mime = detect_image_format(data)
    if not fmt:
        return False, "Unsupported image format. కేవలం PNG, JPG, WEBP formats matrame allow chestaru.", {}

    sha = hashlib.sha256(data).hexdigest()

    return True, "", {
        "format": fmt,
        "mime_type": mime,
        "size_bytes": size,
        "sha256": sha,
    }


def normalize_kind(kind: str) -> str:
    """Normalize kind string to one of ('logo', 'icon', 'photo')."""
    k = (kind or "").strip().lower()
    if "logo" in k:
        return "logo"
    if "icon" in k or "face" in k or "avatar" in k:
        return "icon"
    return "photo"


def store_asset(
    db_path: Optional[str],
    data: bytes,
    filename: str = "",
    kind: str = "photo",
    caption: str = "",
    tags: str = "",
) -> Dict[str, Any]:
    """Validate and store an asset in owner_assets table.

    If an asset with the same sha256 already exists, it updates metadata (kind, caption, tags)
    and returns existing record info with 'duplicate': True.
    Returns dict with keys: ok, id, sha256, kind, mime_type, size_bytes, filename, error.
    """
    init_db(db_path)
    ok, err, info = validate_asset_bytes(data, filename)
    if not ok:
        return {"ok": False, "error": err}

    k = normalize_kind(kind)
    sha = info["sha256"]
    mime = info["mime_type"]
    size = info["size_bytes"]
    fn = filename or f"{k}_{sha[:8]}.{info['format'].lower()}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    path = get_db_path(db_path)
    con = sqlite3.connect(path)
    try:
        with con:
            cur = con.execute("SELECT id FROM owner_assets WHERE sha256 = ?", (sha,))
            row = cur.fetchone()
            if row:
                # Update existing asset's kind/caption/tags if provided
                asset_id = row[0]
                con.execute(
                    "UPDATE owner_assets SET kind = ?, caption = ?, tags = ? WHERE id = ?",
                    (k, caption, tags, asset_id),
                )
                return {
                    "ok": True,
                    "id": asset_id,
                    "sha256": sha,
                    "kind": k,
                    "mime_type": mime,
                    "size_bytes": size,
                    "filename": fn,
                    "duplicate": True,
                }

            cur = con.execute(
                """
                INSERT INTO owner_assets (sha256, filename, kind, mime_type, size_bytes, caption, tags, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sha, fn, k, mime, size, caption, tags, data, now_iso),
            )
            asset_id = cur.lastrowid
            return {
                "ok": True,
                "id": asset_id,
                "sha256": sha,
                "kind": k,
                "mime_type": mime,
                "size_bytes": size,
                "filename": fn,
                "duplicate": False,
            }
    finally:
        con.close()


def list_assets(db_path: Optional[str] = None, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """List assets stored in owner_assets (without binary blob)."""
    init_db(db_path)
    path = get_db_path(db_path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        if kind:
            norm_k = normalize_kind(kind)
            cur.execute(
                """
                SELECT id, sha256, filename, kind, mime_type, size_bytes, caption, tags, created_at
                FROM owner_assets
                WHERE kind = ?
                ORDER BY id ASC
                """,
                (norm_k,),
            )
        else:
            cur.execute(
                """
                SELECT id, sha256, filename, kind, mime_type, size_bytes, caption, tags, created_at
                FROM owner_assets
                ORDER BY id ASC
                """
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_asset(db_path: Optional[str], asset_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve full asset record including data blob by ID."""
    init_db(db_path)
    path = get_db_path(db_path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, sha256, filename, kind, mime_type, size_bytes, caption, tags, data, created_at
            FROM owner_assets
            WHERE id = ?
            """,
            (asset_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def get_asset_by_sha256(db_path: Optional[str], sha256_hash: str) -> Optional[Dict[str, Any]]:
    """Retrieve full asset record including data blob by SHA256."""
    init_db(db_path)
    path = get_db_path(db_path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, sha256, filename, kind, mime_type, size_bytes, caption, tags, data, created_at
            FROM owner_assets
            WHERE sha256 = ?
            """,
            (sha256_hash,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def get_logo(db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve the owner's active logo asset.

    Product builders can call owner_assets.get_logo(db) to get the logo blob & metadata.
    Returns the newest logo asset or None if no logo is registered.
    """
    init_db(db_path)
    path = get_db_path(db_path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, sha256, filename, kind, mime_type, size_bytes, caption, tags, data, created_at
            FROM owner_assets
            WHERE kind = 'logo'
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def delete_asset(db_path: Optional[str], asset_id: int) -> bool:
    """Delete an asset by ID."""
    init_db(db_path)
    path = get_db_path(db_path)
    con = sqlite3.connect(path)
    try:
        with con:
            cur = con.execute("DELETE FROM owner_assets WHERE id = ?", (asset_id,))
            return cur.rowcount > 0
    finally:
        con.close()


def count_assets(db_path: Optional[str] = None, kind: Optional[str] = None) -> int:
    """Count assets in owner_assets."""
    init_db(db_path)
    path = get_db_path(db_path)
    con = sqlite3.connect(path)
    try:
        cur = con.cursor()
        if kind:
            norm_k = normalize_kind(kind)
            cur.execute("SELECT COUNT(*) FROM owner_assets WHERE kind = ?", (norm_k,))
        else:
            cur.execute("SELECT COUNT(*) FROM owner_assets")
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        con.close()
