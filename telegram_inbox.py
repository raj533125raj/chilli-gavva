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
  /pause         stop all AI work and new drafts (Telegram keeps answering)
  /resume        start work again
  /help          show this help

Buttons: every new-draft alert has Approve / Reject / View buttons. After Reject the bot asks why
(price, too generic, bad niche, poor quality, other) and saves the reason with the decision.
Buttons and commands are answered on the next cloud run (about every 30 minutes).

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
import base64
import binascii
import datetime
import re
import sys
import time
import urllib.error
import urllib.request

import guardrails
import notify

API = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE = 3800          # Telegram limit is 4096 characters
MAX_UPDATES = 50
_TIMEOUT = 15
WAITING_DRAFT_AGE_SECONDS = 12 * 60 * 60
WAITING_DRAFT_REMINDER_SECONDS = 12 * 60 * 60
WAITING_DRAFT_REMINDER_STATE = "last_waiting_draft_reminder"

HELP = ("\U0001f336️ Chilli Gavva approvals\n\n"
        "/list - drafts waiting\n"
        "/view ID - read a draft\n"
        "/approve ID - approve (several ids ok)\n"
        "/reject ID - reject\n"
        "/cubes - department cubes status and controls\n"
        "/rules - list active learned rules\n"
        "/pause - emergency brake: stop all AI work and drafts\n"
        "/resume - start work again\n"
        "/buttontest - send a test button (checks that button presses reach the bot)\n"
        "/help - this message\n\n"
        "New drafts come with Approve / Reject / View buttons. After Reject, pick a reason.\n"
        "The bot answers buttons and commands on its next run (about every 30 minutes), not instantly.\n\n"
        "Approve only marks a draft as OK. Nothing is published automatically.")

REASON_LABELS = {"price": "Price", "too generic": "Too generic", "bad niche": "Bad niche",
                 "poor quality": "Poor quality", "other": "Other"}


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
def _call(token, method, params, opener=None, sleep_fn=None):
    """Call Telegram with bounded retries; never expose the token in an error log."""
    url = API.format(token=token, method=method)
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    open_request = opener or urllib.request.urlopen
    sleep = sleep_fn or time.sleep

    def safe_error(label, detail):
        print(f"Telegram API {label}: {detail}", file=sys.stderr)

    attempt = 0
    while True:
        try:
            with open_request(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                retry_after = None
                try:
                    retry_after = float((exc.headers or {}).get("Retry-After"))
                except (TypeError, ValueError):
                    try:
                        body = json.loads(exc.read().decode("utf-8"))
                        retry_after = float(body.get("parameters", {}).get("retry_after", 0))
                    except (AttributeError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                        retry_after = 0
                sleep(max(0, min(retry_after or 0, 30)))
                attempt += 1
                continue
            if 500 <= exc.code <= 599 and attempt == 0:
                sleep(2)
                attempt += 1
                continue
            safe_error("HTTP error", str(exc.code))
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            safe_error("network error", type(exc).__name__)
            return None
        except (UnicodeDecodeError, json.JSONDecodeError):
            safe_error("response error", "invalid JSON")
            return None


def send_text(token, chat_id, text, buttons=None):
    """Send plain text (no HTML parsing, so draft content can never break formatting).

    buttons: optional rows of (label, callback_data) shown as inline buttons under the message.
    """
    if len(text) > MAX_MESSAGE:
        text = text[:MAX_MESSAGE] + "\n\n...(cut, too long for one message)"
    params = {"chat_id": chat_id, "text": text}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row]
                                                      for row in buttons]}
    try:
        return bool(_call(token, "sendMessage", params).get("ok"))
    except Exception:
        return False


def draft_buttons(approval_id):
    return [[("✅ Approve", f"a:{approval_id}"), ("❌ Reject", f"r:{approval_id}"), ("\U0001f441 View", f"v:{approval_id}")]]


def reason_buttons(approval_id):
    items = [(label, f"y:{approval_id}:{key}") for key, label in REASON_LABELS.items()]
    return [items[:3], items[3:]]


ALLOWED_UPDATES = ["message", "callback_query"]   # text messages AND button presses
LAST_FETCH_ERROR = None   # short reason when Telegram could not be read (never contains the token)


def _error_reason(exc):
    code = getattr(exc, "code", None)
    if code == 401:
        return "401 (bot token not accepted)"
    if code == 409:
        return "409 (another program or a webhook is reading this bot)"
    return str(code) if code else type(exc).__name__


def fetch_updates(token, offset):
    """New updates. Always asks for text messages AND button presses: Telegram only stores the update
    types the bot last asked for, so a reader that asks for text only would make button presses vanish."""
    global LAST_FETCH_ERROR
    LAST_FETCH_ERROR = None
    params = {"timeout": 0, "limit": MAX_UPDATES, "allowed_updates": ALLOWED_UPDATES}
    if offset:
        params["offset"] = int(offset)
    try:
        data = _call(token, "getUpdates", params)
    except Exception as exc:
        LAST_FETCH_ERROR = _error_reason(exc)
        return []
    if not isinstance(data, dict):
        LAST_FETCH_ERROR = "API unavailable"
        return []
    if not data.get("ok"):
        LAST_FETCH_ERROR = str(data.get("error_code") or "not ok")
        return []
    return data.get("result", [])


def subscription_note(token):
    """Safe one-line note on what Telegram will deliver to this bot (no token, ids or text)."""
    try:
        info = _call(token, "getWebhookInfo", {}).get("result") or {}
    except Exception as exc:
        return f"subscription check failed: {_error_reason(exc)}"
    parts = ["webhook set: " + ("YES (blocks reading updates)" if info.get("url") else "no")]
    if info.get("allowed_updates") is not None:
        allowed = info.get("allowed_updates") or ["all"]
        parts.append("bot asks for: " + ", ".join(allowed))
        if "callback_query" not in allowed and allowed != ["all"]:
            parts.append("WARNING button presses are not delivered")
    return "; ".join(parts)


# ---- command handling --------------------------------------------------------
_COMMANDS = "list|view|approve|reject|pause|resume|buttontest|rules|cubes|help|start"
# "/approve 4", "/approve" or "/approve@bot 4 5": anything after a real slash command is fine.
_SLASH_CMD = re.compile(rf"^/({_COMMANDS})(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
# Without a slash only the bare form counts: "approve 4", "approve 4, 5", "list". A normal sentence such as
# "Approve button edhuku work avadam ledhu?" is NOT a command.
_PLAIN_CMD = re.compile(rf"^({_COMMANDS})((?:[\s,#]+\d+)*)\s*$", re.IGNORECASE)


def parse_command(text):
    """Return (command, [ids]) or (None, []) for anything that is not a command."""
    text = (text or "").strip()
    m = _SLASH_CMD.match(text) or _PLAIN_CMD.match(text)
    if not m:
        return None, []
    cmd = m.group(1).lower()
    ids = [int(x) for x in re.findall(r"\d+", m.group(2) or "")][:20]
    return ("help" if cmd == "start" else cmd), ids


def parse_start_payload(text):
    """Decode an ``/start ap_<base64url-id>`` command, or return None if malformed.

    IDs remain text throughout encoding/decoding. Canonical unpadded base64url is
    required so alternate spellings cannot identify the same approval.
    """
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"/start(?:@\w+)?\s+([A-Za-z0-9_-]{1,64})", text.strip(), re.IGNORECASE)
    if not match:
        return None
    payload = match.group(1)
    if not payload.startswith("ap_") or len(payload) > 64:
        return None
    encoded = payload[3:]
    if not encoded or len(encoded) % 4 == 1:
        return None
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        approval_id = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if not approval_id or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != encoded:
        return None
    return approval_id


def draft_card(item, db_path=None):
    """Short alert text for one draft: title, Checker score, 3-line Council summary."""
    import checker
    import product_council

    lines = [f"#{item['id']} {item['title'][:70]}"]

    # 3-line Product Council summary if available
    council_lines = product_council.get_council_card_lines(item["id"], db_path=db_path)
    if council_lines:
        lines.extend(council_lines)
    else:
        review = item.get("review") or ""
        score, reasons = checker.parse_review(review)
        if score is not None:
            lines.append(f"Checker: {score}/100")
            lines += [f"- {r[:60]}" for r in reasons[:2]]
        else:
            m = re.search(r"score (\d+)\s*/\s*100", review)
            if m:
                lines.append(f"Checker: {m.group(1)}/100")
                import qa_checklist
                reasons = qa_checklist.top_reasons_from_review(review)
                lines += [f"- {r[:60]}" for r in reasons[:2]]
            else:
                lines.append("Checker: not checked yet")
    return "\n".join(lines)


def _ensure_checked(items, db_path=None):
    import checker
    for it in items:
        rev = it.get("review") or ""
        if not rev:
            full_item = guardrails.get_approval(it["id"], db_path) or it
            if full_item.get("kind") == "scout_idea":
                continue
            payload = full_item.get("payload") or ""
            res = checker.check_draft(payload, it.get("title") or "", db_path=db_path)
            new_rev = checker.format_review(res["score"], res["reasons"], verdict=res.get("verdict", "pass"), fallback=res.get("fallback", False))
            guardrails.set_review(it["id"], new_rev, db_path)
            it["review"] = new_rev


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


def missing_number_text(cmd, db_path=None):
    """Shown when a command needs a draft number: list the REAL waiting drafts, not a made-up example."""
    items = guardrails.pending(db_path)
    if not items:
        return "No drafts are waiting right now."
    ids = [f"#{it['id']}" for it in items[:15]]
    more = f" and {len(items) - 15} more" if len(items) > 15 else ""
    return f"Waiting: {', '.join(ids)}{more}. Send /{cmd} {items[0]['id']}"


def decide_text(ids, approved, db_path=None):
    if not ids:
        return missing_number_text("approve" if approved else "reject", db_path)
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
        return view_text(ids[0], db_path) if ids else missing_number_text("view", db_path)
    if cmd == "approve":
        return decide_text(ids, True, db_path)
    if cmd == "reject":
        return decide_text(ids, False, db_path)
    if cmd == "pause":
        guardrails.set_paused(True, db_path)
        import publisher; publisher.set_global_stop(True, db_path)
        return ("⏸ Paused. From the next run, no AI work and no new drafts. "
                "I will still answer here. Send /resume to start again.")
    if cmd == "resume":
        guardrails.set_paused(False, db_path)
        import publisher; publisher.set_global_stop(False, db_path)
        return "▶️ Resumed. Work starts again on the next run."
    if cmd == "buttontest":
        return "Button test:"
    if cmd == "rules":
        import improver_loop
        text, _ = improver_loop.format_rules_list(db_path)
        return text
    if cmd == "cubes":
        import cubes
        text, _ = cubes.format_telegram_cubes_list(db_path)
        return text
    return HELP


_CALLBACK = re.compile(r"^(a|r|v):(\d{1,9})$|^y:(\d{1,9}):([a-z ]{1,20})$|^(t):ping$")
BUTTON_TEST_OK = "✅ Button presses reach the bot. Nothing was changed."


def handle_callback(token, owner_chat_id, data, db_path=None):
    """Handle one button press. Returns the reply text (already sent), or None if the data is unknown."""
    if data and (data.startswith("cb:cube:") or data.startswith("cube:")):
        import cubes
        reply = cubes.handle_telegram_callback(data, db_path)
        if reply is not None:
            if isinstance(reply, dict):
                send_text(token, owner_chat_id, reply["text"], reply.get("buttons"))
                return reply["text"]
            else:
                send_text(token, owner_chat_id, reply)
                return reply
    if data and data.startswith("mb:"):
        import media_brain
        reply = media_brain.handle_callback(token, owner_chat_id, data, db_path)
        if reply is not None:
            return reply
    if data and (data.startswith("cb:tr:") or data.startswith("tr:")):
        import trend
        reply = trend.handle_callback(token, owner_chat_id, data, db_path)
        if reply is not None:
            return reply
    if data and data.startswith("mkt:"):
        import marketing_agency
        reply = marketing_agency.handle_callback(token, owner_chat_id, data, db_path)
        if reply is not None:
            return reply
    import chat_brain
    if data and data.startswith("cb:"):
        reply = chat_brain.handle_callback(token, owner_chat_id, data, db_path)
        if reply is not None:
            return reply
    import autofix
    if autofix.CALLBACK.fullmatch(data or ""):
        reply = autofix.decide(data, get_state, set_state, db_path)
        send_text(token, owner_chat_id, reply)
        return reply
    import improver_loop
    imp_reply = improver_loop.handle_callback(data, db_path)
    if imp_reply is not None:
        send_text(token, owner_chat_id, imp_reply)
        return imp_reply
    m = _CALLBACK.match(data or "")
    if not m:
        return None
    if m.group(5):   # the /buttontest button: only replies, changes nothing
        send_text(token, owner_chat_id, BUTTON_TEST_OK)
        return BUTTON_TEST_OK
    if m.group(1):
        kind, approval_id = m.group(1), int(m.group(2))
        if kind == "v":
            reply = view_text(approval_id, db_path)
            send_text(token, owner_chat_id, reply, draft_buttons(approval_id)
                      if (guardrails.get_approval(approval_id, db_path) or {}).get("status") == "pending" else None)
            return reply
        if kind == "a":
            reply = decide_text([approval_id], True, db_path)
            send_text(token, owner_chat_id, reply)
            return reply
        changed = guardrails.decide(approval_id, False, db_path)
        if changed:
            reply = f"Rejected: #{approval_id}. Why? Pick a reason so the agents can learn:"
            send_text(token, owner_chat_id, reply, reason_buttons(approval_id))
        else:
            reply = f"Not changed (not found or already decided): #{approval_id}"
            send_text(token, owner_chat_id, reply)
        return reply
    approval_id, reason = int(m.group(3)), m.group(4)
    if guardrails.set_reason(approval_id, reason, db_path):
        reply = f"Saved: #{approval_id} rejected because \"{REASON_LABELS[reason].lower()}\". Thanks!"
    else:
        reply = f"Could not save that reason for #{approval_id}."
    send_text(token, owner_chat_id, reply)
    return reply


def log_button(db_path=None):
    """Remember WHEN a button press from the owner chat arrived (time only), for the daily report."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    conn = _state_conn(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS button_log (ts TEXT)")
    conn.execute("INSERT INTO button_log (ts) VALUES (?)", (now.isoformat(timespec="seconds"),))
    conn.execute("DELETE FROM button_log WHERE ts < ?", ((now - datetime.timedelta(days=30)).isoformat(),))
    conn.commit()
    conn.close()


def new_stats():
    return {"text": 0, "button": 0, "other": 0, "handled": 0,
            "skipped": {"not owner chat": 0, "not a command": 0, "unknown button": 0}}


def process_updates(token, owner_chat_id, db_path=None, stats=None):
    """Handle new commands and button presses from the owner chat. Returns number handled.

    stats (optional dict from new_stats()) is filled with counts only: never ids, tokens or text.
    """
    stats = stats if stats is not None else new_stats()
    offset = get_state("update_offset", None, db_path)
    updates = fetch_updates(token, offset)
    handled = 0
    last = None
    for upd in updates:
        last = upd.get("update_id", last)
        cb = upd.get("callback_query")
        if cb:
            stats["button"] += 1
            # Same rule as typed commands: the press must come from the owner chat. (Before, the presser's
            # personal id also had to equal the chat id, which is never true in a group chat.)
            cb_chat = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
            if cb_chat != str(owner_chat_id):
                stats["skipped"]["not owner chat"] += 1
                continue
            log_button(db_path)
            try:
                _call(token, "answerCallbackQuery", {"callback_query_id": cb.get("id")})
            except Exception:
                pass  # old button presses can no longer be answered; the reply message still goes out
            if handle_callback(token, owner_chat_id, cb.get("data", ""), db_path) is not None:
                handled += 1
            else:
                stats["skipped"]["unknown button"] += 1
            continue
        msg = upd.get("message")
        if not msg:
            stats["other"] += 1
            continue

        if "text" in msg or "photo" in msg or "voice" in msg or "audio" in msg or "document" in msg:
            stats["text"] += 1
        else:
            stats["other"] += 1
            continue

        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if chat_id != str(owner_chat_id):
            stats["skipped"]["not owner chat"] += 1
            continue  # ignore everyone except the owner

        import media_brain
        caption = msg.get("caption", "")

        if "photo" in msg:
            reply_text, buttons, _ = media_brain.process_photo(token, msg["photo"], caption=caption, db_path=db_path)
            send_text(token, owner_chat_id, reply_text, buttons)
            handled += 1
            continue

        if "voice" in msg or "audio" in msg:
            audio_item = msg.get("voice") or msg.get("audio")
            reply_text, buttons, _ = media_brain.process_audio(token, audio_item, caption=caption, db_path=db_path)
            send_text(token, owner_chat_id, reply_text, buttons)
            handled += 1
            continue

        if "document" in msg:
            reply_text, buttons, _ = media_brain.process_document(token, msg["document"], db_path=db_path)
            send_text(token, owner_chat_id, reply_text, buttons)
            handled += 1
            continue

        message_text = msg.get("text", "")

        approval_id = parse_start_payload(message_text)
        if approval_id is not None:
            reply = view_text(approval_id, db_path)
            send_text(token, owner_chat_id, reply, draft_buttons(approval_id))
            handled += 1
            continue
        cmd, ids = parse_command(message_text)
        if not cmd:
            # Check for URL link in message_text
            urls = re.findall(r"https?://[^\s]+", message_text)
            if urls:
                reply_text, buttons, _ = media_brain.process_url(urls[0], db_path=db_path)
                send_text(token, owner_chat_id, reply_text, buttons)
                handled += 1
                continue

            import chat_brain
            chat_brain.process_message(token, owner_chat_id, message_text, db_path=db_path)
            handled += 1
            continue
        if cmd == "rules":
            import improver_loop
            text, buttons = improver_loop.format_rules_list(db_path)
            send_text(token, owner_chat_id, text, buttons)
        elif cmd == "cubes":
            import cubes
            text, buttons = cubes.format_telegram_cubes_list(db_path)
            send_text(token, owner_chat_id, text, buttons)
        else:
            send_text(token, owner_chat_id, handle_command(cmd, ids, db_path))
        if cmd == "list":       # fresh buttons for every waiting draft (up to 10)
            for it in guardrails.pending(db_path)[:10]:
                send_text(token, owner_chat_id, draft_card(it, db_path=db_path), draft_buttons(it["id"]))
        elif cmd == "buttontest":
            send_text(token, owner_chat_id, "Press this test button. The reply comes on the next run "
                      "(about 1-2 minutes with the door bell).", [[("Test button", "t:ping")]])
        handled += 1
    if last is not None:
        set_state("update_offset", int(last) + 1, db_path)
    stats["handled"] = handled
    return handled


def stats_line(stats, note=""):
    """One safe log line: counts and reasons only."""
    skipped = ", ".join(f"{n} {why}" for why, n in stats["skipped"].items() if n) or "none"
    line = (f"Telegram updates: {stats['text']} text, {stats['button']} button, {stats['other']} other; "
            f"handled {stats['handled']}; skipped: {skipped}")
    if LAST_FETCH_ERROR:
        line += f"; READ FAILED: {LAST_FETCH_ERROR}"
    return line + (f"; {note}" if note else "") + "."


def alert_new_drafts(token, owner_chat_id, db_path=None):
    """Announce drafts that joined the queue since the last alert. Returns count announced."""
    items = guardrails.pending(db_path)
    last_seen = int(get_state("last_alerted_id", 0, db_path) or 0)
    if last_seen == 0 and items:
        _ensure_checked(items, db_path)
        # First run: do not flood the phone with the whole backlog, just one summary.
        set_state("last_alerted_id", max(it["id"] for it in items), db_path)
        send_text(token, owner_chat_id, list_text(db_path))
        return len(items)
    new = [it for it in items if it["id"] > last_seen]
    if not new:
        return 0
    _ensure_checked(new, db_path)
    send_text(token, owner_chat_id,
              f"\U0001f336️ {len(new)} new draft(s) ready. Waiting in total: {len(items)}."
              + (f" Buttons for the first 10; use /list for the rest." if len(new) > 10 else ""))
    for it in new[:10]:
        send_text(token, owner_chat_id, draft_card(it, db_path=db_path), draft_buttons(it["id"]))
    set_state("last_alerted_id", max(it["id"] for it in new), db_path)
    return len(new)


def _approval_time(value):
    """Read an approval timestamp as UTC; malformed old rows are simply not reminders."""
    try:
        parsed = datetime.datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return None


def remind_waiting_drafts(token, owner_chat_id, db_path=None, now=None):
    """Send one owner reminder for a queue containing a draft older than 12 hours.

    This is intentionally a nudge only: it never calls an approval-changing
    function.  The timestamp is retained in telegram_state only after a
    successful send, so a Telegram outage can be retried later.
    """
    now = time.time() if now is None else now
    pending = guardrails.pending(db_path)
    has_old_draft = any(ts is not None and now - ts > WAITING_DRAFT_AGE_SECONDS
                         for ts in (_approval_time(item.get("ts")) for item in pending))
    if not has_old_draft:
        return False
    try:
        last = float(get_state(WAITING_DRAFT_REMINDER_STATE, 0, db_path) or 0)
    except (TypeError, ValueError):
        last = 0
    if now - last < WAITING_DRAFT_REMINDER_SECONDS:
        return False
    if not send_text(token, owner_chat_id, f"{len(pending)} drafts waiting - /list"):
        return False
    set_state(WAITING_DRAFT_REMINDER_STATE, now, db_path)
    return True


def daily_waiting_line(db_path=None, now=None):
    """The queue-age line appended to the owner-only daily Telegram report."""
    now = time.time() if now is None else now
    pending = guardrails.pending(db_path)
    times = [stamp for stamp in (_approval_time(item.get("ts")) for item in pending)
             if stamp is not None]
    oldest_hours = max(0, int((now - min(times)) // 3600)) if times else 0
    return f"Drafts waiting: {len(pending)} (oldest {oldest_hours}h)"


def run(db_path=None):
    configured, token, chat_id = notify._telegram_configured()
    if not configured:
        print("Telegram not configured: skipping inbox.")
        return {"configured": False, "commands": 0, "alerts": 0}
    stats = new_stats()
    commands = process_updates(token, chat_id, db_path, stats)
    import autofix
    autofix.poll(get_state, set_state,
                 lambda text, buttons: send_text(token, chat_id, text, buttons), db_path)
    try:
        import control_inbox
        control_inbox.run(db_path, send=lambda line: send_text(token, chat_id, line))
    except Exception as exc:
        print(f"Control inbox skipped: {type(exc).__name__}")
    print(stats_line(stats, subscription_note(token)))
    alerts = alert_new_drafts(token, chat_id, db_path)
    waiting_reminder = remind_waiting_drafts(token, chat_id, db_path)
    try:  # one short health report a day (first run after 03:30 UTC), weekly letter on Sundays
        import daily_report
        daily = daily_report.maybe_send(
            lambda text: send_text(token, chat_id, text + "\n" + daily_waiting_line(db_path)),
            get_state, set_state, db_path)
        import improver_loop
        improver_loop.maybe_run_weekly(
            lambda text, buttons=None: send_text(token, chat_id, text, buttons),
            get_state, set_state, db_path)
    except Exception as exc:
        print(f"Daily report / improver loop skipped: {type(exc).__name__}")
        daily = False
    print(f"Telegram inbox: {commands} command(s) handled, {alerts} new draft(s) announced"
          + (", daily report sent." if daily else "."))
    return {"configured": True, "commands": commands, "alerts": alerts,
            "waiting_reminder": waiting_reminder, "daily_report": daily}


def main(argv):
    if argv and argv[0] == "status":
        configured, _, _ = notify._telegram_configured()
        print(f"Telegram configured: {'yes' if configured else 'no'}")
        return
    run()


if __name__ == "__main__":
    main(sys.argv[1:])
