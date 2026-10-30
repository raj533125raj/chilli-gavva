"""Task queue and worker agents, organised like the office pyramid.

High  : CEO            plans the work
Mid   : Research Manager, Content Manager, QA Reviewer
Entry : the workers that actually do the steps (research, draft, QA check)

Everything is stored in SQLite, so a crash or a restart resumes where it stopped.
Nothing here publishes anything: drafts go to the approval queue (guardrails.py).
"""
import secret_guard
import datetime
import inspect
import json
import os
import random
import re
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

# Every draft is a product spec + store listing in these sections (checked by review_text and the
# QA Checker, used by product_builder.py). Added after the evolvable prompt so it can never drift.
LISTING_SECTIONS = ["product name", "buyer", "price", "platform", "what's inside", "how to use",
                    "listing title", "listing description", "search tags"]
DRAFT_FORMAT = """

Answer with exactly these sections, each starting on its own line with the label in capitals:
PRODUCT NAME: one line
BUYER: who it is for and the problem it solves
PRICE: a price in USD, for example "7 USD"
PLATFORM: Gumroad
WHAT'S INSIDE: the full structure (tabs, pages or sections, with the columns or fields of each)
HOW TO USE: 3-5 short steps
LISTING TITLE: under 140 characters
LISTING DESCRIPTION: 2-4 short paragraphs for buyers anywhere in the world
SEARCH TAGS: exactly 5 tags, separated by commas
Be honest: no income, health, medical or job promises, and no made-up numbers or reviews."""

# Topics the agents must never research or draft (old food / fishery recipes and topic pools).
OFF_LIMITS = re.compile(r"\b(fish(?:es|ing|ery|eries)?|shrimps?|prawns?|aquaculture|tilapia|salmon|seafood|"
                        r"foods?|recipes?|cook(?:s|ing|ed)?|spices?|masalas?|curry|curries|chutneys?|tiffin|"
                        r"kitchens?|dosa|meals?)\b", re.IGNORECASE)


def clean_topic(topic):
    """Drop the "[Budget Sheets Agent]" label: it belongs in the draft title only, not in prompts."""
    return re.sub(r"^\s*\[[^\]]*\]\s*", "", topic or "").strip()


def off_limits(topic):
    m = OFF_LIMITS.search(topic or "")
    return m.group(0) if m else None


def connect(db_path=None):
    import cubes
    conn = improver.connect(db_path)
    cubes.ensure_tables(conn)
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
    import cubes
    if not guardrails.automation_allowed(db_path):
        return None
    conn = connect(db_path)
    conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute("SELECT id, kind, payload, attempts FROM tasks WHERE status = 'queued' "
                        "ORDER BY priority, id LIMIT 50").fetchall()
    row = None
    for r in rows:
        r_kind = r[1]
        if r_kind == "office_task":
            import office_tasks
            if not office_tasks.task_enabled(json.loads(r[2]), db_path, conn):
                continue
        if cubes.is_kind_enabled(r_kind, db_path=db_path, conn=conn):
            row = r
            break
    if not row:
        conn.rollback()
        conn.close()
        return None
    task_id, kind, payload, attempts = row
    agent = KIND_TO_AGENT.get(kind, "CEO")
    if kind == "office_task":
        import office_tasks
        agent = office_tasks.agent(json.loads(payload)["agent_id"])["name"]
    conn.execute("UPDATE tasks SET status = 'running', attempts = attempts + 1, agent = ?, started_ts = ?, machine_id = ? "
                 "WHERE id = ?", (agent, datetime.datetime.now().isoformat(), MACHINE_ID, task_id))
    conn.commit()
    conn.close()
    return {"id": task_id, "kind": kind, "payload": json.loads(payload or "{}"),
            "attempts": attempts + 1, "agent": agent}


def complete(task_id, result, db_path=None):
    conn = connect(db_path)
    conn.execute("UPDATE tasks SET status = 'done', result = ?, finished_ts = ?, error = NULL WHERE id = ?",
                 (json.dumps(secret_guard.scrub_data(result)), datetime.datetime.now().isoformat(), task_id))
    conn.commit()
    conn.close()


class AIUnavailable(Exception):
    """No AI model answered. The task is put back in the queue for a later run (up to MAX_ATTEMPTS)."""


AI_DOWN_MESSAGE = "Chilli Gavva: AI is down, will retry later. No placeholder drafts were made."


def _alert_ai_down(db_path=None):
    """Send ONE Telegram alert per UTC day when no AI model answers. Never raises."""
    try:
        import notify
        day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        conn = connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS ai_alerts (day TEXT PRIMARY KEY)")
        seen = conn.execute("SELECT 1 FROM ai_alerts WHERE day = ?", (day,)).fetchone()
        conn.close()
        if seen:
            return False
        if not notify.send(AI_DOWN_MESSAGE):
            return False  # Telegram not set up or not reachable: try again on a later run
        conn = connect(db_path)
        conn.execute("INSERT OR IGNORE INTO ai_alerts (day) VALUES (?)", (day,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def fail(task_id, attempts, error, db_path=None):
    status = "queued" if attempts < MAX_ATTEMPTS else "failed"
    conn = connect(db_path)
    conn.execute("UPDATE tasks SET status = ?, error = ?, finished_ts = ? WHERE id = ?",
                 (status, secret_guard.scrub_text(error)[:500], datetime.datetime.now().isoformat(), task_id))
    conn.commit()
    row = conn.execute("SELECT agent, kind FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()

    try:
        import doctor
        agent = (row[0] if row and row[0] else "Unknown Agent")
        step = (row[1] if row and row[1] else "unknown_step")
        doctor.diagnose_and_remedy(agent=agent, step=step, error=error, task_id=task_id, db_path=db_path)
    except Exception:
        pass


# ---- review rubric (plain rules, no LLM) ------------------------------------------
def _has_section(lower, label):
    return re.search(r"(?m)^\W*" + re.escape(label).replace("'", "'?") + r"\W*:", lower) is not None


def count_tags(text):
    """How many tags follow the SEARCH TAGS label: one comma (or hashtag) list on the label line,
    or a bullet list on the lines below it. 0 when there is no such section."""
    text = text.replace("\u2019", "'")
    m = re.search(r"(?im)^\W*search tags\W*:[ \t]*(.*)$", text)
    if not m:
        return 0
    first = m.group(1).strip()
    if first:
        parts = re.split(r"[,;]", first)
        if len(parts) == 1 and first.count("#") > 1:
            parts = first.split("#")
        return len([t for t in parts if t.strip(" -*#\u2022\t.")])
    count = 0
    for line in text[m.end():].splitlines():
        line = line.strip()
        if not line:
            if count:
                break
            continue
        if not re.match(r"^([-*#\u2022]|\d+[.)])", line):
            break
        count += 1
    return count


def review_text(text):
    """Plain-rule check of a product spec + store listing. Returns (score 0-100, notes)."""
    score, notes = 100, []
    lower = text.lower().replace("\u2019", "'")
    for phrase in BANNED:
        if phrase in lower:
            score -= 25
            notes.append(f"risky claim: '{phrase}'")
    missing = [s for s in LISTING_SECTIONS if not _has_section(lower, s)]
    if missing:
        score -= min(40, 8 * len(missing))
        notes.append("missing: " + ", ".join(missing))
    if not re.search(r"\$\s?\d|\d\s?(?:usd|us\$|dollars?)\b", lower):
        score -= 10
        notes.append("no USD price")
    tags = count_tags(text)
    if "search tags" not in missing and tags != 5:
        score -= 5
        notes.append(f"{tags} search tags (need 5)")
    if len(text) < 400:
        score -= 20
        notes.append("too short")
    elif len(text) > 6000:
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
Keep what already works and keep the same labelled sections (PRODUCT NAME ... SEARCH TAGS).
Stay honest: no income, health or job promises, no made-up numbers.

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
    blocked = off_limits(topic)
    if blocked:  # old food / fishery recipes and topic pools: never sent to the model
        return {"summary": f"skipped: off-limits topic ({blocked})", "next": []}
    _, template = improver.get_active("research", ctx["db_path"])

    # Inject remembered facts so the agent learns from past runs
    try:
        import agent_memory as _mem
        mem_block = _mem.facts_for_prompt("research", ctx.get("db_path"))
        if not mem_block:
            mem_block = _mem.facts_for_prompt("approved", ctx.get("db_path"))
    except Exception:
        mem_block = ""

    # No web scraping and no news/weather feeds: the model works from the topic and memory only.
    product = clean_topic(topic)
    prompt = template.format(topic=product)
    if mem_block:
        prompt = mem_block + "\n\n" + prompt

    text = _ask(ctx, prompt, size="small")
    if not text:
        raise AIUnavailable("research: no answer from any AI model")
    return {"text": text, "mode": "llm",
            "next": [{"kind": "draft", "payload": {"topic": topic, "research": text[:1500]}}]}


def step_draft(task, ctx):
    topic = task["payload"]["topic"]
    blocked = off_limits(topic)
    if blocked:
        return {"summary": f"skipped: off-limits topic ({blocked})", "next": []}
    product = clean_topic(topic)
    version_id, template = improver.pick_for_trial("draft", ctx["rng"], ctx["db_path"])

    # Inject approved content patterns from memory
    try:
        import agent_memory as _mem
        mem_block = _mem.facts_for_prompt("approved", ctx.get("db_path"))
        if not mem_block:
            mem_block = _mem.facts_for_prompt("draft", ctx.get("db_path"))
    except Exception:
        mem_block = ""

    import improver_loop
    rules_block = improver_loop.get_active_rules_block("writer", ctx.get("db_path"))

    draft_prompt = (template.format(topic=product) + DRAFT_FORMAT
                    + "\n\nResearch notes:\n" + task["payload"].get("research", ""))
    if rules_block:
        draft_prompt += "\n" + rules_block
    if mem_block:
        draft_prompt = mem_block + "\n\n" + draft_prompt
    text = _ask(ctx, draft_prompt, size="large")
    if not text:  # never save a placeholder draft: the task goes back in the queue instead
        raise AIUnavailable("draft: no answer from any AI model")
    mode = "llm"
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
    """QA Checker / Checker Agent: review draft against docs quality rules and manage retries.

    Drafts under 70 go back to writer for rewrite automatically (max 2 retries).
    """
    payload = task["payload"]
    approval_id = payload["approval_id"]
    approval = guardrails.get_approval(approval_id, ctx["db_path"])
    topic = payload.get("topic") or (approval.get("title", "") if approval else "")
    retries = payload.get("retries", 0)
    nxt = []

    import qa_checklist
    import checker
    import product_council

    draft_text = (approval.get("payload") or "") if approval else ""

    # Product Council pipeline: debate and refine draft before checker scoring
    council_res = product_council.run_council(approval_id, draft_text, clean_topic(topic), ctx=ctx, db_path=ctx["db_path"])
    if council_res and council_res.get("draft_text"):
        draft_text = council_res["draft_text"]

    qa_res = qa_checklist.score(draft_text)
    qa_score = qa_res["score"]
    _, notes = review_text(draft_text)

    check = checker.check_draft(draft_text, clean_topic(topic), ctx=ctx, db_path=ctx["db_path"])
    score = check["score"]
    verdict = check["verdict"]
    reasons = check["reasons"]
    fallback = check.get("fallback", False)

    if verdict == "pass" and any(n.startswith("risky") for n in notes):
        verdict = "revise"
        score = min(score, 40)

    if fallback:
        review = f"score {qa_score}/100 (checklist v2) | Checker: {score}/100 | CHECKER: unavailable (checklist only)"
    else:
        review = f"score {qa_score}/100 (checklist v2) | Checker: {score}/100 | CHECKER: {verdict.upper()} (AI {score})"

    if reasons:
        review += " | top: " + "; ".join(reasons[:2])
        review += " | issues: " + "; ".join(reasons[:5])

    if verdict == "revise" or (score < 70 and verdict != "reject"):
        if retries >= 2 or payload.get("rewrite"):
            review += " | already rewritten once, a human decides"
        elif approval and approval.get("status", "pending") != "pending":
            review += " | no rewrite, a human already decided"
        else:
            review += " | one rewrite queued"
            nxt.append({"kind": "rewrite", "payload": {
                "approval_id": approval_id,
                "version_id": payload.get("version_id"),
                "topic": topic,
                "research": payload.get("research", ""),
                "issues": reasons,
                "retries": retries + 1,
                "rewrite": retries + 1
            }})

    guardrails.set_review(approval_id, review, ctx["db_path"])

    version_id = payload.get("version_id")
    if version_id and retries == 0:
        improver.record_result(version_id, score, ctx["db_path"])

    if score >= 70:
        try:
            import agent_memory as _mem
            summary = f"Score {score}/100 on v{version_id}"
            _mem.add_fact("QA Checker", "approved", summary, confidence=score / 100.0, db_path=ctx.get("db_path"))
        except Exception:
            pass

    return {"score": score, "checker": verdict, "issues": reasons, "retries": retries, "next": nxt}


def step_rewrite(task, ctx):
    """Draft Worker: rewrite draft addressing checker's reasons. Replaces pending draft in place."""
    payload = task["payload"]
    approval_id = payload["approval_id"]
    retries = payload.get("retries", 1)
    approval = guardrails.get_approval(approval_id, ctx["db_path"])
    if not approval or approval["status"] != "pending":
        return {"summary": "skipped: a human already decided this draft"}

    import improver_loop
    rules_block = improver_loop.get_active_rules_block("writer", ctx.get("db_path"))

    issues = "\n".join(f"- {i}" for i in payload.get("issues", [])) or "- make it clearer and more complete"
    prompt = REWRITE_PROMPT.format(topic=clean_topic(payload.get("topic", "")), issues=issues,
                                   research=payload.get("research", ""), draft=approval["payload"][:6000])
    if rules_block:
        prompt += "\n" + rules_block
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
            "next": [{"kind": "review", "payload": {"approval_id": approval_id, "retries": retries,
                                                    "version_id": payload.get("version_id"),
                                                    "topic": payload.get("topic", ""),
                                                    "research": payload.get("research", "")}}]}


def step_code_proposal(task, ctx):
    import coder
    return coder.step_code_proposal(task, ctx)


def step_office_task(task, ctx):
    import office_tasks
    return office_tasks.execute(task, ctx)


STEPS = {"plan": step_plan, "research": step_research, "draft": step_draft, "review": step_review,
         "rewrite": step_rewrite, "code_proposal": step_code_proposal, "office_task": step_office_task}


# ---- learning from human decisions ------------------------------------------------
def sync_human_decisions(db_path=None):
    conn = connect(db_path)
    rows = conn.execute("SELECT m.approval_id, m.version_id, a.status FROM draft_meta m "
                        "JOIN approvals a ON a.id = m.approval_id "
                        "WHERE m.synced = 0 AND a.status IN ('approved', 'rejected')").fetchall()
    for approval_id, version_id, status in rows:
        # Same connection, one commit per draft: the count and the "synced" mark are saved together
        # (a second connection here was locked out by this one's open write: "database is locked").
        improver.record_human_decision(version_id, status == "approved", db_path, conn=conn)
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
                 (name, datetime.datetime.now().isoformat(timespec="seconds"), secret_guard.scrub_text(info), MACHINE_ID))
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
    def scoped(prompt, size=None):
        with guardrails.budget_context(db_path):
            return call(prompt, size)
    return scoped


import time

MAX_RUN_TIME_SECONDS = 480  # Stop starting new tasks after ~8 minutes per run


CUBE_BASE_QUOTAS = {
    "executive": {"plan": 1},
    "research": {"research": 4},
    "content": {"drafts": 3},
    "qa": {"review": 3},
    "product": {"product_builds": 2},
    "marketing": {"promotion": 2},
}


def get_daily_pipeline_plan(db_path=None):
    """Generate a daily plan per enabled cube with quotas.

    Adapts to AI daily cap and provider health (never exceeds cap, scales down when providers are down/cooldown).
    Standard base quota per enabled cube department: research 4, drafts 3, council/checker 3, product builds 2.
    """
    import cubes
    import llm_router

    cap = guardrails.daily_cap(db_path)
    calls = guardrails.calls_today(db_path)
    remaining_calls = max(0, cap - calls)

    # Assess provider health multiplier
    health_mult = 1.0
    groq_down = llm_router.in_cooldown("Groq", db_path)
    gemini_down = llm_router.in_cooldown("Gemini", db_path)

    if groq_down and gemini_down:
        health_mult = 0.25
    elif groq_down or gemini_down:
        health_mult = 0.6

    # Cap multiplier
    cap_mult = 1.0
    if remaining_calls <= 0:
        cap_mult = 0.0
    elif remaining_calls < 20:
        cap_mult = 0.2
    elif remaining_calls < 50:
        cap_mult = 0.5

    mult = min(health_mult, cap_mult)

    all_cubes = cubes.list_cubes(include_archived=True, db_path=db_path)

    cube_plans = {}
    all_stages = ["plan", "research", "drafts", "review", "product_builds", "promotion"]
    total_quotas = {s: 0 for s in all_stages}

    for c in all_cubes:
        cid = c["id"]
        is_enabled = c.get("enabled", False)
        base = CUBE_BASE_QUOTAS.get(cid, {})

        quotas = {}
        for stage in all_stages:
            base_q = base.get(stage, 0)
            if is_enabled and mult > 0:
                q_val = max(0, int(round(base_q * mult)))
            else:
                q_val = 0
            quotas[stage] = q_val
            total_quotas[stage] += q_val

        cube_plans[cid] = {
            "cube_name": c["name"],
            "enabled": is_enabled,
            "quotas": quotas,
        }

    return {
        "date": datetime.date.today().isoformat(),
        "cap": cap,
        "calls_today": calls,
        "health_mult": health_mult,
        "cube_plans": cube_plans,
        "total_quotas": total_quotas,
    }


def run_once(max_tasks=5, db_path=None, llm=None, rng=None, max_time_seconds=MAX_RUN_TIME_SECONDS):
    """Run queued tasks safely up to max_tasks or max_time_seconds budget (stops picking new tasks after ~8 mins)."""
    start_time = time.time()
    if guardrails.is_killed():
        return {"status": "killed", "ran": 0, "failed": 0}
    if not guardrails.automation_allowed(db_path):
        return {"status": "manual", "ran": 0, "failed": 0}
    import rest_window
    if rest_window.in_rest_window():
        return {"status": "rest", "ran": 0, "failed": 0}   # daily 6-minute rest: no new AI work or drafts
    import office_tasks
    office_tasks.tick(db_path)
    ctx = {"db_path": db_path, "llm": llm or default_llm(db_path), "rng": rng or random}
    recover_stuck(db_path)
    sync_human_decisions(db_path)
    try:
        import doctor
        doctor.run_doctor_checks(db_path=db_path)
    except Exception:
        pass
    ran = failed = 0
    ai_down = False
    for _ in range(max_tasks):
        if guardrails.is_killed():
            return {"status": "killed", "ran": ran, "failed": failed}
        if not guardrails.automation_allowed(db_path):
            return {"status": "manual", "ran": ran, "failed": failed}
        if time.time() - start_time >= max_time_seconds:
            break
        task = claim_next(db_path)
        if not task:
            break
        try:
            result = STEPS[task["kind"]](task, ctx)
            for follow in result.pop("next", []):
                enqueue(follow["kind"], follow["payload"], db_path=db_path)
            complete(task["id"], result, db_path)
            ran += 1
        except AIUnavailable as exc:
            # Back in the queue for a later run (fail() marks it failed after MAX_ATTEMPTS tries).
            fail(task["id"], task["attempts"], exc, db_path)
            failed += 1
            ai_down = True
            _alert_ai_down(db_path)
            break  # no point trying more AI tasks in this run
        except Exception as exc:
            fail(task["id"], task["attempts"], exc, db_path)
            failed += 1
    out = {"status": "ok", "ran": ran, "failed": failed}
    if ai_down:
        out["ai_down"] = True
    return out


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
