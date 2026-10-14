"""Telegram inbox: approve or reject drafts from your phone.

Each cloud run (GitHub Actions, once an hour) this module:
  1. Reads new Telegram messages sent to the Chilli Gavva bot (getUpdates).
  2. Accepts commands ONLY from the configured owner chat (TELEGRAM_CHAT_ID).
  3. Sends a short alert for every NEW draft that joined the approval queue
     (each draft is announced once, so there is no hourly spam).

Commands (send them to the bot):
  /list          show drafts waiting for approval
  /view 12       show the full text of draft #12
  /approve 12    approve draft #12   (several ids allowed: /approve 12 13)
  /reject 12     reject draft #12
  /help          show this help

Safety:
  - Messages from any other chat are ignored.
  - Only numeric ids are accepted; message text is never executed.
  - Approving here only marks the draft "approved" in the queue. Nothing is
    published, posted or sold automatically.

Zero third-party deps (stdlib only). Uses the same bot token/chat id as notify.py.

CLI:
  python3 telegram_inbox.py          # process commands + send new-draft alerts
  python3 telegram_inbox.py status   # show config state (no network calls)
"""
import json
import re
import sys
import urllib.request

import guardrails
import notify

API = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE = 3800          # Telegram limit is 4096 characters
MAX_UPDATES = 50
_TIMEOUT = 15

HELP = ("\U0001f336️ Chilli Gavva approvals\n\n"
        "/list - drafts waiting\n"
        "/view ID - read a draft\n"
        "/approve ID - approve (several ids ok)\n"
        "/reject ID - reject\n"
        "/help - this message\n\n"
        "Approve only marks a draft as OK. Nothing is published automatically.")


# ---- small state store (kept in agent_data.db so it survives cloud runs) ----
def _state_conn(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def get_state(key, default=None, db_path=None):
    conn = _state_conn(db_path)
    row = conn.execute("SELECT value FROM telegram_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_state(key, value, db_path=None):
    conn = _state_conn(db_path)
    conn.execute("INSERT INTO telegram_state (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()
    conn.close()


# ---- Telegram API ------------------------------------------------------------
def _call(token, method, params):
    url = API.format(token=token, method=method)
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def send_text(token, chat_id, text):
    """Send plain text (no HTML parsing, so draft content can never break formatting)."""
    if len(text) > MAX_MESSAGE:
        text = text[:MAX_MESSAGE] + "\n\n...(cut, too long for one message)"
    try:
        return bool(_call(token, "sendMessage", {"chat_id": chat_id, "text": text}).get("ok"))
    except Exception:
        return False


def fetch_updates(token, offset):
    params = {"timeout": 0, "limit": MAX_UPDATES, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = int(offset)
    try:
        data = _call(token, "getUpdates", params)
    except Exception:
        return []
    return data.get("result", []) if data.get("ok") else []


# ---- command handling --------------------------------------------------------
_CMD = re.compile(r"^/?(list|view|approve|reject|help|start)(?:@\w+)?\b(.*)$", re.IGNORECASE | re.DOTALL)


def parse_command(text):
    """Return (command, [ids]) or (None, []) for anything that is not a command."""
    m = _CMD.match((text or "").strip())
    if not m:
        return None, []
    cmd = m.group(1).lower()
    ids = [int(x) for x in re.findall(r"\d+", m.group(2))][:20]
    return ("help" if cmd == "start" else cmd), ids


def _score(review):
    m = re.search(r"(\d+)\s*/\s*100", review or "")
    return f" [{m.group(1)}/100]" if m else ""


def list_text(db_path=None):
    items = guardrails.pending(db_path)
    if not items:
        return "No drafts waiting. ✅"
    lines = [f"#{it['id']} {it['title'][:70]}{_score(it.get('review'))}" for it in items[:25]]
    more = f"\n...and {len(items) - 25} more" if len(items) > 25 else ""
    return (f"{len(items)} draft(s) waiting:\n\n" + "\n".join(lines) + more
            + "\n\nReply /view ID to read, /approve ID or /reject ID.")


def view_text(approval_id, db_path=None):
    item = guardrails.get_approval(approval_id, db_path)
    if not item:
        return f"Draft #{approval_id} not found."
    review = item.get("review") or "no review yet"
    return (f"#{item['id']} [{item['status']}] {item['title']}\nReview: {review}\n\n"
            f"{item.get('payload') or ''}")


def decide_text(ids, approved, db_path=None):
    if not ids:
        return "Please add the draft number, e.g. /approve 12"
    done, skipped = [], []
    for i in ids:
        (done if guardrails.decide(i, approved, db_path) else skipped).append(i)
    word = "Approved" if approved else "Rejected"
    out = []
    if done:
        out.append(f"{word}: " + ", ".join(f"#{i}" for i in done))
    if skipped:
        out.append("Not changed (not found or already decided): " + ", ".join(f"#{i}" for i in skipped))
    return "\n".join(out)


def handle_command(cmd, ids, db_path=None):
    if cmd == "list":
        return list_text(db_path)
    if cmd == "view":
        return view_text(ids[0], db_path) if ids else "Please add the draft number, e.g. /view 12"
    if cmd == "approve":
        return decide_text(ids, True, db_path)
    if cmd == "reject":
        return decide_text(ids, False, db_path)
    return HELP


def process_updates(token, owner_chat_id, db_path=None):
    """Handle new commands from the owner. Returns number of commands handled."""
    offset = get_state("update_offset", None, db_path)
    updates = fetch_updates(token, offset)
    handled = 0
    last = None
    for upd in updates:
        last = upd.get("update_id", last)
        msg = upd.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if chat_id != str(owner_chat_id):
            continue  # ignore everyone except the owner
        cmd, ids = parse_command(msg.get("text", ""))
        if not cmd:
            continue
        send_text(token, owner_chat_id, handle_command(cmd, ids, db_path))
        handled += 1
    if last is not None:
        set_state("update_offset", int(last) + 1, db_path)
    return handled


def alert_new_drafts(token, owner_chat_id, db_path=None):
    """Announce drafts that joined the queue since the last alert. Returns count announced."""
    items = guardrails.pending(db_path)
    last_seen = int(get_state("last_alerted_id", 0, db_path) or 0)
    if last_seen == 0 and items:
        # First run: do not flood the phone with the whole backlog, just one summary.
        set_state("last_alerted_id", max(it["id"] for it in items), db_path)
        send_text(token, owner_chat_id, list_text(db_path))
        return len(items)
    new = [it for it in items if it["id"] > last_seen]
    if not new:
        return 0
    lines = [f"#{it['id']} {it['title'][:70]}{_score(it.get('review'))}" for it in new[:10]]
    more = f"\n...and {len(new) - 10} more" if len(new) > 10 else ""
    send_text(token, owner_chat_id,
              f"\U0001f336️ {len(new)} new draft(s) ready:\n\n" + "\n".join(lines) + more
              + f"\n\nWaiting in total: {len(items)}. Reply /view ID, /approve ID or /reject ID.")
    set_state("last_alerted_id", max(it["id"] for it in new), db_path)
    return len(new)


def run(db_path=None):
    configured, token, chat_id = notify._telegram_configured()
    if not configured:
        print("Telegram not configured: skipping inbox.")
        return {"configured": False, "commands": 0, "alerts": 0}
    commands = process_updates(token, chat_id, db_path)
    alerts = alert_new_drafts(token, chat_id, db_path)
    print(f"Telegram inbox: {commands} command(s) handled, {alerts} new draft(s) announced.")
    return {"configured": True, "commands": commands, "alerts": alerts}


def main(argv):
    if argv and argv[0] == "status":
        configured, _, _ = notify._telegram_configured()
        print(f"Telegram configured: {'yes' if configured else 'no'}")
        return
    run()


if __name__ == "__main__":
    main(sys.argv[1:])
