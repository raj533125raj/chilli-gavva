"""Chat with the CEO agent. Answers about money, agents and approvals come from the
database (so they are always honest). Free-form questions go to the LLM when a key
exists and the call budget allows; otherwise a clear fallback message is returned."""


def reply(message, status, llm=None):
    text = (message or "").strip()
    if not text:
        return "Ask me about money, agents, approvals, or what to do next."
    low = text.lower()
    money = status["money"]
    if any(w in low for w in ("money", "earn", "revenue", "income", "profit")):
        return (f"Real money earned so far: ${money['real_usd']:.2f}. "
                f"Simulated numbers (NOT real money): ${money['simulated_usd']:.2f}.")
    if any(w in low for w in ("approve", "approval", "pending", "review")):
        items = status["pending_approvals"]
        if not items:
            return "No drafts are waiting for your approval."
        lines = [f"#{i['id']} {i['title']} ({i['review'] or 'not reviewed yet'})" for i in items[:5]]
        return f"{len(items)} item(s) waiting for you:\n" + "\n".join(lines)
    if any(w in low for w in ("status", "agent", "working", "doing", "team")):
        working = [a["name"] for a in status["agents"] if a["state"] == "working"]
        errors = [a["name"] for a in status["agents"] if a["state"] == "error"]
        counts = status["task_counts"]
        worker = status.get("worker", {})
        worker_text = "running" if worker.get("running") else "STOPPED (start it with python3 start.py)"
        return (f"Worker: {worker_text}. Agents working now: {', '.join(working) or 'none'}. Errors: {', '.join(errors) or 'none'}. "
                f"Tasks: {counts.get('done', 0)} done, {counts.get('queued', 0)} queued, "
                f"{counts.get('failed', 0)} failed. Kill switch: {'ON' if status['kill_switch'] else 'off'}.")
    if any(w in low for w in ("stop", "pause", "kill")):
        return "Use the Kill switch button on the dashboard (or run: python3 guardrails.py kill). It stops all agent work at once."
    if llm:
        context = (f"You are the CEO agent of a small zero-cost project. Facts: real money ${money['real_usd']:.2f}, "
                   f"simulated ${money['simulated_usd']:.2f}, mode {status['mode']}. Be brief and honest. "
                   f"Never promise income. User asks: {text}")
        answer = llm(context)
        if answer:
            return answer
    return ("I can answer questions about money, agents and approvals. For open questions I need a free "
            "Groq key (see docs/GROQ_SETUP.md) and today's call budget must not be used up.")
