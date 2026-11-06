"""Approvals Ledger Reader: reads owner decisions from the cloud ledger.

Status: module only, not wired in yet.
Periodically fetches new decisions from the Cloudflare Worker + Durable Object
(GET /decisions?since=<seq>) and applies them idempotently to pending drafts
in agent_data.db via guardrails.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import guardrails
import secret_guard

LEDGER_TIMEOUT = 10  # seconds
LAST_FETCH_ERROR = None


def _state_conn(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def get_last_seq(db_path=None):
    """Retrieve last successfully applied decision sequence number."""
    conn = _state_conn(db_path)
    try:
        row = conn.execute("SELECT value FROM telegram_state WHERE key = 'last_ledger_seq'").fetchone()
        if row and row[0]:
            try:
                return int(row[0])
            except (ValueError, TypeError):
                return 0
        return 0
    finally:
        conn.close()


def set_last_seq(seq, db_path=None):
    """Store the last applied sequence number in state."""
    conn = _state_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO telegram_state (key, value) VALUES ('last_ledger_seq', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(seq),),
        )
        conn.commit()
    finally:
        conn.close()


def _error_reason(exc):
    code = getattr(exc, "code", None)
    if code == 401:
        return "401 (unauthorized)"
    if code == 404:
        return "404 (not found)"
    return str(code) if code else type(exc).__name__


def fetch_decisions(base_url, token, since=0, http_get=None):
    """Fetch decisions from ledger worker.

    Timeout 10s, returns list of decisions.
    On any error returns [] and sets/returns a short reason without logging
    the token or query parameters.
    """
    global LAST_FETCH_ERROR
    LAST_FETCH_ERROR = None

    if not base_url or not token:
        LAST_FETCH_ERROR = "missing base_url or token"
        return []

    # Sanitize base_url
    url_base = base_url.rstrip("/")
    path = f"/decisions?since={urllib.parse.quote(str(since))}"
    full_url = f"{url_base}{path}"

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "ChilliGavvaRobot/1.0",
        "Accept": "application/json",
    }

    try:
        if http_get is not None:
            raw_text = http_get(full_url, headers=headers)
        else:
            req = urllib.request.Request(full_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=LEDGER_TIMEOUT) as resp:
                raw_text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        LAST_FETCH_ERROR = _error_reason(exc)
        return []

    if raw_text is None:
        LAST_FETCH_ERROR = "empty response"
        return []

    try:
        data = json.loads(raw_text)
    except Exception:
        LAST_FETCH_ERROR = "invalid json"
        return []

    if not isinstance(data, dict):
        LAST_FETCH_ERROR = "invalid payload structure"
        return []

    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        LAST_FETCH_ERROR = "decisions not a list"
        return []

    return decisions


def apply_decisions(decisions, db_path=None):
    """Apply owner decisions in seq order.

    - Only acts if draft is pending
    - Owner's FIRST decision wins (if already decided, skips)
    - Updates last_ledger_seq
    - Returns counts: {applied, skipped, unknown}
    """
    counts = {"applied": 0, "skipped": 0, "unknown": 0}
    if not decisions:
        return counts

    # Sort decisions strictly by seq order
    def _get_seq(d):
        try:
            return int(d.get("seq", 0))
        except (ValueError, TypeError):
            return 0

    sorted_decisions = sorted(decisions, key=_get_seq)
    last_seq = get_last_seq(db_path)

    for item in sorted_decisions:
        seq = _get_seq(item)
        # Skip if already processed according to state seq
        if seq <= last_seq:
            counts["skipped"] += 1
            continue

        raw_draft_id = item.get("draft_id")
        try:
            draft_id = int(raw_draft_id)
        except (ValueError, TypeError):
            counts["unknown"] += 1
            if seq > last_seq:
                last_seq = seq
                set_last_seq(last_seq, db_path)
            continue

        action = str(item.get("action") or "").lower().strip()
        reason = item.get("reason")
        if reason:
            reason = str(reason).strip()

        # Check approval in database
        approval = guardrails.get_approval(draft_id, db_path=db_path)
        if not approval:
            counts["unknown"] += 1
            if seq > last_seq:
                last_seq = seq
                set_last_seq(last_seq, db_path)
            continue

        if approval.get("status") != "pending":
            # Already decided: first decision wins, skip
            counts["skipped"] += 1
            if seq > last_seq:
                last_seq = seq
                set_last_seq(last_seq, db_path)
            continue

        if action == "approve":
            ok = guardrails.decide(draft_id, True, db_path=db_path)
            if ok:
                counts["applied"] += 1
            else:
                counts["skipped"] += 1
        elif action == "reject":
            # If reason is known, pass it or call guardrails.set_reason
            ok = guardrails.decide(draft_id, False, db_path=db_path, reason=reason)
            if ok:
                if reason and reason in guardrails.REJECT_REASONS:
                    guardrails.set_reason(draft_id, reason, db_path=db_path)
                counts["applied"] += 1
            else:
                counts["skipped"] += 1
        else:
            counts["unknown"] += 1

        if seq > last_seq:
            last_seq = seq
            set_last_seq(last_seq, db_path)

    return counts


def run(db_path=None, http_get=None):
    """Run ledger sync if configured."""
    url = os.environ.get("LEDGER_URL", "").strip()
    token = os.environ.get("LEDGER_READ_TOKEN", "").strip()

    if not url or not token:
        print("Ledger: not configured")
        return {"configured": False, "counts": {"applied": 0, "skipped": 0, "unknown": 0}}

    since = get_last_seq(db_path)
    decisions = fetch_decisions(url, token, since=since, http_get=http_get)

    if LAST_FETCH_ERROR:
        # Scrub any potential secret before printing
        safe_reason = secret_guard.scrub_text(LAST_FETCH_ERROR)
        print(f"Ledger: fetch error: {safe_reason}")
        return {"configured": True, "error": safe_reason, "counts": {"applied": 0, "skipped": 0, "unknown": 0}}

    counts = apply_decisions(decisions, db_path=db_path)
    print(f"Ledger: sync complete. applied={counts['applied']} skipped={counts['skipped']} unknown={counts['unknown']}")
    return {"configured": True, "counts": counts}


if __name__ == "__main__":
    run()
