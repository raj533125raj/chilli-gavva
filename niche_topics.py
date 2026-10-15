"""niche_topics.py — a growing topic pool per niche agent, with a "done topics" list (no repeats).

Each niche recipe (recipes/niche_*.yaml) has 3 fixed starter topics. Before, the same 3 ran every time
the niche came round (budget sheets runs on Monday AND Saturday). Now, when a niche recipe runs:
  1. its starter topics seed the pool (once),
  2. if fewer than 3 unused topics are left, the AI suggests new, specific ones for that niche,
  3. a topic is never used twice: anything already used (pool "used" rows and every past research or
     draft task of that agent) blocks the same or a very similar topic,
  4. the 3 picked topics are marked used and queued, keeping the recipe's own wording
     ("[Agent] <idea>, priced ..., sold on Gumroad ...").
If no fresh topic is left and the AI is down, nothing is queued that night (no repeats).
"""
import datetime
import json
import re

import guardrails

PICK = 3
ASK_FOR = 8
_STOP = {"the", "and", "for", "with", "a", "an", "of", "to", "in", "on", "who", "your", "you", "that", "their",
         "google", "sheets", "sheet", "template", "printable", "notion", "pdf", "simple", "tracker", "planner"}
_TOPIC = re.compile(r"^\[(?P<agent>[^\]]+)\]\s*(?P<idea>.+?)(?P<suffix>,\s*priced\b.*)$", re.S)


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS niche_topics (
        niche TEXT, idea TEXT, status TEXT DEFAULT 'new', source TEXT, ts TEXT, used_ts TEXT,
        PRIMARY KEY (niche, idea))""")
    return conn


def split_topic(topic):
    """'[Agent] idea, priced ..., ...' -> (agent, idea, suffix) or None."""
    m = _TOPIC.match((topic or "").strip())
    return (m.group("agent").strip(), m.group("idea").strip(), m.group("suffix")) if m else None


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def similar(a, b):
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / len(wa | wb) >= 0.6 or wa <= wb or wb <= wa


def _history(conn, agent):
    """Ideas this agent already worked on (from the task queue), so old runs count as done too."""
    ideas = []
    try:
        rows = conn.execute("SELECT payload FROM tasks WHERE kind IN ('research', 'draft')").fetchall()
    except Exception:
        return ideas
    for (payload,) in rows:
        try:
            parts = split_topic(json.loads(payload or "{}").get("topic", ""))
        except ValueError:
            continue
        if parts and parts[0] == agent:
            ideas.append(parts[1])
    return ideas


def done_topics(niche, db_path=None, agent=None):
    conn = connect(db_path)
    used = [r[0] for r in conn.execute("SELECT idea FROM niche_topics WHERE niche = ? AND status = 'used'", (niche,))]
    hist = _history(conn, agent) if agent else []
    conn.close()
    return list(dict.fromkeys(used + hist))


def _clean_line(line):
    line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line or "").strip().strip('"').strip()
    line = re.sub(r"\s+", " ", line)
    return line if 15 <= len(line) <= 200 and not line.endswith(":") else ""


def suggest_prompt(agent, description, done):
    avoid = "\n".join(f"- {d}" for d in done[-40:]) or "- (none yet)"
    return (f"You help the '{agent}', which makes low-cost digital products: {description}.\n"
            f"Suggest {ASK_FOR} NEW, specific product ideas for this niche. Each idea: one line, under 25 words, "
            "naming a specific buyer and what the product helps them do.\n"
            "Do not repeat or closely resemble any of these ideas already used:\n"
            f"{avoid}\n"
            "Rules: no food, recipes, fishing or farming; no income, health, medical or job promises; "
            "no brand names. Answer with the list only, one idea per line.")


def _ask(llm, prompt):
    try:
        try:
            return llm(prompt, size="small")
        except TypeError:
            return llm(prompt)
    except Exception:
        return None


def fresh_tasks(recipe, db_path=None, llm=None, now=None):
    """Return up to 3 research tasks with topics never used before, or None when the recipe is not a
    niche recipe in the usual format (then its fixed tasks are used as before)."""
    tasks = [t for t in recipe.get("tasks", []) if t.get("kind") == "research"]
    parsed = [split_topic(t.get("payload", {}).get("topic", "")) for t in tasks]
    if not tasks or not all(parsed):
        return None
    niche = recipe.get("name") or parsed[0][0]
    agent = parsed[0][0]
    own_suffix = {idea: suf for _, idea, suf in parsed}           # starter topics keep their own wording
    strictest = max((suf for _, _, suf in parsed), key=len)      # AI topics get the most careful notes
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="seconds")
    import workforce  # lazy: keeps imports light for tests
    conn = connect(db_path)
    for _, idea, _ in parsed:   # starter topics seed the pool once
        conn.execute("INSERT OR IGNORE INTO niche_topics (niche, idea, status, source, ts) VALUES (?, ?, 'new', "
                     "'recipe', ?)", (niche, idea, stamp))
    conn.commit()
    done = done_topics(niche, db_path, agent)

    def available():
        rows = conn.execute("SELECT idea FROM niche_topics WHERE niche = ? AND status = 'new' ORDER BY ts, rowid",
                            (niche,)).fetchall()
        return [r[0] for r in rows if not any(similar(r[0], d) for d in done)]

    pool = available()
    if len(pool) < PICK and llm is not None:
        known = [r[0] for r in conn.execute("SELECT idea FROM niche_topics WHERE niche = ?", (niche,))] + done
        reply = _ask(llm, suggest_prompt(agent, recipe.get("description", niche), done)) or ""
        for line in reply.splitlines():
            idea = _clean_line(line)
            if not idea or workforce.off_limits(idea) or any(similar(idea, k) for k in known):
                continue
            conn.execute("INSERT OR IGNORE INTO niche_topics (niche, idea, status, source, ts) VALUES "
                         "(?, ?, 'new', 'ai', ?)", (niche, idea, stamp))
            known.append(idea)
        conn.commit()
        pool = available()
    picked = []
    for idea in pool:
        if len(picked) == PICK:
            break
        if any(similar(idea, p) for p in picked):
            continue
        picked.append(idea)
        conn.execute("UPDATE niche_topics SET status = 'used', used_ts = ? WHERE niche = ? AND idea = ?",
                     (stamp, niche, idea))
    conn.commit()
    conn.close()
    agent_field = tasks[0].get("payload", {}).get("agent", agent)
    return [{"kind": "research", "priority": tasks[0].get("priority", 5),
             "payload": {"agent": agent_field, "topic": f"[{agent}] {idea}{own_suffix.get(idea, strictest)}"}}
            for idea in picked]


def pool_status(niche, db_path=None):
    conn = connect(db_path)
    rows = dict(conn.execute("SELECT status, COUNT(*) FROM niche_topics WHERE niche = ? GROUP BY status",
                             (niche,)).fetchall())
    conn.close()
    return {"new": rows.get("new", 0), "used": rows.get("used", 0)}
