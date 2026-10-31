"""Persist reviewed office hires as paused workforce members, never executable tools."""
import json
import os
import re
import sqlite3
from contextlib import closing

import agent_engines
import cubes
import workforce

MAX_AGENTS = 50


def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS workforce_agents (
        id TEXT PRIMARY KEY, name_key TEXT UNIQUE NOT NULL, spec TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS workforce_agent_engines (
        agent_id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL)""")


def agent_id(name):
    return "chilli-gavva-robot-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def registered(db_path=None, conn=None):
    """Merge durable hires and engine choices into the built-in workforce registry."""
    if conn is None:
        path = db_path or workforce.guardrails.DB_PATH
        # Registry reads must not create a database (public exports can run before setup).
        if not os.path.exists(path):
            return [dict(row, id=agent_id(row["name"])) for row in workforce.AGENTS]
        with closing(sqlite3.connect(path)) as own:
            return registered(conn=own)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = [dict(row, id=agent_id(row["name"])) for row in workforce.AGENTS]
    if "workforce_agents" in tables:
        for ident, raw, enabled in conn.execute("SELECT id,spec,enabled FROM workforce_agents ORDER BY id LIMIT ?", (MAX_AGENTS,)):
            spec = json.loads(raw)
            result.append({**spec, "id": ident, "level": "entry", "kinds": [], "enabled": bool(enabled)})
    if "workforce_agent_engines" in tables:
        engines = {ident: (provider, model) for ident, provider, model in conn.execute(
            "SELECT agent_id,provider,model FROM workforce_agent_engines")}
        for row in result:
            if row["id"] in engines:
                row["provider"], row["model"] = engines[row["id"]]
    return result


def resolve(value, db_path=None, conn=None):
    if isinstance(value, str):
        for row in registered(db_path, conn):
            if value in (row["name"], row["id"], row["id"].removeprefix("chilli-gavva-robot-")):
                return row
    raise ValueError("unknown agent")


def _text(value, limit, name=False):
    if (not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= limit
            or any(ord(c) < 32 and c not in "\n\t" for c in value)
            or name and not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:[ ._-][A-Za-z0-9]+)*", value)):
        raise ValueError("invalid agent text")
    return value


def _scan(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and agent_engines.credential_like(item):
            raise ValueError("credential-like content")
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _spec(spec):
    if not isinstance(spec, dict) or set(spec) != {"name", "role", "workspace", "engine", "voice_enabled", "briefing"}:
        raise ValueError("invalid agent fields")
    _text(spec["name"], 60, name=True)
    _text(spec["role"], 120)
    if not isinstance(spec["engine"], str) or spec["engine"] not in agent_engines.modes():
        raise ValueError("unsupported engine")
    if type(spec["voice_enabled"]) is not bool:
        raise ValueError("invalid voice setting")
    ws, brief = spec["workspace"], spec["briefing"]
    if not isinstance(ws, dict) or set(ws) != {"cube_id", "room_id"}:
        raise ValueError("invalid workspace")
    _text(ws["cube_id"], 80)
    _text(ws["room_id"], 80)
    cube = cubes.get_cube(ws["cube_id"])
    if not cube or cube.get("planned") or ws["room_id"] not in cube["sub_rooms"]:
        raise ValueError("unknown workspace")
    if not isinstance(brief, dict) or set(brief) != {"who", "does", "never"}:
        raise ValueError("invalid briefing")
    for text in brief.values():
        _text(text, 120)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate fields")
        result[key] = value
    return result


def apply_message(text, db_path=None):
    """Recognize the office's exact send_message JSON; ordinary chat returns None."""
    if not text.lstrip().startswith("{"):
        return None
    try:
        value = json.loads(text, object_pairs_hook=_unique_pairs)
        if not isinstance(value, dict) or "kind" not in value:
            return None
        if value["kind"] not in ("add_agent", "engine_change"):
            return "rejected", "unknown agent request", None
        _scan(value)
        conn = workforce.connect(db_path)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if value["kind"] == "add_agent":
                    if set(value) != {"kind", "spec"}:
                        raise ValueError("invalid agent request")
                    spec = value["spec"]
                    _spec(spec)
                    ident = agent_id(spec["name"])
                    names = [row["name"] for row in registered(conn=conn)]
                    for cube in cubes.load_registry():
                        names.extend(cube["agents"] + cube["sub_agents"])
                        names.extend(name for name in (cube["captain"], cube["right_hand"], cube["left_hand"]) if name)
                    if any(agent_id(name) == ident or name.casefold() == spec["name"].casefold() for name in names):
                        return "rejected", "agent name already exists", None
                    if conn.execute("SELECT COUNT(*) FROM workforce_agents").fetchone()[0] >= MAX_AGENTS:
                        return "rejected", "agent registry is full", None
                    conn.execute("INSERT INTO workforce_agents(id,name_key,spec) VALUES (?,?,?)",
                                 (ident, spec["name"].casefold(), json.dumps(spec)))
                    return "applied", "agent added paused", None
                if set(value) != {"kind", "agent_id", "provider", "model"}:
                    raise ValueError("invalid engine request")
                agent = resolve(value["agent_id"], conn=conn)
                if value["agent_id"] != agent["id"]:
                    raise ValueError("invalid agent identifier")
                if (value["provider"], value["model"]) not in agent_engines.choices():
                    return "rejected", "unsupported engine", None
                conn.execute("""INSERT INTO workforce_agent_engines VALUES (?,?,?)
                    ON CONFLICT(agent_id) DO UPDATE SET provider=excluded.provider,model=excluded.model""",
                             (agent["id"], value["provider"], value["model"]))
                row = conn.execute("SELECT spec FROM workforce_agents WHERE id=?", (agent["id"],)).fetchone()
                if row:
                    spec = json.loads(row[0])
                    spec["engine"] = "local" if value["provider"] == "FreeLLMAPI" else "cloud"
                    conn.execute("UPDATE workforce_agents SET spec=? WHERE id=?", (json.dumps(spec), agent["id"]))
                return "applied", "agent engine updated", None
        finally:
            conn.close()
    except (ValueError, TypeError, RecursionError):
        return "rejected", "invalid agent request", None


def set_enabled(target, enabled, db_path=None):
    """Existing signed set_switch controls enable a hire without resuming its cube."""
    conn = workforce.connect(db_path)
    try:
        with conn:
            row = conn.execute("SELECT id FROM workforce_agents WHERE id=?", (target,)).fetchone()
            if not row:
                return None
            if type(enabled) is not bool:
                return "rejected", "invalid agent switch", None
            conn.execute("UPDATE workforce_agents SET enabled=? WHERE id=?", (enabled, target))
        return "applied", "agent enabled" if enabled else "agent paused", None
    finally:
        conn.close()


def chat(agent, prompt, db_path=None, size=None):
    return agent_engines.chat(prompt, db_path=db_path, size=size, mode=agent.get("engine", "cloud"),
                              provider=agent.get("provider"), model=agent.get("model"))
