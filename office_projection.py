"""office_projection.py — Safe read-only office projection for Binny Mills.

Builds an honest office snapshot reading from existing robot data structures:
cubes.py, public_export.py, guardrails.py, workforce.py, office_agents.py.

Handles:
- Missing or empty database gracefully (returns honest empty projection)
- Teams -> captain -> sub rooms -> agents hierarchy
- Agent status mapping to the honest list:
  ('working', 'thinking', 'just finished', 'needs you', 'waiting',
   'off duty with next shift', 'idle', 'warning', 'error', 'paused', 'not built yet')
- Office counters (agents, running, needs-you, drafts waiting, products READY/NEEDS FIX,
  minutes used/limit, AI providers up/total)
- Approvals queue with 24h auto-decision due time calculation
- Deterministic ordering of teams, sub rooms, agents, and approvals
- Strict sanitization of secrets, tokens, emails, and money amounts
"""

import datetime
import json
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

import cubes
import guardrails
import office_agents
import office_protocol
import public_export
import workforce

AUTO_DECISION_WINDOW_HOURS = 24


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _parse_iso_ts(ts_str: Optional[str]) -> Optional[datetime.datetime]:
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except (ValueError, TypeError):
        return None


def get_agent_engine_and_model(agent_rec: Dict[str, Any]) -> Optional[str]:
    """Retrieve human-friendly model string if available."""
    model = agent_rec.get("model")
    if model and isinstance(model, str):
        return public_export.scrub_text(model)
    return None


def derive_agent_status(
    agent_name: str,
    cube_status: str,
    is_planned: bool,
    agent_enabled: bool,
    running_task: Optional[sqlite3.Row],
    last_task: Optional[sqlite3.Row],
    daemon_running: bool,
    doctor_case: Optional[sqlite3.Row],
) -> tuple[str, str, Optional[int]]:
    """Determine the honest status, short action, and progress % for an agent.

    Honest status list:
      working, thinking, just finished, needs you, waiting,
      off duty with next shift, idle, warning, error, paused, not built yet
    """
    if is_planned:
        return "not built yet", "Not built yet", None

    if cube_status != "on" or not agent_enabled:
        return "paused", "Paused", None

    # Doctor cases needing human attention or stuck
    if doctor_case:
        category = doctor_case["category"]
        if category in ("needs_owner", "stuck", "stuck_task"):
            return "needs you", "Needs owner assistance", None
        return "warning", "Recovery in progress", None

    # Check active task
    if running_task:
        kind = running_task["kind"]
        # Distinguish thinking vs working based on task stage if appropriate, or active working
        if kind in ("plan", "research"):
            status = "thinking"
        else:
            status = "working"
        action = public_export._generic_action_label(agent_name, kind, True)
        progress = 50  # task in progress
        return status, action, progress

    # If last task just finished recently (within last 5 minutes)
    if last_task:
        l_status = last_task["status"]
        finished_ts = last_task["finished_ts"] or last_task["ts"]
        finished_dt = _parse_iso_ts(finished_ts)
        now = datetime.datetime.now(datetime.timezone.utc)

        if l_status == "failed":
            return "error", "Last task failed", None

        if finished_dt and (now - finished_dt).total_seconds() < 300:
            return "just finished", "Task recently completed", 100

        if l_status == "queued":
            return "waiting", "Task queued", 0

    if not daemon_running:
        return "off duty with next shift", "Off duty until next cycle", None

    return "idle", "Idle", None


def get_agent_projection(
    agent_name: str,
    level: str,
    cube_status: str,
    is_planned: bool,
    wf_agents_map: Dict[str, Dict[str, Any]],
    running_tasks_map: Dict[str, sqlite3.Row],
    last_tasks_map: Dict[str, sqlite3.Row],
    doctor_cases_map: Dict[str, sqlite3.Row],
    daemon_running: bool,
) -> Dict[str, Any]:
    """Build single agent dictionary according to protocol."""
    agent_info = wf_agents_map.get(agent_name, {})
    agent_id = agent_info.get("id") or office_agents.agent_id(agent_name)
    agent_enabled = agent_info.get("enabled", True) if agent_name in wf_agents_map else True

    r_task = running_tasks_map.get(agent_name)
    l_task = last_tasks_map.get(agent_name)
    doc_case = doctor_cases_map.get(agent_name)

    status, action, progress = derive_agent_status(
        agent_name=agent_name,
        cube_status=cube_status,
        is_planned=is_planned,
        agent_enabled=agent_enabled,
        running_task=r_task,
        last_task=l_task,
        daemon_running=daemon_running,
        doctor_case=doc_case,
    )

    last_seen = None
    if r_task:
        last_seen = r_task["started_ts"] or r_task["ts"]
    elif l_task:
        last_seen = l_task["finished_ts"] or l_task["started_ts"] or l_task["ts"]

    model = get_agent_engine_and_model(agent_info)

    return office_protocol.validate_agent({
        "id": agent_id,
        "name": agent_name,
        "level": level,
        "status": status,
        "current_action": action,
        "progress": progress,
        "model": model,
        "last_seen": last_seen,
    })


def get_approvals_queue(conn: Optional[sqlite3.Connection], now_utc: datetime.datetime) -> List[Dict[str, Any]]:
    """Build approvals queue with 24h auto-decision due time calculation."""
    if conn is None or not _has_table(conn, "approvals"):
        return []

    try:
        rows = conn.execute(
            "SELECT id, ts, kind, title, review FROM approvals WHERE status = 'pending' ORDER BY id ASC LIMIT 50"
        ).fetchall()
    except sqlite3.Error:
        return []

    items = []
    for app_id, ts_str, kind, title, review in rows:
        created_dt = _parse_iso_ts(ts_str) or now_utc
        waited_seconds = max(0.0, (now_utc - created_dt).total_seconds())
        waited_hours = round(waited_seconds / 3600.0, 1)

        due_dt = created_dt + datetime.timedelta(hours=AUTO_DECISION_WINDOW_HOURS)
        due_iso = due_dt.isoformat().replace("+00:00", "Z")

        score = guardrails._parse_score(review or "")
        clean_title = public_export._clean_product_title(title or f"Pending approval {app_id}")

        items.append(office_protocol.validate_approval({
            "id": app_id,
            "title": clean_title,
            "score": score,
            "waited_hours": waited_hours,
            "auto_decision_due": due_iso,
        }))

    # Sort deterministically by id
    items.sort(key=lambda x: x["id"])
    return items


def project_office_snapshot(
    db_path: Optional[str] = None,
    office_id: str = office_protocol.DEFAULT_OFFICE_ID,
    now: Optional[datetime.datetime] = None,
    allow_money: bool = False,
) -> Dict[str, Any]:
    """Build the safe office snapshot read-only.

    Returns an honest empty snapshot if the database is missing or empty.
    """
    now_utc = now or datetime.datetime.now(datetime.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)

    db_file = db_path or guardrails.DB_PATH
    db_exists = bool(db_file and os.path.exists(db_file))

    conn = None
    if db_exists:
        try:
            conn = sqlite3.connect(f"file:{os.path.abspath(db_file)}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except Exception:
            conn = None

    # Load workforce registered agents map
    try:
        wf_registered = office_agents.registered(db_path=db_file)
    except Exception:
        wf_registered = []
    wf_map = {a["name"]: a for a in wf_registered}

    # Load cube registry
    try:
        registry = cubes.load_registry()
    except Exception:
        registry = []

    # Check daemon / workforce running status
    daemon_running = False
    try:
        w_status = workforce.worker_status(db_file)
        daemon_running = bool(w_status.get("running", False))
    except Exception:
        daemon_running = False

    # Pre-fetch tasks and doctor cases if DB is active
    running_tasks_map = {}
    last_tasks_map = {}
    doctor_cases_map = {}

    if conn is not None and _has_table(conn, "tasks"):
        try:
            r_rows = conn.execute(
                "SELECT id, agent, kind, started_ts, ts FROM tasks WHERE status = 'running' ORDER BY id DESC"
            ).fetchall()
            for r in r_rows:
                if r["agent"] and r["agent"] not in running_tasks_map:
                    running_tasks_map[r["agent"]] = r

            l_rows = conn.execute(
                "SELECT id, agent, kind, status, started_ts, finished_ts, ts FROM tasks "
                "WHERE status IN ('done', 'failed', 'queued') ORDER BY id DESC"
            ).fetchall()
            for r in l_rows:
                if r["agent"] and r["agent"] not in last_tasks_map:
                    last_tasks_map[r["agent"]] = r
        except sqlite3.Error:
            pass

    if conn is not None and _has_table(conn, "doctor_cases"):
        try:
            d_rows = conn.execute(
                "SELECT id, agent, category, status, ts FROM doctor_cases ORDER BY id DESC"
            ).fetchall()
            for r in d_rows:
                if r["agent"] and r["agent"] not in doctor_cases_map:
                    doctor_cases_map[r["agent"]] = r
        except sqlite3.Error:
            pass

    # Build Teams
    teams = []
    all_agent_statuses = []

    # Sort registry deterministically by cube id
    sorted_registry = sorted(registry, key=lambda c: c.get("id", ""))

    for c in sorted_registry:
        cid = c["id"]
        c_status = cubes.get_cube_state(cid, db_path=db_file)
        is_planned = bool(c.get("planned", False))

        # Captain
        captain_name = c.get("captain", "")
        captain_obj = None
        if captain_name:
            captain_obj = get_agent_projection(
                agent_name=captain_name,
                level="captain",
                cube_status=c_status,
                is_planned=is_planned or (captain_name not in wf_map),
                wf_agents_map=wf_map,
                running_tasks_map=running_tasks_map,
                last_tasks_map=last_tasks_map,
                doctor_cases_map=doctor_cases_map,
                daemon_running=daemon_running,
            )
            all_agent_statuses.append(captain_obj["status"])

        # Sub rooms
        sub_rooms_list = []
        for r_name in sorted(c.get("sub_rooms", [])):
            sub_rooms_list.append({
                "name": r_name,
                "agents": [],  # Sub-room room mapping
            })

        # Main agents & Sub agents
        team_agents = []
        # Main agents
        for m_name in sorted(c.get("agents", [])):
            m_obj = get_agent_projection(
                agent_name=m_name,
                level="main",
                cube_status=c_status,
                is_planned=is_planned or (m_name not in wf_map),
                wf_agents_map=wf_map,
                running_tasks_map=running_tasks_map,
                last_tasks_map=last_tasks_map,
                doctor_cases_map=doctor_cases_map,
                daemon_running=daemon_running,
            )
            team_agents.append(m_obj)
            all_agent_statuses.append(m_obj["status"])

        # Sub agents
        for s_name in sorted(c.get("sub_agents", [])):
            s_obj = get_agent_projection(
                agent_name=s_name,
                level="sub",
                cube_status=c_status,
                is_planned=is_planned or (s_name not in wf_map),
                wf_agents_map=wf_map,
                running_tasks_map=running_tasks_map,
                last_tasks_map=last_tasks_map,
                doctor_cases_map=doctor_cases_map,
                daemon_running=daemon_running,
            )
            team_agents.append(s_obj)
            all_agent_statuses.append(s_obj["status"])

        # Sort team agents deterministically by id
        team_agents.sort(key=lambda a: a["id"])

        teams.append(office_protocol.validate_team({
            "id": cid,
            "name": c.get("name", cid),
            "role": c.get("role", ""),
            "status": c_status,
            "captain": captain_obj,
            "sub_rooms": sub_rooms_list,
            "agents": team_agents,
        }))

    # Approvals Queue
    approvals = get_approvals_queue(conn, now_utc)

    # Products count: READY vs NEEDS FIX
    products = public_export.get_public_products(db_path=db_file)
    ready_count = sum(1 for p in products if p.get("truth_check", {}).get("verdict") == "READY")
    needs_fix_count = sum(1 for p in products if p.get("truth_check", {}).get("verdict") == "NEEDS FIX")

    # Minutes used & limit
    minutes_info = public_export.get_public_minutes(db_path=db_file)

    # AI Providers UP / TOTAL
    ai_health = public_export.get_public_ai_health(db_path=db_file, checked_at=now_utc.isoformat().replace("+00:00", "Z"))
    ai_up_count = sum(1 for prov in ai_health if prov.get("state") == "up")
    ai_total_count = len(ai_health)

    # Running and Needs-you counts
    running_count = sum(1 for s in all_agent_statuses if s in ("working", "thinking"))
    needs_you_count = sum(1 for s in all_agent_statuses if s == "needs you")
    if approvals:
        needs_you_count += len(approvals)

    counters = {
        "agents": len(all_agent_statuses),
        "running": running_count,
        "needs_you": needs_you_count,
        "drafts_waiting": len(approvals),
        "products_ready": ready_count,
        "products_needs_fix": needs_fix_count,
        "minutes_used": minutes_info.get("used_this_month"),
        "minutes_limit": minutes_info.get("limit"),
        "ai_providers_up": ai_up_count,
        "ai_providers_total": ai_total_count,
    }

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    raw_snapshot = {
        "version": office_protocol.PROTOCOL_VERSION,
        "office_id": office_id,
        "revision": 1,
        "generated_at": now_utc.isoformat().replace("+00:00", "Z"),
        "teams": teams,
        "counters": counters,
        "approvals": approvals,
    }

    return office_protocol.validate_snapshot(raw_snapshot, allow_money=allow_money)
