"""health_check.py — Quick self-check diagnostics and last run safe logs.

Part of BINNY-W4A ROBOT controls:
  - 10-part system self-check:
      1. DB opens & schema integrity
      2. Last run time (IST & staleness)
      3. Door bell status (always_on / Cloudflare Worker)
      4. Telegram read errors & diagnostics
      5. AI quota left today per provider (counts only, no currency)
      6. GitHub minutes used / limit
      7. Packs READY / NEEDS FIX counts
      8. Disk size of products directory
      9. Telegram plugin load status
      10. Auto-fix state (offers, drills, retry state)
  - Color status: Green (🟢), Amber (🟡), Red (🔴) for each part.
  - Actionable Tenglish fix hints: exactly one fix hint per red line.
  - Safe run log summary: extracts last run output with secret_guard scrubbing and length limits.
  - Undo manager: undoes last Telegram owner action (approvals, rejections, settings, office requests).

Strict rules:
  - No money or currency symbols anywhere (including comments and outputs).
  - No secrets in code, messages, or logs.
  - Atomic writes and deterministic outputs.
  - Owner-facing text in Tenglish house style.
"""

from __future__ import annotations

import datetime
import glob
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import guardrails
import secret_guard

ROOT = Path(__file__).resolve().parent

# Status constants
GREEN = "green"
AMBER = "amber"
RED = "red"

STATUS_ICONS = {
    GREEN: "🟢",
    AMBER: "🟡",
    RED: "🔴",
}

# Maximum age in hours before last run is considered stale / warning
RUN_STALE_AMBER_HOURS = 2.0
RUN_STALE_RED_HOURS = 6.0

# Product disk warning threshold in MB
DISK_WARN_MB = 1024.0      # 1 GB
DISK_CRITICAL_MB = 2048.0  # 2 GB

# Currency filter pattern
CURRENCY_PATTERN = re.compile(
    r"[$₹€£¥₩₽]\s*[+-]?\d[\d,.]*|\b(?:USD|INR|EUR|GBP|JPY|CAD|AUD|NZD|CHF|CNY|RMB|HKD|SGD|AED|SAR|KRW|RUB|BRL|MXN|ZAR|SEK|NOK|DKK|Rs\.?|dollars|rupees|cents|euros|pounds|yen)\s*[:=]?\s*[+-]?\d[\d,.]*|\b[+-]?\d[\d,.]*\s*(?:(?:USD|INR|EUR|GBP|JPY|CAD|AUD|NZD|CHF|CNY|RMB|HKD|SGD|AED|SAR|KRW|RUB|BRL|MXN|ZAR|SEK|NOK|DKK|Rs\.?|dollars|rupees|cents|euros|pounds|yen)\b|[$₹€£¥₩₽])",
    re.IGNORECASE,
)


def scrub_safe_text(text: str, max_chars: int = 3500) -> str:
    """Scrub sensitive paths, money amounts, secrets, and truncate safely."""
    if not text:
        return ""

    # Path scrubbing: keep relative to exported_content or repo filename
    def _path_repl(m: re.Match) -> str:
        full = m.group(0)
        idx = full.find("exported_content/")
        if idx != -1:
            return full[idx:]
        return full.rstrip("/").split("/")[-1]

    text = re.sub(r"(?<![\w\.\-])/(?:[\w\.\-]+/)+[\w\.\-]+", _path_repl, text)
    text = CURRENCY_PATTERN.sub("[hidden]", text)

    try:
        import public_export
        text = public_export.scrub_text(text)
    except Exception:
        text = secret_guard.scrub_text(text)

    text = text.strip()
    if len(text) > max_chars:
        suffix = "\n\n...(cut, length limit)"
        text = text[: max_chars - len(suffix)] + suffix
    return text


def format_ist_time(ts_str: Optional[str]) -> str:
    """Convert ISO timestamp string to IST string representation."""
    if not ts_str:
        return "unknown"
    try:
        from zoneinfo import ZoneInfo
        ist_zone = ZoneInfo("Asia/Kolkata")
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        ist_dt = dt.astimezone(ist_zone)
        return ist_dt.strftime("%Y-%m-%d %H:%M IST")
    except Exception:
        return "unknown"


# ==============================================================================
# 10 INDIVIDUAL HEALTH CHECK FUNCTIONS
# ==============================================================================

def check_database(db_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """1. DB opens & integrity check."""
    try:
        conn = guardrails.connect(str(db_path) if db_path else None)
        cur = conn.cursor()
        res = cur.execute("PRAGMA quick_check").fetchone()
        row_tables = cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()
        table_count = row_tables[0] if row_tables else 0
        conn.close()

        if res and res[0] == "ok":
            return {
                "part": "db",
                "status": GREEN,
                "label": f"DB opens ({table_count} tables, integrity ok)",
                "fix": None,
            }
        return {
            "part": "db",
            "status": RED,
            "label": f"DB integrity check issue: {res[0] if res else 'failed'}",
            "fix": "Database integrity fail ayindi: backup nunchi restore cheyandi or PRAGMA check choodandi.",
        }
    except Exception as exc:
        return {
            "part": "db",
            "status": RED,
            "label": f"DB connection failed ({type(exc).__name__})",
            "fix": "DB open avvaledu: sqlite file permissions mariyu path check cheyandi.",
        }


def check_last_run(db_path: Optional[str | Path] = None, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """2. Last run time (IST & staleness)."""
    now_utc = now or datetime.datetime.now(datetime.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)

    last_ts_str = None
    # 1. Try public cloud heartbeat
    try:
        import public_export
        hb = public_export.get_public_cloud_heartbeat()
        last_ts_str = hb.get("last_run_end") or hb.get("last_run_start")
    except Exception:
        pass

    # 2. Try telegram_state or heartbeat table
    if not last_ts_str:
        try:
            conn = guardrails.connect(str(db_path) if db_path else None)
            row = conn.execute("SELECT value FROM telegram_state WHERE key = 'last_cloud_run_ts'").fetchone()
            if row and row[0]:
                last_ts_str = row[0]
            else:
                h_row = conn.execute("SELECT ts FROM heartbeat WHERE name = 'worker'").fetchone()
                if h_row and h_row[0]:
                    last_ts_str = h_row[0]
                else:
                    t_row = conn.execute("SELECT MAX(finished_ts) FROM tasks WHERE status = 'done'").fetchone()
                    if t_row and t_row[0]:
                        last_ts_str = t_row[0]
            conn.close()
        except Exception:
            pass

    if not last_ts_str:
        return {
            "part": "last_run",
            "status": AMBER,
            "label": "Last run: unknown (no heartbeat recorded yet)",
            "fix": None,
        }

    try:
        clean = last_ts_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        diff_hours = (now_utc - dt).total_seconds() / 3600.0
        ist_str = format_ist_time(last_ts_str)

        if diff_hours < RUN_STALE_AMBER_HOURS:
            return {
                "part": "last_run",
                "status": GREEN,
                "label": f"Last run: {ist_str} ({diff_hours:.1f}h ago)",
                "fix": None,
            }
        elif diff_hours < RUN_STALE_RED_HOURS:
            return {
                "part": "last_run",
                "status": AMBER,
                "label": f"Last run: {ist_str} ({diff_hours:.1f}h ago, delayed)",
                "fix": None,
            }
        else:
            return {
                "part": "last_run",
                "status": RED,
                "label": f"Last run: {ist_str} ({diff_hours:.1f}h ago, stale > 6h)",
                "fix": "Robot run aagi chala samayam ayindi: GitHub Actions workflow runs check cheyandi.",
            }
    except Exception:
        return {
            "part": "last_run",
            "status": AMBER,
            "label": f"Last run: {last_ts_str} (timestamp unparseable)",
            "fix": None,
        }


def check_doorbell(status_fn=None) -> Dict[str, Any]:
    """3. Door bell status (always_on / Cloudflare Worker)."""
    reply: Dict[str, Any] = {}
    try:
        if status_fn is not None:
            reply = status_fn() or {}
        else:
            import always_on
            reply = always_on.status() or {}
    except Exception:
        reply = {}

    if not reply or "door_bell" not in reply:
        return {
            "part": "doorbell",
            "status": AMBER,
            "label": "Door bell: unknown (alarm clock not reachable)",
            "fix": None,
        }

    door_bell_on = bool(reply.get("door_bell"))
    token_ok = reply.get("telegram_token_ok")

    if door_bell_on and token_ok == "yes":
        return {
            "part": "doorbell",
            "status": GREEN,
            "label": "Door bell: on (CF worker ready)",
            "fix": None,
        }
    elif door_bell_on and token_ok != "no":
        return {
            "part": "doorbell",
            "status": GREEN,
            "label": "Door bell: on",
            "fix": None,
        }
    elif door_bell_on and token_ok == "no":
        return {
            "part": "doorbell",
            "status": RED,
            "label": "Door bell: token rejected by Telegram",
            "fix": "Cloudflare Worker lo TELEGRAM_BOT_TOKEN secret check chesi update cheyandi.",
        }
    else:
        return {
            "part": "doorbell",
            "status": AMBER,
            "label": "Door bell: off",
            "fix": None,
        }


def check_telegram_errors(db_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """4. Telegram read errors & diagnostics."""
    last_error = None
    try:
        import telegram_inbox
        last_error = getattr(telegram_inbox, "LAST_FETCH_ERROR", None)
    except Exception:
        pass

    # Check telegram_state for stored error count or error kind
    diag_summary = None
    try:
        import telegram_diagnostics
        state = telegram_diagnostics._current.get()
        if state:
            err_count = state.get("http_error", 0) + state.get("network_error", 0)
            if err_count > 0:
                diag_summary = f"{err_count} errors in current session"
    except Exception:
        pass

    if last_error:
        is_401 = "401" in str(last_error)
        is_409 = "409" in str(last_error)
        status = RED if (is_401 or is_409) else AMBER
        fix_hint = (
            "Telegram bot token maarindi leda invalid: GitHub Secrets lo TELEGRAM_BOT_TOKEN sari cheyandi."
            if is_401 else
            "Telegram webhook conflict (409): webhook delete cheyandi or inkoka reader stop cheyandi."
            if is_409 else
            "Telegram API connection check cheyandi."
        )
        return {
            "part": "telegram_errors",
            "status": status,
            "label": f"Telegram errors: {last_error}",
            "fix": fix_hint if status == RED else None,
        }

    return {
        "part": "telegram_errors",
        "status": GREEN,
        "label": "Telegram updates: ok (no read errors)",
        "fix": None,
    }


def check_ai_quota(db_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """5. AI quota left today per provider (counts only, strictly no currency)."""
    providers_info: List[str] = []
    has_exhausted = False
    has_configured = False

    try:
        import llm_router
        statuses = llm_router.provider_status(db_path=str(db_path) if db_path else None)
        for st in statuses:
            pname = st.get("provider", "Unknown")
            is_cfg = st.get("configured", False)
            if not is_cfg:
                continue
            has_configured = True
            models = st.get("models", [])
            total_calls = sum(m.get("calls_today", 0) for m in models)
            total_cap = sum(m.get("daily_cap", 100) for m in models)
            all_capped = bool(models) and all(m.get("capped", False) for m in models)

            left = max(0, total_cap - total_calls)
            if all_capped or (total_cap > 0 and left == 0):
                has_exhausted = True
                providers_info.append(f"{pname} (0 left / {total_cap})")
            else:
                providers_info.append(f"{pname} ({left} left)")
    except Exception:
        pass

    # Fallback to usage_log if router status is not available
    if not providers_info:
        try:
            conn = guardrails.connect(str(db_path) if db_path else None)
            today = guardrails.today()
            row = conn.execute("SELECT llm_calls FROM usage_log WHERE day = ?", (today,)).fetchone()
            conn.close()
            used = row[0] if row else 0
            cap = guardrails.DEFAULT_DAILY_CAP
            left = max(0, cap - used)
            providers_info.append(f"Global ({left}/{cap} left)")
            if left == 0:
                has_exhausted = True
        except Exception:
            providers_info.append("unknown")

    info_str = ", ".join(providers_info) if providers_info else "none configured"

    if has_exhausted:
        return {
            "part": "ai_quota",
            "status": RED,
            "label": f"AI quota left today: {info_str}",
            "fix": "Roju AI quota aypoyindi: repati varaku aagandi or provider safety caps penchandi.",
        }
    if not has_configured and providers_info == ["none configured"]:
        return {
            "part": "ai_quota",
            "status": AMBER,
            "label": "AI providers: none configured",
            "fix": None,
        }
    return {
        "part": "ai_quota",
        "status": GREEN,
        "label": f"AI quota left today: {info_str}",
        "fix": None,
    }


def check_github_minutes(status_fn=None, db_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """6. GitHub minutes used / limit."""
    minutes_str = "unknown"
    used = None
    limit = None

    try:
        reply = None
        if status_fn is not None:
            reply = status_fn() or {}
        else:
            import always_on
            reply = always_on.status() or {}
        if reply and "month_minutes" in reply:
            used = reply.get("month_minutes")
            limit = 2000
    except Exception:
        pass

    if used is None:
        try:
            import public_export
            m_info = public_export.get_public_minutes(str(db_path) if db_path else None)
            used = m_info.get("used_this_month")
            limit = m_info.get("limit", 2000)
        except Exception:
            pass

    if used is None:
        return {
            "part": "github_minutes",
            "status": AMBER,
            "label": "GitHub minutes: unknown (alarm clock not reachable)",
            "fix": None,
        }

    pct = (used / limit * 100.0) if limit and limit > 0 else 0
    label = f"GitHub minutes: {used}/{limit} ({pct:.0f}%)"

    if pct >= 95.0:
        return {
            "part": "github_minutes",
            "status": RED,
            "label": label,
            "fix": "GitHub free minutes aypothunnai: runs interval penchandi or unnecessary runs aapandi.",
        }
    elif pct >= 80.0:
        return {
            "part": "github_minutes",
            "status": AMBER,
            "label": label,
            "fix": None,
        }
    else:
        return {
            "part": "github_minutes",
            "status": GREEN,
            "label": label,
            "fix": None,
        }


def check_packs_quality(db_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """7. Packs READY / NEEDS FIX counts."""
    ready_cnt = 0
    needs_fix_cnt = 0

    try:
        import public_export
        products = public_export.get_public_products(str(db_path) if db_path else None)
        for p in products:
            tc = p.get("truth_check") or {}
            v = tc.get("verdict")
            if v == "READY":
                ready_cnt += 1
            elif v == "NEEDS FIX":
                needs_fix_cnt += 1
    except Exception:
        pass

    label = f"Packs quality: {ready_cnt} READY, {needs_fix_cnt} NEEDS FIX"
    if needs_fix_cnt > 0 and ready_cnt == 0:
        return {
            "part": "packs_quality",
            "status": RED,
            "label": label,
            "fix": "Upload packs lo defects unnai: /pack tho checklist mariyu truth check repair choodandi.",
        }
    elif needs_fix_cnt > 0:
        return {
            "part": "packs_quality",
            "status": AMBER,
            "label": label,
            "fix": None,
        }
    return {
        "part": "packs_quality",
        "status": GREEN,
        "label": label,
        "fix": None,
    }


def check_products_disk(products_dir: Optional[str | Path] = None) -> Dict[str, Any]:
    """8. Disk size of products directory."""
    p_path = Path(products_dir) if products_dir else (ROOT / "exported_content" / "products")
    total_bytes = 0
    file_count = 0

    if p_path.is_dir():
        try:
            for root, _, files in os.walk(p_path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total_bytes += os.path.getsize(fp)
                        file_count += 1
                    except OSError:
                        pass
        except Exception:
            pass

    size_mb = total_bytes / (1024.0 * 1024.0)
    label = f"Products disk: {size_mb:.1f} MB ({file_count} files)"

    if size_mb >= DISK_CRITICAL_MB:
        return {
            "part": "products_disk",
            "status": RED,
            "label": label,
            "fix": "Products folder size 2GB daatindi: old upload packs archive cheyandi.",
        }
    elif size_mb >= DISK_WARN_MB:
        return {
            "part": "products_disk",
            "status": AMBER,
            "label": label,
            "fix": None,
        }
    return {
        "part": "products_disk",
        "status": GREEN,
        "label": label,
        "fix": None,
    }


def check_plugins_health(repo_root: Optional[str | Path] = None) -> Dict[str, Any]:
    """9. Telegram plugin load status & import errors."""
    root = Path(repo_root or ROOT)
    plugin_files = sorted(root.glob("tg_*.py"))
    loaded = 0
    failed: List[str] = []

    for pf in plugin_files:
        mod_name = pf.stem
        try:
            if mod_name in importlib.sys.modules:
                mod = importlib.sys.modules[mod_name]
            else:
                spec = importlib.util.spec_from_file_location(mod_name, str(pf))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                else:
                    failed.append(mod_name)
                    continue
            if hasattr(mod, "handle"):
                loaded += 1
            else:
                failed.append(mod_name)
        except Exception:
            failed.append(mod_name)

    if failed:
        return {
            "part": "plugins",
            "status": RED,
            "label": f"Plugin load errors: {len(failed)} failed ({', '.join(failed)})",
            "fix": f"Telegram plugins load avvaledu ({', '.join(failed)}): syntax mariyu imports check cheyandi.",
        }
    return {
        "part": "plugins",
        "status": GREEN,
        "label": f"Plugins: {loaded} loaded ok (0 errors)",
        "fix": None,
    }


def check_autofix_state(db_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """10. Auto-fix state (retries, drills, offers)."""
    offers_found = 0
    retry_problem = None

    try:
        conn = guardrails.connect(str(db_path) if db_path else None)
        rows = conn.execute("SELECT key, value FROM telegram_state WHERE key LIKE 'autofix_%'").fetchall()
        conn.close()

        for k, v in rows:
            if k.startswith("autofix_offer:"):
                offers_found += 1
    except Exception:
        pass

    # Check state document for autofix_retry
    try:
        import run_failure
        state_file = ROOT / "state.json"
        if state_file.is_file():
            doc = run_failure.state_document(state_file)
            if doc and doc.get("autofix_retry"):
                retry_problem = doc["autofix_retry"].get("problem")
    except Exception:
        pass

    if retry_problem:
        return {
            "part": "autofix",
            "status": AMBER,
            "label": f"Auto-fix: retry pending ({retry_problem[:30]})",
            "fix": None,
        }
    elif offers_found > 0:
        return {
            "part": "autofix",
            "status": GREEN,
            "label": f"Auto-fix: active ({offers_found} offer waiting)",
            "fix": None,
        }
    return {
        "part": "autofix",
        "status": GREEN,
        "label": "Auto-fix: ready (idle)",
        "fix": None,
    }


# ==============================================================================
# MAIN HEALTH CARD FORMATTER
# ==============================================================================

def run_all_checks(
    db_path: Optional[str | Path] = None,
    products_dir: Optional[str | Path] = None,
    repo_root: Optional[str | Path] = None,
    status_fn=None,
    now: Optional[datetime.datetime] = None,
) -> List[Dict[str, Any]]:
    """Run all 10 self-checks in deterministic order."""
    checks = [
        check_database(db_path),
        check_last_run(db_path, now=now),
        check_doorbell(status_fn),
        check_telegram_errors(db_path),
        check_ai_quota(db_path),
        check_github_minutes(status_fn, db_path),
        check_packs_quality(db_path),
        check_products_disk(products_dir),
        check_plugins_health(repo_root),
        check_autofix_state(db_path),
    ]
    return checks


def format_health_card(checks: List[Dict[str, Any]]) -> str:
    """Format single health card: one line per part with icon + Tenglish fix hint for red lines."""
    lines = ["🏥 Chilli Gavva Robot Health Card", ""]
    red_fixes: List[str] = []

    for item in checks:
        icon = STATUS_ICONS.get(item["status"], "⚪")
        lines.append(f"{icon} {item['label']}")
        if item["status"] == RED and item.get("fix"):
            red_fixes.append(f"👉 Fix: {item['fix']}")

    if red_fixes:
        lines.append("")
        lines.append("⚠️ Action needed (Tenglish fix hints):")
        lines.extend(red_fixes)
    else:
        lines.append("")
        lines.append("Anni systems baagunnai! (All critical parts green or acceptable).")

    return scrub_safe_text("\n".join(lines))


def get_health_summary(
    db_path: Optional[str | Path] = None,
    products_dir: Optional[str | Path] = None,
    repo_root: Optional[str | Path] = None,
    status_fn=None,
    now: Optional[datetime.datetime] = None,
) -> str:
    """Generate the full /health answer string."""
    checks = run_all_checks(
        db_path=db_path,
        products_dir=products_dir,
        repo_root=repo_root,
        status_fn=status_fn,
        now=now,
    )
    return format_health_card(checks)


# ==============================================================================
# LAST RUN LOGS EXTRACTOR
# ==============================================================================

def get_last_run_summary(
    summary_path: Optional[str | Path] = None,
    state_file: Optional[str | Path] = None,
    db_path: Optional[str | Path] = None,
    max_lines: int = 25,
) -> str:
    """Extract and format safe lines from the last run summary/logs.

    Strict secret_guard scrubbing and safe lines filtering.
    """
    candidates = []
    if summary_path:
        candidates.append(Path(summary_path))
    candidates.extend([
        ROOT / "summary.txt",
        ROOT / "last_run.log",
        ROOT / "run.log",
    ])

    raw_text = ""
    for c in candidates:
        if c.is_file():
            try:
                raw_text = c.read_text(encoding="utf-8", errors="replace")
                if raw_text.strip():
                    break
            except Exception:
                continue

    # Fallback to run_failure helper or telegram_state if summary file not found
    if not raw_text:
        try:
            import run_failure
            sf = state_file or (ROOT / "state.json")
            if Path(sf).is_file():
                doc = run_failure.state_document(Path(sf))
                raw_text = json.dumps(doc, indent=2)
        except Exception:
            pass

    if not raw_text:
        try:
            conn = guardrails.connect(str(db_path) if db_path else None)
            row = conn.execute("SELECT value FROM telegram_state WHERE key = 'last_run_failed_step'").fetchone()
            conn.close()
            if row and row[0]:
                raw_text = f"Last run status: failed at step '{row[0]}'"
            else:
                raw_text = "No recent run summary file found. (Clean state)."
        except Exception:
            raw_text = "No recent run summary found."

    # Filter safe lines (scrubbed, secret-free, no private tokens or stacktraces with local usernames)
    safe_lines = []
    for line in raw_text.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        scrubbed_line = scrub_safe_text(line_clean)
        if scrubbed_line:
            safe_lines.append(scrubbed_line)

    if not safe_lines:
        safe_lines = ["Run summary details empty or scrubbed for safety."]

    header = "📋 Last Run Summary (Safe Lines Only):"
    body = "\n".join(safe_lines[-max_lines:])
    full = f"{header}\n\n{body}"
    return scrub_safe_text(full, max_chars=3500)


# ==============================================================================
# UNDO MANAGER (OWNER-ONLY ACTION REVERSAL)
# ==============================================================================

UNDO_EXPIRY_SECONDS = 600  # 10 minutes window to undo
LAST_ACTION_STATE_KEY = "owner_last_action"
UNDO_CONFIRM_KEY = "owner_undo_confirm"


def record_owner_action(
    action_type: str,
    details: Dict[str, Any],
    db_path: Optional[str | Path] = None,
    now: Optional[datetime.datetime] = None,
) -> None:
    """Record an owner Telegram action that is eligible for undo.

    Supported action_types:
      - 'approval': {'id': draft_id, 'previous_status': 'pending'}
      - 'rejection': {'id': draft_id, 'previous_status': 'pending', 'previous_reason': None}
      - 'settings': {'previous_settings': {...}}
      - 'office_request': {'request_id': int_or_str, 'intent': '...', 'args': {...}}
    """
    now_utc = now or datetime.datetime.now(datetime.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)

    record = {
        "action_type": action_type,
        "details": details,
        "ts": now_utc.isoformat(),
    }

    try:
        conn = guardrails.connect(str(db_path) if db_path else None)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO telegram_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (LAST_ACTION_STATE_KEY, json.dumps(record)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_last_undoable_action(
    db_path: Optional[str | Path] = None,
    now: Optional[datetime.datetime] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Retrieve last action if within the 10-minute window.

    Returns (action_dict, error_reason).
    """
    now_utc = now or datetime.datetime.now(datetime.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)

    raw = None
    try:
        conn = guardrails.connect(str(db_path) if db_path else None)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute("SELECT value FROM telegram_state WHERE key = ?", (LAST_ACTION_STATE_KEY,)).fetchone()
        conn.close()
        if row and row[0]:
            raw = row[0]
    except Exception:
        pass

    if not raw:
        return None, "Undoing kosam emi recent actions levu (no recent action found)."

    try:
        record = json.loads(raw)
    except Exception:
        return None, "Action record corrupt ayindi."

    action_ts_str = record.get("ts")
    if not action_ts_str:
        return None, "Action timestamp missing."

    try:
        clean = action_ts_str.replace("Z", "+00:00")
        action_dt = datetime.datetime.fromisoformat(clean)
        if action_dt.tzinfo is None:
            action_dt = action_dt.replace(tzinfo=datetime.timezone.utc)
        elapsed = (now_utc - action_dt).total_seconds()
        if elapsed > UNDO_EXPIRY_SECONDS:
            return None, f"10 minutes daatindi ({int(elapsed // 60)} min ago). Safe window aypoyindi, undo cheyadam kudardu."
    except Exception:
        return None, "Timestamp verification failed."

    return record, None


def describe_action_for_undo(record: Dict[str, Any]) -> str:
    """Format short Tenglish human-readable description of the action."""
    atype = record.get("action_type")
    details = record.get("details", {})

    if atype == "approval":
        aid = details.get("id", "?")
        return f"Draft #{aid} approval"
    elif atype == "rejection":
        aid = details.get("id", "?")
        return f"Draft #{aid} rejection"
    elif atype == "settings":
        return "Settings change"
    elif atype == "office_request":
        intent = details.get("intent", "UI change")
        return f"Office request ({intent})"
    return atype or "last action"


def apply_undo(
    record: Dict[str, Any],
    db_path: Optional[str | Path] = None,
) -> Tuple[bool, str]:
    """Execute undo safely for the recorded action.

    Returns (success: bool, message: str).
    """
    atype = record.get("action_type")
    details = record.get("details", {})

    conn = guardrails.connect(str(db_path) if db_path else None)
    try:
        if atype in ("approval", "rejection"):
            aid = details.get("id")
            if aid is None:
                return False, "Draft ID missing."
            # Revert to pending status, clear decision timestamp and reason
            conn.execute(
                "UPDATE approvals SET status = 'pending', decided_ts = NULL, reason = NULL WHERE id = ?",
                (aid,),
            )
            # Clear last_alerted_id if needed so it stays accessible
            conn.commit()
            action_name = "approved" if atype == "approval" else "rejected"
            return True, f"Draft #{aid} undo chesam: malli 'pending' status ki revert ayindi ✓."

        elif atype == "settings":
            prev_settings = details.get("previous_settings")
            if not prev_settings:
                return False, "Previous settings data ledu."
            try:
                import tg_settings
                tg_settings.save_settings(prev_settings, db_path=str(db_path) if db_path else None)
                return True, "Settings ni previous state ki undo chesam ✓."
            except Exception as e:
                return False, f"Settings revert cheyadam lo error: {e}"

        elif atype == "office_request":
            req_id = details.get("request_id")
            if req_id is not None:
                # Remove or mark cancelled from office_requests
                cols = {row[1] for row in conn.execute("PRAGMA table_info(office_requests)").fetchall()}
                if "id" in cols:
                    conn.execute("DELETE FROM office_requests WHERE id = ?", (req_id,))
                conn.commit()
                return True, f"Office request (#{req_id}) undo chesam (cancelled) ✓."
            else:
                # If no specific id, delete the latest row
                conn.execute("DELETE FROM office_requests WHERE rowid = (SELECT MAX(rowid) FROM office_requests)")
                conn.commit()
                return True, "Last office request undo chesam (cancelled) ✓."

        return False, f"Unknown action type '{atype}'."
    except Exception as exc:
        return False, f"Undo fail ayindi: {type(exc).__name__}"
    finally:
        # Clear last action record so it cannot be undone twice
        conn.execute("DELETE FROM telegram_state WHERE key = ?", (LAST_ACTION_STATE_KEY,))
        conn.commit()
        conn.close()
