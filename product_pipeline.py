"""Durable approved-product delivery. No publishing; all files remain private.

The scheduled builder prepares packs. The existing pack-products step confirms
encryption, and the next owner inbox sends any unsent readiness receipts. Local
runs with DB_KEY can finish all three stages together. No workflow changes needed.
"""
import datetime
import hashlib
import html
import json
import os
import time

import checker
import cubes
import guardrails
import launch_pack
import product_builder
import product_council
import rest_window

DAILY_BUILDS = 2


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS product_deliveries (
        approval_id INTEGER PRIMARY KEY, status TEXT, reason TEXT DEFAULT '',
        folder TEXT DEFAULT '', files TEXT DEFAULT '[]', digest TEXT DEFAULT '',
        notified INTEGER DEFAULT 0, lease_until REAL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS product_delivery_attempts (
        day TEXT, approval_id INTEGER, PRIMARY KEY (day, approval_id))""")
    conn.commit()
    return conn


def _allowed(db_path):
    return (not guardrails.is_killed() and not guardrails.is_paused(db_path)
            and guardrails.automation_allowed(db_path) and not rest_window.in_rest_window()
            and cubes.get_cube_state("product", db_path) == "on"
            and cubes.is_kind_enabled("review", db_path=db_path))


def _reserve(aid, day, db_path):
    """Reserve a daily slot atomically, shared by CLI and other entry points."""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT COUNT(*) FROM product_delivery_attempts WHERE day = ?", (day,)).fetchone()[0] >= DAILY_BUILDS:
            return False
        if conn.execute("SELECT 1 FROM product_delivery_attempts WHERE day = ? AND approval_id = ?", (day, aid)).fetchone():
            return False
        old = conn.execute("SELECT lease_until FROM product_deliveries WHERE approval_id = ?", (aid,)).fetchone()
        if old and old[0] > time.time():
            return False
        conn.execute("INSERT INTO product_delivery_attempts VALUES (?, ?)", (day, aid))
        conn.execute("INSERT INTO product_deliveries (approval_id, status, lease_until) VALUES (?, 'building', ?) "
                     "ON CONFLICT(approval_id) DO UPDATE SET status='building', lease_until=excluded.lease_until",
                     (aid, time.time() + 600))
        conn.commit()
        return True
    finally:
        conn.close()


def _stuck(aid, stage, reason, db_path, folder=""):
    """Store safe, concrete diagnostics for /doctor without exposing model text."""
    import doctor
    reason = f"Product #{aid}: {stage}: {reason}"
    with connect(db_path) as conn:
        old = conn.execute("SELECT reason FROM product_deliveries WHERE approval_id=?", (aid,)).fetchone()
        conn.execute("UPDATE product_deliveries SET status='retry', reason=?, folder=?, lease_until=0 WHERE approval_id=?",
                     (reason, folder, aid))
    product_builder._record(db_path, aid, "needs_fix" if folder else "retry", folder, reason)
    if not old or old[0] != reason:
        facts = doctor.collect_facts("Product Builder", f"product_{aid}_{stage}", reason, db_path=db_path)
        doctor.record_case(facts, "product_pipeline", reason,
                           "Approval retained; retry on a later daily slot or ask owner to review",
                           "needs_owner", db_path=db_path)
    return {"id": aid, "status": "retry", "reason": reason, "folder": folder}


def _inventory(folder):
    """Names plus a digest; no product contents are retained in delivery receipts."""
    from scripts.db_crypt import _file_digest
    if not os.path.isdir(folder) or os.path.islink(folder):
        raise ValueError("Product folder is missing or unsupported")
    names = []
    hashes = []
    for base, dirs, files in os.walk(folder):
        if any(os.path.islink(os.path.join(base, name)) for name in dirs + files):
            raise ValueError("Unsupported product file")
        for name in sorted(files):
            path = os.path.join(base, name)
            relative = os.path.relpath(path, folder)
            names.append(relative)
            hashes.append((relative, _file_digest(path)))
    digest = hashlib.sha256(json.dumps(sorted(hashes)).encode()).hexdigest()
    return sorted(names), digest


def mark_encrypted(db_path=None, out_dir=None, archive=None):
    """Called only AFTER pack_products succeeds, before the database is saved."""
    out_dir = out_dir or product_builder.PRODUCTS_DIR
    if not archive or not os.path.isfile(archive):
        raise ValueError("Encrypted product archive is missing")
    with open(archive, "rb") as stream:
        if stream.read(8) != b"Salted__":
            raise ValueError("Encrypted product archive is invalid")
    conn = connect(db_path)
    rows = conn.execute("SELECT approval_id, folder, digest FROM product_deliveries WHERE status='prepared'").fetchall()
    conn.close()
    for aid, folder, expected in rows:
        if not launch_pack.is_eligible(aid, db_path):
            continue
        names, digest = _inventory(os.path.join(out_dir, folder))
        if digest != expected or not names:
            _stuck(aid, "encryption", "Product files changed before encryption confirmation", db_path, folder)
            continue
        with connect(db_path) as conn:
            conn.execute("UPDATE product_deliveries SET status='ready', reason='' WHERE approval_id=?", (aid,))
        product_builder._record(db_path, aid, "built", folder, "READY; encrypted private pack")


def notify_ready(db_path=None, send=None):
    """Retry failed sends without rebuilding or spending any AI calls."""
    import notify
    send = send or (lambda text: notify.send(html.escape(text)))
    conn = connect(db_path)
    rows = conn.execute("SELECT d.approval_id, a.title FROM product_deliveries d JOIN approvals a ON a.id=d.approval_id "
                        "WHERE d.status='ready' AND d.notified=0 AND a.status='approved' ORDER BY d.approval_id").fetchall()
    conn.close()
    for aid, title in rows:
        if aid in product_builder.OWNER_HOLDS:
            continue
        title = " ".join((title or "Product").split())[:140]
        try:
            sent = send(f"Product ready: #{aid} {title} — files ready to upload\n/pack {aid}")
        except Exception:
            sent = False
        if sent:
            with connect(db_path) as conn:
                conn.execute("UPDATE product_deliveries SET notified=1, reason='' WHERE approval_id=?", (aid,))
        else:
            _delivery_problem(aid, "Telegram ready notification unavailable; encrypted pack retained", db_path)


def _delivery_problem(aid, reason, db_path):
    """A delivery outage retains the prepared/ready state, so retry needs no AI."""
    import doctor
    with connect(db_path) as conn:
        old = conn.execute("SELECT reason FROM product_deliveries WHERE approval_id=?", (aid,)).fetchone()
        conn.execute("UPDATE product_deliveries SET reason=? WHERE approval_id=?", (reason, aid))
    if not old or old[0] != reason:
        reason = f"Product #{aid}: {reason}"
        facts = doctor.collect_facts("Product Builder", f"product_{aid}_delivery", reason, db_path=db_path)
        doctor.record_case(facts, "product_pipeline", reason, "Retry private delivery; approval retained",
                           "needs_owner", db_path=db_path)


def encryption_failed(db_path=None):
    conn = connect(db_path)
    rows = conn.execute("SELECT approval_id FROM product_deliveries WHERE status='prepared'").fetchall()
    conn.close()
    for (aid,) in rows:
        _delivery_problem(aid, "Private encryption failed; prepared pack retained for retry", db_path)


def pack_text(aid, db_path=None):
    item = guardrails.get_approval(aid, db_path)
    if not item or item["status"] != "approved" or aid in product_builder.OWNER_HOLDS:
        return "No approved upload pack for that ID."
    conn = connect(db_path)
    row = conn.execute("SELECT status, files FROM product_deliveries WHERE approval_id=?", (aid,)).fetchone()
    conn.close()
    if not row or row[0] != "ready":
        return f"Pack #{aid} is not ready yet. Use /doctor for blocked stages."
    # Only saved relative names; this command never opens product files.
    names = [name for name in json.loads(row[1]) if isinstance(name, str)
             and not os.path.isabs(name) and ".." not in name.split(os.sep)]
    return f"Pack #{aid} — files ready to upload:\n" + "\n".join(names)


def pending(db_path=None):
    conn = connect(db_path)
    rows = conn.execute("SELECT a.id, a.title, a.payload, a.review FROM approvals a "
                        "LEFT JOIN product_deliveries d ON d.approval_id=a.id "
                        "WHERE a.status='approved' AND a.kind='public_draft' AND COALESCE(d.status,'') NOT IN ('ready','prepared') "
                        "ORDER BY COALESCE((SELECT MAX(day) FROM product_delivery_attempts p WHERE p.approval_id=a.id),''), a.id").fetchall()
    conn.close()
    return [dict(zip(("id", "title", "payload", "review"), row)) for row in rows
            if row[0] not in product_builder.OWNER_HOLDS]


def run_pending(db_path=None, out_dir=None, llm=None, max_builds=DAILY_BUILDS,
                notify_fn=None, day=None, max_time_seconds=240):
    """Prioritise approved work, at most two attempts per UTC day, oldest attempt first."""
    if not _allowed(db_path):
        return []
    import workforce
    out_dir = out_dir or product_builder.PRODUCTS_DIR
    llm = llm or workforce.default_llm(db_path)
    day = day or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    started = time.monotonic()
    results = []
    for item in pending(db_path):
        aid, title, payload, review = (item[key] for key in ("id", "title", "payload", "review"))
        if len(results) >= max_builds or time.monotonic() - started >= max_time_seconds or not _allowed(db_path):
            break
        if aid in product_builder.OWNER_HOLDS or not _reserve(aid, day, db_path):
            continue
        stage = "council"
        try:
            if not product_builder.niche_of(title):
                results.append(_stuck(aid, "niche", "Unsupported product niche", db_path))
                continue
            summary = product_council.get_council_summary(aid, db_path)
            if not summary or summary["verdict"] not in ("PASS", "APPROVED"):
                summary = product_council.run_council(aid, payload, title, ctx={"llm": llm},
                                                      db_path=db_path, max_rounds=1, max_ai_calls=3)
            if summary.get("verdict") not in ("PASS", "APPROVED"):
                results.append(_stuck(aid, stage, "Council did not pass the approved draft", db_path))
                continue
            stage = "checker"
            if not checker.review_passes(review):
                check = checker.check_draft(payload, title, ctx={"llm": llm}, db_path=db_path)
                review = checker.format_review(check["score"], check["reasons"], check["verdict"], check.get("fallback", False))
                guardrails.set_review(aid, review, db_path)
            if not checker.review_passes(review) or any(n.startswith("risky") for n in workforce.review_text(payload)[1]):
                results.append(_stuck(aid, stage, "Checker did not pass the approved draft", db_path))
                continue
            stage = "build"
            # Do not start a spec request when the free call allowance is exhausted.
            existing = next((build["folder"] for build in product_builder.builds(db_path)
                             if build["approval_id"] == aid and build["folder"]
                             and build["status"] in ("built", "needs_fix", "awaiting_encryption")
                             and os.path.isfile(os.path.join(out_dir, build["folder"], "listing.md"))), None)
            if not existing and not guardrails.can_call_llm(db_path):
                results.append(_stuck(aid, stage, "Daily AI call cap reached", db_path))
                break
            result = launch_pack.build_launch_pack(aid, db_path=db_path, out_dir=out_dir,
                                                   llm=llm, existing_folder=existing)
            if result["status"] != "ready":
                reason = ("; ".join(result.get("reasons", [])[:2])
                          or result.get("reason") or "Launch pack did not pass truth checks")
                results.append(_stuck(aid, "truth_or_build", reason,
                                      db_path, result.get("folder", "")))
                continue
            folder = result["folder"]
            names, digest = _inventory(os.path.join(out_dir, folder))
            with connect(db_path) as conn:
                conn.execute("UPDATE product_deliveries SET status='prepared', reason='', folder=?, files=?, digest=?, lease_until=0 WHERE approval_id=?",
                             (folder, json.dumps(names), digest, aid))
            product_builder._record(db_path, aid, "awaiting_encryption", folder, "Truth check READY; awaiting private encryption")
            results.append({"id": aid, "status": "prepared", "folder": folder})
        except Exception as error:
            results.append(_stuck(aid, stage, f"Stage failed ({type(error).__name__})", db_path))
    # CI supplies DB_KEY only in its separate pack-products step. Prepared packs
    # survive that boundary; encryption failures never become ready receipts.
    with connect(db_path) as conn:
        prepared = conn.execute("SELECT 1 FROM product_deliveries WHERE status='prepared' LIMIT 1").fetchone()
    if prepared and os.environ.get("DB_KEY"):
        try:
            archive = launch_pack.encrypt_handoff(out_dir)
            mark_encrypted(db_path, out_dir, archive)
        except Exception:
            encryption_failed(db_path)
    notify_ready(db_path, notify_fn)
    # R66-JB: repair up to 3 old NEEDS FIX products per day. Never stops the pipeline.
    try:
        import product_repair
        product_repair.run_daily(db_path=db_path, out_dir=out_dir, llm=llm, max_repairs=3)
    except Exception:
        pass
    return results
