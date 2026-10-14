"""Task queue and worker agents, organised like the office pyramid.

High  : CEO            plans the work
Mid   : Research Manager, Content Manager, QA Reviewer
Entry : the workers that actually do the steps (research, draft, QA check)

Everything is stored in SQLite, so a crash or a restart resumes where it stopped.
Nothing here publishes anything: drafts go to the approval queue (guardrails.py).
"""
import datetime
import inspect
import json
import os
import random
import socket
import sqlite3
import sys

import guardrails
import improver

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MAX_ATTEMPTS = 3
DEFAULT_TOPIC = "a free mini-tool page (calculator or prompt generator) that helps small creators"

def get_machine_id():
    """Stable identifier for this machine — hostname + last 4 chars of MAC.
    Used to tag tasks and heartbeats so two machines can share a DB without confusion."""
    import uuid
    host = socket.gethostname().replace(" ", "-")[:20]
    mac = "%012x" % uuid.getnode()
    return f"{host}-{mac[-4:]}"

MACHINE_ID = get_machine_id()


AGENTS = [
    {"name": "CEO", "level": "high", "role": "Plans the day's work and sets priorities", "kinds": ["plan"]},
    {"name": "Research Manager", "level": "mid", "role": "Owns research quality", "kinds": []},
    {"name": "Content Manager", "level": "mid", "role": "Owns drafts and tone", "kinds": []},
    {"name": "QA Reviewer", "level": "mid", "role": "Scores drafts, blocks risky claims", "kinds": []},
    {"name": "Research Worker", "level": "entry", "role": "Finds and lists ideas", "kinds": ["research"]},
    {"name": "Draft Worker", "level": "entry", "role": "Writes page drafts and one rewrite when asked",
     "kinds": ["draft", "rewrite"]},
    {"name": "QA Checker", "level": "entry",
     "role": "Checks every draft with the large model, sends weak ones back for one rewrite", "kinds": ["review"]},
    {"name": "Review Worker", "level": "entry", "role": "Runs the rule checklist the QA Checker builds on",
     "kinds": []},
    {"name": "Coder Worker", "level": "entry", "role": "Proposes code patches (never applies them)",
     "kinds": ["code_proposal"]},
]
KIND_TO_AGENT = {k: a["name"] for a in AGENTS for k in a["kinds"]}

BANNED = ["guaranteed", "get rich", "risk-free", "risk free", "passive income", "per day", "100%", "earn $"]
CTA_WORDS = ["sign up", "subscribe", "try", "get started", "join", "download"]


def connect(db_path=None):
    conn = improver.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, payload TEXT,
        status TEXT DEFAULT 'queued', attempts INTEGER DEFAULT 0, agent TEXT,
        result TEXT, error TEXT, priority INTEGER DEFAULT 5, finished_ts TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS draft_meta (
        approval_id INTEGER PRIMARY KEY, version_id INTEGER, synced INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS heartbeat (
        name TEXT PRIMARY KEY, ts TEXT, info TEXT)""")
    # Performance: index for claim_next() hot path
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority, id)")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "started_ts" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN started_ts TEXT")
    if "machine_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN machine_id TEXT")
    hb_cols = [r[1] for r in conn.execute("PRAGMA table_info(heartbeat)").fetchall()]
    if "machine_id" not in hb_cols:
        conn.execute("ALTER TABLE heartbeat ADD COLUMN machine_id TEXT")
    conn.commit()
    return conn


# ---- queue -------------------------------------------------------------------
def enqueue(kind, payload=None, priority=5, db_path=None):
    conn = connect(db_path)
    cur = conn.execute("INSERT INTO tasks (ts, kind, payload, priority) VALUES (?, ?, ?, ?)",
                       (datetime.datetime.now().isoformat(), kind, json.dumps(payload or {}), priority))
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return task_id


def claim_next(db_path=None):
    conn = connect(db_path)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT id, kind, payload, attempts FROM tasks WHERE status = 'queued' "
                       "ORDER BY priority, id LIMIT 1").fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return None
    task_id, kind, payload, attempts = row
    agent = KIND_TO_AGENT.get(kind, "CEO")
    conn.execute("UPDATE tasks SET status = 'running', attempts = attempts + 1, agent = ?, started_ts = ?, machine_id = ? "
                 "WHERE id = ?", (agent, datetime.datetime.now().isoformat(), MACHINE_ID, task_id))
    conn.commit()
    conn.close()
    return {"id": task_id, "kind": kind, "payload": json.loads(payload or "{}"),
            "attempts": attempts + 1, "agent": agent}


def complete(task_id, result, db_path=None):
    conn = connect(db_path)
    conn.execute("UPDATE tasks SET status = 'done', result = ?, finished_ts = ?, error = NULL WHERE id = ?",
                 (json.dumps(result), datetime.datetime.now().isoformat(), task_id))
    conn.commit()
    conn.close()


def fail(task_id, attempts, error, db_path=None):
    status = "queued" if attempts < MAX_ATTEMPTS else "failed"
    conn = connect(db_path)
    conn.execute("UPDATE tasks SET status = ?, error = ?, finished_ts = ? WHERE id = ?",
                 (status, str(error)[:500], datetime.datetime.now().isoformat(), task_id))
    conn.commit()
    conn.close()


# ---- review rubric (plain rules, no LLM) ------------------------------------------
def review_text(text):
    score, notes = 100, []
    lower = text.lower()
    for phrase in BANNED:
        if phrase in lower:
            score -= 25
            notes.append(f"risky claim: '{phrase}'")
    if not any(w in lower for w in CTA_WORDS):
        score -= 15
        notes.append("no clear call to action")
    if len(text) < 200:
        score -= 20
        notes.append("too short")
    elif len(text) > 1500:
        score -= 10
        notes.append("too long")
    return max(0, score), notes


# ---- QA Checker (large model, strict JSON) --------------------------------------
CHECKER_VERDICTS = ("pass", "revise", "reject")
CHECKER_PROMPT = """You are the QA Checker for a small digital-product team. Check this draft before a human sees it.
Be strict and honest. Rubric:
1. Factual errors, made-up numbers, statistics or reviews.
2. Risky claims: income or earnings promises, health or medical claims, job or interview guarantees.
3. Missing parts the brief asked for: product contents/structure, listing title, listing description, 5 search tags.
4. Price in USD, target buyer and selling platform are stated.
5. Clear, plain writing.
6. Fits global buyers who pay in USD.
Verdict: "pass" = ready for a human, "revise" = fixable with one rewrite, "reject" = unsafe or unusable.
Reply with ONLY this JSON and nothing else:
{{"score": <0-100>, "verdict": "pass" | "revise" | "reject", "issues": ["short issue", "..."]}}

Brief: {topic}

Draft:
{draft}"""
REWRITE_PROMPT = """Rewrite the product draft below so it fixes every issue the QA Checker found.
Keep what already works. Stay honest: no income, health or job promises, no made-up numbers.

Brief: {topic}

Issues to fix:
{issues}

Research notes:
{research}

Draft to rewrite:
{draft}"""
REWRITTEN_TAG = "(rewritten)"


def parse_checker_reply(text):
    """Read the checker's JSON reply. Returns {"score", "verdict", "issues"}, or None when unusable."""
    if not isinstance(text, str):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict", "")).strip().lower()
    score = data.get("score")
    if verdict not in CHECKER_VERDICTS or isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = [issues]
    issues = [str(i).strip()[:200] for i in issues if str(i).strip()][:5]
    return {"score": max(0, min(100, int(round(score)))), "verdict": verdict, "issues": issues}


def check_draft(ctx, draft, topic=""):
    """Ask the large model to check a draft. None means the checker is unavailable."""
    prompt = CHECKER_PROMPT.format(topic=topic or "not given", draft=(draft or "")[:6000])
    try:
        return parse_checker_reply(_ask(ctx, prompt, size="large"))
    except Exception:
        return None


# ---- step implementations ---------------------------------------------------------
MAX_PENDING_APPROVALS = 10
ANGLES = ["for first-time visitors", "for people short on time", "for people who prefer step-by-step help"]


def _config_int(key, default):
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return int(json.load(f).get(key, default))
    except Exception:
        return default


def _ask(ctx, prompt, size=None):
    """Call the LLM with a size hint ("small"/"large") when the callable supports it.

    Test stubs and older callables that take only (prompt) still work.
    """
    fn = ctx["llm"]
    if size is not None:
        try:
            params = inspect.signature(fn).parameters
            if "size" in params or any(p.kind == p.VAR_KEYWORD for p in params.values()):
                return fn(prompt, size=size)
        except (TypeError, ValueError):
            pass
    return fn(prompt)


def step_plan(task, ctx):
    """Plan the day's drafts. Stops adding drafts when too many wait for the human."""
    topic = task["payload"].get("topic") or DEFAULT_TOPIC
    waiting = len(guardrails.pending(ctx["db_path"]))
    wanted = max(1, min(len(ANGLES), _config_int("daily_drafts", 2)))
    if waiting >= MAX_PENDING_APPROVALS:
        return {"summary": f"Skipped: {waiting} drafts already wait for approval", "next": []}
    nxt = [{"kind": "research", "payload": {"topic": f"{topic} ({ANGLES[i]})"}} for i in range(wanted)]
    return {"summary": f"Plan: {wanted} draft(s) for '{topic}'", "next": nxt}


def step_research(task, ctx):
    topic = task["payload"]["topic"]
    _, template = improver.get_active("research", ctx["db_path"])

    # Inject remembered facts so the agent learns from past runs
    try:
        import agent_memory as _mem
        mem_block = _mem.facts_for_prompt("research", ctx.get("db_path"))
        if not mem_block:
            mem_block = _mem.facts_for_prompt("approved", ctx.get("db_path"))
    except Exception:
        mem_block = ""

    # Pull live content/weather feed digest (best-effort, never blocks)
    feed_ctx = ""
    try:
        import feeds as _feeds
        feed_ctx = _feeds.for_prompt(max_per_feed=2)
    except Exception:
        pass

    # Fetch real web context for the topic (best-effort, never blocks)
    web_ctx = ""
    try:
        import web_fetcher as _wf
        search_url = (
            "https://www.google.com/search?q="
            + topic.replace(" ", "+").replace("(", "").replace(")", "")[:120]
            + "&num=3"
        )
        words, err = _wf.fetch_words(search_url, max_words=300)
        if words and not err:
            web_ctx = "\nWeb context (Google snippet):\n" + words
    except Exception:
        pass

    prompt = template.format(topic=topic)
    if mem_block:
        prompt = mem_block + "\n\n" + prompt
    if feed_ctx:
        prompt += "\n\n" + feed_ctx
    if web_ctx:
        prompt += web_ctx

    text = _ask(ctx, prompt, size="small")
    mode = "llm"
    if not text:
        mode = "fallback"
        text = (f"1. Define who the page is for ({topic}).\n2. List 3 problems it solves.\n"
                "3. Pick one simple feature to build first.\n4. Decide how to measure interest (visits, signups).\n"
                "5. Write the page in plain language, no income promises.")
    return {"text": text, "mode": mode,
            "next": [{"kind": "draft", "payload": {"topic": topic, "research": text[:1500]}}]}


def step_draft(task, ctx):
    topic = task["payload"]["topic"]
    version_id, template = improver.pick_for_trial("draft", ctx["rng"], ctx["db_path"])

    # Inject approved content patterns from memory
    try:
        import agent_memory as _mem
        mem_block = _mem.facts_for_prompt("approved", ctx.get("db_path"))
        if not mem_block:
            mem_block = _mem.facts_for_prompt("draft", ctx.get("db_path"))
    except Exception:
        mem_block = ""

    draft_prompt = template.format(topic=topic) + "\nContext notes:\n" + task["payload"].get("research", "")
    if mem_block:
        draft_prompt = mem_block + "\n\n" + draft_prompt
    text = _ask(ctx, draft_prompt, size="large")
    mode = "llm"
    if not text:
        mode = "fallback"
        text = (f"# Free tool: {topic[:60]}\n\nBenefit 1: Saves time on a boring task.\n"
                "Benefit 2: Works in your browser, no account needed.\nBenefit 3: Free to try today.\n\n"
                "Get started: try the tool now and subscribe for updates when new tools launch.")
    approval_id = guardrails.request_approval("public_draft", f"Draft: {topic[:60]} (v{version_id}, {mode})",
                                              text, db_path=ctx["db_path"])
    conn = connect(ctx["db_path"])
    conn.execute("INSERT OR REPLACE INTO draft_meta (approval_id, version_id) VALUES (?, ?)", (approval_id, version_id))
    conn.commit()
    conn.close()
    return {"approval_id": approval_id, "version_id": version_id, "mode": mode,
            "next": [{"kind": "review", "payload": {"approval_id": approval_id, "version_id": version_id,
                                                    "topic": topic,
                                                    "research": task["payload"].get("research", "")}}]}


def step_review(task, ctx):
    """QA Checker: rule checklist plus a large-model check.

    pass -> waits for the human. revise -> ONE rewrite is queued (never for a rewrite).
    reject -> stays in the queue marked "CHECKER: REJECT". Only a human approves or rejects.
    """
    payload = task["payload"]
    approval_id = payload["approval_id"]
    approval = guardrails.get_approval(approval_id, ctx["db_path"])
    rule_score, notes = review_text(approval["payload"])
    topic = payload.get("topic") or approval.get("title", "")
    is_rewrite = bool(payload.get("rewrite"))
    check = check_draft(ctx, approval["payload"], topic)
    nxt, issues = [], []

    if check is None:
        verdict = "unavailable"
        score = rule_score
        review = (f"score {score}/100" + (": " + "; ".join(notes) if notes else "")
                  + " | CHECKER: unavailable (rule check only)")
    else:
        verdict = check["verdict"]
        if verdict == "pass" and any(n.startswith("risky") for n in notes):
            verdict = "revise"  # the plain rules found a banned phrase the model let through
        score = round((rule_score + check["score"]) / 2)
        issues = check["issues"] + [n for n in notes if n not in check["issues"]]
        review = f"score {score}/100 (rules {rule_score}, checker {check['score']}) | CHECKER: {verdict.upper()}"
        if verdict == "revise":
            if is_rewrite:
                review += " | already rewritten once, a human decides"
            elif approval.get("status", "pending") != "pending":
                review += " | no rewrite, a human already decided"
            else:
                review += " | one rewrite queued"
                nxt.append({"kind": "rewrite", "payload": {
                    "approval_id": approval_id, "version_id": payload.get("version_id"), "topic": topic,
                    "research": payload.get("research", ""), "issues": issues}})
        if issues:
            review += " | issues: " + "; ".join(issues[:5])
    guardrails.set_review(approval_id, review, ctx["db_path"])
    if not is_rewrite:
        # A rewrite says nothing new about the prompt version, so only first drafts feed the improver
        improver.record_result(payload["version_id"], score, ctx["db_path"])

    # Remember high-scoring patterns so future drafts can learn from them
    if score >= 70:
        try:
            import agent_memory as _mem
            summary = f"Score {score}/100 on v{task['payload']['version_id']}"
            if notes:
                summary += " — passed: " + "; ".join(n for n in notes if not n.startswith("risky") and not n.startswith("no ") and not n.startswith("too "))
            _mem.add_fact("QA Checker", "approved", summary, confidence=score / 100.0, db_path=ctx.get("db_path"))
        except Exception:
            pass

    return {"score": score, "notes": notes, "checker": verdict, "issues": issues, "next": nxt}


def step_rewrite(task, ctx):
    """Draft Worker: the single rewrite the QA Checker asked for. Replaces the pending draft in place."""
    payload = task["payload"]
    approval_id = payload["approval_id"]
    approval = guardrails.get_approval(approval_id, ctx["db_path"])
    if not approval or approval["status"] != "pending":
        return {"summary": "skipped: a human already decided this draft"}
    if approval["title"].endswith(REWRITTEN_TAG):
        return {"summary": "skipped: this draft was already rewritten once"}
    issues = "\n".join(f"- {i}" for i in payload.get("issues", [])) or "- make it clearer and more complete"
    prompt = REWRITE_PROMPT.format(topic=payload.get("topic", ""), issues=issues,
                                   research=payload.get("research", ""), draft=approval["payload"][:6000])
    text = _ask(ctx, prompt, size="large")
    if not text:
        guardrails.set_review(approval_id, (approval["review"] or "") + " | rewrite failed, original kept",
                              ctx["db_path"])
        return {"mode": "fallback", "summary": "rewrite failed, original draft kept"}
    conn = guardrails.connect(ctx["db_path"])
    conn.execute("UPDATE approvals SET payload = ?, title = ? WHERE id = ? AND status = 'pending'",
                 (text, f"{approval['title']} {REWRITTEN_TAG}", approval_id))
    conn.commit()
    conn.close()
    return {"mode": "llm", "approval_id": approval_id,
            "next": [{"kind": "review", "payload": {"approval_id": approval_id, "rewrite": 1,
                                                    "version_id": payload.get("version_id"),
                                                    "topic": payload.get("topic", "")}}]}


def step_code_proposal(task, ctx):
    import coder
    return coder.step_code_proposal(task, ctx)


STEPS = {"plan": step_plan, "research": step_research, "draft": step_draft, "review": step_review,
         "rewrite": step_rewrite, "code_proposal": step_code_proposal}


# ---- learning from human decisions ------------------------------------------------
def sync_human_decisions(db_path=None):
    conn = connect(db_path)
    rows = conn.execute("SELECT m.approval_id, m.version_id, a.status FROM draft_meta m "
                        "JOIN approvals a ON a.id = m.approval_id "
                        "WHERE m.synced = 0 AND a.status IN ('approved', 'rejected')").fetchall()
    for approval_id, version_id, status in rows:
        improver.record_human_decision(version_id, status == "approved", db_path)
        conn.execute("UPDATE draft_meta SET synced = 1 WHERE approval_id = ?", (approval_id,))
    conn.commit()
    conn.close()
    return len(rows)


# ---- crash recovery and heartbeat -------------------------------------------------
def recover_stuck(db_path=None, max_age_minutes=10):
    """A task left 'running' after a crash goes back to the queue (it keeps its attempt count)."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(minutes=max_age_minutes)).isoformat()
    conn = connect(db_path)
    cur = conn.execute("UPDATE tasks SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'queued' END, "
                       "error = 'recovered after a crash or stop' "
                       "WHERE status = 'running' AND (started_ts IS NULL OR started_ts < ?)",
                       (MAX_ATTEMPTS, cutoff))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def heartbeat(info="alive", db_path=None, name="worker"):
    conn = connect(db_path)
    conn.execute("INSERT INTO heartbeat (name, ts, info, machine_id) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(name) DO UPDATE SET ts = excluded.ts, info = excluded.info, machine_id = excluded.machine_id",
                 (name, datetime.datetime.now().isoformat(timespec="seconds"), info, MACHINE_ID))
    conn.commit()
    conn.close()


def worker_status(db_path=None, stale_after_seconds=180, name="worker"):
    conn = connect(db_path)
    row = conn.execute("SELECT ts, info FROM heartbeat WHERE name = ?", (name,)).fetchone()
    conn.close()
    if not row:
        return {"running": False, "last_seen": None, "age_seconds": None, "info": "never started"}
    age = (datetime.datetime.now() - datetime.datetime.fromisoformat(row[0])).total_seconds()
    running = age <= stale_after_seconds and not row[1].startswith("stopped")
    return {"running": running, "last_seen": row[0], "age_seconds": int(age), "info": row[1]}


# ---- runner -----------------------------------------------------------------------
def default_llm(db_path=None):
    """Return a callable that routes prompts through the LLM fallback chain.

    Order: Groq → Gemini (free) → Cloudflare Workers AI.
    Falls back to legacy engine.generate_with_ollama for local dev if all cloud
    providers fail and an ollama_model is configured.
    """
    def call(prompt, size=None):
        import llm_router  # stdlib only, lazy import keeps tests fast
        result = llm_router.chat(prompt, db_path=db_path, size=size)
        if result:
            return result
        # Last-resort: local Ollama (dev only, ignored in CI)
        try:
            import engine
            return engine.AutonomousResearchEngine.generate_with_ollama(prompt)
        except Exception:
            return None
    return call


def run_once(max_tasks=5, db_path=None, llm=None, rng=None):
    """Run up to max_tasks queued tasks. Stops at once if the kill switch is on."""
    if guardrails.is_killed():
        return {"status": "killed", "ran": 0, "failed": 0}
    ctx = {"db_path": db_path, "llm": llm or default_llm(db_path), "rng": rng or random}
    recover_stuck(db_path)
    sync_human_decisions(db_path)
    ran = failed = 0
    for _ in range(max_tasks):
        if guardrails.is_killed():
            return {"status": "killed", "ran": ran, "failed": failed}
        task = claim_next(db_path)
        if not task:
            break
        try:
            result = STEPS[task["kind"]](task, ctx)
            for follow in result.pop("next", []):
                enqueue(follow["kind"], follow["payload"], db_path=db_path)
            complete(task["id"], result, db_path)
            ran += 1
        except Exception as exc:
            fail(task["id"], task["attempts"], exc, db_path)
            failed += 1
    return {"status": "ok", "ran": ran, "failed": failed}


def ensure_daily_plan(db_path=None):
    """Enqueue at most one plan task per day, so the cloud runner never floods the queue."""
    conn = connect(db_path)
    day = datetime.date.today().isoformat()
    exists = conn.execute("SELECT 1 FROM tasks WHERE kind = 'plan' AND ts LIKE ?", (day + "%",)).fetchone()
    conn.close()
    if exists:
        return None
    topic = DEFAULT_TOPIC
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            topic = json.load(f).get("current_goal") or topic
    except Exception:
        pass
    return enqueue("plan", {"topic": topic}, priority=1, db_path=db_path)


def agent_states(db_path=None):
    conn = connect(db_path)
    out = []
    for a in AGENTS:
        running = conn.execute("SELECT COUNT(*) FROM tasks WHERE agent = ? AND status = 'running'",
                               (a["name"],)).fetchone()[0]
        last = conn.execute("SELECT status FROM tasks WHERE agent = ? ORDER BY id DESC LIMIT 1",
                            (a["name"],)).fetchone()
        done = conn.execute("SELECT COUNT(*) FROM tasks WHERE agent = ? AND status = 'done'",
                            (a["name"],)).fetchone()[0]
        state = "working" if running else ("error" if last and last[0] == "failed" else "idle")
        out.append({**a, "state": state, "tasks_done": done})
    conn.close()
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        ensure_daily_plan()
        print(run_once(max_tasks=int(sys.argv[2]) if len(sys.argv) > 2 else 5))
    elif cmd == "queue":
        conn = connect()
        for row in conn.execute("SELECT id, kind, status, agent, attempts FROM tasks ORDER BY id DESC LIMIT 20"):
            print(row)
        conn.close()
    else:
        print("usage: workforce.py run [max_tasks] | queue")
