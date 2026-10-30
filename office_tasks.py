"""Bounded recurring office work; definitions are data, never executable commands."""
import datetime
import json
import re
import time

import cubes
import guardrails
import secret_guard

INTERVALS = {"1h": 3600, "1d": 86400, "1w": 604800}
INTERVAL_ALIASES = {"hourly": "1h", "daily": "1d", "weekly": "1w"}
MAX_MISSIONS = 50
MAX_PENDING = 20
MAX_DISPATCH = 10


def connect(db_path=None):
    import workforce
    conn = workforce.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS office_missions (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, agent_id TEXT NOT NULL, every TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active', next_run REAL NOT NULL, last_run REAL,
        updated REAL NOT NULL)""")
    conn.commit()
    return conn


def agent(value):
    import workforce
    if not isinstance(value, str):
        raise ValueError("invalid agent")
    for row in workforce.AGENTS:
        slug = re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-")
        if value in (row["name"], slug, "chilli-gavva-robot-" + slug):
            return {**row, "id": "chilli-gavva-robot-" + slug}
    raise ValueError("unknown agent")


def mission_value(value):
    if not isinstance(value, dict) or set(value) != {"title", "agent_id", "every"}:
        raise ValueError("invalid mission")
    title, every = value["title"], value["every"]
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or not isinstance(every, str):
        raise ValueError("invalid mission")
    every = INTERVAL_ALIASES.get(every, every)
    if every not in INTERVALS:
        raise ValueError("invalid interval")
    return secret_guard.scrub_text(title.strip()), agent(value["agent_id"])["id"], every


def add_mission(record, db_path=None, now=None):
    now = time.time() if now is None else now
    title, recipient, every = mission_value(record["value"])
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM office_missions WHERE id = ?", (record["target"],)).fetchone():
                return "rejected", "mission id already exists", None
            if conn.execute("SELECT COUNT(*) FROM office_missions WHERE status = 'active'").fetchone()[0] >= MAX_MISSIONS:
                return "held", "recurring mission cap reached", None
            conn.execute("INSERT INTO office_missions (id,title,agent_id,every,next_run,updated) VALUES (?,?,?,?,?,?)",
                         (record["target"], title, recipient, every, now + INTERVALS[every], now))
        return "applied", "recurring mission stored", None
    finally:
        conn.close()


def change_mission(record, db_path=None, now=None):
    now = time.time() if now is None else now
    patch = record["value"]
    archive = record["action"] == "archive_mission"
    if archive:
        if patch is not None:
            raise ValueError("archive requires null value")
    elif (not isinstance(patch, dict) or not patch or set(patch) - {"title", "agent_id", "every"}):
        raise ValueError("invalid mission edit")
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT title,agent_id,every,status FROM office_missions WHERE id=?", (record["target"],)).fetchone()
            if not row:
                return "rejected", "mission not found", None
            if archive:
                conn.execute("UPDATE office_missions SET status='archived',updated=? WHERE id=?", (now, record["target"]))
            else:
                if row[3] == "archived":
                    return "held", "archived mission cannot be edited", None
                value = {**dict(zip(("title", "agent_id", "every"), row[:3])), **patch}
                title, recipient, every = mission_value(value)
                conn.execute("UPDATE office_missions SET title=?,agent_id=?,every=?,next_run=?,updated=? WHERE id=?",
                             (title, recipient, every, now + INTERVALS[every], now, record["target"]))
            # An edit must not run the queued, superseded instructions; running work is left alone.
            for task_id, payload in conn.execute("SELECT id,payload FROM tasks WHERE kind='office_task' AND status='queued'").fetchall():
                if json.loads(payload).get("mission_id") == record["target"]:
                    conn.execute("UPDATE tasks SET status='cancelled',finished_ts=? WHERE id=?",
                                 (datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(), task_id))
        return "applied", "mission archived" if archive else "mission updated", None
    finally:
        conn.close()


def task_enabled(payload, db_path=None, conn=None):
    try:
        recipient = agent(payload.get("agent_id"))
    except (AttributeError, ValueError):
        return False
    if not cubes.is_agent_enabled(recipient["name"], db_path, conn=conn):
        return False
    if payload.get("trigger_id"):
        if conn is None:
            own = connect(db_path)
            try:
                return task_enabled(payload, db_path, own)
            finally:
                own.close()
        import office_triggers
        return office_triggers.enabled(payload["trigger_id"], conn)
    if payload.get("mission_id"):
        if conn is None:
            own = connect(db_path)
            try:
                return task_enabled(payload, db_path, own)
            finally:
                own.close()
        row = conn.execute("SELECT status FROM office_missions WHERE id = ?", (payload["mission_id"],)).fetchone()
        return row is not None and row[0] == "active"
    return True


def tick(db_path=None, now=None, dry_run=False):
    """Queue each due mission once. Missed intervals coalesce; pending work cannot flood the queue."""
    import rest_window
    now = time.time() if now is None else now
    if guardrails.is_killed() or guardrails.is_paused(db_path) or rest_window.in_rest_window():
        return {"queued": 0, "held": True}
    conn = connect(db_path)
    queued = 0
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE kind='office_task' AND status IN ('queued','running')").fetchone()[0]
            rows = conn.execute("SELECT id,title,agent_id,every,next_run FROM office_missions "
                                "WHERE status='active' AND next_run<=? ORDER BY next_run,id LIMIT ?",
                                (now, MAX_DISPATCH)).fetchall()
            for ident, title, recipient, every, due in rows:
                if pending >= MAX_PENDING:
                    break
                payload = {"mission_id": ident, "text": title, "agent_id": recipient}
                if not task_enabled(payload, db_path, conn):
                    continue
                existing = conn.execute("SELECT payload FROM tasks WHERE kind='office_task' AND status IN ('queued','running')").fetchall()
                if any(json.loads(row[0]).get("mission_id") == ident for row in existing):
                    continue
                queued += 1
                pending += 1
                if not dry_run:
                    conn.execute("INSERT INTO tasks(ts,kind,payload,priority) VALUES (?,'office_task',?,5)",
                                 (datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(), json.dumps(payload)))
                    step = INTERVALS[every]
                    next_run = due + (int((now - due) // step) + 1) * step
                    conn.execute("UPDATE office_missions SET next_run=?,last_run=? WHERE id=?", (next_run, now, ident))
        import office_triggers
        triggered = office_triggers.tick(db_path, now, dry_run, limit=MAX_DISPATCH - queued)
        return {"queued": queued + triggered["queued"]}
    finally:
        conn.close()


def execute(task, ctx):
    import workforce
    payload = task["payload"]
    if not task_enabled(payload, ctx.get("db_path")):
        return {"summary": "office work no longer enabled", "next": []}
    recipient = agent(payload["agent_id"])
    if payload.get("summary"):
        import chat_brain
        import notify
        summary = (chat_brain.fetch_drafts_waiting(ctx.get("db_path")) if payload.get("metric") == "drafts_waiting"
                   else chat_brain.fetch_status(ctx.get("db_path")))
        summary = secret_guard.scrub_text(summary)
        if not notify.send(summary):
            raise OSError("office summary could not be delivered")
        return {"agent_id": recipient["id"], "answer": summary, "next": []}
    import chat_brain
    state, reason, answer = chat_brain.office_reply(recipient["id"], payload["text"], ctx.get("db_path"),
                                                   llm=lambda prompt: workforce._ask(ctx, prompt, size="small"))
    if state != "applied":
        raise workforce.AIUnavailable("office mission: no AI answer")
    return {"agent_id": recipient["id"], "answer": secret_guard.scrub_text(answer), "next": []}
