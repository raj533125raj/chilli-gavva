"""Registry and state management for Chilli Gavva department 'cubes'.

A 'cube' represents a whole department pack:
captain + sub rooms + main agents + sub agents + left/right hand helpers + task kinds.

Cubes are defined in `cubes.json` in the repository root and their operational state
(On / Off / Archived) is persisted in SQLite.
"""
import datetime
import json
import os
import sqlite3

import logging
import re
import secret_guard

logger = logging.getLogger("cubes")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CUBES_JSON_PATH = os.path.join(BASE_DIR, "cubes.json")
CUBE_PACKS_DIR = os.path.join(BASE_DIR, "cube_packs")

# Maximum size limits for pack files to guard against accidental bloat / attacks
MAX_PACK_FILE_SIZE = 100 * 1024  # 100 KB max for cube.json or recipe files


def _get_db_path(db_path=None):
    if db_path:
        return db_path
    try:
        import guardrails
        return guardrails.DB_PATH
    except Exception:
        return os.path.join(BASE_DIR, "agent_data.db")


def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS cube_state (
        id TEXT PRIMARY KEY,
        status TEXT DEFAULT 'on',
        updated_ts TEXT
    )""")


def connect(db_path=None):
    path = _get_db_path(db_path)
    conn = sqlite3.connect(path)
    ensure_tables(conn)
    conn.commit()
    return conn


def _check_no_secrets(obj, path=""):
    """Check recursively that no secret-looking keys or tokens exist in pack data."""
    if isinstance(obj, str):
        if secret_guard.scrub_text(obj) != obj:
            raise ValueError(f"Secret or token pattern detected in pack data at '{path}'")
        # Check credential_like patterns
        if bool(re.search(
            r"api[\s_-]?key|access[\s_-]?token|secret|password|credential|private[\s_-]?key|bearer\s"
            r"|\b(?:sk-(?:proj-|ant-)?|gh[pousr]_|github_pat_|gsk_|xox[baprs]-|freellmapi-)[\w-]{8,}"
            r"|\bAKIA[0-9A-Z]{16}\b|\beyJ[\w-]{10,}\."
            r"|-----BEGIN|\bssh-(?:rsa|ed25519)\s|https?://[^\s/@:]+:[^\s/@]+@"
            r"|\b\d{6,}:[\w-]{20,}|\b[0-9a-f]{32,}\b"
            r"|\b(?=[A-Za-z0-9_+/=-]{40,}\b)(?=[A-Za-z0-9_+/=-]*[A-Za-z])"
            r"(?=[A-Za-z0-9_+/=-]*[0-9])[A-Za-z0-9_+/=-]{40,}", obj, re.I)):
            raise ValueError(f"Credential-like text detected in pack data at '{path}'")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _check_no_secrets(k, f"{path}.key({k})")
            _check_no_secrets(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            _check_no_secrets(item, f"{path}[{idx}]")


def validate_cube_pack(pack_data, pack_dir_name=None):
    """Strict validation for an individual cube pack.

    Must contain:
      id, name, colour (or color), status (planned/on/off), captain, sub_rooms[{name, main_agent, sub_agents[]}],
      tools allowed (tools_allowed or tools), schedule hints (schedule_hints or schedule)
    Status defaults to 'planned' if missing or planned.
    Planned/off packs never run tasks.
    """
    if not isinstance(pack_data, dict):
        raise ValueError("Pack cube.json must be a JSON object")

    cid = pack_data.get("id")
    if not cid or not isinstance(cid, str):
        raise ValueError("Pack missing valid string 'id'")
    cid = cid.strip()
    if not re.fullmatch(r"[a-z0-9_-]+", cid):
        raise ValueError(f"Invalid pack id '{cid}': must be lowercase alphanumeric, dashes, or underscores")
    if pack_dir_name and cid != pack_dir_name:
        raise ValueError(f"Pack id '{cid}' does not match folder name '{pack_dir_name}'")

    name = pack_data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        raise ValueError(f"Pack '{cid}' missing valid string 'name'")

    captain = pack_data.get("captain")
    if not captain or not isinstance(captain, str) or not captain.strip():
        raise ValueError(f"Pack '{cid}' missing valid string 'captain'")

    # colour / color
    colour = pack_data.get("colour") or pack_data.get("color")
    if colour is not None and not isinstance(colour, str):
        raise ValueError(f"Pack '{cid}' colour must be a string")

    # status: planned / on / off (defaults to planned)
    raw_status = pack_data.get("status")
    if raw_status is None or str(raw_status).strip() == "":
        status = "planned"
    else:
        status = str(raw_status).strip().lower()
    if status not in ("planned", "on", "off"):
        raise ValueError(f"Pack '{cid}' invalid status '{status}': must be 'planned', 'on', or 'off'")

    # sub_rooms: list of dicts with name, main_agent, sub_agents
    sub_rooms = pack_data.get("sub_rooms")
    if not isinstance(sub_rooms, list) or len(sub_rooms) == 0:
        raise ValueError(f"Pack '{cid}' sub_rooms must be a non-empty list")

    room_names = []
    main_agents = []
    sub_agents = []
    sub_rooms_parsed = []

    for idx, room in enumerate(sub_rooms):
        if not isinstance(room, dict):
            raise ValueError(f"Pack '{cid}' sub_room at index {idx} must be an object")
        r_name = room.get("name")
        if not r_name or not isinstance(r_name, str) or not r_name.strip():
            raise ValueError(f"Pack '{cid}' sub_room at index {idx} missing valid 'name'")
        r_main = room.get("main_agent")
        if not r_main or not isinstance(r_main, str) or not r_main.strip():
            raise ValueError(f"Pack '{cid}' sub_room '{r_name}' missing valid 'main_agent'")
        r_sub = room.get("sub_agents", [])
        if not isinstance(r_sub, list):
            raise ValueError(f"Pack '{cid}' sub_room '{r_name}' 'sub_agents' must be a list")

        room_names.append(r_name.strip())
        main_agents.append(r_main.strip())
        for sa in r_sub:
            if not isinstance(sa, str) or not sa.strip():
                raise ValueError(f"Pack '{cid}' sub_room '{r_name}' sub_agent must be a non-empty string")
            sub_agents.append(sa.strip())

        sub_rooms_parsed.append({
            "name": r_name.strip(),
            "main_agent": r_main.strip(),
            "sub_agents": [s.strip() for s in r_sub]
        })

    # tools allowed
    tools = pack_data.get("tools_allowed")
    if tools is None:
        tools = pack_data.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError(f"Pack '{cid}' tools allowed must be a list")

    # schedule hints
    schedule = pack_data.get("schedule_hints")
    if schedule is None:
        schedule = pack_data.get("schedule")
    if schedule is not None and not isinstance(schedule, (dict, list, str)):
        raise ValueError(f"Pack '{cid}' schedule hints must be a dict, list, or string")

    # Check for secrets
    _check_no_secrets(pack_data, cid)

    # Normalized cube representation conforming to standard cubes registry schema
    # planned flag is True if status is 'planned'
    is_planned = (status == "planned")
    # All cube agents
    all_agents = list(dict.fromkeys(main_agents))
    all_sub_agents = list(dict.fromkeys(sub_agents))

    role = pack_data.get("role") or f"{name} operations and tasks"

    normalized = {
        "id": cid,
        "name": name.strip(),
        "role": role.strip(),
        "captain": captain.strip(),
        "sub_rooms": room_names,
        "sub_rooms_detailed": sub_rooms_parsed,
        "agents": all_agents,
        "sub_agents": all_sub_agents,
        "right_hand": all_agents[0] if all_agents else captain.strip(),
        "left_hand": all_agents[1] if len(all_agents) > 1 else None,
        "kinds": pack_data.get("kinds", []),
        "planned": is_planned,
        "status": status,
        "pack_status": status,
        "colour": colour or pack_data.get("color") or "#708090",
        "tools_allowed": tools,
        "schedule_hints": schedule,
        "is_pack": True,
    }

    if "agents_info" in pack_data:
        normalized["agents_info"] = pack_data["agents_info"]

    return normalized


def load_cube_packs(packs_dir=None, existing_ids=None):
    """Scans packs_dir for packs (cube_packs/<id>/cube.json).

    Skips any broken pack with a clear logged reason and never breaks the robot.
    Returns list of valid normalized cube objects.
    """
    directory = packs_dir or CUBE_PACKS_DIR
    if not os.path.exists(directory) or not os.path.isdir(directory):
        return []

    seen_ids = set(existing_ids or [])
    loaded_packs = []

    # Sort directory entries for deterministic loading order
    entries = sorted(os.listdir(directory))
    for entry in entries:
        pack_folder = os.path.join(directory, entry)
        if not os.path.isdir(pack_folder):
            continue

        cube_json_file = os.path.join(pack_folder, "cube.json")
        if not os.path.exists(cube_json_file):
            continue

        # File size check
        try:
            sz = os.path.getsize(cube_json_file)
            if sz > MAX_PACK_FILE_SIZE:
                logger.warning(f"Skipping cube pack '{entry}': cube.json exceeds maximum size limit ({sz} > {MAX_PACK_FILE_SIZE} bytes)")
                continue
        except OSError as e:
            logger.warning(f"Skipping cube pack '{entry}': unable to check file size ({e})")
            continue

        # Parse JSON
        try:
            with open(cube_json_file, encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as e:
            logger.warning(f"Skipping cube pack '{entry}': JSON parse error: {e}")
            continue

        # Validate pack
        try:
            normalized = validate_cube_pack(raw_data, pack_dir_name=entry)
        except Exception as e:
            logger.warning(f"Skipping cube pack '{entry}': validation error: {e}")
            continue

        cid = normalized["id"]
        if cid in seen_ids:
            logger.warning(f"Skipping cube pack '{entry}': duplicate cube id '{cid}'")
            continue

        # Validate recipes/ folder if present
        recipes_dir = os.path.join(pack_folder, "recipes")
        if os.path.exists(recipes_dir) and os.path.isdir(recipes_dir):
            recipe_skip = False
            for r_file in os.listdir(recipes_dir):
                r_path = os.path.join(recipes_dir, r_file)
                if os.path.isfile(r_path):
                    if os.path.getsize(r_path) > MAX_PACK_FILE_SIZE:
                        logger.warning(f"Skipping cube pack '{entry}': recipe file '{r_file}' exceeds size limit")
                        recipe_skip = True
                        break
                    try:
                        with open(r_path, encoding="utf-8") as rf:
                            r_content = rf.read()
                            _check_no_secrets(r_content, f"{entry}/recipes/{r_file}")
                    except Exception as e:
                        logger.warning(f"Skipping cube pack '{entry}': recipe file '{r_file}' check error: {e}")
                        recipe_skip = True
                        break
            if recipe_skip:
                continue

        seen_ids.add(cid)
        loaded_packs.append(normalized)

    return loaded_packs


def load_registry(path=None, packs_dir=None):
    """Loads base cubes from cubes.json PLUS every valid pack from cube_packs/.

    A broken pack is skipped with a clear reason and never breaks the robot.
    """
    p = path or CUBES_JSON_PATH
    base_cubes = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            base_cubes = json.load(f)
        validate_registry(base_cubes)

    # Base cubes ids
    existing_ids = {c["id"] for c in base_cubes if isinstance(c, dict) and "id" in c}

    # If loading the default registry path, also load cube packs from packs_dir or CUBE_PACKS_DIR
    packs = []
    if path is None or packs_dir is not None:
        packs = load_cube_packs(packs_dir=packs_dir, existing_ids=existing_ids)

    return list(base_cubes) + packs


def validate_registry(cubes, workforce_agents=None):
    if not isinstance(cubes, list):
        raise ValueError("cubes registry must be a list of cube objects")

    if workforce_agents is None:
        try:
            import workforce
            workforce_agents = workforce.AGENTS
        except Exception:
            workforce_agents = []

    wf_agent_map = {a["name"]: a for a in workforce_agents}
    seen_wf_agents = set()
    cube_ids = set()

    for idx, cube in enumerate(cubes):
        if not isinstance(cube, dict):
            raise ValueError(f"Cube at index {idx} is not a dictionary")
        cid = cube.get("id")
        if not cid or not isinstance(cid, str):
            raise ValueError(f"Cube at index {idx} missing valid 'id'")
        if cid in cube_ids:
            raise ValueError(f"Duplicate cube id: '{cid}'")
        cube_ids.add(cid)

        required_keys = ["id", "name", "role", "captain", "sub_rooms", "agents", "sub_agents", "right_hand", "left_hand", "kinds"]
        for key in required_keys:
            if key not in cube:
                raise ValueError(f"Cube '{cid}' missing required field '{key}'")

        if not isinstance(cube["sub_rooms"], list):
            raise ValueError(f"Cube '{cid}' field 'sub_rooms' must be a list")
        if not isinstance(cube["agents"], list):
            raise ValueError(f"Cube '{cid}' field 'agents' must be a list")
        if not isinstance(cube["sub_agents"], list):
            raise ValueError(f"Cube '{cid}' field 'sub_agents' must be a list")
        if not isinstance(cube["kinds"], list):
            raise ValueError(f"Cube '{cid}' field 'kinds' must be a list")

        cube_planned = bool(cube.get("planned", False))

        # Check existing workforce agents mapping
        all_cube_agents = set(cube["agents"] + cube["sub_agents"])
        if cube["captain"]:
            all_cube_agents.add(cube["captain"])
        if cube["right_hand"]:
            all_cube_agents.add(cube["right_hand"])
        if cube["left_hand"]:
            all_cube_agents.add(cube["left_hand"])

        for ag in all_cube_agents:
            if ag in wf_agent_map:
                seen_wf_agents.add(ag)

    # Ensure all non-planned workforce agents are mapped
    for wf_ag in wf_agent_map:
        if wf_ag not in seen_wf_agents:
            raise ValueError(f"Workforce agent '{wf_ag}' is not mapped to any cube")

    return True


def get_cube_state(cube_id, db_path=None, conn=None, default="on"):
    if db_path and not os.path.exists(db_path) and conn is None:
        return default
    close_conn = False
    if conn is None:
        conn = connect(db_path)
        close_conn = True
    else:
        ensure_tables(conn)
    try:
        row = conn.execute("SELECT status FROM cube_state WHERE id = ?", (cube_id,)).fetchone()
        return row[0] if row else default
    except sqlite3.Error:
        return default
    finally:
        if close_conn:
            conn.close()


def set_cube_status(cube_id, status, db_path=None):
    status = status.lower()
    if status not in ("on", "off", "archived"):
        raise ValueError(f"Invalid status '{status}'. Must be 'on', 'off', or 'archived'.")
    conn = connect(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute("""INSERT INTO cube_state (id, status, updated_ts) VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated_ts = excluded.updated_ts""",
                 (cube_id, status, now))
    conn.commit()
    conn.close()


def turn_on(cube_id, db_path=None):
    set_cube_status(cube_id, "on", db_path)


def turn_off(cube_id, db_path=None):
    set_cube_status(cube_id, "off", db_path)


def archive_cube(cube_id, db_path=None):
    set_cube_status(cube_id, "archived", db_path)


def restore_cube(cube_id, db_path=None):
    set_cube_status(cube_id, "on", db_path)


def _is_planned_pack(cube):
    # The legacy registry's planned label is descriptive, not an execution gate.
    return bool(cube.get("is_pack") and cube.get("planned"))


def _default_cube_state(cube):
    # Only imported packs define an initial operational state in their manifest.
    return cube.get("pack_status", "planned") if cube.get("is_pack") else "on"


def list_cubes(include_archived=False, db_path=None, registry_path=None, packs_dir=None):
    registry = load_registry(registry_path, packs_dir=packs_dir)
    out = []
    for c in registry:
        c_copy = dict(c)
        # For cube packs, default initial status is pack's status (planned/off/on)
        default_st = _default_cube_state(c)
        c_copy["status"] = get_cube_state(c["id"], db_path, default=default_st)
        c_copy["enabled"] = (c_copy["status"] == "on" and not _is_planned_pack(c))
        if include_archived or c_copy["status"] != "archived":
            out.append(c_copy)
    return out


def get_cube(cube_id, db_path=None, registry_path=None, packs_dir=None):
    registry = load_registry(registry_path, packs_dir=packs_dir)
    for c in registry:
        if c["id"] == cube_id:
            c_copy = dict(c)
            default_st = _default_cube_state(c)
            c_copy["status"] = get_cube_state(c["id"], db_path, default=default_st)
            c_copy["enabled"] = (c_copy["status"] == "on" and not _is_planned_pack(c))
            return c_copy
    return None


def get_cube_for_kind(kind, registry_path=None, packs_dir=None):
    registry = load_registry(registry_path, packs_dir=packs_dir)
    for c in registry:
        if kind in c.get("kinds", []):
            return c
    return None


def get_cube_for_agent(agent_name, registry_path=None, db_path=None, conn=None):
    registry = load_registry(registry_path)
    # Primary membership check (captain, main agents, sub agents)
    for c in registry:
        if (agent_name == c.get("captain") or
            agent_name in c.get("agents", []) or
            agent_name in c.get("sub_agents", [])):
            return c
    # Secondary helper check (right hand, left hand)
    for c in registry:
        if (agent_name == c.get("right_hand") or
            agent_name == c.get("left_hand")):
            return c
    import office_agents
    for agent in office_agents.registered(db_path, conn):
        if agent["name"] == agent_name and agent.get("workspace"):
            return next((c for c in registry if c["id"] == agent["workspace"]["cube_id"]), None)
    return None


def is_kind_enabled(kind, db_path=None, registry_path=None, conn=None, packs_dir=None):
    c = get_cube_for_kind(kind, registry_path, packs_dir=packs_dir)
    if not c:
        return True
    if _is_planned_pack(c):
        return False
    default_st = _default_cube_state(c)
    st = get_cube_state(c["id"], db_path=db_path, conn=conn, default=default_st)
    return st == "on"


def is_agent_enabled(agent_name, db_path=None, registry_path=None, conn=None):
    import office_agents
    for agent in office_agents.registered(db_path, conn):
        if agent["name"] == agent_name and agent.get("enabled") is False:
            return False
    c = get_cube_for_agent(agent_name, registry_path, db_path, conn)
    if not c:
        return True
    if _is_planned_pack(c):
        return False
    default_st = _default_cube_state(c)
    st = get_cube_state(c["id"], db_path=db_path, conn=conn, default=default_st)
    return st == "on"


# ---- Telegram Formatting and Callback Helpers ------------------------------------

def format_telegram_cubes_list(db_path=None):
    cubes = list_cubes(include_archived=True, db_path=db_path)
    if not cubes:
        return "No department cubes configured.", []

    lines = ["🧊 *Department Cubes*\n"]
    buttons = []

    for c in cubes:
        st = c["status"].upper()
        icon = "🟢" if st == "ON" else ("🔴" if st == "OFF" else "📦")
        planned_tag = " (Planned)" if c.get("planned") else ""
        lines.append(f"{icon} *{c['name']}* [{st}]{planned_tag}")
        lines.append(f"  Captain: {c['captain']}")
        if c.get("kinds"):
            lines.append(f"  Tasks: {', '.join(c['kinds'])}")
        lines.append("")

        cid = c["id"]
        if st == "ON":
            buttons.append([("⏸ Switch Off", f"cb:cube:off:{cid}"), ("🗑 Delete", f"cb:cube:del:{cid}")])
        elif st == "OFF":
            buttons.append([("▶️ Switch On", f"cb:cube:on:{cid}"), ("🗑 Delete", f"cb:cube:del:{cid}")])
        elif st == "ARCHIVED":
            buttons.append([("🔄 Restore", f"cb:cube:on:{cid}")])

    return "\n".join(lines).strip(), buttons


def handle_telegram_callback(data, db_path=None):
    """Handle callback queries starting with cb:cube: or cube:"""
    if not data:
        return None

    prefix = None
    if data.startswith("cb:cube:"):
        prefix = "cb:cube:"
    elif data.startswith("cube:"):
        prefix = "cube:"

    if not prefix:
        return None

    payload = data[len(prefix):]
    parts = payload.split(":")
    action = parts[0]
    cube_id = parts[1] if len(parts) > 1 else ""

    c = get_cube(cube_id, db_path=db_path)

    if action == "off":
        if c:
            turn_off(cube_id, db_path=db_path)
            return f"⏸ Cube '{c['name']}' switched OFF. Tasks for this department will wait safely."
        return f"Cube '{cube_id}' not found."

    if action == "on":
        if c:
            turn_on(cube_id, db_path=db_path)
            return f"▶️ Cube '{c['name']}' switched ON. Agents resumed."
        return f"Cube '{cube_id}' not found."

    if action == "del":
        if c:
            # Requires Yes/No confirmation
            text = f"❓ Delete (archive) cube '{c['name']}'?\nTasks will be paused. (This action is recoverable)."
            buttons = [[("Yes, Delete", f"cb:cube:delyes:{cube_id}"), ("No, Cancel", f"cb:cube:delno:{cube_id}")]]
            return {"text": text, "buttons": buttons}
        return f"Cube '{cube_id}' not found."

    if action == "delyes":
        if c:
            archive_cube(cube_id, db_path=db_path)
            return f"📦 Cube '{c['name']}' deleted (archived). You can restore it anytime."
        return f"Cube '{cube_id}' not found."

    if action == "delno":
        return "Action cancelled."

    return None


# ---- Public Export Shape Formatting ------------------------------------------------

def _agent_role(agent_name, workforce_agents_map):
    if agent_name in workforce_agents_map:
        return workforce_agents_map[agent_name].get("role", "")
    return ""


def get_public_cubes_export(db_path=None, registry_path=None, packs_dir=None):
    """Generates the safe 'cubes' array for public status export.

    Shaped for the office:
    cube -> captain -> sub rooms -> agents -> sub agents.

    Contains names, roles, counts, status, and planned status.
    No prompts, no money amounts or currency symbols, no product ideas.
    """
    try:
        import workforce
        import office_agents
        wf_agents = office_agents.registered(db_path)
    except Exception:
        wf_agents = []

    wf_map = {a["name"]: a for a in wf_agents}
    registry = load_registry(registry_path, packs_dir=packs_dir)
    registry = [dict(c, agents=list(c["agents"])) for c in registry]
    for agent in wf_agents:
        if agent.get("workspace"):
            for cube in registry:
                if cube["id"] == agent["workspace"]["cube_id"]:
                    cube["agents"].append(agent["name"])

    export_list = []
    for c in registry:
        default_st = _default_cube_state(c)
        st = get_cube_state(c["id"], db_path, default=default_st)
        is_planned = bool(c.get("planned", False))

        captain_name = c.get("captain", "")
        captain_obj = {
            "name": captain_name,
            "role": _agent_role(captain_name, wf_map),
            "planned": is_planned or (captain_name not in wf_map)
        }

        sub_rooms_list = [{"name": r} for r in c.get("sub_rooms", [])]

        agents_list = []
        if c.get("sub_rooms_detailed"):
            # Structure agent hierarchy matching the sub_rooms definition if detailed
            for room in c["sub_rooms_detailed"]:
                main_ag = room["main_agent"]
                sub_ags_objs = [{
                    "name": sa,
                    "role": _agent_role(sa, wf_map),
                    "planned": is_planned or (sa not in wf_map)
                } for sa in room.get("sub_agents", [])]
                agents_list.append({
                    "name": main_ag,
                    "role": _agent_role(main_ag, wf_map),
                    "planned": is_planned or (main_ag not in wf_map),
                    "sub_agents": sub_ags_objs
                })
        else:
            for main_agent in c.get("agents", []):
                sub_agents_objs = []
                for sub_ag in c.get("sub_agents", []):
                    sub_agents_objs.append({
                        "name": sub_ag,
                        "role": _agent_role(sub_ag, wf_map),
                        "planned": is_planned or (sub_ag not in wf_map)
                    })

                agents_list.append({
                    "name": main_agent,
                    "role": _agent_role(main_agent, wf_map),
                    "planned": is_planned or (main_agent not in wf_map),
                    "sub_agents": sub_agents_objs
                })

        counts = {
            "sub_rooms": len(sub_rooms_list),
            "agents": len(agents_list),
            "sub_agents": len(c.get("sub_agents", [])),
            "kinds": len(c.get("kinds", []))
        }

        export_item = {
            "id": c["id"],
            "name": c["name"],
            "role": c["role"],
            "status": st,
            "enabled": (st == "on" and not _is_planned_pack(c)),
            "planned": is_planned,
            "counts": counts,
            "captain": captain_obj,
            "sub_rooms": sub_rooms_list,
            "agents": agents_list
        }
        if c.get("colour"):
            export_item["colour"] = c["colour"]

        export_list.append(export_item)

    return export_list


if __name__ == "__main__":
    cubes = load_registry()
    print(f"Loaded {len(cubes)} cubes from registry.")
