"""office_protocol.py — Versioned schema and validation for SAFE office snapshot.

Used by office projection (read by Binny Mills / Binny-JG robot office interface).
Enforces:
  - Strict schema (version, office_id, revision, generated_at)
  - Team / Cube hierarchy: teams -> captain -> sub rooms -> agents
  - Agent statuses from honest list:
      'working', 'thinking', 'just finished', 'needs you', 'waiting',
      'off duty with next shift', 'idle', 'warning', 'error', 'paused',
      'not built yet'
  - Current action (short, scrubbed), progress %, model, last_seen
  - Office counters: agents, running, needs-you, drafts waiting,
    products READY/NEEDS FIX, minutes used/limit, AI providers up/total
  - Approvals queue: id, short title, score, waited hours, auto-decision due time (24h rule)
  - Strict validation: capped sizes, no secrets, no emails, no money amounts (unless owner_only flag)
"""

import datetime
import math
import os
import re
from typing import Any, Dict, List, Optional

import public_export
import secret_guard

PROTOCOL_VERSION = "1.0.0"
DEFAULT_OFFICE_ID = "binny-mills-office"

HONEST_STATUSES = (
    "working",
    "thinking",
    "just finished",
    "needs you",
    "waiting",
    "off duty with next shift",
    "idle",
    "warning",
    "error",
    "paused",
    "not built yet",
)

AGENT_ACTIVITIES = (
    "working",
    "idle",
    "waiting",
    "needs_you",
    "error",
    "off_duty",
)

AGENT_LEVELS = ("captain", "main", "sub")

# Maximum string lengths and collection sizes for validation
MAX_TITLE_LEN = 120
MAX_ACTION_LEN = 100
MAX_NAME_LEN = 80
MAX_ID_LEN = 80
MAX_KIND_LEN = 60
MAX_TEAMS_COUNT = 50
MAX_AGENTS_COUNT = 100
MAX_APPROVALS_COUNT = 100
MAX_SUB_ROOMS_COUNT = 50
MAX_MEETINGS_COUNT = 50
MAX_LETTERS_COUNT = 20


class ProtocolValidationError(ValueError):
    """Raised when data fails office protocol validation."""
    pass


def scrub_safe(text: Any, allow_money: bool = False) -> str:
    """Scrub secrets, tokens, emails, and optionally money/currency strings."""
    if not isinstance(text, str):
        return ""
    # Standard scrubbing from public_export / secret_guard
    scrubbed = public_export.scrub_text(text)
    if not allow_money:
        scrubbed = public_export.CURRENCY_PATTERN.sub("[hidden]", scrubbed)
    return scrubbed.strip()


def validate_agent(agent: Dict[str, Any], allow_money: bool = False) -> Dict[str, Any]:
    """Validate and normalize an agent record."""
    if not isinstance(agent, dict):
        raise ProtocolValidationError("Agent record must be a dictionary")

    # Required fields
    for field in ("id", "name", "level", "status"):
        if field not in agent:
            raise ProtocolValidationError(f"Agent missing required field: {field}")

    agent_id = str(agent["id"])[:MAX_ID_LEN]
    name = scrub_safe(str(agent["name"])[:MAX_NAME_LEN], allow_money=allow_money)

    level = str(agent["level"]).lower()
    if level not in AGENT_LEVELS:
        raise ProtocolValidationError(f"Invalid agent level: {level}. Expected one of {AGENT_LEVELS}")

    status = str(agent["status"]).lower()
    if status not in HONEST_STATUSES:
        raise ProtocolValidationError(f"Invalid agent status: {status}. Expected one of {HONEST_STATUSES}")

    activity = agent.get("activity")
    if activity is not None:
        activity = str(activity).lower()
        if activity not in AGENT_ACTIVITIES:
            raise ProtocolValidationError(f"Invalid agent activity: {activity}. Expected one of {AGENT_ACTIVITIES}")

    action = scrub_safe(str(agent.get("current_action", "") or "")[:MAX_ACTION_LEN], allow_money=allow_money)

    progress = agent.get("progress")
    if progress is not None:
        if isinstance(progress, (int, float)) and math.isfinite(progress):
            progress = max(0, min(100, int(round(progress))))
        else:
            progress = None

    model = agent.get("model")
    if model is not None:
        model = scrub_safe(str(model)[:MAX_NAME_LEN], allow_money=allow_money)

    last_seen = agent.get("last_seen")
    if last_seen is not None:
        last_seen = public_export._iso(last_seen)

    record = {
        "id": agent_id,
        "name": name,
        "level": level,
        "status": status,
        "current_action": action,
        "progress": progress,
        "model": model,
        "last_seen": last_seen,
    }
    if activity is not None:
        record["activity"] = activity
    return record


def validate_sub_room(sub_room: Dict[str, Any], allow_money: bool = False) -> Dict[str, Any]:
    """Validate a sub-room entry."""
    if not isinstance(sub_room, dict):
        raise ProtocolValidationError("Sub room must be a dictionary")
    name = scrub_safe(str(sub_room.get("name", ""))[:MAX_NAME_LEN], allow_money=allow_money)
    agents_raw = sub_room.get("agents", [])
    if not isinstance(agents_raw, list):
        raise ProtocolValidationError("Sub room agents must be a list")
    agents = [validate_agent(a, allow_money=allow_money) for a in agents_raw[:MAX_AGENTS_COUNT]]
    return {
        "name": name,
        "agents": agents,
    }


def validate_team(team: Dict[str, Any], allow_money: bool = False) -> Dict[str, Any]:
    """Validate a team (cube) entry."""
    if not isinstance(team, dict):
        raise ProtocolValidationError("Team must be a dictionary")

    for field in ("id", "name", "role", "status"):
        if field not in team:
            raise ProtocolValidationError(f"Team missing required field: {field}")

    team_id = str(team["id"])[:MAX_ID_LEN]
    name = scrub_safe(str(team["name"])[:MAX_NAME_LEN], allow_money=allow_money)
    role = scrub_safe(str(team["role"])[:MAX_TITLE_LEN], allow_money=allow_money)
    status = str(team["status"]).lower()

    captain_raw = team.get("captain")
    captain = validate_agent(captain_raw, allow_money=allow_money) if captain_raw else None

    sub_rooms_raw = team.get("sub_rooms", [])
    if not isinstance(sub_rooms_raw, list):
        raise ProtocolValidationError("sub_rooms must be a list")
    sub_rooms = [validate_sub_room(r, allow_money=allow_money) for r in sub_rooms_raw[:MAX_SUB_ROOMS_COUNT]]

    agents_raw = team.get("agents", [])
    if not isinstance(agents_raw, list):
        raise ProtocolValidationError("Team agents must be a list")
    agents = [validate_agent(a, allow_money=allow_money) for a in agents_raw[:MAX_AGENTS_COUNT]]

    return {
        "id": team_id,
        "name": name,
        "role": role,
        "status": status,
        "captain": captain,
        "sub_rooms": sub_rooms,
        "agents": agents,
    }


def validate_counters(counters: Dict[str, Any]) -> Dict[str, Any]:
    """Validate office counters."""
    if not isinstance(counters, dict):
        raise ProtocolValidationError("counters must be a dictionary")

    def _non_neg(val):
        if type(val) in (int, float) and math.isfinite(val) and val >= 0:
            return int(val)
        return 0

    def _nullable_int(val):
        if type(val) in (int, float) and math.isfinite(val) and val >= 0:
            return int(val)
        return None

    products_ready = _non_neg(counters.get("products_ready", 0))
    products_needs_fix = _non_neg(counters.get("products_needs_fix", 0))

    ai_up = _non_neg(counters.get("ai_providers_up", 0))
    ai_total = _non_neg(counters.get("ai_providers_total", 0))

    return {
        "agents": _non_neg(counters.get("agents", 0)),
        "running": _non_neg(counters.get("running", 0)),
        "needs_you": _non_neg(counters.get("needs_you", 0)),
        "drafts_waiting": _non_neg(counters.get("drafts_waiting", 0)),
        "products_ready": products_ready,
        "products_needs_fix": products_needs_fix,
        "minutes_used": _nullable_int(counters.get("minutes_used")),
        "minutes_limit": _nullable_int(counters.get("minutes_limit")),
        "ai_providers_up": ai_up,
        "ai_providers_total": ai_total,
    }


def validate_meeting(meeting: Dict[str, Any], allow_money: bool = False) -> Dict[str, Any]:
    """Validate a meeting item (draft_number and list of agent names)."""
    if not isinstance(meeting, dict):
        raise ProtocolValidationError("Meeting must be a dictionary")

    for field in ("draft_number", "agents"):
        if field not in meeting:
            raise ProtocolValidationError(f"Meeting missing field: {field}")

    draft_num = meeting["draft_number"]
    if type(draft_num) is not int:
        try:
            draft_num = int(draft_num)
        except (ValueError, TypeError):
            raise ProtocolValidationError("Meeting draft_number must be an integer")

    agents_raw = meeting["agents"]
    if not isinstance(agents_raw, list):
        raise ProtocolValidationError("Meeting agents must be a list")

    cleaned_agents = []
    for a in agents_raw[:MAX_AGENTS_COUNT]:
        scrubbed = scrub_safe(str(a)[:MAX_NAME_LEN], allow_money=allow_money)
        if scrubbed:
            cleaned_agents.append(scrubbed)

    return {
        "draft_number": draft_num,
        "agents": cleaned_agents,
    }


def validate_letter(letter: Dict[str, Any], allow_money: bool = False) -> Dict[str, Any]:
    """Validate a hand-off letter (from_team, to_team, kind, at). No text, titles, or money."""
    if not isinstance(letter, dict):
        raise ProtocolValidationError("Letter must be a dictionary")

    for field in ("from_team", "to_team", "kind", "at"):
        if field not in letter:
            raise ProtocolValidationError(f"Letter missing field: {field}")

    from_team = scrub_safe(str(letter["from_team"])[:MAX_NAME_LEN], allow_money=allow_money)
    to_team = scrub_safe(str(letter["to_team"])[:MAX_NAME_LEN], allow_money=allow_money)
    kind = scrub_safe(str(letter["kind"])[:MAX_KIND_LEN], allow_money=allow_money)
    at_val = public_export._iso(letter.get("at")) or ""

    return {
        "from_team": from_team,
        "to_team": to_team,
        "kind": kind,
        "at": at_val,
    }


def validate_approval(approval: Dict[str, Any], allow_money: bool = False) -> Dict[str, Any]:
    """Validate an item in the approvals queue."""
    if not isinstance(approval, dict):
        raise ProtocolValidationError("Approval item must be a dictionary")

    for field in ("id", "title"):
        if field not in approval:
            raise ProtocolValidationError(f"Approval item missing field: {field}")

    app_id = approval["id"]
    if type(app_id) is not int:
        try:
            app_id = int(app_id)
        except (ValueError, TypeError):
            raise ProtocolValidationError("Approval id must be an integer")

    title = scrub_safe(str(approval["title"])[:MAX_TITLE_LEN], allow_money=allow_money)

    score = approval.get("score")
    if score is not None:
        if type(score) in (int, float) and math.isfinite(score):
            score = max(0, min(100, int(score)))
        else:
            score = None

    waited_hours = approval.get("waited_hours")
    if waited_hours is not None:
        if type(waited_hours) in (int, float) and math.isfinite(waited_hours):
            waited_hours = round(max(0.0, float(waited_hours)), 1)
        else:
            waited_hours = 0.0
    else:
        waited_hours = 0.0

    auto_decision_due = approval.get("auto_decision_due")
    if auto_decision_due is not None:
        auto_decision_due = public_export._iso(auto_decision_due)

    return {
        "id": app_id,
        "title": title,
        "score": score,
        "waited_hours": waited_hours,
        "auto_decision_due": auto_decision_due,
    }


def validate_snapshot(snapshot: Dict[str, Any], allow_money: bool = False) -> Dict[str, Any]:
    """Validate and sanitize a full office projection snapshot."""
    if not isinstance(snapshot, dict):
        raise ProtocolValidationError("Snapshot must be a dictionary")

    # Header check
    version = str(snapshot.get("version", PROTOCOL_VERSION))
    if version != PROTOCOL_VERSION:
        raise ProtocolValidationError(f"Unsupported schema version: {version}")

    office_id = scrub_safe(str(snapshot.get("office_id", DEFAULT_OFFICE_ID))[:MAX_ID_LEN], allow_money=allow_money)
    revision = int(snapshot.get("revision", 1))

    gen_at = snapshot.get("generated_at")
    generated_at = public_export._iso(gen_at) if gen_at else datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    # Teams / Cubes
    teams_raw = snapshot.get("teams", [])
    if not isinstance(teams_raw, list):
        raise ProtocolValidationError("Snapshot teams must be a list")
    teams = [validate_team(t, allow_money=allow_money) for t in teams_raw[:MAX_TEAMS_COUNT]]

    # Counters
    counters = validate_counters(snapshot.get("counters", {}))

    # Approvals
    approvals_raw = snapshot.get("approvals", [])
    if not isinstance(approvals_raw, list):
        raise ProtocolValidationError("Snapshot approvals must be a list")
    approvals = [validate_approval(a, allow_money=allow_money) for a in approvals_raw[:MAX_APPROVALS_COUNT]]

    # Meetings (optional)
    meetings_raw = snapshot.get("meetings")
    meetings = None
    if meetings_raw is not None:
        if not isinstance(meetings_raw, list):
            raise ProtocolValidationError("Snapshot meetings must be a list")
        meetings = [validate_meeting(m, allow_money=allow_money) for m in meetings_raw[:MAX_MEETINGS_COUNT]]

    # Letters (optional)
    letters_raw = snapshot.get("letters")
    letters = None
    if letters_raw is not None:
        if not isinstance(letters_raw, list):
            raise ProtocolValidationError("Snapshot letters must be a list")
        letters = [validate_letter(l, allow_money=allow_money) for l in letters_raw[:MAX_LETTERS_COUNT]]

    result = {
        "version": version,
        "office_id": office_id,
        "revision": revision,
        "generated_at": generated_at,
        "teams": teams,
        "counters": counters,
        "approvals": approvals,
    }
    if meetings is not None:
        result["meetings"] = meetings
    if letters is not None:
        result["letters"] = letters

    return result
