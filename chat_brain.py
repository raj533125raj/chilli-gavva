"""chat_brain.py — Plain language Telegram interface for Chilli Gavva.

Allows the owner to converse with the bot in plain language (English or Telugu-English mix/Tenglish).
Queries real database data and constructs confirmation cards for action requests.
"""

import json
import re
import sqlite3
import datetime
import guardrails
import llm_router

# Actions requiring confirmation buttons (Yes / No)
ACTION_INTENTS = {"pause", "resume", "approve", "reject", "run_now"}

# Query intents answering from real database state
QUERY_INTENTS = {
    "status",
    "drafts_waiting",
    "rejected_reasons",
    "truth_check_status",
    "ai_health",
    "github_minutes",
    "last_errors",
    "scout_count",
    "trend_count",
    "capabilities",
}

# Regex to scrub secret keys, tokens, and currency / money amounts
CURRENCY_PATTERN = re.compile(
    r"(\$|₹|€|£)\s*\d+(?:\.\d+)?|\b\d+(?:\.\d+)?\s*(?:USD|INR|EUR|GBP|dollars|rupees)\b",
    re.IGNORECASE
)


def _connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            role TEXT,
            content TEXT,
            ts TEXT
        )
    """)
    conn.commit()
    return conn


def add_chat_memory(chat_id, role, content, db_path=None):
    """Store a message in chat memory and keep only the last 10 messages per chat."""
    conn = _connect(db_path)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO chat_memory (chat_id, role, content, ts) VALUES (?, ?, ?, ?)",
        (str(chat_id), role, scrub_secrets_and_money(content), ts)
    )
    conn.commit()
    # Trim to last 10 messages for this chat_id
    rows = conn.execute(
        "SELECT id FROM chat_memory WHERE chat_id = ? ORDER BY id DESC LIMIT 10",
        (str(chat_id),)
    ).fetchall()
    if rows:
        min_id = rows[-1][0]
        conn.execute(
            "DELETE FROM chat_memory WHERE chat_id = ? AND id < ?",
            (str(chat_id), min_id)
        )
        conn.commit()
    conn.close()


def get_chat_memory(chat_id, limit=10, db_path=None):
    """Retrieve recent chat history for a chat_id."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT role, content FROM chat_memory WHERE chat_id = ? ORDER BY id ASC LIMIT ?",
        (str(chat_id), limit)
    ).fetchall()
    conn.close()
    return [{"role": role, "content": content} for role, content in rows]


def scrub_secrets_and_money(text):
    """Scrub secret credentials and money/currency values from text."""
    if not text:
        return ""
    text = llm_router.scrub(text)
    text = CURRENCY_PATTERN.sub("[hidden]", text)
    return text


def rule_based_classify(text):
    """Rule-based intent classifier supporting English and Telugu-English mix (Tenglish).

    Returns (intent, params_dict) or ("capabilities", {}).
    """
    text_clean = text.lower().strip()
    ids = [int(x) for x in re.findall(r"\d+", text_clean)]

    # 1. Clear Actions
    if any(k in text_clean for k in ["pause", "aapu", "stop work", "brake", "hold work"]):
        return "pause", {}
    if any(k in text_clean for k in ["resume", "start again", "malli start", "continue work", "restart work"]):
        return "resume", {}
    if any(k in text_clean for k in ["run now", "ippude run", "run engine", "execute now", "trigger run"]):
        return "run_now", {}

    # 2. Queries (check specific query terms before broad action words like 'reject' / 'approve')
    if any(k in text_clean for k in ["reject reason", "enduku reject", "why reject", "reasons", "rejected drafts", "rejection reason"]):
        return "rejected_reasons", {}
    if any(k in text_clean for k in ["ready", "needs fix", "truth check", "products status", "which products"]):
        return "truth_check_status", {}
    if any(k in text_clean for k in ["ai health", "llm health", "models working", "ai status", "providers"]):
        return "ai_health", {}
    if any(k in text_clean for k in ["github minutes", "minutes", "action minutes", "runs count"]):
        return "github_minutes", {}
    if any(k in text_clean for k in ["error", "failures", "failed tasks", "dhenilo error", "last errors"]):
        return "last_errors", {}
    if any(k in text_clean for k in ["scout", "scout count", "ideas count", "scout ideas"]):
        return "scout_count", {}
    if any(k in text_clean for k in ["trend", "trend count", "trends", "rising tools"]):
        return "trend_count", {}
    if any(k in text_clean for k in ["drafts", "pending", "waiting", "list drafts", "enniyunnai"]):
        return "drafts_waiting", {}
    if any(k in text_clean for k in ["status", "ela undhi", "today status", "bot status", "state"]):
        return "status", {}

    # 3. Actions with optional IDs
    if any(k in text_clean for k in ["approve", "ok cheyi", "pass cheyi", "accept"]):
        return "approve", {"ids": ids} if ids else {}
    if any(k in text_clean for k in ["reject", "voddu", "reject cheyi", "decline", "dismiss"]):
        return "reject", {"ids": ids} if ids else {}

    # Check if numbers only with simple word
    if ids and ("approve" in text_clean or "ok" in text_clean):
        return "approve", {"ids": ids}
    if ids and ("reject" in text_clean or "no" in text_clean or "bad" in text_clean):
        return "reject", {"ids": ids}

    return "capabilities", {}


CLASSIFY_PROMPT = """You are an intent classifier for Chilli Gavva AI assistant.
Classify the user's message (which may be in English or Telugu-English mix / Tenglish) into one of the following JSON objects:

Possible intents:
Actions:
- "pause": pause AI work
- "resume": resume AI work
- "approve": approve draft(s) (extract list of integer IDs in "ids" parameter if mentioned)
- "reject": reject draft(s) (extract list of integer IDs in "ids" parameter if mentioned)
- "run_now": run engine / worker now

Queries:
- "status": today's status, pause state, usage
- "drafts_waiting": list or count of waiting drafts
- "rejected_reasons": why drafts were rejected
- "truth_check_status": products READY or NEEDS FIX status
- "ai_health": status of AI providers
- "github_minutes": GitHub Actions minutes used this month
- "last_errors": recent failed tasks or errors
- "scout_count": scout ideas count
- "trend_count": trend items count
- "capabilities": greeting, help, unknown, or out of scope requests

Return JSON strictly in this format:
{{"intent": "<intent_name>", "ids": [<integer_ids_if_any>]}}

User message: {message}"""


def classify_intent(message, db_path=None):
    """Classify message intent using llm_router with rule-based fallback."""
    # Attempt LLM classification
    try:
        raw_llm = llm_router.chat(CLASSIFY_PROMPT.format(message=message), db_path=db_path, size="small")
        if raw_llm:
            start = raw_llm.find("{")
            end = raw_llm.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(raw_llm[start:end + 1])
                intent = data.get("intent", "").lower().strip()
                ids = data.get("ids") or []
                if isinstance(ids, int):
                    ids = [ids]
                elif not isinstance(ids, list):
                    ids = []
                ids = [int(x) for x in ids if isinstance(x, (int, str)) and str(x).isdigit()]

                all_intents = ACTION_INTENTS | QUERY_INTENTS
                if intent in all_intents:
                    params = {"ids": ids} if ids else {}
                    return intent, params
    except Exception:
        pass

    # Fallback to rule-based keyword matching
    return rule_based_classify(message)


# ---- Data Fetchers for Real DB State ----

def fetch_status(db_path=None):
    paused = guardrails.is_paused(db_path)
    calls = guardrails.calls_today(db_path)
    cap = guardrails.daily_cap(db_path)
    pending = len(guardrails.pending(db_path))
    status_str = "PAUSED ⏸" if paused else "RUNNING ▶️"
    return (f"🤖 Chilli Gavva Status:\n"
            f"- State: {status_str}\n"
            f"- LLM calls today: {calls} / {cap}\n"
            f"- Pending drafts waiting: {pending}")


def fetch_drafts_waiting(db_path=None):
    items = guardrails.pending(db_path)
    if not items:
        return "No drafts are waiting for approval right now. ✅"
    lines = [f"#{it['id']} {it['title'][:60]}" for it in items[:10]]
    more = f"\n...and {len(items) - 10} more" if len(items) > 10 else ""
    return f"📝 {len(items)} Draft(s) waiting for approval:\n\n" + "\n".join(lines) + more


def fetch_rejected_reasons(db_path=None):
    conn = guardrails.connect(db_path)
    rows = conn.execute(
        "SELECT id, title, reason, decided_ts FROM approvals WHERE status = 'rejected' ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()
    if not rows:
        return "No rejected drafts found in history."
    lines = []
    for r in rows:
        reason_str = r[2] or "no reason recorded"
        lines.append(f"#{r[0]} {r[1][:50]} -> Reason: {reason_str}")
    return "❌ Recent Draft Rejections:\n\n" + "\n".join(lines)


def fetch_truth_check_status(db_path=None):
    conn = guardrails.connect(db_path)
    try:
        rows = conn.execute("""
            SELECT pb.approval_id, pb.folder, tc.verdict, tc.score
            FROM product_builds pb
            LEFT JOIN truth_checks tc ON pb.approval_id = tc.approval_id
            ORDER BY pb.approval_id DESC LIMIT 10
        """).fetchall()
    except sqlite3.Error:
        rows = []
    conn.close()

    if not rows:
        return "No product builds evaluated by Truth Checker yet."

    ready = []
    needs_fix = []
    for app_id, folder, verdict, score in rows:
        name = folder.split("/")[-1] if folder else f"Product #{app_id}"
        v_str = (verdict or "NEEDS FIX").upper()
        sc_str = f"{score}/100" if score is not None else "no score"
        entry = f"#{app_id} {name[:40]} [{v_str} - {sc_str}]"
        if v_str == "READY":
            ready.append(entry)
        else:
            needs_fix.append(entry)

    res = "🔍 Product Truth Check Status:\n\n"
    if ready:
        res += "✅ READY:\n" + "\n".join(ready) + "\n\n"
    if needs_fix:
        res += "⚠️ NEEDS FIX:\n" + "\n".join(needs_fix)
    return res.strip()


def fetch_ai_health(db_path=None):
    statuses = llm_router.provider_status()
    lines = []
    for s in statuses:
        p_name = s["provider"]
        cfg = "Configured" if s["configured"] else "Not configured"
        in_cd = " (In Cooldown)" if llm_router.in_cooldown(p_name, db_path) else ""
        lines.append(f"- {p_name}: {cfg}{in_cd}")
    return "⚡ AI Health & Providers:\n" + "\n".join(lines)


def fetch_github_minutes(db_path=None):
    try:
        import daily_report
        return "⏱ " + daily_report._minutes()
    except Exception:
        return "⏱ GitHub minutes status unavailable."


def fetch_last_errors(db_path=None):
    conn = guardrails.connect(db_path)
    try:
        rows = conn.execute("""
            SELECT id, kind, agent, error, finished_ts FROM tasks
            WHERE status = 'failed' OR error IS NOT NULL
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    except sqlite3.Error:
        rows = []
    conn.close()

    if not rows:
        return "No recent task errors recorded. All systems operational! ✅"

    lines = []
    for r in rows:
        err_msg = (r[3] or "Unknown error")[:80]
        lines.append(f"- Task #{r[0]} ({r[1]} / {r[2]}): {err_msg}")
    return "🚨 Last Task Errors:\n\n" + "\n".join(lines)


def fetch_scout_count(db_path=None):
    try:
        import scout
        counts = scout.get_scout_ideas_count(db_path)
        return (f"💡 Scout Ideas Count:\n"
                f"- Total discovered: {counts.get('total', 0)}\n"
                f"- Pending approval: {counts.get('pending', 0)}\n"
                f"- Approved: {counts.get('approved', 0)}")
    except Exception:
        return "💡 Scout ideas count unavailable."


def fetch_trend_count(db_path=None):
    try:
        import trend
        counts = trend.get_trend_items_count(db_path)
        return (f"🔥 Trend Items Count:\n"
                f"- Total collected: {counts.get('total', 0)}\n"
                f"- Proposed: {counts.get('proposed', 0)}\n"
                f"- Approved: {counts.get('approved', 0)}")
    except Exception:
        return "🔥 Trend items count unavailable."


def fetch_capabilities():
    return (
        "🤖 I am Chilli Gavva Bot! Here is what I can do:\n\n"
        "📊 QUERIES (Ask me anytime in Telugu or English):\n"
        "• Today's status & LLM call usage\n"
        "• Pending drafts waiting for approval\n"
        "• Reasons why drafts were rejected\n"
        "• Which products are READY or NEED FIX\n"
        "• AI provider health & status\n"
        "• GitHub Actions minutes used\n"
        "• Recent task errors or failures\n"
        "• Scout ideas count\n\n"
        "⚡ ACTIONS (I will ask for YES/NO confirmation first):\n"
        "• Pause AI work\n"
        "• Resume AI work\n"
        "• Approve draft #id\n"
        "• Reject draft #id\n"
        "• Run engine / tasks now\n\n"
        "🔒 Safety: I will never perform actions directly without confirmation, and I never expose secret tokens or sensitive financial data."
    )


# ---- Action Confirmations and Execution ----

def build_confirmation_card(action, params):
    """Build confirmation card text and Yes/No inline buttons."""
    param_str = ""
    if action in ("approve", "reject") and params.get("ids"):
        param_str = " " + ", ".join(f"#{i}" for i in params["ids"])
    elif action in ("approve", "reject") and not params.get("ids"):
        return f"Please specify draft ID(s) to {action}. Example: {action} #12", None

    prompt_text = f"⚠️ Confirmation Required:\nDo you want to execute '{action.upper()}{param_str}'?"

    ids_payload = ",".join(str(i) for i in params.get("ids", []))
    cb_yes = f"cb:y:{action}:{ids_payload}" if ids_payload else f"cb:y:{action}"
    cb_no = f"cb:n:{action}:{ids_payload}" if ids_payload else f"cb:n:{action}"

    buttons = [[("✅ Yes", cb_yes), ("❌ No", cb_no)]]
    return prompt_text, buttons


def execute_action(action, params_str, db_path=None):
    """Execute action after Yes button confirmation."""
    ids = [int(x) for x in params_str.split(",") if x.isdigit()] if params_str else []

    if action == "pause":
        guardrails.set_paused(True, db_path)
        try:
            import publisher
            publisher.set_global_stop(True, db_path)
        except Exception:
            pass
        return "⏸ Action executed: Paused all AI work and new drafts."

    if action == "resume":
        guardrails.set_paused(False, db_path)
        try:
            import publisher
            publisher.set_global_stop(False, db_path)
        except Exception:
            pass
        return "▶️ Action executed: Resumed work."

    if action == "approve":
        if not ids:
            return "No draft IDs provided for approval."
        done, skipped = [], []
        for i in ids:
            (done if guardrails.decide(i, True, db_path) else skipped).append(i)
        res = []
        if done:
            res.append(f"Approved: " + ", ".join(f"#{i}" for i in done))
        if skipped:
            res.append("Skipped (not found or decided): " + ", ".join(f"#{i}" for i in skipped))
        return "✅ " + "\n".join(res)

    if action == "reject":
        if not ids:
            return "No draft IDs provided for rejection."
        done, skipped = [], []
        for i in ids:
            (done if guardrails.decide(i, False, db_path) else skipped).append(i)
        res = []
        if done:
            res.append(f"Rejected: " + ", ".join(f"#{i}" for i in done))
        if skipped:
            res.append("Skipped (not found or decided): " + ", ".join(f"#{i}" for i in skipped))
        return "❌ " + "\n".join(res)

    if action == "run_now":
        try:
            import workforce
            res = workforce.run_once(max_tasks=3, db_path=db_path)
            return f"🚀 Engine run triggered!\nResult: {res['status']}, ran {res.get('ran', 0)} task(s)."
        except Exception as exc:
            return f"🚀 Engine run failed: {type(exc).__name__}"

    return f"Unknown action: {action}"


# ---- Main Handler Functions for Telegram ----

def handle_callback(token, owner_chat_id, data, db_path=None):
    """Handle callback queries starting with 'cb:'."""
    if not data or not data.startswith("cb:"):
        return None

    import telegram_inbox
    parts = data.split(":", 3)
    # cb:y:<action>:<params> or cb:n:<action>:<params>
    if len(parts) < 3:
        return None

    choice = parts[1]
    action = parts[2]
    params_str = parts[3] if len(parts) > 3 else ""

    if choice == "n":
        reply = f"Cancelled action '{action}'."
        telegram_inbox.send_text(token, owner_chat_id, reply)
        return reply

    if choice == "y":
        reply = execute_action(action, params_str, db_path)
        reply = scrub_secrets_and_money(reply)
        telegram_inbox.send_text(token, owner_chat_id, reply)
        return reply

    return None


def process_message(token, owner_chat_id, message_text, db_path=None):
    """Process plain language message from Telegram owner chat."""
    import telegram_inbox

    add_chat_memory(owner_chat_id, "user", message_text, db_path)

    intent, params = classify_intent(message_text, db_path=db_path)

    if intent in ACTION_INTENTS:
        card_text, buttons = build_confirmation_card(intent, params)
        card_text = scrub_secrets_and_money(card_text)
        telegram_inbox.send_text(token, owner_chat_id, card_text, buttons)
        add_chat_memory(owner_chat_id, "assistant", card_text, db_path)
        return card_text

    # Query intents
    if intent == "status":
        reply = fetch_status(db_path)
    elif intent == "drafts_waiting":
        reply = fetch_drafts_waiting(db_path)
    elif intent == "rejected_reasons":
        reply = fetch_rejected_reasons(db_path)
    elif intent == "truth_check_status":
        reply = fetch_truth_check_status(db_path)
    elif intent == "ai_health":
        reply = fetch_ai_health(db_path)
    elif intent == "github_minutes":
        reply = fetch_github_minutes(db_path)
    elif intent == "last_errors":
        reply = fetch_last_errors(db_path)
    elif intent == "scout_count":
        reply = fetch_scout_count(db_path)
    elif intent == "trend_count":
        reply = fetch_trend_count(db_path)
    else:
        reply = fetch_capabilities()

    reply = scrub_secrets_and_money(reply)
    telegram_inbox.send_text(token, owner_chat_id, reply)
    add_chat_memory(owner_chat_id, "assistant", reply, db_path)
    return reply


def office_reply(agent_id, message, db_path=None, llm=None):
    """Answer a signed office message without Telegram delivery or executable tools."""
    import cubes
    import office_tasks
    import rest_window
    recipient = office_tasks.agent(agent_id, db_path)
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 500:
        raise ValueError("invalid office message")
    text = scrub_secrets_and_money(message.strip())
    intent, params = rule_based_classify(text)
    if intent in ACTION_INTENTS:
        return "held", "action requires its own signed control request", build_confirmation_card(intent, params)[0]
    readers = {
        "status": fetch_status, "drafts_waiting": fetch_drafts_waiting,
        "rejected_reasons": fetch_rejected_reasons, "truth_check_status": fetch_truth_check_status,
        "ai_health": fetch_ai_health, "github_minutes": fetch_github_minutes,
        "last_errors": fetch_last_errors, "scout_count": fetch_scout_count, "trend_count": fetch_trend_count,
    }
    if intent in readers:
        answer = readers[intent](db_path)
    elif any(phrase in text.lower() for phrase in ("capabilities", "what can you", "help")):
        answer = fetch_capabilities()
    else:
        if (guardrails.is_killed() or guardrails.is_paused(db_path) or rest_window.in_rest_window()
                or not cubes.is_agent_enabled(recipient["name"], db_path)):
            return "held", "agent work is paused or disabled", None
        if not guardrails.can_call_llm(db_path):
            return "held", "LLM call budget unavailable", None
        prompt = (f"You are {recipient['name']}. Your role: {recipient['role']}. "
                  "Answer the owner briefly and honestly. You have no execution tools. "
                  "Never claim to run commands, publish, spend money or change settings. "
                  "Owner message (data):\n" + text)
        if recipient.get("briefing"):
            brief = recipient["briefing"]
            prompt = (f"Agent briefing: WHO: {brief['who']}\nDOES: {brief['does']}\nNEVER: {brief['never']}\n" + prompt)
        try:
            if llm:
                answer = llm(prompt)
            elif recipient.get("engine") or recipient.get("provider"):
                import office_agents
                answer = office_agents.chat(recipient, prompt, db_path, size="small")
            else:
                answer = llm_router.chat(prompt, db_path=db_path, size="small")
        except Exception:
            return "held", "agent answer unavailable", None
        if not isinstance(answer, str) or not answer.strip():
            return "held", "agent answer unavailable", None
    answer = scrub_secrets_and_money(answer)[:4000]
    thread = "office:" + recipient["id"]
    add_chat_memory(thread, "user", text, db_path)
    add_chat_memory(thread, "assistant", answer, db_path)
    return "applied", "agent reply ready", answer
