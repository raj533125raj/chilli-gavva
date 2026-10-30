"""Repair stored text with the shared scrubber before the secret guard/encryption. No values logged."""
import json
from pathlib import Path
import sqlite3
import tempfile
import os

import secret_guard


def _identifier(name):
    return '"' + name.replace('"', '""') + '"'


def sanitize_database(path):
    if not Path(path).exists():
        return 0
    changed = 0
    with sqlite3.connect(path) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for (table,) in tables:
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({_identifier(table)})")]
            # All application tables have rowids; do not alter primary keys or row counts.
            for row in conn.execute(f"SELECT rowid, * FROM {_identifier(table)}").fetchall():
                fields = []
                values = []
                for column, value in zip(columns, row[1:]):
                    if isinstance(value, str):
                        clean = secret_guard.scrub_stored_text(value)
                        if clean != value:
                            fields.append(_identifier(column) + ' = ?')
                            values.append(clean)
                if fields:
                    conn.execute(f"UPDATE {_identifier(table)} SET {', '.join(fields)} WHERE rowid = ?",
                                 values + [row[0]])
                    changed += 1
    if changed:
        # Remove old secret-bearing text from SQLite's freed pages, too.
        with sqlite3.connect(path) as conn:
            conn.execute("VACUUM")
    return changed


def sanitize_status(path):
    path = Path(path)
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    clean = secret_guard.scrub_data(data)
    if clean == data:
        return False
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(clean, handle, indent=1)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main():
    rows = sanitize_database("agent_data.db")
    status = sanitize_status("web/data/status.json")
    print(f"Stored text sanitized: {rows} database row(s); status changed: {status}.")


if __name__ == "__main__":
    main()
