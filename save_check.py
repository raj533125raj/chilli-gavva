"""save_check.py — does this run have anything real to save?

Used by the "Save state" step of the cloud run. A quiet run still rewrites agent_data.db (same rows,
new file bytes) and web/data/status.json (new timestamp), so git always saw a change and committed.
This compares real content instead:
  - agent_data.db: every row (SQL dump), not the file bytes
  - web/data/status.json: everything except the timestamp and the worker's "seconds ago"
  - exported_content/ and .scheduler_locks/: any change counts

Exit code 0 = something real changed (save it), 3 = nothing real changed (skip the save).
Any problem while checking counts as "changed", so state is never lost.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

DB = "agent_data.db"
DB_ENCRYPTED = "agent_data.db.enc"   # the database is committed encrypted; its key is in the DB_KEY secret
STATUS = os.path.join("web", "data", "status.json")
OTHER_PATHS = ["products.tar.gz.enc", ".scheduler_locks"]
VOLATILE_STATUS_KEYS = {"generated_at", "worker"}


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, check=False)


def _head_bytes(path, cwd):
    out = _git(["show", f"HEAD:{path}"], cwd)
    return out.stdout if out.returncode == 0 else None


def db_rows(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _head_db_bytes(cwd):
    """The committed database, decrypted with DB_KEY. None when nothing is committed yet."""
    encrypted = _head_bytes(DB_ENCRYPTED, cwd)
    if encrypted is None:
        return _head_bytes(DB, cwd)   # before the encrypted copy exists: compare the plaintext copy, if any
    import importlib.util
    spec = importlib.util.spec_from_file_location("db_crypt", os.path.join(cwd, "scripts", "db_crypt.py"))
    db_crypt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(db_crypt)
    with tempfile.TemporaryDirectory() as tmp:
        enc_path = os.path.join(tmp, "head.db.enc")
        out_path = os.path.join(tmp, "head.db")
        with open(enc_path, "wb") as f:
            f.write(encrypted)
        db_crypt.decrypt(enc_path, out_path)
        with open(out_path, "rb") as f:
            return f.read()


def db_changed(cwd):
    path = os.path.join(cwd, DB)
    if not os.path.exists(path):
        return False
    old = _head_db_bytes(cwd)
    if old is None:
        return True
    with tempfile.TemporaryDirectory() as tmp:
        old_path = os.path.join(tmp, "head.db")
        with open(old_path, "wb") as f:
            f.write(old)
        return db_rows(old_path) != db_rows(path)


def _strip(status):
    return {k: v for k, v in status.items() if k not in VOLATILE_STATUS_KEYS} if isinstance(status, dict) else status


def status_changed(cwd):
    path = os.path.join(cwd, STATUS)
    old = _head_bytes(STATUS.replace(os.sep, "/"), cwd)
    if not os.path.exists(path):
        return False
    if old is None:
        return True
    with open(path, encoding="utf-8") as f:
        new = json.load(f)
    return _strip(json.loads(old.decode("utf-8"))) != _strip(new)


def other_changed(cwd):
    out = _git(["status", "--porcelain", "--"] + OTHER_PATHS, cwd)
    return out.returncode != 0 or bool(out.stdout.strip())


def real_changes(cwd="."):
    """List of what really changed (empty list = nothing to save)."""
    reasons = []
    for name, check in (("database rows", db_changed), ("dashboard numbers", status_changed),
                        ("product or lock files", other_changed)):
        try:
            if check(cwd):
                reasons.append(name)
        except Exception as exc:   # unsure -> save, never lose state
            reasons.append(f"{name} (could not compare: {type(exc).__name__})")
    return reasons


def main(cwd="."):
    reasons = real_changes(cwd)
    if reasons:
        print("Real changes: " + ", ".join(reasons) + ".")
        return 0
    print("Nothing real changed (only file bytes or timestamps).")
    return 3


if __name__ == "__main__":
    sys.exit(main())
