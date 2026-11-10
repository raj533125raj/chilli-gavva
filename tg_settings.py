"""tg_settings.py — Telegram plugin for managing owner settings.

Allows owner to view and update robot operational settings via Telegram:
- Quiet hours (no messages except failures, e.g. 'quiet 22-08', 'quiet off')
- Morning post time (e.g. 'morning 9')
- Niches on/off (e.g. 'niche budget off', 'niche adhd on')
- Robot run slots on/off (e.g. 'night run off', 'night slot on')
- Daily message limit (e.g. 'daily message limit 50', 'message limit 30')

Uses a 2-step confirmation flow with Yes/No keyboard (10-minute expiry).
Settings stored in state under 'owner_settings' as versioned JSON.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

SETTINGS_STATE_KEY = "owner_settings"
PENDING_STATE_KEY = "pending_settings_change"
SETTINGS_SCHEMA_VERSION = 1
CONFIRM_EXPIRY_SECONDS = 600  # 10 minutes

# Default known niches (maps alias/key to canonical recipe name prefix/name)
KNOWN_NICHES = {
    "budget": "niche_budget_sheets",
    "budget_sheets": "niche_budget_sheets",
    "budget sheets": "niche_budget_sheets",
    "freelancer": "niche_freelancer_trackers",
    "freelancer_trackers": "niche_freelancer_trackers",
    "freelancer trackers": "niche_freelancer_trackers",
    "adhd": "niche_adhd_planners",
    "adhd_planners": "niche_adhd_planners",
    "adhd planners": "niche_adhd_planners",
    "planners": "niche_planners_2027",
    "planners_2027": "niche_planners_2027",
    "2027 planners": "niche_planners_2027",
    "resume": "niche_resume_bundle",
    "resume_bundle": "niche_resume_bundle",
    "resume bundle": "niche_resume_bundle",
    "notion": "niche_notion_templates",
    "notion_templates": "niche_notion_templates",
    "notion templates": "niche_notion_templates",
}

# Canonical niche display titles
NICHE_DISPLAY_NAMES = {
    "niche_budget_sheets": "Budget Sheets",
    "niche_freelancer_trackers": "Freelancer Trackers",
    "niche_adhd_planners": "ADHD Planners",
    "niche_planners_2027": "2027 Planners",
    "niche_resume_bundle": "Resume Bundle",
    "niche_notion_templates": "Notion Templates",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": SETTINGS_SCHEMA_VERSION,
    "quiet_hours": {
        "enabled": False,
        "start": 22,
        "end": 8,
    },
    "morning_post_hour": 8,
    "niches": {
        "niche_budget_sheets": True,
        "niche_freelancer_trackers": True,
        "niche_adhd_planners": True,
        "niche_planners_2027": True,
        "niche_resume_bundle": True,
        "niche_notion_templates": False,  # paused by default
    },
    "run_slots": {
        "night": True,
    },
    "daily_message_limit": 50,
    "updated_at": "2026-01-01T00:00:00Z",
}

COMMANDS = [
    {
        "cmd": "/settings",
        "words": [
            "settings",
            "setting",
            "setup",
            "robot settings",
            "settings chupinchu",
            "chupinchu settings",
            "options",
            "configuration",
        ],
        "help_en": "View or configure owner robot settings (quiet hours, morning post time, niches, run slots, daily message limit)",
        "help_te": "Robot settings chupisthundi mariyu quiet hours, morning time, niches, run slots marchavachu",
    }
]


# ---------------- State Helper Functions ----------------


def get_default_settings() -> dict[str, Any]:
    """Return a deep copy of default settings."""
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def _read_state_from_db(db_path: str, key: str) -> Optional[str]:
    """Read a key from telegram_state table directly from sqlite db_path."""
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT value FROM telegram_state WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _write_state_to_db(db_path: str, key: str, value: str) -> bool:
    """Write a key to telegram_state table directly in sqlite db_path."""
    if not db_path:
        return False
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT INTO telegram_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def load_settings(ctx_or_db: Any = None) -> dict[str, Any]:
    """Load settings from ctx (via state_get) or db_path or default fallback."""
    raw = None
    if isinstance(ctx_or_db, dict) and callable(ctx_or_db.get("state_get")):
        raw = ctx_or_db["state_get"](SETTINGS_STATE_KEY, None)
    elif isinstance(ctx_or_db, str):
        raw = _read_state_from_db(ctx_or_db, SETTINGS_STATE_KEY)

    defaults = get_default_settings()
    if not raw:
        return defaults

    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            return defaults
    except Exception:
        return defaults

    merged = get_default_settings()
    merged["version"] = data.get("version", SETTINGS_SCHEMA_VERSION)

    qh = data.get("quiet_hours")
    if isinstance(qh, dict):
        merged["quiet_hours"]["enabled"] = bool(qh.get("enabled", False))
        if "start" in qh and isinstance(qh["start"], int) and 0 <= qh["start"] <= 23:
            merged["quiet_hours"]["start"] = qh["start"]
        if "end" in qh and isinstance(qh["end"], int) and 0 <= qh["end"] <= 23:
            merged["quiet_hours"]["end"] = qh["end"]

    mph = data.get("morning_post_hour")
    if isinstance(mph, int) and 0 <= mph <= 23:
        merged["morning_post_hour"] = mph

    niches = data.get("niches")
    if isinstance(niches, dict):
        for k, v in niches.items():
            merged["niches"][k] = bool(v)

    slots = data.get("run_slots")
    if isinstance(slots, dict):
        for k, v in slots.items():
            merged["run_slots"][k] = bool(v)

    dml = data.get("daily_message_limit")
    if isinstance(dml, int) and dml >= 0:
        merged["daily_message_limit"] = dml

    if "updated_at" in data and isinstance(data["updated_at"], str):
        merged["updated_at"] = data["updated_at"]

    return merged


def save_settings(
    settings: dict[str, Any],
    ctx_or_db: Any = None,
    now: Optional[datetime.datetime] = None,
) -> bool:
    """Save settings into state store under 'owner_settings'."""
    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    settings["updated_at"] = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    settings["version"] = SETTINGS_SCHEMA_VERSION
    payload = json.dumps(settings, indent=2, sort_keys=True)

    if isinstance(ctx_or_db, dict) and callable(ctx_or_db.get("state_set")):
        ctx_or_db["state_set"](SETTINGS_STATE_KEY, payload)
        return True
    elif isinstance(ctx_or_db, str):
        return _write_state_to_db(ctx_or_db, SETTINGS_STATE_KEY, payload)
    return False


def _get_pending_change(ctx_or_db: Any = None) -> Optional[dict[str, Any]]:
    """Retrieve pending settings change from state."""
    raw = None
    if isinstance(ctx_or_db, dict) and callable(ctx_or_db.get("state_get")):
        raw = ctx_or_db["state_get"](PENDING_STATE_KEY, None)
    elif isinstance(ctx_or_db, str):
        raw = _read_state_from_db(ctx_or_db, PENDING_STATE_KEY)

    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _set_pending_change(
    pending: Optional[dict[str, Any]], ctx_or_db: Any = None
) -> bool:
    """Store or clear pending settings change."""
    payload = json.dumps(pending) if pending else ""
    if isinstance(ctx_or_db, dict) and callable(ctx_or_db.get("state_set")):
        ctx_or_db["state_set"](PENDING_STATE_KEY, payload)
        return True
    elif isinstance(ctx_or_db, str):
        return _write_state_to_db(ctx_or_db, PENDING_STATE_KEY, payload)
    return False


# ---------------- Read-only Queries for other modules ----------------


def is_niche_enabled(niche_or_recipe: str, ctx_or_db: Any = None) -> bool:
    """Check if a specific niche / recipe is enabled in owner settings."""
    settings = load_settings(ctx_or_db)
    niches = settings.get("niches", {})

    canonical = KNOWN_NICHES.get(niche_or_recipe.lower(), niche_or_recipe)
    if canonical in niches:
        return bool(niches[canonical])

    for k, v in niches.items():
        if k == niche_or_recipe or k == canonical:
            return bool(v)
    return True


def is_slot_enabled(slot_name: str, ctx_or_db: Any = None) -> bool:
    """Check if a specific run slot from schedule.yaml is enabled."""
    settings = load_settings(ctx_or_db)
    run_slots = settings.get("run_slots", {})
    name_clean = slot_name.strip().lower()
    if name_clean in run_slots:
        return bool(run_slots[name_clean])
    return True


def is_quiet_hour(hour_or_dt: Any = None, ctx_or_db: Any = None) -> bool:
    """Check if the given hour or datetime falls within quiet hours."""
    settings = load_settings(ctx_or_db)
    qh = settings.get("quiet_hours", {})
    if not qh.get("enabled", False):
        return False

    start = qh.get("start", 22)
    end = qh.get("end", 8)

    hour: int
    if isinstance(hour_or_dt, int):
        hour = hour_or_dt
    elif isinstance(hour_or_dt, datetime.datetime):
        hour = hour_or_dt.hour
    elif hour_or_dt is None:
        hour = datetime.datetime.now(datetime.timezone.utc).hour
    else:
        try:
            hour = int(hour_or_dt)
        except Exception:
            hour = datetime.datetime.now(datetime.timezone.utc).hour

    if start == end:
        return False
    elif start < end:
        return start <= hour < end
    else:
        return hour >= start or hour < end


def get_morning_post_hour(ctx_or_db: Any = None) -> int:
    """Get morning post hour (default 8)."""
    settings = load_settings(ctx_or_db)
    return int(settings.get("morning_post_hour", 8))


def get_daily_message_limit(ctx_or_db: Any = None) -> int:
    """Get owner-configured daily message limit (default 50)."""
    settings = load_settings(ctx_or_db)
    return int(settings.get("daily_message_limit", 50))


# ---------------- Formatting View Output ----------------


def format_settings_text(settings: dict[str, Any]) -> str:
    """Format full owner settings card in Tenglish house style."""
    lines = ["⚙️ *Robot Settings (Owner Configuration)*", ""]

    # 1. Quiet hours
    qh = settings.get("quiet_hours", {})
    if qh.get("enabled"):
        s = f"{qh.get('start', 22):02d}:00"
        e = f"{qh.get('end', 8):02d}:00"
        lines.append(f"• *Quiet Hours:* ON ({s} - {e} UTC) — Failures thappa vere alerts vundavu")
    else:
        lines.append("• *Quiet Hours:* OFF (All operational alerts active)")

    # 2. Morning post time
    mph = settings.get("morning_post_hour", 8)
    lines.append(f"• *Morning Post Time:* {mph:02d}:00 UTC")

    # 3. Niches
    lines.append("\n📁 *Product Niches Status:*")
    niches = settings.get("niches", {})
    for niche_key, disp_name in NICHE_DISPLAY_NAMES.items():
        is_on = niches.get(niche_key, True)
        status_sym = "✅ ON" if is_on else "⏸ OFF"
        lines.append(f"   {status_sym} - {disp_name} (`{niche_key}`)")

    # 4. Run Slots
    lines.append("\n⏱ *Robot Run Slots:*")
    slots = settings.get("run_slots", {})
    for slot_name, is_on in slots.items():
        status_sym = "✅ ON" if is_on else "⏸ OFF"
        lines.append(f"   {status_sym} - Slot `{slot_name}`")

    # 5. Daily message limit
    dml = settings.get("daily_message_limit", 50)
    lines.append(f"\n💬 *Daily Message Limit:* {dml} messages/day")

    lines.append("\n✏️ *Settings marchadaniki commands pampandi:*")
    lines.append("• `quiet 22-08` / `quiet off` — Quiet hours marchu")
    lines.append("• `morning 9` — Morning post time marchu")
    lines.append("• `niche budget off` / `niche budget on` — Niche toggle")
    lines.append("• `night run off` / `night run on` — Slot toggle")
    lines.append("• `message limit 60` — Daily message limit marchu")
    lines.append("• `settings reset` — Default settings restore chey")

    return "\n".join(lines)


# ---------------- Parsing Setting Change Requests ----------------


def parse_setting_request(text: str) -> Optional[dict[str, Any]]:
    """Parse text into a candidate setting change or None if not a setting command."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    # Reset command
    if re.match(
        r"^(?:settings\s+reset|reset\s+settings|default\s+settings|restore\s+defaults)$",
        cleaned,
        re.IGNORECASE,
    ):
        return {
            "type": "reset",
            "target": None,
            "value": True,
            "description": "All settings ni default values ki reset cheyali",
        }

    # Quiet hours: e.g. 'quiet 22-08', 'quiet 22 to 8', 'quiet hours 22-8', 'quiet off', 'quiet on', 'silent 23-07'
    m_qh_off = re.match(
        r"^(?:quiet|quiet\s*hours|silent|silence)\s+(?:off|disable|disabled|no)$",
        cleaned,
        re.IGNORECASE,
    )
    if m_qh_off:
        return {
            "type": "quiet_hours",
            "target": None,
            "value": {"enabled": False, "start": 22, "end": 8},
            "description": "Quiet hours OFF cheyali (anni alerts normal ga vasthayi)",
        }

    m_qh_on = re.match(
        r"^(?:quiet|quiet\s*hours|silent|silence)\s+(?:on|enable|enabled|yes)$",
        cleaned,
        re.IGNORECASE,
    )
    if m_qh_on:
        return {
            "type": "quiet_hours",
            "target": None,
            "value": {"enabled": True, "start": 22, "end": 8},
            "description": "Quiet hours ON cheyali (22:00 - 08:00 UTC)",
        }

    m_qh = re.match(
        r"^(?:quiet|quiet\s*hours|silent|silence)\s+(\d{1,2})\s*(?:-|to|varaku|\s+)\s*(\d{1,2})$",
        cleaned,
        re.IGNORECASE,
    )
    if m_qh:
        s_val = int(m_qh.group(1))
        e_val = int(m_qh.group(2))
        if 0 <= s_val <= 23 and 0 <= e_val <= 23:
            return {
                "type": "quiet_hours",
                "target": None,
                "value": {"enabled": True, "start": s_val, "end": e_val},
                "description": f"Quiet hours ON cheyali ({s_val:02d}:00 nunchi {e_val:02d}:00 UTC varaku)",
            }

    # Morning post time: e.g. 'morning 9', 'morning post 8', 'morning post time 9', 'morning 09:00'
    m_morning = re.match(
        r"^(?:morning|morning\s+post|morning\s+time|morning\s+post\s+time|podunna)\s+(\d{1,2})(?::00)?$",
        cleaned,
        re.IGNORECASE,
    )
    if m_morning:
        h_val = int(m_morning.group(1))
        if 0 <= h_val <= 23:
            return {
                "type": "morning_post_hour",
                "target": None,
                "value": h_val,
                "description": f"Morning post time ni {h_val:02d}:00 UTC ki marchali",
            }

    # Niches on/off: e.g. 'niche budget off', 'niche adhd on', 'niche freelancer_trackers off'
    # Or without prefix 'niche': 'budget niche off', 'adhd niche on'
    m_niche = re.match(
        r"^(?:niche\s+)?([a-z0-9_ ]+?)(?:\s+niche)?\s+(on|off|enable|disable|enabled|disabled)$",
        cleaned,
        re.IGNORECASE,
    )
    if m_niche:
        niche_cand = m_niche.group(1).strip().lower().replace(" ", "_")
        action = m_niche.group(2).lower()
        is_enable = action in ("on", "enable", "enabled")

        # Resolve to known niche if possible
        target_niche = KNOWN_NICHES.get(niche_cand, niche_cand)
        if target_niche in NICHE_DISPLAY_NAMES or niche_cand in KNOWN_NICHES:
            target_key = KNOWN_NICHES.get(niche_cand, target_niche)
            disp = NICHE_DISPLAY_NAMES.get(target_key, target_key)
            status_text = "ON (enable)" if is_enable else "OFF (pause)"
            return {
                "type": "niche",
                "target": target_key,
                "value": is_enable,
                "description": f"Niche '{disp}' ({target_key}) ni {status_text} cheyali",
            }

    # Robot run slots on/off: e.g. 'night run off', 'night slot on', 'slot night off', 'run night off'
    m_slot1 = re.match(
        r"^([a-z0-9_]+)\s+(?:run|slot)\s+(on|off|enable|disable|enabled|disabled)$",
        cleaned,
        re.IGNORECASE,
    )
    m_slot2 = re.match(
        r"^(?:run|slot)\s+([a-z0-9_]+)\s+(on|off|enable|disable|enabled|disabled)$",
        cleaned,
        re.IGNORECASE,
    )
    m_slot = m_slot1 or m_slot2
    if m_slot:
        slot_name = m_slot.group(1).strip().lower()
        action = m_slot.group(2).lower()
        is_enable = action in ("on", "enable", "enabled")
        status_text = "ON (enable)" if is_enable else "OFF (pause)"
        return {
            "type": "run_slot",
            "target": slot_name,
            "value": is_enable,
            "description": f"Robot run slot '{slot_name}' ni {status_text} cheyali",
        }

    # Daily message limit: e.g. 'message limit 50', 'daily message limit 30', 'msg limit 100', 'daily limit 40'
    m_dml = re.match(
        r"^(?:daily\s+)?(?:message|msg)\s+limit\s+(\d+)$", cleaned, re.IGNORECASE
    )
    m_dml_alt = re.match(
        r"^limit\s+(?:daily\s+)?(?:message|msg|messages)\s+(\d+)$",
        cleaned,
        re.IGNORECASE,
    )
    m_dml_match = m_dml or m_dml_alt
    if m_dml_match:
        limit_val = int(m_dml_match.group(1))
        if 0 <= limit_val <= 1000:
            return {
                "type": "daily_message_limit",
                "target": None,
                "value": limit_val,
                "description": f"Daily message limit ni {limit_val} ki marchali",
            }

    return None


def apply_setting_change(
    settings: dict[str, Any], change: dict[str, Any]
) -> dict[str, Any]:
    """Apply a parsed setting change to settings dict and return updated copy."""
    s = json.loads(json.dumps(settings))
    ctype = change.get("type")
    cval = change.get("value")
    ctarget = change.get("target")

    if ctype == "reset":
        s = get_default_settings()
    elif ctype == "quiet_hours":
        if isinstance(cval, dict):
            s["quiet_hours"] = {
                "enabled": bool(cval.get("enabled", False)),
                "start": int(cval.get("start", 22)),
                "end": int(cval.get("end", 8)),
            }
    elif ctype == "morning_post_hour":
        if isinstance(cval, int) and 0 <= cval <= 23:
            s["morning_post_hour"] = cval
    elif ctype == "niche":
        if ctarget:
            if "niches" not in s or not isinstance(s["niches"], dict):
                s["niches"] = {}
            s["niches"][ctarget] = bool(cval)
    elif ctype == "run_slot":
        if ctarget:
            if "run_slots" not in s or not isinstance(s["run_slots"], dict):
                s["run_slots"] = {}
            s["run_slots"][ctarget] = bool(cval)
    elif ctype == "daily_message_limit":
        if isinstance(cval, int) and cval >= 0:
            s["daily_message_limit"] = cval

    return s


def is_confirmation_reply(text: str) -> Optional[bool]:
    """Check if text is a confirmation response (Yes / No).

    Returns True for Yes, False for No, None for neither.
    """
    cleaned = (text or "").strip().lower()
    if re.match(r"^(?:yes|y|avunu|sare|confirm|ok|okay|ha)$", cleaned):
        return True
    if re.match(r"^(?:no|n|vaddu|kaadhu|cancel|stop|oddu)$", cleaned):
        return False
    return None


def _is_settings_view_command(text: str) -> bool:
    """Check if the text is asking to view settings."""
    cleaned = (text or "").strip().lower()
    if cleaned in ("/settings", "settings", "setting", "setup", "robot settings", "options"):
        return True
    for item in COMMANDS:
        if cleaned == item["cmd"]:
            return True
        for w in item.get("words", []):
            if cleaned == w.lower():
                return True
    return False


# ---------------- Plugin Entry Point ----------------


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Plugin handle implementation following the Telegram Plugin Interface.

    ctx keys:
      - db_path: str
      - chat_id: int | str
      - now: UTC datetime
      - send_text: callable(text, keyboard=None)
      - send_document, send_photo, send_media_group
      - state_get: callable(key, default=None)
      - state_set: callable(key, value)

    Returns:
      - None if text is not handled by this plugin
      - {"text": "..."} to reply via return dict
      - {"handled": True} if message already sent
    """
    raw_text = (text or "").strip()
    if not raw_text:
        return None

    now_dt = ctx.get("now") or datetime.datetime.now(datetime.timezone.utc)
    now_ts = now_dt.timestamp() if hasattr(now_dt, "timestamp") else time.time()

    # 1. Check if there is an active pending confirmation
    pending = _get_pending_change(ctx)
    if pending:
        created_ts = pending.get("ts", 0)
        desc = pending.get("description", "Setting change")
        conf_decision = is_confirmation_reply(raw_text)

        # Check for expiry (10 minutes)
        if (now_ts - created_ts) > CONFIRM_EXPIRY_SECONDS:
            _set_pending_change(None, ctx)
            if conf_decision is not None:
                reply = (
                    "⚠️ Settings change request expire ayipoyindi (10 minutes limit).\n"
                    "Malli request pampandi."
                )
                if callable(ctx.get("send_text")):
                    ctx["send_text"](reply)
                    return {"handled": True}
                return {"text": reply}
            # If not a yes/no, fall through and let normal parser handle it
        else:
            if conf_decision is True:
                # Apply change
                current_settings = load_settings(ctx)
                updated_settings = apply_setting_change(current_settings, pending)
                save_settings(updated_settings, ctx, now=now_dt)
                _set_pending_change(None, ctx)

                reply = (
                    f"✅ Setting update chesam: {desc}.\n\n"
                    f"{format_settings_text(updated_settings)}"
                )
                if callable(ctx.get("send_text")):
                    ctx["send_text"](reply)
                    return {"handled": True}
                return {"text": reply}
            elif conf_decision is False:
                # Cancel change
                _set_pending_change(None, ctx)
                reply = f"❌ Cancel chesam: {desc} change apply avvaledu."
                if callable(ctx.get("send_text")):
                    ctx["send_text"](reply)
                    return {"handled": True}
                return {"text": reply}
            # If text is another setting command or /settings, override pending

    # 2. Check if request is to view settings
    if _is_settings_view_command(raw_text):
        settings = load_settings(ctx)
        reply = format_settings_text(settings)
        if callable(ctx.get("send_text")):
            ctx["send_text"](reply)
            return {"handled": True}
        return {"text": reply}

    # 3. Check if request is a setting modification command
    change = parse_setting_request(raw_text)
    if change:
        # Create pending change with 10-minute expiry
        change["ts"] = now_ts
        _set_pending_change(change, ctx)

        desc = change.get("description", "Setting change")
        prompt = (
            f"❓ *Setting Change Confirmation*\n\n"
            f"{desc}?\n\n"
            f"Confirm cheyadaniki *Yes* leda vaddu anukunte *No* pampandi (10 minutes lo reply ivvandi)."
        )
        kb_param = [["Yes", "No"]]
        if callable(ctx.get("send_text")):
            try:
                ctx["send_text"](prompt, keyboard=kb_param)
            except TypeError:
                ctx["send_text"](prompt)
            return {"handled": True}
        return {"text": prompt}

    # Text not recognized as setting command
    return None
