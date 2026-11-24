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
import format_registry
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
        # Upsert: a draft stopped before a daily slot was reserved (CCG-2) still shows its reason in /doctor.
        conn.execute("INSERT INTO product_deliveries (approval_id, status, reason, folder, lease_until) "
                     "VALUES (?, 'retry', ?, ?, 0) ON CONFLICT(approval_id) DO UPDATE SET status='retry', "
                     "reason=excluded.reason, folder=excluded.folder, lease_until=0", (aid, reason, folder))
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


def _marketing_lines(db_path):
    """CC1-1 hook: private marketing files for READY packs (max 3 per run); never stops delivery."""
    try:
        import marketing_auto
        return marketing_auto.run_ready(db_path)
    except Exception:
        return {}


def notify_ready(db_path=None, send=None):
    """Retry failed sends without rebuilding or spending any AI calls."""
    import notify
    send = send or (lambda text: notify.send(html.escape(text)))
    conn = connect(db_path)
    rows = conn.execute("SELECT d.approval_id, a.title FROM product_deliveries d JOIN approvals a ON a.id=d.approval_id "
                        "WHERE d.status='ready' AND d.notified=0 AND a.status='approved' ORDER BY d.approval_id").fetchall()
    conn.close()
    marketing = _marketing_lines(db_path)   # also backfills older READY packs
    for aid, title in rows:
        if product_builder.is_held(aid, db_path):
            continue
        title = " ".join((title or "Product").split())[:140]
        try:
            sent = send(f"Product ready: #{aid} {title} — files ready to upload\n/pack {aid}"
                        + (f"\n{marketing[aid]}" if marketing.get(aid) else ""))
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
    if not item or item["status"] != "approved" or product_builder.is_held(aid, db_path):
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


DAMAGED_TEXT = ("Pack #{aid} lo file paadayyindi ({name}). Ee pack ippudu upload cheyakandi; robot files malli "
                "build chestundi, ready ayyaka 'Product ready' message vastundi.")


def damaged_file(path):
    """Why a finished product file is unusable ('' when fine). Cheap structural checks only: a PDF must start
    with %PDF- and end with %%EOF, an Office file or zip must be a readable zip."""
    import zipfile
    name = path.lower()
    try:
        if name.endswith(".pdf"):
            with open(path, "rb") as stream:
                head = stream.read(5)
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 1024))
                tail = stream.read()
            return "" if head == b"%PDF-" and b"%%EOF" in tail else "broken PDF"
        if name.endswith((".xlsx", ".docx", ".zip")):
            if not zipfile.is_zipfile(path):
                return "broken file"
            with zipfile.ZipFile(path) as archive:
                return "" if archive.testzip() is None else "broken file"
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
        return "unreadable file"
    return ""


def check_ready_files(db_path=None, out_dir=None, send=None):
    """A READY pack whose PDF/xlsx/docx/zip got damaged (bad disk, half copy) is taken back: it is no longer
    offered as ready, the owner gets ONE line, and the pipeline builds every file again (normal build path).
    Returns [(approval id, file name)]."""
    out_dir = out_dir or product_builder.PRODUCTS_DIR
    conn = connect(db_path)
    rows = conn.execute("SELECT approval_id, folder, files FROM product_deliveries WHERE status='ready'").fetchall()
    conn.close()
    damaged = []
    for aid, folder, files in rows:
        try:
            names = [n for n in json.loads(files or "[]") if isinstance(n, str)]
        except ValueError:
            names = []
        base = os.path.join(out_dir, folder or "")
        if not folder or os.path.basename(folder) != folder or not os.path.isdir(base):
            continue
        bad = next((n for n in names if ".." not in n.split("/") and not os.path.isabs(n)
                    and os.path.isfile(os.path.join(base, n)) and damaged_file(os.path.join(base, n))), None)
        if not bad:
            continue
        reason = f"Product #{aid}: damaged file {bad}; rebuild"
        with connect(db_path) as conn:
            conn.execute("UPDATE product_deliveries SET status='retry', reason=?, notified=0, lease_until=0 "
                         "WHERE approval_id=?", (reason, aid))
        # 'retry' (not needs_fix): run_pending must NOT reuse this folder as is, it builds all files again.
        product_builder._record(db_path, aid, "retry", folder, reason)
        damaged.append((aid, bad))
        try:
            if send is None:
                import notify
                send = notify.send
            send(DAMAGED_TEXT.format(aid=aid, name=os.path.basename(bad)))
        except Exception as exc:
            print(f"Damaged pack message not sent ({type(exc).__name__}).")
    return damaged


def pending(db_path=None):
    conn = connect(db_path)
    rows = conn.execute("SELECT a.id, a.title, a.payload, a.review FROM approvals a "
                        "LEFT JOIN product_deliveries d ON d.approval_id=a.id "
                        "WHERE a.status='approved' AND a.kind='public_draft' AND COALESCE(d.status,'') NOT IN ('ready','prepared') "
                        # CCG-2: never tried first; then the owner's own approvals before auto-approved ones
                        "ORDER BY COALESCE((SELECT MAX(day) FROM product_delivery_attempts p WHERE p.approval_id=a.id),''), "
                        "COALESCE(a.reason,'') = 'auto-approved', a.id").fetchall()
    conn.close()
    return [dict(zip(("id", "title", "payload", "review"), row)) for row in rows
            if not product_builder.is_held(row[0], db_path)]


def buildable(title, payload):
    """True when a builder can make this approved draft (niche agent kind, or a format found in the text)."""
    niche = product_builder.niche_of(title)
    if niche and niche in product_builder.NICHES:
        if format_registry.can_build({"kind": product_builder.NICHES[niche].get("kind")}):
            return True
    fmt = format_registry.classify(f"{title}\n{payload}")
    return bool(fmt and format_registry.can_build({"kind": fmt}))



def prepare_upload(folder, out_dir):
    """Rebuild an upload pack atomically so retries and revised listings cannot leave stale files."""
    import pathlib
    import tempfile
    import upload_pack
    import store_kits
    import gumroad_images
    uploads = pathlib.Path(out_dir) / "upload"
    uploads.mkdir(parents=True, exist_ok=True)
    destination = uploads / folder
    if destination.is_symlink():
        raise ValueError("Upload destination must be a real directory")
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=uploads) as temp:
        staged = upload_pack.make(pathlib.Path(out_dir) / folder, temp)
        product_builder.complete_buyer_zip(pathlib.Path(out_dir) / folder, staged)
        store_kits.build(staged)
        if gumroad_images.available():
            gumroad_images.for_pack(staged)
        import gumroad_handoff
        gumroad_handoff.build(staged, source=pathlib.Path(out_dir)/folder)
        previous = pathlib.Path(temp) / "previous"
        if destination.exists():
            os.replace(destination, previous)
        try:
            os.replace(staged, destination)
        except OSError:
            if previous.exists():
                os.replace(previous, destination)
            raise
    return destination

def run_pending(db_path=None, out_dir=None, llm=None, max_builds=DAILY_BUILDS,
                notify_fn=None, day=None, max_time_seconds=240):
    """Drain already-approved work within the time and AI-call budgets."""
    if not _allowed(db_path):
        return []
    import workforce
    out_dir = out_dir or product_builder.PRODUCTS_DIR
    llm = llm or workforce.default_llm(db_path)
    day = day or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    started = time.monotonic()
    results = []
    # Keep max_builds for existing CLI callers; approvals have already passed
    # their own quota. A second build quota strands approved backlogs.
    for item in pending(db_path):
        aid, title, payload, review = (item[key] for key in ("id", "title", "payload", "review"))
        if time.monotonic() - started >= max_time_seconds or not _allowed(db_path):
            break
        if product_builder.is_held(aid, db_path):
            continue
        # BINNY-V4A: Feasibility check before build. If no builder can make the idea,
        # mark the draft 'needs_owner' with a Tenglish reason (never build a fake product).
        # CCG-2: checked BEFORE a daily slot is reserved (no AI, no files), so a draft that is not a
        # buildable product never takes one of the DAILY_BUILDS slots from a real product draft.
        try:
            can_make = buildable(title, payload)
        except Exception as error:
            if product_builder.disk_full(error):
                # Nothing reserved yet; the draft keeps its place and the next run tries again.
                with connect(db_path) as conn:
                    conn.execute("INSERT INTO product_deliveries (approval_id, status, reason, lease_until) "
                                 "VALUES (?, 'retry', ?, 0) ON CONFLICT(approval_id) DO UPDATE SET "
                                 "status='retry', reason=excluded.reason, lease_until=0",
                                 (aid, f"Product #{aid}: disk full; retry next run"))
                raise
            results.append(_stuck(aid, "feasibility", f"Stage failed ({type(error).__name__})", db_path))
            continue
        if not can_make:
            tenglish_reason = format_registry.get_tenglish_unbuildable_reason(payload)
            with guardrails.connect(db_path) as conn:
                conn.execute("UPDATE approvals SET status='rejected', reason=? WHERE id=?",
                             (tenglish_reason, aid))
            results.append(_stuck(aid, "feasibility", tenglish_reason, db_path))
            continue
        if not product_builder.niche_for_draft(title, payload):
            results.append(_stuck(aid, "niche", "Unsupported product niche", db_path))
            continue
        if not _reserve(aid, day, db_path):
            continue
        stage = "council"
        try:
            summary = product_council.get_council_summary(aid, db_path)
            if not summary or summary["verdict"] not in ("PASS", "APPROVED"):
                summary = product_council.review_approved(aid, payload, title, ctx={"llm": llm}, db_path=db_path)
            if summary.get("verdict") not in ("PASS", "APPROVED"):
                waiting = summary.get("status") != "rejected"
                status = "waiting_ai" if waiting else "council_rejected"
                reason = ("AI kosam waiting; next run retry chestondi" if waiting else
                          "Council malli no cheppindi; oka improvement tarvata reject: " + summary.get("reason", "quality checks"))
                with connect(db_path) as conn:
                    conn.execute("UPDATE product_deliveries SET status=?,reason=?,lease_until=0 WHERE approval_id=?",
                                 (status, reason, aid))
                    if waiting:
                        conn.execute("DELETE FROM product_delivery_attempts WHERE day=? AND approval_id=?", (day, aid))
                    else:
                        conn.execute("UPDATE approvals SET status='rejected',reason=?,decided_ts=? WHERE id=? AND status='approved'",
                                     (reason, datetime.datetime.now(datetime.timezone.utc).isoformat(), aid))
                if not waiting:
                    import notify
                    try:
                        (notify_fn or (lambda text: notify.send(html.escape(text))))(
                            f"Pack #{aid}: {reason}\nNext: /doctor lo reason chudandi; /list lo vere Draft enchukondi.")
                    except Exception:
                        pass
                product_builder._record(db_path, aid, status, note=reason)
                results.append({"id": aid, "status": status, "reason": reason})
                continue
            saved = guardrails.get_approval(aid, db_path)
            if not saved or saved['status'] != 'approved':
                continue
            payload, review = saved['payload'], saved['review']
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
            stage = "upload"
            prepare_upload(folder, out_dir)
            names, digest = _inventory(os.path.join(out_dir, folder))
            with connect(db_path) as conn:
                conn.execute("UPDATE product_deliveries SET status='prepared', reason='', folder=?, files=?, digest=?, lease_until=0 WHERE approval_id=?",
                             (folder, json.dumps(names), digest, aid))
            product_builder._record(db_path, aid, "awaiting_encryption", folder, "Truth check READY; awaiting private encryption")
            results.append({"id": aid, "status": "prepared", "folder": folder})
        except Exception as error:
            if product_builder.disk_full(error):
                # Not the draft's fault: give back today's slot so the next run builds it again, and stop
                # this run (nothing more can be written). product_builder.main tells the owner once.
                with connect(db_path) as conn:
                    conn.execute("DELETE FROM product_delivery_attempts WHERE day = ? AND approval_id = ?", (day, aid))
                    conn.execute("UPDATE product_deliveries SET status='retry', reason=?, lease_until=0 "
                                 "WHERE approval_id=?", (f"Product #{aid}: disk full; retry next run", aid))
                raise
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
    import gumroad_handoff
    gumroad_handoff.build_missing(out_dir, db_path)
    notify_ready(db_path, notify_fn)
    # R66-JB: repair up to 3 old NEEDS FIX products per day. Never stops the pipeline.
    try:
        import product_repair
        product_repair.run_daily(db_path=db_path, out_dir=out_dir, llm=llm, max_repairs=3)
    except Exception:
        pass
    import promise_check
    promise_check.record_build_cycle(db_path)
    return results
