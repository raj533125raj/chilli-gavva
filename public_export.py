"""public_export.py — Safe public status exports for Chilli Gavva dashboard.

All exported data is strictly safe for public repositories:
  - NO money amounts or currency strings
  - NO prompts or template internals
  - NO unapproved product ideas or private product content
  - NO secrets, API keys, bearer tokens, or credential key/token text
  - All lists are capped
  - Strict scrubbing applied to all outputs
"""

import datetime
import json
import math
import os
import re
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import guardrails
import secret_guard
import workforce

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_DIR = os.path.join(BASE_DIR, "exported_content", "products")

# Regex to scrub credentials, key=, token=, bearer tokens, API keys
SECRET_PATTERN = re.compile(
    r"\b(?:api[_-]?key|key|access_token|token|secret|password|cred)\b[\"']?\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)|\bBearer\s+[^\s,;\"']+",
    re.IGNORECASE
)

# Regex to scrub money / currency amounts (NO currency in public export).
_CURRENCIES = (r"USD|INR|EUR|GBP|JPY|CAD|AUD|NZD|CHF|CNY|RMB|HKD|SGD|AED|SAR|"
               r"KRW|RUB|BRL|MXN|ZAR|SEK|NOK|DKK|Rs\.?|dollars|rupees|cents|euros|pounds|yen")
CURRENCY_PATTERN = re.compile(
    r"[$₹€£¥₩₽]\s*[+-]?\d[\d,.]*|\b(?:" + _CURRENCIES + r")\s*[:=]?\s*[+-]?\d[\d,.]*"
    r"|\b[+-]?\d[\d,.]*\s*(?:(?:" + _CURRENCIES + r")\b|[$₹€£¥₩₽])",
    re.IGNORECASE
)


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def scrub_text(text: str) -> str:
    """Scrub secrets, tokens, API keys, and money/currency values from any public string."""
    if not text or not isinstance(text, str):
        return ""
    text = secret_guard.scrub_text(text)
    text = SECRET_PATTERN.sub("[redacted]", text)
    text = secret_guard.EMAIL.sub("[redacted]", text)
    text = CURRENCY_PATTERN.sub("[hidden]", text)
    return text.strip()


def scrub_public_data(value):
    """Apply the public string policy recursively, with a final cap on every list."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {scrub_text(key) if isinstance(key, str) else key: scrub_public_data(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_public_data(item) for item in value[:200]]
    return value


def _clean_product_title(title: str) -> str:
    """Extract a clean, generic short title from draft title."""
    if not title:
        return "Digital Product"
    t = scrub_text(title)
    t = re.sub(r"^\s*Draft:\s*", "", t, flags=re.I)
    t = re.sub(r"^\s*\[[^\]]+\]\s*", "", t)
    t = re.sub(r"\s+\(v\d+.*?\)$", "", t, flags=re.I)
    t = t.strip()
    return (t[:80] if len(t) > 80 else t) or "Digital Product"


def get_public_products(db_path=None, out_dir=None) -> list:
    """Return owner-APPROVED products only.

    Each item:
      - id: approval ID
      - short_title: clean short title (no sensitive text, no currency)
      - has_image: bool
      - listing_status: 'draft' | 'ready' | 'listed'
      - truth_check: {"verdict": "READY" | "NEEDS FIX", "status": "READY" | "NEEDS FIX", "reasons": [...]}
      - store_url: str or None (only if public store URL exists)
    """
    out_dir = out_dir or PRODUCTS_DIR
    db_file = db_path or guardrails.DB_PATH
    if not os.path.exists(db_file):
        return []

    products = []
    try:
        conn = sqlite3.connect(db_file)
        if not _has_table(conn, "approvals"):
            conn.close()
            return []

        # Owner-APPROVED products only
        rows = conn.execute(
            "SELECT id, title, payload FROM approvals WHERE status = 'approved' AND kind = 'public_draft' ORDER BY id DESC LIMIT 50"
        ).fetchall()

        # Build map of product_builds
        build_map = {}
        if _has_table(conn, "product_builds"):
            b_rows = conn.execute("SELECT approval_id, status, folder FROM product_builds").fetchall()
            build_map = {r[0]: {"status": r[1], "folder": r[2]} for r in b_rows}

        # Build map of truth_checks
        tc_map = {}
        if _has_table(conn, "truth_checks"):
            tc_rows = conn.execute("SELECT approval_id, verdict, score FROM truth_checks").fetchall()
            tc_map = {r[0]: {"verdict": r[1], "score": r[2]} for r in tc_rows}

        conn.close()

        for app_id, title, payload in rows:
            clean_title = _clean_product_title(title)
            b_info = build_map.get(app_id, {})
            b_status = b_info.get("status")
            b_folder = b_info.get("folder")

            # Check image existence
            has_image = False
            folder_path = None
            if b_folder and os.path.isdir(os.path.join(out_dir, b_folder)):
                folder_path = os.path.join(out_dir, b_folder)
            else:
                # Look for matching folder on disk
                if os.path.isdir(out_dir):
                    matched = next((d for d in os.listdir(out_dir) if d.startswith(f"{app_id}-")), None)
                    if matched:
                        folder_path = os.path.join(out_dir, matched)

            if folder_path and os.path.isdir(folder_path):
                files = os.listdir(folder_path)
                has_image = any(f.endswith((".png", ".jpg", ".jpeg")) for f in files)

            # Check store_url if public
            store_url = None
            if payload:
                try:
                    p_data = json.loads(payload) if isinstance(payload, str) and payload.strip().startswith("{") else {}
                    url_candidate = p_data.get("store_url") or p_data.get("gumroad_url")
                    if url_candidate and str(url_candidate).startswith("https://"):
                        store_url = str(url_candidate).strip()
                except Exception:
                    store_url = None

            # Determine listing status
            if store_url:
                listing_status = "listed"
            elif b_status in ("built", "ready", "not_for_sale") or folder_path:
                listing_status = "ready"
            else:
                listing_status = "draft"

            # Determine truth check verdict and generic reasons
            tc_info = tc_map.get(app_id, {})
            tc_verdict = (tc_info.get("verdict") or "").lower()
            reasons = []

            if folder_path and os.path.exists(os.path.join(folder_path, "TRUTH_CHECK.md")):
                try:
                    with open(os.path.join(folder_path, "TRUTH_CHECK.md"), encoding="utf-8") as f:
                        tc_content = f.read()
                    if "Verdict: READY" in tc_content or "VERDICT: READY" in tc_content:
                        tc_verdict = "ready"
                    elif "Verdict: NEEDS FIX" in tc_content or "VERDICT: NEEDS FIX" in tc_content:
                        tc_verdict = "needs fix"

                    # Extract short generic problem bullet lines if present
                    prob_match = re.search(r"## Problems\s*\n((?:- .*\n?)+)", tc_content)
                    if prob_match:
                        raw_bullets = re.findall(r"- (.*)", prob_match.group(1))
                        for b in raw_bullets:
                            gen_reason = scrub_text(b)[:100]
                            if gen_reason and gen_reason not in reasons:
                                reasons.append(gen_reason)
                except Exception:
                    pass

            verdict_str = "READY" if tc_verdict in ("ready", "pass") else "NEEDS FIX"
            if verdict_str == "READY":
                reasons = []

            products.append({
                "id": app_id,
                "short_title": clean_title,
                "has_image": has_image,
                "listing_status": listing_status,
                "truth_check": {
                    "verdict": verdict_str,
                    "status": verdict_str,
                    "reasons": reasons[:3]
                },
                "store_url": store_url
            })

    except Exception:
        pass

    return products[:20]


def _categorize_reject_reason(raw: str) -> str:
    """Classify a raw review/rejection reason into a short generic reason category."""
    if not raw or not isinstance(raw, str):
        return "Quality check issue"
    low = raw.lower()
    if any(k in low for k in ["risky", "money", "earnings", "income", "guarantee", "claim", "banned"]):
        return "Risky or prohibited claim"
    if any(k in low for k in ["buyer", "audience", "target", "who it is for"]):
        return "Target audience unclear"
    if any(k in low for k in ["structure", "missing", "feature", "inside", "contents"]):
        return "Product structure incomplete"
    if any(k in low for k in ["free", "beat", "value", "conviction"]):
        return "Unclear value over free template"
    if any(k in low for k in ["short", "length", "too short"]):
        return "Draft content too short"
    if any(k in low for k in ["tag", "search tag"]):
        return "Search tags requirement not met"
    if any(k in low for k in ["price", "usd", "currency"]):
        return "Missing standard pricing format"
    return "Formatting or rubric issue"


def get_public_reject_reasons(db_path=None) -> list:
    """Top 5 generic reasons with counts (last 30 days) from checker/council/reviews."""
    db_file = db_path or guardrails.DB_PATH
    if not os.path.exists(db_file):
        return []

    counts = {}
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat()

    try:
        conn = sqlite3.connect(db_file)

        # 1. Rejections in approvals table
        if _has_table(conn, "approvals"):
            a_rows = conn.execute(
                "SELECT reason, review FROM approvals WHERE status = 'rejected' "
                "AND (decided_ts >= ? OR ts >= ?)", (cutoff, cutoff)
            ).fetchall()
            for r_text, rev_text in a_rows:
                raw_list = []
                if r_text:
                    raw_list.append(r_text)
                if rev_text:
                    # Extract reasons after issues: or top:
                    m = re.search(r"\|\s*(?:top|issues):\s*([^|]+)", rev_text, re.I)
                    if m:
                        raw_list.extend(m.group(1).split(";"))
                for raw in raw_list:
                    cat = _categorize_reject_reason(raw)
                    counts[cat] = counts.get(cat, 0) + 1

        # 2. Council debate retries
        if _has_table(conn, "council_debates"):
            c_rows = conn.execute(
                "SELECT reasons FROM council_debates WHERE verdict IN ('RETRY', 'FLAGGED') "
                "AND ts >= ?", (cutoff,)
            ).fetchall()
            for (c_reasons,) in c_rows:
                if c_reasons:
                    for raw in c_reasons.split(";"):
                        cat = _categorize_reject_reason(raw)
                        counts[cat] = counts.get(cat, 0) + 1

        # 3. Tasks table reviews
        if _has_table(conn, "tasks"):
            t_rows = conn.execute(
                "SELECT result FROM tasks WHERE kind = 'review' AND status = 'done' "
                "AND finished_ts >= ?", (cutoff,)
            ).fetchall()
            for (res_str,) in t_rows:
                if res_str:
                    try:
                        res = json.loads(res_str)
                        issues = res.get("issues") or []
                        for iss in issues:
                            cat = _categorize_reject_reason(iss)
                            counts[cat] = counts.get(cat, 0) + 1
                    except Exception:
                        pass

        conn.close()
    except Exception:
        pass

    # Sort DESC by count
    sorted_reasons = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [{"reason": cat, "count": cnt} for cat, cnt in sorted_reasons[:5]]


def get_public_report(db_path=None) -> dict:
    """Today's counts (drafts made, waiting, approved, products built, errors)."""
    db_file = db_path or guardrails.DB_PATH
    today_start = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT00:00:00Z")

    report = {
        "drafts_made": 0,
        "drafts_waiting": 0,
        "drafts_approved": 0,
        "products_built": 0,
        "errors": 0
    }

    if not os.path.exists(db_file):
        return report

    try:
        conn = sqlite3.connect(db_file)

        if _has_table(conn, "approvals"):
            report["drafts_made"] = conn.execute(
                "SELECT COUNT(*) FROM approvals WHERE ts >= ?", (today_start,)
            ).fetchone()[0]
            report["drafts_waiting"] = conn.execute(
                "SELECT COUNT(*) FROM approvals WHERE status = 'pending'"
            ).fetchone()[0]
            report["drafts_approved"] = conn.execute(
                "SELECT COUNT(*) FROM approvals WHERE status = 'approved' AND (decided_ts >= ? OR ts >= ?)",
                (today_start, today_start)
            ).fetchone()[0]

        if _has_table(conn, "product_builds"):
            report["products_built"] = conn.execute(
                "SELECT COUNT(*) FROM product_builds WHERE status = 'built' AND ts >= ?", (today_start,)
            ).fetchone()[0]

        if _has_table(conn, "tasks"):
            report["errors"] = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'failed' AND finished_ts >= ?", (today_start,)
            ).fetchone()[0]

        conn.close()
    except Exception:
        pass

    return report


def _generic_action_label(agent_name: str, task_kind: str, is_running: bool) -> str:
    """Return a short generic label for the worker's action."""
    if not is_running:
        return "Idle"
    kind_labels = {
        "plan": "Planning daily tasks",
        "research": "Researching topic ideas",
        "draft": "Writing product spec",
        "rewrite": "Refining product draft",
        "review": "Checking product quality",
        "code_proposal": "Reviewing system checks"
    }
    return kind_labels.get(task_kind, f"Executing {agent_name} task")


def get_public_workers(db_path=None) -> list:
    """Per agent state working/idle/off, short last action label, last_seen ISO time."""
    db_file = db_path or guardrails.DB_PATH
    w_status = workforce.worker_status(db_file)
    daemon_running = w_status.get("running", False)

    workers = []
    if not os.path.exists(db_file):
        for a in workforce.AGENTS:
            workers.append({
                "name": a["name"],
                "state": "off",
                "last_action": "Idle",
                "last_seen": None
            })
        return workers[:20]

    try:
        conn = sqlite3.connect(db_file)
        for a in workforce.AGENTS:
            name = a["name"]

            # Running task for this agent?
            r_task = conn.execute(
                "SELECT kind, started_ts FROM tasks WHERE agent = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
                (name,)
            ).fetchone()

            # Last task for this agent
            l_task = conn.execute(
                "SELECT finished_ts, started_ts, ts, kind, status FROM tasks WHERE agent = ? ORDER BY id DESC LIMIT 1",
                (name,)
            ).fetchone()

            if r_task:
                state = "working"
                action = _generic_action_label(name, r_task[0], True)
                last_seen = r_task[1] or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                state = "idle" if daemon_running else "off"
                action = "Idle"
                last_seen = None
                if l_task:
                    last_seen = l_task[0] or l_task[1] or l_task[2]

            workers.append({
                "name": name,
                "state": state,
                "last_action": scrub_text(action)[:50],
                "last_seen": last_seen
            })

        conn.close()
    except Exception:
        for a in workforce.AGENTS:
            workers.append({
                "name": a["name"],
                "state": "off",
                "last_action": "Idle",
                "last_seen": None
            })

    return workers[:20]


def get_public_pipeline(db_path=None) -> dict:
    """Today's plan vs done per stage (from throughput plan), counts only."""
    plan_info = workforce.get_daily_pipeline_plan(db_path)
    total_quotas = plan_info.get("total_quotas", {})

    today_start = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    db_file = db_path or guardrails.DB_PATH

    done_counts = {
        "plan": 0,
        "research": 0,
        "drafts": 0,
        "review": 0,
        "product_builds": 0,
        "promotion": 0
    }

    if os.path.exists(db_file):
        try:
            conn = sqlite3.connect(db_file)
            if _has_table(conn, "tasks"):
                rows = conn.execute(
                    "SELECT kind, COUNT(*) FROM tasks WHERE status = 'done' AND finished_ts >= ? GROUP BY kind",
                    (today_start,)
                ).fetchall()
                for k, cnt in rows:
                    if k == "plan":
                        done_counts["plan"] += cnt
                    elif k == "research":
                        done_counts["research"] += cnt
                    elif k in ("draft", "rewrite"):
                        done_counts["drafts"] += cnt
                    elif k == "review":
                        done_counts["review"] += cnt

            if _has_table(conn, "product_builds"):
                done_counts["product_builds"] = conn.execute(
                    "SELECT COUNT(*) FROM product_builds WHERE status = 'built' AND ts >= ?", (today_start,)
                ).fetchone()[0]

            if _has_table(conn, "marketing_kits"):
                done_counts["promotion"] = conn.execute(
                    "SELECT COUNT(*) FROM marketing_kits WHERE created_at >= ?", (today_start,)
                ).fetchone()[0]

            conn.close()
        except Exception:
            pass

    stages = ["plan", "research", "drafts", "review", "product_builds", "promotion"]
    pipeline = {}
    for stg in stages:
        pipeline[stg] = {
            "plan": total_quotas.get(stg, 0),
            "done": done_counts.get(stg, 0)
        }

    return pipeline


PUBLIC_AI_PROVIDERS = ("Groq", "Gemini", "Cloudflare", "OpenRouter", "FreeLLMAPI")


def get_public_ai_health(db_path=None, checked_at=None) -> list:
    """Read router observations with reason and checked_at."""
    checked_at = checked_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {
        name: {
            "provider": name,
            "model": None,
            "state": "unknown",
            "reason": "No activity recorded",
            "checked_at": checked_at
        }
        for name in PUBLIC_AI_PROVIDERS
    }

    db_file = db_path or guardrails.DB_PATH
    if not os.path.exists(db_file):
        return list(result.values())

    try:
        uri = Path(db_file).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        if not _has_table(conn, "provider_log"):
            conn.close()
            return list(result.values())

        rows = conn.execute("SELECT day, provider, ok, fail FROM provider_log ORDER BY day DESC").fetchall()
        seen = set()
        for day, provider, ok, fail in rows:
            if provider not in result or provider in seen:
                continue
            try:
                observed_day = datetime.date.fromisoformat(day)
            except (TypeError, ValueError):
                continue
            if observed_day.isoformat() != day or day > checked_at[:10]:
                continue
            seen.add(provider)
            if not all(isinstance(count, int) and count >= 0 for count in (ok, fail)):
                continue
            if ok > 0 and fail == 0:
                result[provider]["state"] = "up"
                result[provider]["reason"] = "All recent calls succeeded"
            elif fail > 0 and ok == 0:
                result[provider]["state"] = "down"
                result[provider]["reason"] = "Provider cooldown or error"
            elif ok > 0 and fail > 0:
                result[provider]["state"] = "unknown"
                result[provider]["reason"] = "Mixed outcomes recorded"

        conn.close()
    except Exception:
        pass

    return list(result.values())


def get_public_minutes(db_path=None) -> dict:
    """Minutes used/limit ONLY from a real local record (null otherwise)."""
    used_min = None
    limit_min = None

    db_file = db_path or guardrails.DB_PATH
    if os.path.exists(db_file):
        try:
            conn = sqlite3.connect(db_file)
            if _has_table(conn, "control"):
                r_used = conn.execute("SELECT value FROM control WHERE key IN ('month_minutes', 'used_this_month', 'minutes_used')").fetchone()
                r_lim = conn.execute("SELECT value FROM control WHERE key IN ('minutes_limit', 'limit', 'month_limit')").fetchone()
                if r_used and r_used[0] is not None:
                    try:
                        used_min = int(r_used[0])
                    except (ValueError, TypeError):
                        pass
                if r_lim and r_lim[0] is not None:
                    try:
                        limit_min = int(r_lim[0])
                    except (ValueError, TypeError):
                        pass

            if (used_min is None and limit_min is None) and _has_table(conn, "action_minutes"):
                r_act = conn.execute("SELECT used_this_month, limit_minutes FROM action_minutes ORDER BY id DESC LIMIT 1").fetchone()
                if r_act:
                    try:
                        used_min = int(r_act[0]) if r_act[0] is not None else None
                        limit_min = int(r_act[1]) if r_act[1] is not None else None
                    except (ValueError, TypeError):
                        pass

            conn.close()
        except Exception:
            pass

    return {
        "used_this_month": used_min,
        "limit": limit_min
    }


# These readers never initialize tables or persist telemetry in the private DB.
def _office_rows(db_path, query, params=()):
    try:
        uri = Path(db_path or guardrails.DB_PATH).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query, params)]
    except (OSError, sqlite3.Error):
        return []


def _iso(value):
    """Normalize recorded timestamps; reject arbitrary text rather than copying it."""
    try:
        if type(value) in (int, float):
            stamp = datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
        elif isinstance(value, str):
            stamp = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        else:
            return None
        return stamp.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _short(value, limit=120):
    return " ".join(scrub_text(value).split())[:limit]


def _public_agent(value):
    if isinstance(value, str):
        for agent in workforce.AGENTS:
            name = agent["name"]
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if value in (name, slug, "chilli-gavva-robot-" + slug):
                return _short(name)
    return "Unknown agent"


def _number(value, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return value if maximum is None or value <= maximum else None


def get_public_agent_budgets(db_path=None, checked_at=None):
    """Per-agent observations, when recorded; task counts are not LLM call counts.

    Current usage_log records only a shared daily total. Optional agent_usage rows
    (day, agent, calls_used, calls_limit, context_percent) supply actual per-agent
    telemetry; absent observations remain null, including context utilization.
    """
    day = (checked_at or guardrails.today())[:10]
    rows = _office_rows(db_path, "SELECT agent,calls_used,calls_limit,context_percent "
                        "FROM agent_usage WHERE day=? ORDER BY rowid DESC LIMIT 100", (day,))
    observed = {}
    for row in rows:
        observed.setdefault(row["agent"], row)
    result = []
    for agent in workforce.AGENTS[:20]:
        row = observed.get(agent["name"], {})
        used, limit = _number(row.get("calls_used")), _number(row.get("calls_limit"))
        result.append({"agent": _short(agent["name"]),
                       "calls_used_today": used if type(used) is int else None,
                       "calls_limit_today": limit if type(limit) is int else None,
                       "context_percent": _number(row.get("context_percent"), 100)})
    return result


def get_public_loop_events(db_path=None):
    """Last 50 failures/repeated attempts and Doctor cases, with fixed labels."""
    result = []
    rows = _office_rows(db_path, "SELECT id,agent,status,attempts,COALESCE(finished_ts,started_ts,ts) AS at "
                        "FROM tasks WHERE status='failed' OR attempts>=3 "
                        "ORDER BY julianday(COALESCE(finished_ts,started_ts,ts)) DESC,id DESC LIMIT 50")
    for row in rows:
        loop = isinstance(row["attempts"], int) and row["attempts"] >= 3
        result.append({"agent": _public_agent(row["agent"]), "kind": "loop" if loop else "error",
                       "label": "Repeated task attempts" if loop else "Task failed", "at": _iso(row["at"])})
    for row in _office_rows(db_path, "SELECT agent,category,ts FROM doctor_cases ORDER BY julianday(ts) DESC,id DESC LIMIT 50"):
        loop = row["category"] in ("repeat_loop", "loop_limit", "stuck", "stuck_task")
        result.append({"agent": _public_agent(row["agent"]), "kind": "loop" if loop else "error",
                       "label": "Loop prevention check" if loop else "Recovery check", "at": _iso(row["ts"])})
    return sorted((row for row in result if row["at"]),
                  key=lambda row: datetime.datetime.fromisoformat(row["at"].replace("Z", "+00:00")),
                  reverse=True)[:50]


def get_public_activity_events(db_path=None):
    """Stable task identifiers for the archive; never copy payloads/results/errors."""
    labels = {"queued": "Task queued", "running": "Task started", "done": "Task completed",
              "failed": "Task failed", "cancelled": "Task cancelled"}
    rows = _office_rows(db_path, "SELECT id,agent,status,COALESCE(finished_ts,started_ts,ts) AS at "
                        "FROM tasks ORDER BY julianday(COALESCE(finished_ts,started_ts,ts)) DESC,id DESC LIMIT 200")
    return [{"id": _short("task:" + str(row["id"]), 80), "agent": _public_agent(row["agent"]),
             "label": labels.get(row["status"], "Task activity"), "at": _iso(row["at"])}
            for row in rows if _iso(row["at"])]


def _trigger_condition(value):
    kind = value.get("kind")
    if kind == "at":
        return "At scheduled time"
    if kind == "task_done":
        watch = value.get("watch_id")
        if isinstance(watch, str) and re.fullmatch(r"task:[1-9][0-9]{0,9}", watch):
            return watch + " completed"
    if kind == "agent_state" and value.get("match_state") in ("idle", "working", "error"):
        return _public_agent(value.get("watch_id")) + " is " + value["match_state"]
    if kind == "state":
        condition = value.get("condition")
        if (isinstance(condition, dict) and condition.get("metric") in ("drafts_waiting", "queued_tasks", "failed_tasks")
                and condition.get("op") in (">", ">=", "<", "<=", "==")
                and type(condition.get("value")) is int and 0 <= condition["value"] <= 10000):
            return f"{condition['metric']} {condition['op']} {condition['value']}"
    return "Unknown condition"


def get_public_schedules(db_path=None):
    """Capped definitions. Mission titles and trigger labels are executable prompts."""
    result = []
    for row in _office_rows(db_path, "SELECT id,every,next_run,status,updated FROM office_missions ORDER BY updated DESC,id LIMIT 100"):
        state = row["status"] if row["status"] in ("active", "paused", "archived") else "unknown"
        result.append({"id": _short(row["id"], 80), "name": "Recurring mission",
                       "cadence": row["every"] if row["every"] in ("1h", "1d", "1w") else None,
                       "next_run": _iso(row["next_run"]) if state == "active" else None,
                       "state": state, "_updated": _number(row["updated"]) or 0})
    for row in _office_rows(db_path, "SELECT id,definition,archived,last_fired,updated FROM office_triggers ORDER BY updated DESC,id LIMIT 100"):
        try:
            value = json.loads(row["definition"])
            if not isinstance(value, dict):
                continue
        except (ValueError, TypeError):
            continue
        state = "archived" if row["archived"] else "active" if value.get("enabled") is True else "paused"
        result.append({"id": _short(row["id"], 80), "name": "Office trigger",
                       "condition": _trigger_condition(value),
                       "next_run": _iso(value.get("at")) if state == "active" and value.get("kind") == "at" and row["last_fired"] is None else None,
                       "state": state, "_updated": _number(row["updated"]) or 0})
    result.sort(key=lambda row: row["_updated"], reverse=True)
    return [{key: value for key, value in row.items() if key != "_updated"} for row in result[:100]]


def get_public_receipts(db_path=None):
    """Receipt outcomes only. Replies, signatures, digests and raw reasons stay private.

    Older receipts do not store request type, so it is explicitly unknown.
    """
    actions = {"approve", "reject", "pause", "resume", "set_switch", "add_mission", "edit_mission",
               "archive_mission", "add_trigger", "edit_trigger", "set_trigger", "delete_trigger",
               "send_message", "chat", "ask", "route_request", "set_policy", "set_autonomy", "set_budget",
               "set_cube", "cube_on", "cube_off"}
    reasons = {"applied": "Request applied", "held": "Request held", "rejected": "Request rejected"}
    rows = _office_rows(db_path, "SELECT id,receipt FROM office_requests "
                        "ORDER BY julianday(json_extract(CASE WHEN json_valid(receipt) THEN receipt ELSE '{}' END,'$.at')) DESC,rowid DESC LIMIT 50")
    result = []
    for row in rows:
        try:
            value = json.loads(row["receipt"])
            if not isinstance(value, dict):
                continue
        except (ValueError, TypeError):
            continue
        state = value.get("state")
        state = state if isinstance(state, str) and state in reasons else "unknown"
        request_type = value.get("type")
        result.append({"id": _short(row["id"], 80),
                       "type": request_type if isinstance(request_type, str) and request_type in actions else "unknown",
                       "state": state, "safe_reason": reasons.get(state, "Outcome unavailable"),
                       "at": _iso(value.get("at"))})
    return [row for row in result if row["at"]][:50]


def get_public_workspaces():
    """Small registry projection; floor is unknown until explicitly configured."""
    import cubes
    try:
        rows = cubes.load_registry()
    except (OSError, ValueError, TypeError):
        return []
    return [{"id": _short(row.get("id"), 80), "name": _short(row.get("name")),
             "floor": _short(row["floor"], 80) if isinstance(row.get("floor"), str)
             else row.get("floor") if type(row.get("floor")) is int else None} for row in rows[:50]]


_EXPORT_PROCESS_STARTED_AT = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_public_cloud_heartbeat(checked_at=None):
    """Cloud cycle export interval, not a claim that the whole workflow has finished.

    export_status is imported before cloud_runner executes its cycle. Local and
    self-hosted processes must never claim a GitHub-hosted cloud observation.
    """
    checked_at = checked_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    cloud = (os.environ.get("GITHUB_ACTIONS") == "true" and
             os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted" and
             re.fullmatch(r"[0-9]{1,30}", run_id) is not None)
    return {"last_run_start": _iso(_EXPORT_PROCESS_STARTED_AT) if cloud else None,
            "last_run_end": _iso(checked_at) if cloud else None,
            "runner": "github-actions" if cloud else None, "run_id": _short(run_id, 30) if cloud else None}


def get_public_changelog(repo_dir=None):
    """Only the last 20 local commit subjects; never author identities or bodies."""
    try:
        result = subprocess.run(["git", "log", "-20", "--no-show-signature", "--no-notes",
                                 "--no-decorate", "--no-color", "--format=%s"], cwd=repo_dir or BASE_DIR,
                                capture_output=True, text=True, timeout=5, check=True)
        return [_short(subject, 160) for subject in result.stdout.splitlines()[:20]]
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return []


def get_public_office_export(db_path=None, checked_at=None):
    checked_at = checked_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"agent_budgets": get_public_agent_budgets(db_path, checked_at),
            "loop_events": get_public_loop_events(db_path),
            "activity_events": get_public_activity_events(db_path),
            "schedules": get_public_schedules(db_path), "receipts": get_public_receipts(db_path),
            "workspaces": get_public_workspaces(), "cloud_heartbeat": get_public_cloud_heartbeat(checked_at),
            "changelog": get_public_changelog()}


def get_full_public_export(db_path=None, checked_at=None) -> dict:
    """Consolidate all new safe public fields."""
    checked_at = checked_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return scrub_public_data({
        "products": get_public_products(db_path),
        "reject_reasons": get_public_reject_reasons(db_path),
        "report": get_public_report(db_path),
        "workers": get_public_workers(db_path),
        "pipeline": get_public_pipeline(db_path),
        "ai_health": get_public_ai_health(db_path, checked_at=checked_at),
        "minutes": get_public_minutes(db_path),
        **get_public_office_export(db_path, checked_at)
    })
