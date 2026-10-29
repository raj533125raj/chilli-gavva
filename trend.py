"""trend.py — TREND agent for collecting new and rising tech, AI tools, and frameworks.

Once a day TREND collects what is new and rising for Chilli Gavva (AI tools, free agent
frameworks, digital product trends) from free public sources reachable without new secrets:
  - GitHub trending/search API (unauthenticated or GITHUB_TOKEN / GITHUB_MODELS_TOKEN if present)
  - Hugging Face trending models/spaces API
  - Public RSS feeds of 3-5 tech/product blogs

Processing:
  1. Dedupes collected items.
  2. Scores each item for usefulness to Chilli Gavva (earning digital products, agent tooling).
  3. Stores top 10 items per day in the database ('trend_items') with a 1-line rationale ('why').
  4. Sends ONE short Telegram digest per day (top 5 with links).
  5. Provides interactive 'Deep dive' button (calls chat_brain/llm_router for summary)
     and 'Propose' button (creates an Approve/Reject proposal card in guardrails approval queue).
  6. Public status gets ONLY a count (never unapproved trend item details or URLs).
  7. Never installs or adds anything automatically. Nothing changes without human approval.
"""

import datetime
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

import guardrails
import notify

TREND_STATE_KEY = "trend_last_run_day"
_TIMEOUT = 10
_UA = {"User-Agent": "ChilliGavvaBot/1.0 (research; +https://chilligavva.com)"}

# Pre-defined RSS feeds (3-5 well-known tech/product sources)
RSS_FEEDS = [
    ("Hacker News", "https://news.ycombinator.com/rss"),
    ("GitHub Blog", "https://github.blog/feed/"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("Product Hunt", "https://www.producthunt.com/feed"),
]

# Fallback trend items when network endpoints are offline or unavailable
FALLBACK_TREND_ITEMS = [
    {
        "title": "Smolagents — Lightweight Agent Framework by Hugging Face",
        "url": "https://github.com/huggingface/smolagents",
        "snippet": "Simple and efficient Python framework for building LLM agents with minimal abstraction.",
        "source": "github",
    },
    {
        "title": "CrewAI — Orchestrating Autonomous AI Agent Teams",
        "url": "https://github.com/crewAIInc/crewAI",
        "snippet": "Framework for orchestrating role-playing, autonomous AI agents for complex tasks.",
        "source": "github",
    },
    {
        "title": "AutoGPT — Open Source Agent Platform",
        "url": "https://github.com/Significant-Gravitas/AutoGPT",
        "snippet": "Accessible AI agent builder and workspace for autonomous multi-agent workflows.",
        "source": "github",
    },
    {
        "title": "DeepSeek R1 Open Weights Reasoning Model",
        "url": "https://huggingface.co/deepseek-ai/DeepSeek-R1",
        "snippet": "Open weights reasoning model trained with large scale reinforcement learning.",
        "source": "huggingface",
    },
    {
        "title": "LangGraph — Multi-Agent Workflow Framework",
        "url": "https://github.com/langchain-ai/langgraph",
        "snippet": "Build resilient language agent applications with stateful multi-actor workflows.",
        "source": "github",
    },
    {
        "title": "Notion API & Template Builder Workflow",
        "url": "https://www.producthunt.com",
        "snippet": "Rising trend in automated Notion templates and digital workspace planning tools.",
        "source": "rss",
    },
]


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS trend_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT,
        title TEXT,
        url TEXT,
        source TEXT,
        snippet TEXT,
        score INTEGER,
        why TEXT,
        status TEXT DEFAULT 'collected',
        approval_id INTEGER,
        ts TEXT)""")
    conn.execute("CREATE TABLE IF NOT EXISTS trend_state (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    return conn


def get_state(key, default=None, db_path=None):
    conn = connect(db_path)
    row = conn.execute("SELECT value FROM trend_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_state(key, value, db_path=None):
    conn = connect(db_path)
    conn.execute("INSERT INTO trend_state (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()
    conn.close()


def _today_iso(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.date().isoformat()


def already_ran_today(db_path=None, now=None):
    last = get_state(TREND_STATE_KEY, default=None, db_path=db_path)
    return last == _today_iso(now)


def record_run_today(db_path=None, now=None):
    set_state(TREND_STATE_KEY, _today_iso(now), db_path=db_path)


# ---- Data Fetching ----

def _http_get(url, headers=None, fetcher=None):
    """Fetch text from URL safely using custom fetcher or urllib."""
    if fetcher is not None:
        try:
            raw, err = fetcher(url)
            return (raw or "") if not err else ""
        except Exception:
            return ""
    req_headers = dict(_UA)
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def fetch_github_trending(fetcher=None) -> list[dict]:
    """Fetch trending GitHub repos via public search API."""
    items = []
    headers = {}
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_MODELS_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    url = "https://api.github.com/search/repositories?q=topic:ai-agent+OR+topic:agent+OR+topic:llm&sort=stars&order=desc&per_page=10"
    raw = _http_get(url, headers=headers, fetcher=fetcher)
    if not raw:
        return items
    try:
        data = json.loads(raw)
        for repo in data.get("items", [])[:10]:
            name = repo.get("full_name") or repo.get("name") or ""
            html_url = repo.get("html_url") or ""
            desc = (repo.get("description") or "").strip()[:200]
            if name and html_url:
                items.append({
                    "title": name,
                    "url": html_url,
                    "snippet": desc or f"GitHub repository {name}",
                    "source": "github",
                })
    except Exception:
        pass
    return items


def fetch_huggingface_trending(fetcher=None) -> list[dict]:
    """Fetch trending Hugging Face models / spaces via API."""
    items = []
    url = "https://huggingface.co/api/models?sort=likes&direction=-1&limit=10"
    raw = _http_get(url, fetcher=fetcher)
    if not raw:
        return items
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            for model in data[:10]:
                model_id = model.get("id") or model.get("modelId") or ""
                if model_id:
                    items.append({
                        "title": f"HF Model: {model_id}",
                        "url": f"https://huggingface.co/{model_id}",
                        "snippet": f"Trending Hugging Face model with likes and downloads.",
                        "source": "huggingface",
                    })
    except Exception:
        pass
    return items


def _parse_rss_xml(text: str) -> list[dict]:
    items = []
    if not text:
        return items
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return items
    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()[:200]
        if title and link:
            items.append({"title": title, "url": link, "snippet": desc})
    # Atom
    if not items:
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            summ = (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip()[:200]
            if title and link:
                items.append({"title": title, "url": link, "snippet": summ})
    return items


def fetch_rss_feeds(fetcher=None) -> list[dict]:
    """Fetch recent items from public tech RSS feeds."""
    items = []
    for source_name, feed_url in RSS_FEEDS:
        raw = _http_get(feed_url, fetcher=fetcher)
        parsed = _parse_rss_xml(raw)
        for entry in parsed[:3]:
            entry["source"] = "rss"
            items.append(entry)
    return items


def fetch_all_raw_trends(fetcher=None) -> list[dict]:
    """Fetch raw trend items across sources; falls back to default candidates if needed."""
    raw_items = []
    try:
        raw_items.extend(fetch_github_trending(fetcher=fetcher))
    except Exception:
        pass
    try:
        raw_items.extend(fetch_huggingface_trending(fetcher=fetcher))
    except Exception:
        pass
    try:
        raw_items.extend(fetch_rss_feeds(fetcher=fetcher))
    except Exception:
        pass

    if len(raw_items) < 10:
        # Fill with fallback candidates
        raw_items.extend(FALLBACK_TREND_ITEMS)

    return raw_items


def dedupe_items(items: list[dict]) -> list[dict]:
    """Deduplicate items by canonical URL or title."""
    seen_urls = set()
    seen_titles = set()
    unique = []
    for it in items:
        url = (it.get("url") or "").strip().lower()
        title = (it.get("title") or "").strip().lower()
        if not title:
            continue
        if url and url in seen_urls:
            continue
        if title in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        seen_titles.add(title)
        unique.append(it)
    return unique


# ---- Scoring & Rationale ----

def rule_based_score(item: dict) -> dict:
    """Rule-based fallback scoring for usefulness to Chilli Gavva."""
    title = (item.get("title") or "").lower()
    snippet = (item.get("snippet") or "").lower()
    combined = title + " " + snippet

    score = 60
    reasons = []

    if any(k in combined for k in ["agent", "multi-agent", "framework", "autogen", "crewai", "langgraph"]):
        score += 20
        reasons.append("High relevance to agent tooling and workforce automation.")
    if any(k in combined for k in ["template", "planner", "printable", "notion", "digital product", "workflow"]):
        score += 15
        reasons.append("Direct application for digital product creation.")
    if any(k in combined for k in ["llm", "deepseek", "huggingface", "model", "reasoning"]):
        score += 10
        reasons.append("Useful AI model capabilities for content drafting.")

    score = max(10, min(98, score))
    why = reasons[0] if reasons else "Provides insights into current AI tools and digital workflows."

    res = dict(item)
    res["score"] = score
    res["why"] = why
    return res


SCORE_PROMPT = """You are TREND, an AI agent analyzing rising tech tools, agent frameworks, and digital product trends for Chilli Gavva.
Score each item from 1 to 100 on usefulness for Chilli Gavva (building agent tooling and low-cost digital products).
Provide a 1-line rationale ('why') for each item.

Rules:
- Do NOT include any money amounts or currency symbols.
- Reply strictly with a JSON array of objects:
[
  {
    "title": "item title",
    "score": 85,
    "why": "1-line explanation of usefulness to Chilli Gavva"
  }
]

Items to evaluate:
{items_json}"""


def score_items(items: list[dict], llm_fn=None) -> list[dict]:
    """Score candidates using llm_router with rule-based fallback."""
    if not items:
        return []

    scored = []
    if llm_fn is not None:
        try:
            compact = [{"title": it["title"], "snippet": it.get("snippet", "")} for it in items[:12]]
            prompt = SCORE_PROMPT.format(items_json=json.dumps(compact))
            reply = llm_fn(prompt, size="small") if callable(llm_fn) else None
            if reply and "[" in reply and "]" in reply:
                start = reply.find("[")
                end = reply.rfind("]")
                evals = json.loads(reply[start:end + 1])
                eval_map = {e.get("title", "").strip().lower(): e for e in evals if isinstance(e, dict)}
                for it in items:
                    it_copy = dict(it)
                    matched = eval_map.get(it["title"].strip().lower())
                    if matched and isinstance(matched.get("score"), (int, float)):
                        it_copy["score"] = max(1, min(100, int(matched["score"])))
                        it_copy["why"] = str(matched.get("why") or "").strip() or "Relevant for agent tooling."
                        scored.append(it_copy)
                    else:
                        scored.append(rule_based_score(it))
        except Exception:
            scored = []

    if not scored or len(scored) < len(items):
        # Fallback to rule-based scoring for remaining items
        already_handled = {s["title"] for s in scored}
        for it in items:
            if it["title"] not in already_handled:
                scored.append(rule_based_score(it))

    return scored


# ---- Run Cycle & DB Persistence ----

def run_trend(db_path=None, llm=None, fetcher=None) -> list[dict]:
    """Run TREND agent: fetch, dedupe, score, save top 10 in DB, and send 1 Telegram digest."""
    if llm is None:
        try:
            import llm_router
            llm = lambda p, **kw: llm_router.chat(p, db_path=db_path, **kw)
        except Exception:
            llm = None

    raw = fetch_all_raw_trends(fetcher=fetcher)
    unique = dedupe_items(raw)
    scored = score_items(unique, llm_fn=llm)

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    top_10 = scored[:10]

    day_str = _today_iso()
    ts_now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = connect(db_path)

    saved_items = []
    for item in top_10:
        cur = conn.execute(
            "INSERT INTO trend_items (day, title, url, source, snippet, score, why, status, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'collected', ?)",
            (day_str, item["title"], item.get("url", ""), item.get("source", "web"),
             item.get("snippet", ""), item.get("score", 50), item.get("why", ""), ts_now)
        )
        conn.commit()
        item_copy = dict(item)
        item_copy["id"] = cur.lastrowid
        item_copy["day"] = day_str
        saved_items.append(item_copy)

    conn.close()

    # Send ONE short Telegram digest for top 5 items
    send_telegram_digest(saved_items[:5], db_path=db_path)

    sync_approved_trends(db_path)
    return saved_items


def maybe_run(db_path=None, llm=None, now=None, fetcher=None):
    """Run TREND if it has not run today."""
    if already_ran_today(db_path=db_path, now=now):
        sync_approved_trends(db_path)
        return {"ran": False, "reason": "already ran today"}
    items = run_trend(db_path=db_path, llm=llm, fetcher=fetcher)
    record_run_today(db_path=db_path, now=now)
    return {"ran": True, "items": items}


# ---- Telegram Integration & Callbacks ----

def digest_buttons(items: list[dict]):
    """Generate Deep dive and Propose inline buttons for top trend items."""
    buttons = []
    for idx, item in enumerate(items, 1):
        item_id = item["id"]
        row = [
            (f"🔍 Deep dive #{item_id}", f"cb:tr:d:{item_id}"),
            (f"💡 Propose #{item_id}", f"cb:tr:p:{item_id}"),
        ]
        buttons.append(row)
    return buttons


def send_telegram_digest(items: list[dict], db_path=None):
    """Send ONE short Telegram digest with top items and interactivity buttons."""
    if not items:
        return False
    configured, token, chat_id = notify._telegram_configured()
    if not configured:
        return False

    lines = ["🔥 TREND Digest — Today's Top AI Tools & Trends:\n"]
    for idx, it in enumerate(items, 1):
        lines.append(f"{idx}. {it['title']} (Score: {it.get('score', 50)}/100)")
        lines.append(f"   Why: {it.get('why', '')}")
        if it.get("url"):
            lines.append(f"   Link: {it['url']}")
        lines.append("")

    lines.append("Use buttons below for a Deep Dive summary or to Propose an item.")
    text = "\n".join(lines)
    btns = digest_buttons(items)

    import telegram_inbox
    return telegram_inbox.send_text(token, chat_id, text, buttons=btns)


_CALLBACK_PATTERN = re.compile(r"^(?:cb:)?tr:(d|p):(\d{1,9})$")


def handle_callback(token, owner_chat_id, data, db_path=None):
    """Handle Telegram callback queries for Deep dive (summary) and Propose."""
    m = _CALLBACK_PATTERN.match(data or "")
    if not m:
        return None

    kind, item_id_str = m.group(1), m.group(2)
    item_id = int(item_id_str)

    import telegram_inbox
    conn = connect(db_path)
    row = conn.execute(
        "SELECT id, title, url, source, snippet, score, why, status, approval_id FROM trend_items WHERE id = ?",
        (item_id,)
    ).fetchone()
    conn.close()

    if not row:
        reply = f"Trend item #{item_id} not found."
        telegram_inbox.send_text(token, owner_chat_id, reply)
        return reply

    item = {
        "id": row[0], "title": row[1], "url": row[2], "source": row[3],
        "snippet": row[4], "score": row[5], "why": row[6], "status": row[7],
        "approval_id": row[8]
    }

    if kind == "d":
        # Deep dive: request short summary on demand from chat_brain / llm_router
        summary = generate_deep_dive_summary(item, db_path=db_path)
        reply = f"🔍 Deep Dive on #{item['id']} {item['title']}:\n\n{summary}\n\nLink: {item['url']}"
        telegram_inbox.send_text(token, owner_chat_id, reply)
        return reply

    if kind == "p":
        # Propose: create Approve/Reject card in guardrails approval queue
        res = propose_trend_item(item["id"], db_path=db_path)
        if res.get("approval_id"):
            approval_id = res["approval_id"]
            card_text = (f"💡 Proposed Trend Item #{item['id']} as Approval #{approval_id}:\n"
                         f"Title: {item['title']}\n"
                         f"Why: {item['why']}\n"
                         f"Link: {item['url']}\n\n"
                         f"Nothing changes until you approve.")
            btns = telegram_inbox.draft_buttons(approval_id)
            telegram_inbox.send_text(token, owner_chat_id, card_text, buttons=btns)
            reply = f"Proposed #{item['id']} (Created approval card #{approval_id})."
        else:
            reply = res.get("reason", "Could not propose trend item.")
            telegram_inbox.send_text(token, owner_chat_id, reply)
        return reply

    return None


SUMMARY_PROMPT = """You are an AI assistant for Chilli Gavva.
Provide a clear, 2-3 sentence deep-dive summary explaining what this tool or trend is, and how Chilli Gavva can practically leverage it for agent tooling or digital products.

Title: {title}
Snippet: {snippet}
Why: {why}
Link: {url}

Rules:
- Do NOT include any money amounts or currency symbols.
- Keep the summary under 100 words."""


def generate_deep_dive_summary(item: dict, db_path=None) -> str:
    """Generate a short deep dive summary via llm_router with fallback."""
    try:
        import llm_router
        prompt = SUMMARY_PROMPT.format(
            title=item.get("title", ""),
            snippet=item.get("snippet", ""),
            why=item.get("why", ""),
            url=item.get("url", ""),
        )
        reply = llm_router.chat(prompt, db_path=db_path, size="small")
        if reply and len(reply.strip()) > 20:
            return reply.strip()
    except Exception:
        pass

    # Fallback summary
    return (f"{item.get('title')} is a rising resource from {item.get('source', 'the web')}. "
            f"{item.get('why', 'It provides valuable capabilities for AI agent tooling and automation.')} "
            f"Snippet: {item.get('snippet', 'No detailed snippet available.')}")


def propose_trend_item(item_id: int, db_path=None) -> dict:
    """Mark trend item as 'proposed' and request human approval in queue."""
    conn = connect(db_path)
    row = conn.execute("SELECT id, title, url, source, snippet, score, why, status, approval_id "
                       "FROM trend_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "reason": "Item not found."}

    item_status = row[7]
    existing_app_id = row[8]
    if item_status == "proposed" and existing_app_id:
        conn.close()
        return {"ok": True, "approval_id": existing_app_id, "already": True}

    title = f"[Trend Proposal] {row[1]}"
    payload_text = (f"Trend Item #{row[0]}\n"
                    f"Title: {row[1]}\n"
                    f"Source: {row[3]}\n"
                    f"URL: {row[2]}\n"
                    f"Score: {row[5]}/100\n"
                    f"Why: {row[6]}\n"
                    f"Snippet: {row[4]}")
    review_str = f"Score: {row[5]}/100 | {row[6]}"

    approval_id = guardrails.request_approval(
        kind="trend_proposal",
        title=title,
        payload=payload_text,
        review=review_str,
        db_path=db_path
    )

    conn.execute("UPDATE trend_items SET status = 'proposed', approval_id = ? WHERE id = ?",
                 (approval_id, item_id))
    conn.commit()
    conn.close()

    return {"ok": True, "approval_id": approval_id}


def sync_approved_trends(db_path=None):
    """Sync human approval status for trend proposals."""
    conn = connect(db_path)
    rows = conn.execute("SELECT id, approval_id FROM trend_items WHERE status = 'proposed'").fetchall()
    synced_count = 0

    for item_id, approval_id in rows:
        if not approval_id:
            continue
        approval = guardrails.get_approval(approval_id, db_path)
        if not approval:
            continue
        status = approval.get("status")
        if status == "approved":
            conn.execute("UPDATE trend_items SET status = 'approved' WHERE id = ?", (item_id,))
            conn.commit()
            synced_count += 1
        elif status == "rejected":
            conn.execute("UPDATE trend_items SET status = 'rejected' WHERE id = ?", (item_id,))
            conn.commit()
            synced_count += 1

    conn.close()
    return synced_count


def get_trend_items_count(db_path=None) -> dict:
    """Return count of trend items for public status exports (never item text or URLs)."""
    target = db_path or guardrails.DB_PATH
    if not os.path.exists(target):
        return {"total": 0, "proposed": 0, "approved": 0}
    conn = connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM trend_items").fetchone()[0]
        proposed = conn.execute("SELECT COUNT(*) FROM trend_items WHERE status = 'proposed'").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM trend_items WHERE status = 'approved'").fetchone()[0]
    except sqlite3.Error:
        total = proposed = approved = 0
    finally:
        conn.close()
    return {"total": total, "proposed": proposed, "approved": approved}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        ran = already_ran_today()
        counts = get_trend_items_count()
        print(f"TREND ran today: {'yes' if ran else 'no'}")
        print(f"Trend items count: {counts['total']} total ({counts['proposed']} proposed, {counts['approved']} approved)")
    elif cmd == "run":
        res = run_trend()
        print(f"TREND generated {len(res)} items.")
    elif cmd == "maybe-run":
        res = maybe_run()
        print(f"TREND maybe-run: {res}")
    else:
        print("usage: python3 trend.py [status|run|maybe-run]")


if __name__ == "__main__":
    main()
