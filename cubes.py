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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CUBES_JSON_PATH = os.path.join(BASE_DIR, "cubes.json")


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


def load_registry(path=None):
    p = path or CUBES_JSON_PATH
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    validate_registry(data)
    return data


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


def get_cube_state(cube_id, db_path=None, conn=None):
    if db_path and not os.path.exists(db_path) and conn is None:
        return "on"
    close_conn = False
    if conn is None:
        conn = connect(db_path)
        close_conn = True
    else:
        ensure_tables(conn)
    try:
        row = conn.execute("SELECT status FROM cube_state WHERE id = ?", (cube_id,)).fetchone()
        return row[0] if row else "on"
    except sqlite3.Error:
        return "on"
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


def list_cubes(include_archived=False, db_path=None, registry_path=None):
    registry = load_registry(registry_path)
    out = []
    for c in registry:
        c_copy = dict(c)
        c_copy["status"] = get_cube_state(c["id"], db_path)
        c_copy["enabled"] = (c_copy["status"] == "on")
        if include_archived or c_copy["status"] != "archived":
            out.append(c_copy)
    return out


def get_cube(cube_id, db_path=None, registry_path=None):
    registry = load_registry(registry_path)
    for c in registry:
        if c["id"] == cube_id:
            c_copy = dict(c)
            c_copy["status"] = get_cube_state(c["id"], db_path)
            c_copy["enabled"] = (c_copy["status"] == "on")
            return c_copy
    return None


def get_cube_for_kind(kind, registry_path=None):
    registry = load_registry(registry_path)
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


def is_kind_enabled(kind, db_path=None, registry_path=None, conn=None):
    c = get_cube_for_kind(kind, registry_path)
    if not c:
        return True
    st = get_cube_state(c["id"], db_path=db_path, conn=conn)
    return st == "on"


def is_agent_enabled(agent_name, db_path=None, registry_path=None, conn=None):
    import office_agents
    for agent in office_agents.registered(db_path, conn):
        if agent["name"] == agent_name and agent.get("enabled") is False:
            return False
    c = get_cube_for_agent(agent_name, registry_path, db_path, conn)
    if not c:
        return True
    st = get_cube_state(c["id"], db_path=db_path, conn=conn)
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


def get_public_cubes_export(db_path=None, registry_path=None):
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
    registry = load_registry(registry_path)
    registry = [dict(c, agents=list(c["agents"])) for c in registry]
    for agent in wf_agents:
        if agent.get("workspace"):
            for cube in registry:
                if cube["id"] == agent["workspace"]["cube_id"]:
                    cube["agents"].append(agent["name"])

    export_list = []
    for c in registry:
        st = get_cube_state(c["id"], db_path)
        is_planned = bool(c.get("planned", False))

        captain_name = c.get("captain", "")
        captain_obj = {
            "name": captain_name,
            "role": _agent_role(captain_name, wf_map),
            "planned": is_planned or (captain_name not in wf_map)
        }

        sub_rooms_list = [{"name": r} for r in c.get("sub_rooms", [])]

        agents_list = []
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

        export_list.append({
            "id": c["id"],
            "name": c["name"],
            "role": c["role"],
            "status": st,
            "enabled": (st == "on"),
            "planned": is_planned,
            "counts": counts,
            "captain": captain_obj,
            "sub_rooms": sub_rooms_list,
            "agents": agents_list
        })

    return export_list


if __name__ == "__main__":
    cubes = load_registry()
    print(f"Loaded {len(cubes)} cubes from registry.")
