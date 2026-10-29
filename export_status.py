"""Builds the public status snapshot used by the dashboard (web/index.html).

The snapshot contains counts, titles and short logs only. It never contains
API keys, config values, or the full text of drafts.
"""
import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys

import guardrails
import improver
import service_intake
import tool_builder
import workforce

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_PATH = os.path.join(BASE_DIR, "web", "data", "status.json")


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _money(db_path):
    """Real money only (rows recorded with proof by record_earning.py), plus the activity log."""
    conn = sqlite3.connect(db_path or guardrails.DB_PATH)
    real = 0.0
    if _has_table(conn, "campaigns") and "source" in _cols(conn, "campaigns"):
        real = conn.execute("SELECT COALESCE(SUM(earnings), 0) FROM campaigns "
                            "WHERE source = 'real' AND status != 'VOIDED'").fetchone()[0]
    logs = []
    if _has_table(conn, "agent_logs"):
        logs = conn.execute("SELECT timestamp, agent_name, status, message FROM agent_logs "
                            "ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()
    return real, [{"ts": t[:19], "agent": a, "status": s, "message": (m or "")[:200]} for t, a, s, m in logs]


def purge_simulated(db_path=None):
    """Delete the old engine.py simulation rows: fake money, its agent logs, fake discoveries and
    health reports. Real earnings (source = 'real'), drafts, approvals, tasks and Telegram state
    are never touched. Safe to run every cycle; returns the number of rows deleted per table."""
    conn = sqlite3.connect(db_path or guardrails.DB_PATH)
    deleted = {}
    try:
        if _has_table(conn, "campaigns"):
            where = "COALESCE(source, 'simulated') != 'real'" if "source" in _cols(conn, "campaigns") else "1"
            deleted["campaigns"] = conn.execute(f"DELETE FROM campaigns WHERE {where}").rowcount
        if _has_table(conn, "research_discoveries"):
            where = "COALESCE(source, 'simulated') != 'real'" if "source" in _cols(conn, "research_discoveries") else "1"
            deleted["research_discoveries"] = conn.execute(f"DELETE FROM research_discoveries WHERE {where}").rowcount
        for table in ("agent_logs", "health_reports"):  # only the old simulation (engine.py) writes these
            if _has_table(conn, table):
                deleted[table] = conn.execute(f"DELETE FROM {table}").rowcount
        conn.commit()
    finally:
        conn.close()
    return {t: n for t, n in deleted.items() if n}



def _llm_providers() -> list:
    """Return LLM provider status list; silently returns [] if llm_router unavailable."""
    try:
        import llm_router
        return llm_router.provider_status()
    except Exception:
        return []

def build_status_private(db_path=None):
    """The full local snapshot (money, prompts, logs, ...). Used by the local server only; never written to the repo."""
    real, logs = _money(db_path)
    conn = workforce.connect(db_path)
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall())
    recent = [{"id": r[0], "kind": r[1], "status": r[2], "agent": r[3], "attempts": r[4]} for r in
              conn.execute("SELECT id, kind, status, agent, attempts FROM tasks ORDER BY id DESC LIMIT 12")]
    prompts = [{"id": r[0], "name": r[1], "active": bool(r[2]), "trials": r[3],
                "quality": round(improver.quality((r[3], r[4], r[5], r[6])), 1)} for r in
               conn.execute("SELECT id, name, active, trials, score_sum, decisions, approvals "
                            "FROM prompt_versions ORDER BY name, id")]
    validation = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'validation_log'").fetchone():
        v = conn.execute("SELECT COUNT(*), COALESCE(SUM(visitors),0), COALESCE(SUM(signups),0), "
                         "COALESCE(SUM(engaged),0) FROM validation_log").fetchone()
        validation = {"days": v[0], "visitors": v[1], "signups": v[2], "engaged": v[3],
                      "targets": {"visitors": 1000, "signups": 50, "engaged_pct": 30}}
    conn.close()
    # Service requests (last 10)
    svc_reqs = service_intake.list_requests(db_path)[:10]
    # Built tools
    tb_conn = tool_builder.connect(db_path)
    built_tools = [{"slug": r[0], "title": r[1], "built_ts": r[2]} for r in
                   tb_conn.execute("SELECT slug, title, built_ts FROM tool_pages ORDER BY approval_id DESC LIMIT 10").fetchall()]
    tb_conn.close()
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "money": {"real_usd": round(real, 2)},
        "kill_switch": guardrails.is_killed(),
        "worker": workforce.worker_status(db_path),
        "llm": {"calls_today": guardrails.calls_today(db_path), "cap": guardrails.daily_cap()},
        "agents": workforce.agent_states(db_path),
        "task_counts": counts,
        "recent_tasks": recent,
        "pending_approvals": guardrails.pending(db_path),
        "prompts": prompts,
        "validation": validation,
        "logs": logs,
        "service_requests": svc_reqs,
        "built_tools": built_tools,
        "llm_providers": _llm_providers(),
    }


# The public snapshot: only these fields. No money, prompts, logs, validation, service requests, built tools,
# worker info or approval review text. The robot's own files and the dashboard are the only readers.
PUBLIC_AGENT_KEYS = ("name", "level", "role", "kinds", "state", "tasks_done")
PUBLIC_TASK_KEYS = ("id", "kind", "status", "agent", "attempts")
PUBLIC_APPROVAL_KEYS = ("id", "ts", "kind", "title")


def _public_approval(approval):
    """Return the small, anonymous approval record safe for the public file.

    A pending approval is an idea the owner has not accepted yet.  Its real
    title is therefore private, even though the identifier and queue time are
    useful on the public dashboard.
    """
    item = {k: approval.get(k) for k in PUBLIC_APPROVAL_KEYS}
    if approval.get("status") != "approved":
        item["title"] = f"Draft #{approval.get('id')}"
    return item


def _handoffs(db_path=None, now_utc=None):
    """Build a list of recent agent activity/handoffs (last 24 hours).

    Each row: {from_agent, to_agent, item: short label like 'draft #36', at: ISO time}.
    Only agent names/roles and item ids; NO money amounts, NO prompts, NO product ideas/content.
    Max 20 rows, newest first.
    """
    now_dt = now_utc or datetime.datetime.now(datetime.timezone.utc)
    cutoff = (now_dt - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    handoffs = []
    db_file = db_path or guardrails.DB_PATH
    if not os.path.exists(db_file):
        return handoffs

    try:
        conn = sqlite3.connect(db_file)
        if _has_table(conn, "tasks"):
            rows = conn.execute(
                "SELECT id, kind, status, agent, result, finished_ts, ts FROM tasks "
                "ORDER BY id DESC LIMIT 100"
            ).fetchall()
            for tid, kind, status, agent, result_str, fin_ts, create_ts in rows:
                if status != "done" or not agent:
                    continue
                ts = fin_ts or create_ts or ""
                # Normalize timestamp to ISO string with Z if length permits
                ts_iso = ts[:19] + "Z" if len(ts) >= 19 else ts
                if ts_iso < cutoff:
                    continue

                res = {}
                if result_str:
                    try:
                        res = json.loads(result_str)
                    except Exception:
                        res = {}

                # 1. Draft generated -> waiting for approval
                approval_id = res.get("approval_id")
                if approval_id:
                    handoffs.append({
                        "from_agent": agent,
                        "to_agent": "Content Manager",
                        "item": f"draft #{approval_id}",
                        "at": ts_iso,
                    })

                # 2. Next tasks created in follow-up
                next_tasks = res.get("next")
                if isinstance(next_tasks, list):
                    for nxt in next_tasks:
                        if isinstance(nxt, dict):
                            nxt_kind = nxt.get("kind")
                            to_agent = workforce.KIND_TO_AGENT.get(nxt_kind, "CEO")
                            # If payload points to an approval draft, label item as draft #id or task #id
                            nxt_payload = nxt.get("payload") or {}
                            app_id = nxt_payload.get("approval_id")
                            item_label = f"draft #{app_id}" if app_id else f"task #{tid}"
                            handoffs.append({
                                "from_agent": agent,
                                "to_agent": to_agent,
                                "item": item_label,
                                "at": ts_iso,
                            })

        conn.close()
    except Exception:
        pass

    # Sort newest first, max 20 rows
    handoffs.sort(key=lambda x: x["at"], reverse=True)
    return handoffs[:20]


def build_status(db_path=None):
    """The snapshot written to web/data/status.json (safe for a public repo)."""
    full = build_status_private(db_path)
    return {
        "generated_at": full["generated_at"],
        "kill_switch": full["kill_switch"],
        "llm": {"calls_today": full["llm"]["calls_today"], "cap": full["llm"]["cap"]},
        "agents": [{k: a.get(k) for k in PUBLIC_AGENT_KEYS} for a in full["agents"]],
        "task_counts": full["task_counts"],
        "recent_tasks": [{k: t.get(k) for k in PUBLIC_TASK_KEYS} for t in full["recent_tasks"]],
        "pending_approvals": [_public_approval(a) for a in full["pending_approvals"]],
        "worker": {"running": full["worker"].get("running"), "age_seconds": full["worker"].get("age_seconds")},
        "handoffs": _handoffs(db_path),
    }


# Names are public constants from llm_router._PROVIDERS, never configuration values.
PUBLIC_AI_PROVIDERS = ("Groq", "Gemini", "GitHubModels", "Cloudflare", "OpenRouter", "FreeLLMAPI")


def _ai_health(db_path=None, checked_at=None):
    """Read router observations without probing providers or reading credentials.

    provider_log stores daily counts, not ordered events or model identifiers.
    Only a latest day with exclusively successes/failures establishes an outcome;
    mixed counts cannot establish the last outcome. Missing models stay null.
    checked_at is the UTC snapshot check time, NOT a fabricated last-call time.
    """
    checked_at = checked_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {name: {"provider": name, "model": None, "state": "unknown", "checked_at": checked_at}
              for name in PUBLIC_AI_PROVIDERS}
    conn = None
    try:
        uri = Path(db_path or guardrails.DB_PATH).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        if not _has_table(conn, "provider_log"):
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
            elif fail > 0 and ok == 0:
                result[provider]["state"] = "down"
    except (sqlite3.Error, OSError, ValueError):
        # A missing/unreadable recording is unknown, not a provider outage.
        pass
    finally:
        if conn is not None:
            conn.close()
    return list(result.values())


def write_status(path=None, db_path=None):
    path = path or STATUS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        # Keep build_status's legacy in-process shape; the file gains only extra fields.
        snapshot = dict(build_status(db_path))
        snapshot["ai_health"] = _ai_health(db_path, snapshot["generated_at"])
        # Actions totals are fetched remotely by always_on, not persisted locally.
        # Do not contact that service or substitute LLM usage or a guessed plan limit.
        snapshot["minutes"] = {"used_this_month": None, "limit": None}
        scout_counts = {"total": 0, "pending": 0, "approved": 0}
        try:
            import scout
            scout_counts = scout.get_scout_ideas_count(db_path)
        except Exception:
            pass
        snapshot["scout_ideas_count"] = scout_counts
        trend_counts = {"total": 0, "proposed": 0, "approved": 0}
        try:
            import trend
            trend_counts = trend.get_trend_items_count(db_path)
        except Exception:
            pass
        snapshot["trend_items_count"] = trend_counts
        media_counts = {"total": 0}
        try:
            import media_brain
            media_counts = media_brain.get_media_items_count(db_path)
        except Exception:
            pass
        snapshot["media_items_count"] = media_counts
        mkt_counts = {"total": 0, "draft": 0, "approved": 0}
        try:
            import marketing_agency
            mkt_counts = marketing_agency.get_marketing_kits_count(db_path)
        except Exception:
            pass
        snapshot["marketing_kits_count"] = mkt_counts
        json.dump(snapshot, f, indent=1)
    return path


if __name__ == "__main__":
    print("wrote", write_status())
