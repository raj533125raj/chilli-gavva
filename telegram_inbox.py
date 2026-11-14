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
import os
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
import telegram_diagnostics as diagnostics

API = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE = 3500          # Enforced maximum outgoing message limit
MAX_UPDATES = 50
_TIMEOUT = 15
WAITING_DRAFT_AGE_SECONDS = 12 * 60 * 60
WAITING_DRAFT_REMINDER_SECONDS = 12 * 60 * 60
WAITING_DRAFT_REMINDER_STATE = "last_waiting_draft_reminder"
UNKNOWN_TEXT_HINT = "Ardam kaaledu. 'help' ani type cheyandi or command malli cheppandi."

HELP = ("\U0001f336️ Chilli Gavva approvals\n\n"
        "/status - compact system status\n"
        "/list - drafts waiting\n"
        "/view ID - read a draft\n"
        "/approve ID - approve (several ids ok)\n"
        "/packs - list ready upload packs\n"
        "/pack ID - plain listing text for store\n"
        "/marketing N - marketing files built for pack N\n"
        "/reject ID - reject\n"
        "/pipeline - daily plan vs done per stage, backlog & stuck items\n"
        "/cubes - department cubes status and controls\n"
        "/rules - list active learned rules\n"
        "/doctor - show last 10 doctor cases\n"
        "/today - what to post today\n"
        "/posted N - mark post N of today as done\n"
        "/pause - emergency brake: stop all AI work and drafts\n"
        "/resume - start work again\n"
        "/buttontest - send a test button (checks that button presses reach the bot)\n"
        "/help - this message\n\n"
        "New drafts come with Approve / Reject / View buttons. After Reject, pick a reason.\n"
        "The bot answers buttons and commands on its next run (about every 30 minutes), not instantly.\n\n"
        "Approve only marks a draft as OK. Nothing is published automatically.")

REASON_LABELS = {
    "too generic": "Too generic",
    "duplicate": "Duplicate",
    "weak niche": "Weak niche",
    "promises too much": "Promises too much",
    "other": "Other",
}

# The one-tap reject labels live in guardrails.REJECT_REASONS (no runtime patching).


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
                status = getattr(resp, "status", 200)
                status = status if type(status) is int else 200
                result = json.loads(resp.read().decode("utf-8"))
            ok = isinstance(result, dict) and result.get("ok") is True
            code = result.get("error_code") if isinstance(result, dict) else None
            code = code if type(code) is int else status
            diagnostics.record(method, status, None if ok else diagnostics.error_kind(code), ok)
            return result
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                diagnostics.record(method, exc.code, "rate_limit", terminal=False)
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
            # An outbound message may have been delivered before a 5xx response.
            # Retrying that non-idempotent request can send a duplicate.
            if 500 <= exc.code <= 599 and attempt == 0 and method != "sendMessage":
                diagnostics.record(method, exc.code, "server", terminal=False)
                sleep(2)
                attempt += 1
                continue
            safe_error("HTTP error", str(exc.code))
            diagnostics.record(method, exc.code, diagnostics.error_kind(exc.code))
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            safe_error("network error", type(exc).__name__)
            diagnostics.record(method, kind="timeout" if isinstance(exc, TimeoutError) else "network")
            return None
        except (UnicodeDecodeError, json.JSONDecodeError):
            safe_error("response error", "invalid JSON")
            diagnostics.record(method, kind="invalid_response")
            return None


def _scrub_outgoing(text: str) -> str:
    """Scrub secrets, tokens, API keys, emails, and truncate to <= MAX_MESSAGE chars."""
    if not text:
        return ""
    import public_export
    scrubbed = public_export.scrub_text(text)
    if len(scrubbed) > MAX_MESSAGE:
        cut_suffix = "\n\n...(cut, message length limit)"
        scrubbed = scrubbed[:MAX_MESSAGE - len(cut_suffix)] + cut_suffix
    return scrubbed


def _call_multipart(token, method, fields, files, opener=None, sleep_fn=None):
    """Call Telegram multipart upload with bounded retries; never expose the token."""
    import uuid
    import mimetypes
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in fields.items():
        if value is None:
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        if isinstance(value, bytes):
            body.extend(value)
        else:
            body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for field_name, (filename, file_bytes) in files.items():
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8"))
        body.extend(f'Content-Type: {ctype}\r\n\r\n'.encode("utf-8"))
        body.extend(file_bytes)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    url = API.format(token=token, method=method)
    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    open_request = opener or urllib.request.urlopen
    sleep = sleep_fn or time.sleep

    def safe_error(label, detail):
        print(f"Telegram API {label}: {detail}", file=sys.stderr)

    attempt = 0
    while True:
        try:
            with open_request(req, timeout=_TIMEOUT) as resp:
                status = getattr(resp, "status", 200)
                status = status if type(status) is int else 200
                result = json.loads(resp.read().decode("utf-8"))
            ok = isinstance(result, dict) and result.get("ok") is True
            code = result.get("error_code") if isinstance(result, dict) else None
            code = code if type(code) is int else status
            diagnostics.record(method, status, None if ok else diagnostics.error_kind(code), ok)
            return result
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                diagnostics.record(method, exc.code, "rate_limit", terminal=False)
                retry_after = None
                try:
                    retry_after = float((exc.headers or {}).get("Retry-After"))
                except (TypeError, ValueError):
                    try:
                        resp_body = json.loads(exc.read().decode("utf-8"))
                        retry_after = float(resp_body.get("parameters", {}).get("retry_after", 0))
                    except (AttributeError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                        retry_after = 0
                sleep(max(0, min(retry_after or 0, 30)))
                attempt += 1
                continue
            safe_error("HTTP error", str(exc.code))
            diagnostics.record(method, exc.code, diagnostics.error_kind(exc.code))
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            safe_error("network error", type(exc).__name__)
            diagnostics.record(method, kind="timeout" if isinstance(exc, TimeoutError) else "network")
            return None
        except (UnicodeDecodeError, json.JSONDecodeError):
            safe_error("response error", "invalid JSON")
            diagnostics.record(method, kind="invalid_response")
            return None


def send_text(token, chat_id, text, buttons=None, keyboard=None):
    """Send plain text (no HTML parsing, so draft content can never break formatting).

    buttons: optional rows of (label, callback_data) shown as inline buttons under the message.
    keyboard: optional custom reply keyboard.
      - list of rows of button texts e.g. [["✅ 91", "❌ 91"], ...] -> ReplyKeyboardMarkup with resize_keyboard=True
      - "remove" or {"remove_keyboard": True} -> ReplyKeyboardRemove
    """
    text = _scrub_outgoing(text)
    if not text:
        diagnostics.record("sendMessage", kind="empty_text")
        return False
    params = {"chat_id": chat_id, "text": text}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row]
                                                      for row in buttons]}
    elif keyboard is not None:
        if keyboard == "remove" or (isinstance(keyboard, dict) and keyboard.get("remove_keyboard")):
            params["reply_markup"] = {"remove_keyboard": True}
        elif isinstance(keyboard, list):
            keyboard_rows = []
            for row in keyboard:
                if isinstance(row, list):
                    keyboard_rows.append([{"text": str(btn)} if not isinstance(btn, dict) else btn for btn in row])
                else:
                    keyboard_rows.append([{"text": str(row)}])
            params["reply_markup"] = {"keyboard": keyboard_rows, "resize_keyboard": True}
        elif isinstance(keyboard, dict):
            params["reply_markup"] = keyboard

    try:
        result = _call(token, "sendMessage", params)
        return isinstance(result, dict) and result.get("ok") is True
    except Exception:
        diagnostics.record("sendMessage", kind="unexpected")
        return False


def send_document(token, chat_id, path, caption=""):
    """Send a local document to chat_id using Telegram multipart upload."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    filename = os.path.basename(path)
    fields = {"chat_id": str(chat_id)}
    if caption:
        scrubbed = _scrub_outgoing(caption)
        if scrubbed:
            fields["caption"] = scrubbed
    files = {"document": (filename, data)}
    try:
        res = _call_multipart(token, "sendDocument", fields, files)
        return isinstance(res, dict) and res.get("ok") is True
    except Exception:
        diagnostics.record("sendDocument", kind="unexpected")
        return False


def send_photo(token, chat_id, path, caption=""):
    """Send a local photo to chat_id using Telegram multipart upload."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    filename = os.path.basename(path)
    fields = {"chat_id": str(chat_id)}
    if caption:
        scrubbed = _scrub_outgoing(caption)
        if scrubbed:
            fields["caption"] = scrubbed
    files = {"photo": (filename, data)}
    try:
        res = _call_multipart(token, "sendPhoto", fields, files)
        return isinstance(res, dict) and res.get("ok") is True
    except Exception:
        diagnostics.record("sendPhoto", kind="unexpected")
        return False


def send_media_group(token, chat_id, paths, caption=""):
    """Send a group of local photos/documents to chat_id using Telegram multipart upload."""
    valid_paths = [p for p in paths if os.path.isfile(p)]
    if not valid_paths or len(valid_paths) > 10:
        return False
    media = []
    files = {}
    for idx, path in enumerate(valid_paths):
        attach_name = f"attach_{idx}"
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return False
        filename = os.path.basename(path)
        ext = os.path.splitext(filename)[1].lower()
        is_photo = ext in (".jpg", ".jpeg", ".png", ".webp")
        media_type = "photo" if is_photo else "document"
        item = {"type": media_type, "media": f"attach://{attach_name}"}
        if idx == 0 and caption:
            scrubbed = _scrub_outgoing(caption)
            if scrubbed:
                item["caption"] = scrubbed
        media.append(item)
        files[attach_name] = (filename, data)

    fields = {
        "chat_id": str(chat_id),
        "media": json.dumps(media)
    }
    try:
        res = _call_multipart(token, "sendMediaGroup", fields, files)
        return isinstance(res, dict) and res.get("ok") is True
    except Exception:
        diagnostics.record("sendMediaGroup", kind="unexpected")
        return False


def edit_text(token, chat_id, message_id, text, buttons=None):
    """Edit message plain text in place (e.g. 'Approved ✓' / 'Rejected: <reason>')."""
    text = _scrub_outgoing(text)
    if not text:
        return False
    params = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row]
                                                      for row in buttons]}
    try:
        result = _call(token, "editMessageText", params)
        return isinstance(result, dict) and result.get("ok") is True
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
        code = data.get("error_code")
        LAST_FETCH_ERROR = str(code) if type(code) is int and 100 <= code <= 599 else "not ok"
        return []
    return data.get("result", [])


def acknowledge_update(token, update_id):
    """Confirm on Telegram before any reply or side effect; independent of the next git save.

    getUpdates also returns pending newer updates. Leave them unconfirmed for the normal poll.
    An uncertain acknowledgement suppresses processing; a crash can lose a reply, never replay it.
    """
    try:
        result = _call(token, "getUpdates", {"offset": update_id + 1, "timeout": 0,
                                           "limit": 1, "allowed_updates": ALLOWED_UPDATES})
        if isinstance(result, dict) and result.get("ok") is True:
            return
    except Exception:
        pass
    raise RuntimeError("Telegram acknowledgement failed; this update was not handled.")


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


# ---- Office Requests Queue ---------------------------------------------------
OFFICE_INTENTS = {
    "rename_agent",
    "rename_room",
    "move_room",
    "resize_room",
    "theme",
    "language",
    "focus_room",
    "mute",
    "unmute",
    "undo_last_ui_change",
}

SAFE_OWNER_INTENTS = {
    "list_drafts",
    "show_status",
    "daily_report",
    "list_packs",
    "show_pack",
    "help",
    "what_happened_today",
    "post_today",
    "post_done",
}

RISKY_OWNER_INTENTS = {
    "approve_draft",
    "reject_draft",
    "pause_all",
    "resume_all",
    "run_now",
}

CONFIRM_EXPIRY_SECONDS = 10 * 60  # 10 minutes


def _ensure_office_requests_table(conn):
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='office_requests'").fetchone()
    if not cur:
        conn.execute("""CREATE TABLE IF NOT EXISTS office_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intent TEXT,
            args TEXT,
            ts TEXT
        )""")
    else:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(office_requests)").fetchall()}
        for col in ("intent", "args", "ts"):
            if col not in cols:
                conn.execute(f"ALTER TABLE office_requests ADD COLUMN {col} TEXT")


def store_office_request(intent, args, db_path=None):
    """Store an office UI request in the office_requests queue table.

    Three modules create this table with different columns (this file, office_requests.py and
    control_inbox.py). The row is therefore built from the columns that really exist, every
    NOT NULL column gets a value, and the connection is always closed (a failed insert used to
    crash the whole inbox and leave the database locked).
    """
    import uuid
    conn = guardrails.connect(db_path)
    try:
        _ensure_office_requests_table(conn)
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        args_json = json.dumps(args or {})
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(office_requests)").fetchall()}
        values = {"intent": intent, "args": args_json, "ts": ts}
        known = {
            "nonce": uuid.uuid4().hex,
            "digest": "local_office_ui",
            "receipt": json.dumps({"at": ts, "state": "held", "type": intent, "reason": "Office UI request queued"}),
            "kind": "undo" if intent == "undo_last_ui_change" else str(intent),
            "status": "queued",
            "target": str((args or {}).get("room") or (args or {}).get("agent") or (args or {}).get("target") or ""),
            "value": args_json,
            "label_te": _format_office_action_desc(intent, args or {}),
        }
        for name, row in columns.items():
            column_type, not_null, default, is_key = str(row[2] or "").upper(), row[3], row[4], row[5]
            if name in values:
                continue
            if name == "id":
                if "INT" not in column_type:          # text ids need a value; integer ids count by themselves
                    values["id"] = f"req_{uuid.uuid4().hex[:12]}"
            elif name in known:
                values[name] = known[name]
            elif not_null and default is None and not is_key:
                values[name] = ""
        names = list(values)
        conn.execute(f"INSERT INTO office_requests ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                     [values[name] for name in names])
        conn.commit()
    finally:
        conn.close()


def _format_office_action_desc(intent, args):
    args = args or {}
    if intent == "rename_agent":
        return f"rename agent {args.get('old', '')} to {args.get('new', '')}".strip()
    if intent == "rename_room":
        return f"rename room {args.get('old', '')} to {args.get('new', '')}".strip()
    if intent == "move_room":
        return f"move room {args.get('name', '')} near {args.get('near', '')}".strip()
    if intent == "resize_room":
        return f"{args.get('name', '')} room {args.get('size', '')}".strip()
    if intent == "theme":
        return f"theme {args.get('theme', '')}".strip()
    if intent == "language":
        return f"language {args.get('language', '')}".strip()
    if intent == "focus_room":
        return f"focus room {args.get('name', '')}".strip()
    if intent == "mute":
        return "mute"
    if intent == "unmute":
        return "unmute"
    if intent == "undo_last_ui_change":
        return "undo last UI change"
    return intent


def _format_risky_action_desc(intent, args):
    args = args or {}
    if intent == "approve_draft":
        n = args.get("n")
        return f"approve draft #{n}" if n is not None else "approve draft"
    if intent == "reject_draft":
        n = args.get("n")
        reason = args.get("reason")
        base = f"reject draft #{n}" if n is not None else "reject draft"
        return f"{base} ({reason})" if reason else base
    if intent == "pause_all":
        return "pause all"
    if intent == "resume_all":
        return "resume all"
    if intent == "run_now":
        return "run now"
    return intent


def create_confirmation_prompt(intent, args, db_path=None):
    """Create confirmation message and inline Yes/No buttons for a risky intent."""
    import uuid
    token = uuid.uuid4().hex[:12]
    now = time.time()
    record = {"intent": intent, "args": args, "ts": now}
    set_state(f"kd_confirm:{token}", json.dumps(record), db_path=db_path)
    action_desc = _format_risky_action_desc(intent, args)
    prompt_text = f"Confirm: {action_desc}? Yes/No"
    buttons = [[("Yes", f"kd:y:{token}"), ("No", f"kd:n:{token}")]]
    return prompt_text, buttons


def execute_confirmed_risky_action(intent, args, db_path=None):
    """Execute confirmed risky intent using the same code paths existing commands/buttons use."""
    args = args or {}
    if intent == "approve_draft":
        n = args.get("n")
        if n is None:
            return missing_number_text("approve", db_path)
        appr = guardrails.get_approval(n, db_path)
        if appr and appr.get("status") == "approved":
            return f"Approved ✓ #{n}"
        changed = guardrails.decide(n, True, db_path)
        if changed:
            return f"Approved ✓ #{n}"
        return f"Not changed (not found or already decided): #{n}"

    if intent == "reject_draft":
        n = args.get("n")
        if n is None:
            return missing_number_text("reject", db_path)
        reason = args.get("reason", "")
        reason_label = REASON_LABELS.get(reason.lower(), reason) if reason else ""
        appr = guardrails.get_approval(n, db_path)
        if appr and appr.get("status") == "rejected":
            r_exist = appr.get("reason")
            return f"Rejected: {r_exist}" if r_exist else f"Rejected: #{n}"
        changed = guardrails.decide(n, False, db_path)
        if changed:
            if reason_label:
                guardrails.set_reason(n, reason_label, db_path)
                return f"Rejected: {reason_label}"
            return f"Rejected: #{n}"
        return f"Not changed (not found or already decided): #{n}"

    if intent == "pause_all":
        guardrails.set_paused(True, db_path)
        import publisher
        publisher.set_global_stop(True, db_path)
        return ("⏸ Paused. From the next run, no AI work and no new drafts. "
                "I will still answer here. Send /resume to start again.")

    if intent == "resume_all":
        guardrails.set_paused(False, db_path)
        import publisher
        publisher.set_global_stop(False, db_path)
        return "▶️ Resumed. Work starts again on the next run."

    if intent == "run_now":
        try:
            import workforce
            res = workforce.run_once(max_tasks=3, db_path=db_path)
            return f"🚀 Engine run triggered!\nResult: {res['status']}, ran {res.get('ran', 0)} task(s)."
        except Exception as exc:
            return f"🚀 Engine run failed: {type(exc).__name__}"

    return "Unknown action."


# ---- Plugin Loader -----------------------------------------------------------
_PLUGIN_IMPORT_ERRORS_LOGGED = set()


def discover_plugins(repo_root=None):
    """Discover tg_*.py plugin modules in repo root (sorted, errors logged once)."""
    import importlib
    import importlib.util
    import glob
    root = repo_root or os.path.dirname(os.path.abspath(__file__))
    pattern = os.path.join(root, "tg_*.py")
    plugin_files = sorted(glob.glob(pattern))
    plugins = []

    for ppath in plugin_files:
        mod_name = os.path.splitext(os.path.basename(ppath))[0]
        try:
            if root != os.path.dirname(os.path.abspath(__file__)):
                spec = importlib.util.spec_from_file_location(mod_name, ppath)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                else:
                    raise ImportError(f"Cannot load spec from {ppath}")
            else:
                if mod_name in sys.modules:
                    mod = sys.modules[mod_name]
                else:
                    mod = importlib.import_module(mod_name)
            if hasattr(mod, "handle"):
                plugins.append(mod)
        except Exception as exc:
            if mod_name not in _PLUGIN_IMPORT_ERRORS_LOGGED:
                _PLUGIN_IMPORT_ERRORS_LOGGED.add(mod_name)
                print(f"Telegram plugin load error for {mod_name}: {exc}", file=sys.stderr)
            continue
    return plugins


def _send_text_kw(token, chat_id, text, buttons=None, keyboard=None):
    """Helper that passes keyboard only if non-None, avoiding unexpected kwarg in mocked send_text."""
    if keyboard is not None:
        return send_text(token, chat_id, text, buttons=buttons, keyboard=keyboard)
    return send_text(token, chat_id, text, buttons=buttons)


def download_telegram_file_to_path(token: str, file_id: str, dest_path: str, max_bytes: int = 2 * 1024 * 1024) -> bool:
    """Download a file by file_id from Telegram Bot API and save it to dest_path.

    Returns True on success, False on error or if max_bytes exceeded.
    """
    try:
        import media_brain
        data, err = media_brain.download_telegram_file(token, file_id, max_bytes=max_bytes)
        if err or not data:
            return False
        if len(data) > max_bytes:
            return False
        dest_dir = os.path.dirname(dest_path)
        if dest_dir:
            os.makedirs(dest_dir, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def build_plugin_ctx(token, chat_id, db_path=None, now=None, message=None):
    """Build the standard plugin ctx dictionary."""
    if now is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(now, datetime.datetime):
        now_utc = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    else:
        now_utc = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)

    def _ctx_send_text(text, keyboard=None):
        return _send_text_kw(token, chat_id, text, keyboard=keyboard)

    def _ctx_download_file(file_id, dest_path, max_bytes=2 * 1024 * 1024):
        return download_telegram_file_to_path(token, file_id, dest_path, max_bytes=max_bytes)

    ctx = {
        "db_path": db_path,
        "chat_id": str(chat_id),
        "now": now_utc,
        "send_text": _ctx_send_text,
        "send_document": lambda path, caption="": send_document(token, chat_id, path, caption=caption),
        "send_photo": lambda path, caption="": send_photo(token, chat_id, path, caption=caption),
        "send_media_group": lambda paths, caption="": send_media_group(token, chat_id, paths, caption=caption),
        "download_file": _ctx_download_file,
        "state_get": lambda key, default=None: get_state(key, default=default, db_path=db_path),
        "state_set": lambda key, value: set_state(key, value, db_path=db_path),
    }
    if message is not None:
        ctx["message"] = message
    return ctx


def call_plugins(text, token, chat_id, db_path=None, now=None, plugins=None, message=None):
    """Run loaded plugins on text in sorted order. Returns (handled: bool, reply_dict or None)."""
    if plugins is None:
        plugins = discover_plugins()
    if not plugins:
        return False, None

    ctx = build_plugin_ctx(token, chat_id, db_path=db_path, now=now, message=message)
    # A "did not understand" suggestion (tg_menu) is only a fallback: a later plugin that really owns
    # the command (for example /ideas in tg_reports) must win.
    fallback = None
    for plugin in plugins:
        try:
            res = plugin.handle(text, ctx)
        except Exception as exc:
            print(f"Plugin error in {getattr(plugin, '__name__', 'unknown')}: {exc}", file=sys.stderr)
            continue
        if res is not None:
            if isinstance(res, str):
                res = {"text": res}
            elif not isinstance(res, dict):
                res = {"handled": True}
            reply_text = str(res.get("text", ""))
            # tg_menu's guesses ("ardam kaaledu ... try these", "Meeru '/ops' anukunnara?") are only a
            # fallback. Without the second check its typo guess for "Yes" hid the reply from tg_settings,
            # so a settings change could never be confirmed.
            if fallback is None and (reply_text.startswith("Meeru cheppindi ardam kaaledu")
                                     or (reply_text.startswith("Meeru '") and "anukunnara?" in reply_text)):
                fallback = res
                continue
            return True, res
    if fallback is not None:
        return True, fallback
    return False, None


def plugin_words(plugins=None):
    """Lower-case words and slash commands every loaded plugin declares in COMMANDS."""
    words = set()
    for plugin in (discover_plugins() if plugins is None else plugins):
        for entry in getattr(plugin, "COMMANDS", None) or []:
            if not isinstance(entry, dict):
                continue
            for word in [entry.get("cmd", "")] + list(entry.get("words") or []):
                word = " ".join(str(word).lower().split())
                if word:
                    words.add(word)
    return words


def plugin_can_answer(text, plugins=None):
    """True when the text looks like something a plugin answers (checked without running plugins)."""
    low = " ".join(str(text or "").lower().split()).rstrip("?.! ")
    if not low:
        return False
    if low.startswith("/"):
        return True
    try:
        for word in plugin_words(plugins):
            if low == word or low.startswith(word + " ") or low.endswith(" " + word):
                return True
        import talk_intents
        return talk_intents.detect(text) is not None
    except Exception:
        return False


# What the old owner_commands parser's safe intents run, as a command word (to compare with talk_intents).
_OWNER_INTENT_COMMAND = {"show_pack": "/pack", "list_drafts": "/list", "list_packs": "/packs",
                         "show_status": "/status", "what_happened_today": "daily", "daily_report": "daily",
                         "post_today": "/today", "post_done": "/posted"}
_MENU_FALLBACK = "Meeru cheppindi ardam kaaledu"


def plugins_first(text, owner_intent, plugins=None):
    """True when a plugin must see this plain text BEFORE the old owner_commands intent is used.

    owner_commands matches loosely ("pack 3 ..." is always show_pack, "... approve ..." is always
    approve_draft), so plugin sentences such as "pack 3 files pampu" or "auto approve status" never
    reached their plugin. Office wording (theme, rename room, undo ...) keeps its old meaning.
    """
    raw = str(text or "").strip()
    low = " ".join(raw.lower().split()).rstrip("?.! ")
    if not low or owner_intent == "unknown":
        return False                      # unknown text goes to the plugins anyway
    if raw.startswith("/"):
        return True                       # a slash command that is not built in belongs to a plugin
    if owner_intent in OFFICE_INTENTS:
        # "quiet 22-8" / "quiet off" are robot settings (tg_settings), not the office "mute".
        try:
            import tg_settings
            return bool(tg_settings.parse_setting_request(raw))
        except Exception:
            return False
    try:
        # Exactly a word a plugin declares ("office status", "auto approve"). Today's-posts wording keeps
        # the built-in /today and /posted answers it always had.
        if low in plugin_words(plugins) and owner_intent not in ("post_today", "post_done"):
            return True
        import talk_intents
        found = talk_intents.detect(raw)
        if not found or not talk_intents.should_run(found) or found["confidence"] < 0.9:
            return False
        return found["command"].split()[0] != _OWNER_INTENT_COMMAND.get(owner_intent)
    except Exception:
        return False


# ---- command handling --------------------------------------------------------
_COMMANDS = "status|list|view|approve|reject|packs|pack|marketing|pipeline|pause|resume|buttontest|rules|cubes|doctor|autofix_drill|help|start|today|posted|ab|week"
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


def pipeline_text(db_path=None):
    """Format today's daily pipeline plan vs done per stage, backlog, and stuck items."""
    import workforce
    import doctor
    import product_builder

    # Run doctor check to detect and hand off any stuck items
    try:
        doctor_res = doctor.run_doctor_checks(db_path=db_path)
    except Exception:
        doctor_res = {"stuck_found": 0, "handled": 0}

    stuck_items = doctor.detect_stuck_tasks(db_path=db_path)

    plan_info = workforce.get_daily_pipeline_plan(db_path=db_path)
    total_quotas = plan_info["total_quotas"]

    # Calculate done today per stage
    today_str = datetime.date.today().isoformat()
    conn = workforce.connect(db_path)
    done_rows = conn.execute(
        "SELECT kind, COUNT(*) FROM tasks WHERE status = 'done' AND finished_ts LIKE ? GROUP BY kind",
        (today_str + "%",)
    ).fetchall()
    done_by_kind = dict(done_rows)

    # Queue backlog counts
    queued_rows = conn.execute(
        "SELECT kind, COUNT(*) FROM tasks WHERE status = 'queued' GROUP BY kind"
    ).fetchall()
    queued_by_kind = dict(queued_rows)
    conn.close()

    pending_approvals = len(guardrails.pending(db_path))
    built_products = len([b for b in product_builder.builds(db_path) if b["status"] == "built"])

    lines = [f"📊 *Product Pipeline Status* ({today_str})\n"]
    lines.append(f"AI Calls Today: {plan_info['calls_today']} / {plan_info['cap']}")
    lines.append(f"Health Scaling: {int(plan_info['health_mult'] * 100)}%\n")

    lines.append("*Today's Plan vs Done:*")
    lines.append(f"• Research: {done_by_kind.get('research', 0)} / {total_quotas.get('research', 0)} planned")
    lines.append(f"• Drafts: {done_by_kind.get('draft', 0)} / {total_quotas.get('drafts', 0)} planned")
    lines.append(f"• Reviews: {done_by_kind.get('review', 0)} / {total_quotas.get('review', 0)} planned")
    lines.append(f"• Builds: {built_products} / {total_quotas.get('product_builds', 0)} planned\n")

    lines.append("*Current Backlog:*")
    lines.append(f"• Queued Tasks: {sum(queued_by_kind.values())} (Research {queued_by_kind.get('research', 0)}, Drafts {queued_by_kind.get('draft', 0)})")
    lines.append(f"• Pending Approvals: {pending_approvals}")
    lines.append(f"• Built Products: {built_products}\n")

    if stuck_items:
        lines.append(f"⚠️ *Stuck Items:* {len(stuck_items)} (handed to Doctor)")
        for s in stuck_items[:3]:
            lines.append(f"  - Task #{s['task_id']} ({s['agent']}, {s['step']})")
    else:
        lines.append("✅ *Stuck Items:* 0")

    return "\n".join(lines)


def _format_ist_time(ts_str):
    """Convert ISO timestamp to IST string or return 'unknown'."""
    if not ts_str:
        return "unknown"
    try:
        from zoneinfo import ZoneInfo
        ist_zone = ZoneInfo("Asia/Kolkata")
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        ist_dt = dt.astimezone(ist_zone)
        return ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")
    except Exception:
        return "unknown"


def status_text(db_path=None):
    """Compact status: AI providers, GitHub minutes, drafts, products, pipeline, last run IST."""
    import public_export

    # 1. AI providers ok/failing (names only)
    ok_providers = []
    fail_providers = []
    try:
        ai_health = public_export.get_public_ai_health(db_path)
        for item in ai_health:
            p_name = item.get("provider")
            state = item.get("state")
            if state == "up":
                ok_providers.append(p_name)
            elif state == "down":
                fail_providers.append(p_name)
    except Exception:
        pass

    ai_parts = []
    if ok_providers:
        ai_parts.append(f"ok: {', '.join(ok_providers)}")
    if fail_providers:
        ai_parts.append(f"failing: {', '.join(fail_providers)}")
    ai_line = f"AI providers: {'; '.join(ai_parts)}" if ai_parts else "AI providers: unknown"

    # 2. GitHub minutes used/limit or "unknown"
    try:
        minutes_info = public_export.get_public_minutes(db_path)
        used = minutes_info.get("used_this_month")
        limit = minutes_info.get("limit")
        if used is not None and limit is not None:
            min_str = f"{used}/{limit}"
        elif used is not None:
            min_str = str(used)
        else:
            min_str = "unknown"
    except Exception:
        min_str = "unknown"
    gh_line = f"GitHub minutes: {min_str}"

    # 3. Today's drafts made/waiting/approved & products built today
    try:
        rep = public_export.get_public_report(db_path)
        d_made = rep.get("drafts_made", 0)
        d_waiting = rep.get("drafts_waiting", 0)
        d_approved = rep.get("drafts_approved", 0)
        built_today = rep.get("products_built", 0)
    except Exception:
        d_made, d_waiting, d_approved, built_today = 0, 0, 0, 0
    drafts_line = f"Drafts today: {d_made} made, {d_waiting} waiting, {d_approved} approved"
    built_line = f"Products built today: {built_today}"

    # 4. Products READY / NEEDS FIX counts
    try:
        products = public_export.get_public_products(db_path)
        ready_cnt = sum(1 for p in products if (p.get("truth_check") or {}).get("verdict") == "READY")
        needs_fix_cnt = sum(1 for p in products if (p.get("truth_check") or {}).get("verdict") == "NEEDS FIX")
    except Exception:
        ready_cnt, needs_fix_cnt = 0, 0
    prod_check_line = f"Product quality: {ready_cnt} READY, {needs_fix_cnt} NEEDS FIX"

    # 5. Pipeline plan vs done
    try:
        pipeline = public_export.get_public_pipeline(db_path)
        pipe_items = []
        for stage, data in pipeline.items():
            pipe_items.append(f"{stage} {data.get('done', 0)}/{data.get('plan', 0)}")
        pipe_line = f"Pipeline: {', '.join(pipe_items)}" if pipe_items else "Pipeline: unknown"
    except Exception:
        pipe_line = "Pipeline: unknown"

    # 6. Last run time IST
    last_run_ist = "unknown"
    try:
        hb = public_export.get_public_cloud_heartbeat()
        end_ts = hb.get("last_run_end") or hb.get("last_run_start")
        if end_ts:
            last_run_ist = _format_ist_time(end_ts)
        else:
            state_ts = get_state("last_cloud_run_ts", None, db_path)
            if state_ts:
                last_run_ist = _format_ist_time(state_ts)
            else:
                import workforce
                conn = workforce.connect(db_path)
                row = conn.execute("SELECT ts FROM heartbeat WHERE name = 'worker'").fetchone()
                if not row:
                    row = conn.execute("SELECT MAX(finished_ts) FROM tasks WHERE status = 'done'").fetchone()
                conn.close()
                if row and row[0]:
                    last_run_ist = _format_ist_time(row[0])
    except Exception:
        pass
    last_run_line = f"Last run: {last_run_ist}"

    # 7. Packs the owner already listed on Gumroad (/listed N <link>); no line when there are none
    listed_line = ""
    try:
        import tg_listed
        listed_line = tg_listed.status_line(db_path)
    except Exception:
        listed_line = ""

    return "\n".join([line for line in [
        "🤖 Chilli Gavva Status",
        ai_line,
        gh_line,
        drafts_line,
        built_line,
        prod_check_line,
        listed_line,
        pipe_line,
        last_run_line,
    ] if line])


def _format_size(size_bytes: int) -> str:
    """Format file size into human readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _find_ready_packs(products_dir=None):
    """Return all ready upload packs as sorted list of (num, title, path)."""
    import pathlib
    import product_builder
    base = pathlib.Path(products_dir or product_builder.PRODUCTS_DIR)
    packs = []
    if base.is_dir():
        search_dirs = []
        if (base / "upload").is_dir():
            search_dirs.append(base / "upload")
        search_dirs.append(base)
        seen = set()
        for sdir in search_dirs:
            try:
                entries = sorted(sdir.iterdir())
            except OSError:
                continue
            for p in entries:
                try:
                    if not p.is_dir() or p.name == "upload" or p.resolve() in seen:
                        continue
                    if (p / "product.zip").is_file() and (p / "listing.txt").is_file() and (p / "checklist.txt").is_file():
                        seen.add(p.resolve())
                        m = re.match(r"^(\d+)", p.name)
                        num = int(m.group(1)) if m else None
                        title = "Ready Pack"
                        try:
                            txt = (p / "listing.txt").read_text(encoding="utf-8", errors="replace")
                            m_title = re.search(r"^Title:\s*(.+)$", txt, re.MULTILINE)
                            if m_title:
                                title = m_title.group(1).strip()
                            else:
                                title = p.name
                        except Exception:
                            pass
                        packs.append((num, title, p))
                except OSError:
                    continue
    packs.sort(key=lambda x: (x[0] if x[0] is not None else 999999, str(x[2])))
    return packs


def ready_packs_text(page=1, products_dir=None):
    """Format ready upload packs, 10 per page, with pagination hints."""
    import math
    packs = _find_ready_packs(products_dir=products_dir)
    if not packs:
        return "No ready upload packs found."

    total_packs = len(packs)
    page_size = 10
    total_pages = max(1, math.ceil(total_packs / page_size))

    try:
        page_num = int(page) if page is not None else 1
    except (ValueError, TypeError):
        page_num = 1
    if page_num < 1:
        page_num = 1
    if page_num > total_pages:
        page_num = total_pages

    start_idx = (page_num - 1) * page_size
    page_items = packs[start_idx:start_idx + page_size]

    lines = [f"Ready Upload Packs ({page_num}/{total_pages}):"]
    for num, title, _ in page_items:
        prefix = f"#{num}" if num is not None else "-"
        lines.append(f"{prefix} {title}")

    if page_num < total_pages:
        lines.append(f"\nSend /packs {page_num + 1} for next page.")
    return "\n".join(lines)


def today_posts_text(plan_path=None, products_dir=None, date=None):
    """Load or build the post plan and return today's post text <= 1200 chars."""
    import post_planner
    plan_file = plan_path or post_planner.PLAN_PATH
    prod_dir = products_dir or "exported_content/products"
    plan = post_planner.load_plan(plan_file, products_dir=prod_dir)
    return post_planner.today_text(plan, date=date)


def mark_post_done_text(n, plan_path=None, products_dir=None, date=None):
    """Mark post N as done, atomically save plan, and return confirmation + week summary."""
    import post_planner
    plan_file = plan_path or post_planner.PLAN_PATH
    prod_dir = products_dir or "exported_content/products"
    plan = post_planner.load_plan(plan_file, products_dir=prod_dir)

    if n is None or n < 1:
        return "Please specify a valid post number: /posted <number> (e.g. /posted 1)"

    ok = post_planner.mark_posted(plan, date=date, n=n)
    if not ok:
        return f"Could not mark post {n} as done. Check /today for available posts."

    post_planner.save_plan(plan, plan_file)
    summary = post_planner.week_summary(plan, date=date)
    return f"Sare, post {n} done ✓\n{summary}"


def _find_pack_target(aid, products_dir=None):
    """Find the directory path of ready pack aid, or None."""
    import pathlib
    import product_builder
    base = pathlib.Path(products_dir or product_builder.PRODUCTS_DIR)
    if not base.is_dir():
        return None
    search_dirs = []
    if (base / "upload").is_dir():
        search_dirs.append(base / "upload")
    search_dirs.append(base)
    for sdir in search_dirs:
        try:
            entries = sorted(sdir.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                if not p.is_dir() or p.name == "upload":
                    continue
                if re.match(rf"^{aid}(?:-.*)?$", p.name):
                    if (p / "product.zip").is_file() and (p / "listing.txt").is_file() and (p / "checklist.txt").is_file():
                        return p
            except OSError:
                continue
    return None


def upload_pack_listing_text(aid, db_path=None, products_dir=None):
    target = _find_pack_target(aid, products_dir=products_dir)
    if target:
        try:
            raw = (target / "listing.txt").read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return f"Could not read pack #{aid}: {exc}"
        try:
            import upload_pack
            upload_pack._check_money(raw, f"pack #{aid} listing")
        except ValueError:
            return f"Pack #{aid} contains a money amount; owner must set price."
        clean = []
        for line in raw.splitlines():
            if ".zip" in line.lower() or "/" in line or os.sep in line:
                continue
            clean.append(line)
        return _scrub_outgoing("\n".join(clean).strip())
    try:
        import product_pipeline
        return product_pipeline.pack_text(aid, db_path)
    except Exception:
        return f"No ready upload pack found for #{aid}."


def pack_detail_text(aid, db_path=None, products_dir=None, marketing_dir=None):
    """Return detailed pack information: files with sizes, deep QA verdict, marketing assets, and listing lint."""
    import pathlib
    target = _find_pack_target(aid, products_dir=products_dir)
    if not target:
        try:
            import product_pipeline
            pipe_text = product_pipeline.pack_text(aid, db_path)
            if "No approved upload pack" not in pipe_text and "not ready yet" not in pipe_text:
                return _scrub_outgoing(pipe_text)
        except Exception:
            pass
        return f"No ready upload pack found for #{aid}."

    # Validate no money amounts in listing.txt
    listing_path = target / "listing.txt"
    listing_raw = ""
    if listing_path.is_file():
        try:
            listing_raw = listing_path.read_text(encoding="utf-8", errors="replace")
            import upload_pack
            upload_pack._check_money(listing_raw, f"pack #{aid} listing")
        except ValueError:
            return f"Pack #{aid} contains a money amount; owner must set price."
        except Exception as exc:
            return f"Could not read pack #{aid}: {exc}"

    # Extract title
    title = f"Pack #{aid}"
    m_title = re.search(r"^Title:\s*(.+)$", listing_raw, re.MULTILINE)
    if m_title:
        title = m_title.group(1).strip()
    else:
        title = target.name

    lines = [f"Pack #{aid}: {title}\n"]

    # 1. Files (names + sizes)
    lines.append("Files:")
    file_items = []
    try:
        for f in sorted(target.iterdir()):
            if f.is_file():
                try:
                    size_str = _format_size(f.stat().st_size)
                    file_items.append(f"- {f.stem} ({f.suffix.lstrip('.') or 'file'}, {size_str})")
                except OSError:
                    file_items.append(f"- {f.stem} ({f.suffix.lstrip('.') or 'file'})")
    except OSError:
        pass
    if file_items:
        lines.extend(file_items)
    else:
        lines.append("- (no files found)")
    lines.append("")

    # 2. Deep QA verdict
    qa_verdict = "unknown"
    qa_score = None
    try:
        import product_qa_deep
        qa_res = product_qa_deep.check_folder(str(target))
        if isinstance(qa_res, dict):
            qa_verdict = qa_res.get("verdict", "unknown")
            qa_score = qa_res.get("score")
    except Exception:
        qa_verdict = "check skipped"

    if qa_score is not None:
        lines.append(f"Deep QA: {qa_verdict} (score: {qa_score})")
    else:
        lines.append(f"Deep QA: {qa_verdict}")

    # 3. Marketing files ready (carousel/pins/reel/blog yes-no)
    mkt_base = pathlib.Path(marketing_dir or "exported_content/marketing")
    pack_slug = target.name

    carousel_ready = False
    pins_ready = False
    reel_ready = False
    blog_ready = False

    candidate_mkt_dirs = []
    if mkt_base.is_dir():
        p_exact = mkt_base / pack_slug
        if p_exact.is_dir():
            candidate_mkt_dirs.append(p_exact)
        m_id = str(aid)
        for child in mkt_base.iterdir():
            if child.is_dir() and (child.name == m_id or child.name.startswith(f"{m_id}-")):
                if child not in candidate_mkt_dirs:
                    candidate_mkt_dirs.append(child)

    for cdir in candidate_mkt_dirs:
        car_dir = cdir / "carousel"
        if car_dir.is_dir() and any(car_dir.glob("*.png")):
            carousel_ready = True
        pins_dir = cdir / "pins"
        if pins_dir.is_dir() and (any(pins_dir.glob("*.png")) or (pins_dir / "pins.json").is_file()):
            pins_ready = True
        reel_dir = cdir / "reel"
        if reel_dir.is_dir() and (any(reel_dir.glob("*.png")) or (reel_dir / "reel.txt").is_file()):
            reel_ready = True
        if (cdir / "blog.md").is_file():
            blog_ready = True

    lines.append(
        f"Marketing: carousel={'yes' if carousel_ready else 'no'}, "
        f"pins={'yes' if pins_ready else 'no'}, "
        f"reel={'yes' if reel_ready else 'no'}, "
        f"blog={'yes' if blog_ready else 'no'}"
    )

    # 4. listing_lint result if listing_lint.py exists (import guarded)
    try:
        import listing_lint  # type: ignore
        lint_fn = getattr(listing_lint, "lint_listing", None)
        folder_fn = getattr(listing_lint, "lint_folder", None)
        if callable(lint_fn):
            lines.append(f"Listing lint: {lint_fn(str(target))}")
        elif callable(folder_fn):
            results, ok = folder_fn(str(target))
            fails = sum(1 for r in results if r[1] == "FAIL")
            warns = sum(1 for r in results if r[1] == "WARN")
            lines.append(f"Listing lint: {'pass' if ok else 'fail'} ({fails} fail, {warns} warn)")
        else:
            lines.append("Listing lint: available")
    except ImportError:
        pass
    except Exception as exc:
        lines.append(f"Listing lint: error ({type(exc).__name__})")

    details = "\n".join(lines).strip()
    # The owner copies the listing from here, so the full listing text always comes first.
    listing = upload_pack_listing_text(aid, db_path=db_path, products_dir=products_dir)
    room = 3500 - len(listing) - 2
    full_text = listing + ("\n\n" + details[:room] if room > 40 else "")
    return _scrub_outgoing(full_text[:3500])


def week_summary_text(db_path=None, plan_path=None, products_dir=None, date=None):
    """Format this week's counts in Tenglish style: drafts, packs ready, posts done, A/B leaders."""
    import datetime
    import post_planner
    import ab_results

    if date is None:
        ref_dt = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    elif isinstance(date, str):
        ref_dt = datetime.datetime.fromisoformat(date)
    elif isinstance(date, datetime.date) and not isinstance(date, datetime.datetime):
        ref_dt = datetime.datetime.combine(date, datetime.time.min)
    else:
        ref_dt = date

    start_of_week = (ref_dt - datetime.timedelta(days=ref_dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    since_iso = start_of_week.isoformat()

    conn = guardrails.connect(db_path)
    try:
        made = conn.execute("SELECT COUNT(*) FROM approvals WHERE ts >= ?", (since_iso,)).fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM approvals WHERE status = 'approved' AND decided_ts >= ?", (since_iso,)).fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM approvals WHERE status = 'rejected' AND decided_ts >= ?", (since_iso,)).fetchone()[0]
    finally:
        conn.close()

    packs = _find_ready_packs(products_dir=products_dir)
    packs_count = len(packs)

    plan_file = plan_path or post_planner.PLAN_PATH
    prod_dir = products_dir or "exported_content/products"
    plan = post_planner.load_plan(plan_file, products_dir=prod_dir)
    posts_str = post_planner.week_summary(plan, date=(ref_dt.date() if isinstance(ref_dt, datetime.datetime) else ref_dt))

    leaders = ab_results.get_all_ab_leaders(db_path=db_path)
    if leaders:
        leader_lines = []
        for lead in leaders:
            leader_lines.append(f"  • Pack #{lead['pack']}: #{lead['title_num']} ({lead['title']}) — {lead['clicks']} clicks 👑")
        ab_section = "A/B Title Leaders:\n" + "\n".join(leader_lines)
    else:
        ab_section = "A/B Title Leaders: Inka clicks record avaledu."

    lines = [
        "🗓️ *Ee Vaaram Summary (This Week)*:\n",
        f"• *Drafts*: {made} made, {approved} approved, {rejected} rejected",
        f"• *Upload packs ready*: {packs_count} packs",
        f"• *Posts*: {posts_str}",
        f"\n{ab_section}\n",
        "Next steps kosam /packs or /today choodandi!",
    ]
    return _scrub_outgoing("\n".join(lines).strip())


# "/marketing 5", "pack 5 marketing" or Tenglish "marketing 5 chupinchu".
_MARKETING_PHRASE = re.compile(
    r"^\s*(?:/marketing\s+(\d{1,6})|pack\s+(\d{1,6})\s+marketing|marketing\s+(\d{1,6})\s+chupinchu)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def parse_marketing_phrase(text):
    """Return the pack number for a /marketing request, or None if this is not one."""
    m = _MARKETING_PHRASE.match((text or "").strip())
    if not m:
        return None
    for group in m.groups():
        if group is not None:
            return int(group)
    return None


def _find_marketing_pack_dir(n, marketing_dir=None):
    """Marketing folder for pack n (exact or '<n>-' prefix names), or None."""
    import pathlib
    # Default: the shared marketing folder, then the private one marketing_auto fills for READY packs
    # (exported_content/products/marketing/<pack>/). Before, files made there were never found.
    bases = [marketing_dir] if marketing_dir else ["exported_content/marketing",
                                                   "exported_content/products/marketing"]
    for base in (pathlib.Path(item) for item in bases):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            try:
                if child.is_dir() and (child.name == str(n) or child.name.startswith(f"{n}-")):
                    return child
            except OSError:
                continue
    return None


def _marketing_pin_titles(pack_dir):
    """The 5 A/B pin titles: pins.json first, then ab_titles.txt ('#'-lines skipped)."""
    titles = []
    pins_json = pack_dir / "pins" / "pins.json"
    if pins_json.is_file():
        try:
            data = json.loads(pins_json.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("title"):
                        t = str(item["title"]).strip()
                    elif isinstance(item, str) and item.strip():
                        t = item.strip()
                    else:
                        t = ""
                    if t and t not in titles:
                        titles.append(t)
        except (OSError, ValueError):
            pass
    if not titles:
        ab_file = pack_dir / "pins" / "ab_titles.txt"
        if ab_file.is_file():
            try:
                for line in ab_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.lstrip().startswith("#"):
                        continue
                    cleaned = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
                    if cleaned and cleaned not in titles:
                        titles.append(cleaned)
            except OSError:
                pass
    return titles[:5]


def marketing_text(n=None, marketing_dir=None):
    """One message on what exists for pack n in exported_content/marketing/<pack>/ (owner only)."""
    if n is None:
        return "Send /marketing <pack number> (e.g. /marketing 5)."
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "Send /marketing <pack number> (e.g. /marketing 5)."

    pack_dir = _find_marketing_pack_dir(n, marketing_dir)
    if pack_dir is None:
        return f"Pack #{n} ki marketing files inka ledu. /pack {n} tho pack status chudandi."

    lines = [f"Marketing pack #{n} ({pack_dir.name}):"]

    car_dir = pack_dir / "carousel"
    slides = list(car_dir.glob("slide-*.png")) if car_dir.is_dir() else []
    lines.append(f"Carousel: {len(slides)} slides")

    pins_dir = pack_dir / "pins"
    titles = _marketing_pin_titles(pack_dir)
    if pins_dir.is_dir() and (titles or list(pins_dir.glob("pin_*.png")) or (pins_dir / "pins.json").is_file()):
        if titles:
            count_line = f"Pins: {len(list(pins_dir.glob('pin_*.png'))) or len(titles)} (5 A/B titles):"
        else:
            count_line = f"Pins: {len(list(pins_dir.glob('pin_*.png')))}"
        lines.append(count_line)
        for i, t in enumerate(titles, 1):
            lines.append(f"  {i}. {t}")
    else:
        lines.append("Pins: 0")

    reel_dir = pack_dir / "reel"
    frames = list(reel_dir.glob("frame-*.png")) if reel_dir.is_dir() else []
    lines.append(f"Reel frames: {len(frames)}")

    blog_file = pack_dir / "blog.md"
    if blog_file.is_file():
        try:
            words = len(blog_file.read_text(encoding="utf-8", errors="replace").split())
        except OSError:
            words = 0
        lines.append(f"Blog: {words} words")
    else:
        lines.append("Blog: 0 words")

    lines.append(f"LAUNCH.md: {'yes' if (pack_dir / 'LAUNCH.md').is_file() else 'no'}")
    lines.append(f"seo_meta: {'yes' if (pack_dir / 'seo_meta.json').is_file() else 'no'}")
    lines.append(f"Mac lo folder: Marketing/{pack_dir.name}")

    return _scrub_outgoing("\n".join(lines))


def handle_command(cmd, ids, db_path=None):
    if cmd == "status":
        return status_text(db_path)
    if cmd == "packs":
        page = ids[0] if ids else 1
        return ready_packs_text(page=page)
    if cmd == "pack":
        return pack_detail_text(ids[0], db_path) if ids else "Send /pack <number> to view pack details."
    if cmd == "marketing":
        return marketing_text(ids[0] if ids else None)
    if cmd == "list":
        return list_text(db_path)
    if cmd == "view":
        return view_text(ids[0], db_path) if ids else missing_number_text("view", db_path)
    if cmd == "approve":
        return decide_text(ids, True, db_path)
    if cmd == "reject":
        return decide_text(ids, False, db_path)
    if cmd == "pipeline":
        return pipeline_text(db_path)
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
    if cmd == "doctor":
        import doctor
        return doctor.format_last_cases(db_path=db_path)
    if cmd == "autofix_drill":
        import autofix
        res = autofix.run_drill()
        return f"Auto-fix drill: {res['verdict']}\n{res['summary']}"
    if cmd == "today":
        return today_posts_text()
    if cmd == "posted":
        n = ids[0] if ids else None
        return mark_post_done_text(n)
    if cmd == "ab":
        import ab_results
        if len(ids) >= 3:
            return ab_results.parse_and_handle_ab(f"/ab {ids[0]} {ids[1]} {ids[2]}", db_path=db_path)
        elif len(ids) == 1:
            return ab_results.format_ab_table(str(ids[0]), db_path=db_path)
        else:
            return "A/B Title Tracking:\nUsage: /ab <pack> (to view) or /ab <pack> <title 1-5> <clicks> (to update)."
    if cmd == "week":
        return week_summary_text(db_path=db_path)
    return HELP


_CALLBACK = re.compile(r"^(a|r|v):(\d{1,9})$|^y:(\d{1,9}):(.{1,30})$|^(t):ping$")
BUTTON_TEST_OK = "✅ Button presses reach the bot. Nothing was changed."


def handle_callback(token, owner_chat_id, data, db_path=None, message_id=None):
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
    if data and data.startswith("doc:"):
        import doctor
        reply = doctor.handle_callback(token, owner_chat_id, data, db_path)
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
    if data and data.startswith("kd:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            choice, conf_token = parts[1], parts[2]
            state_key = f"kd_confirm:{conf_token}"
            raw_rec = get_state(state_key, None, db_path=db_path)
            if not raw_rec:
                reply = "Confirmation expired or not found."
                send_text(token, owner_chat_id, reply)
                return reply
            try:
                rec = json.loads(raw_rec)
            except Exception:
                rec = {}
            # Delete token to make it single-use
            set_state(state_key, "", db_path=db_path)
            now = time.time()
            created_ts = rec.get("ts", 0)
            if now - created_ts > CONFIRM_EXPIRY_SECONDS:
                reply = "Confirmation expired (exceeded 10 minutes)."
                send_text(token, owner_chat_id, reply)
                return reply
            if choice == "n":
                reply = f"Cancelled action '{rec.get('intent', 'action')}'."
                send_text(token, owner_chat_id, reply)
                return reply
            if choice == "y":
                reply = execute_confirmed_risky_action(rec.get("intent"), rec.get("args"), db_path=db_path)
                send_text(token, owner_chat_id, reply)
                return reply
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
            appr = guardrails.get_approval(approval_id, db_path)
            if appr and appr.get("status") == "approved":
                return "Approved ✓"
            changed = guardrails.decide(approval_id, True, db_path)
            if changed:
                reply = f"Approved ✓ #{approval_id}"
                if message_id:
                    edit_text(token, owner_chat_id, message_id, reply)
                else:
                    send_text(token, owner_chat_id, reply)
            else:
                reply = f"Not changed (not found or already decided): #{approval_id}"
                send_text(token, owner_chat_id, reply)
            return reply
        # Reject button pressed
        appr = guardrails.get_approval(approval_id, db_path)
        if appr and appr.get("status") == "rejected":
            reason = appr.get("reason")
            return f"Rejected: {reason}" if reason else "Rejected"
        changed = guardrails.decide(approval_id, False, db_path)
        if changed:
            reply = f"Rejected: #{approval_id}. Why? Pick a reason so the agents can learn:"
            if message_id:
                edit_text(token, owner_chat_id, message_id, reply, reason_buttons(approval_id))
            else:
                send_text(token, owner_chat_id, reply, reason_buttons(approval_id))
        else:
            reply = f"Not changed (not found or already decided): #{approval_id}"
            send_text(token, owner_chat_id, reply)
        return reply
    approval_id, raw_reason = int(m.group(3)), m.group(4).strip()
    reason_label = REASON_LABELS.get(raw_reason.lower(), raw_reason)
    # Check if reason is already set in DB
    conn = guardrails.connect(db_path)
    cur_reason_row = conn.execute("SELECT reason FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    conn.close()
    if cur_reason_row and cur_reason_row[0]:
        return f"Rejected: {cur_reason_row[0]}"

    if guardrails.set_reason(approval_id, reason_label, db_path) or guardrails.set_reason(approval_id, raw_reason, db_path):
        reply = f"Rejected: {reason_label}"
        if message_id:
            edit_text(token, owner_chat_id, message_id, reply)
        else:
            send_text(token, owner_chat_id, reply)
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
    return {"text": 0, "button": 0, "other": 0, "handled": 0, "received": 0, "commands": 0,
            "skipped": {"not owner chat": 0, "not a command": 0, "unknown button": 0}}


def _link_belongs_to_command(message_text):
    """True when a message with a link is a slash command or a /listed sentence (handled by plugins)."""
    text = (message_text or "").strip()
    if text.startswith("/"):
        return True
    try:
        import tg_listed
        return tg_listed.parse(text) is not None
    except Exception:
        return False


def process_updates(token, owner_chat_id, db_path=None, stats=None):
    """Handle new commands and button presses from the owner chat. Returns number handled.

    stats (optional dict from new_stats()) is filled with counts only: never ids, tokens or text.
    """
    global LAST_FETCH_ERROR
    stats = stats if stats is not None else new_stats()
    offset = get_state("update_offset", None, db_path)
    LAST_FETCH_ERROR = None
    updates = fetch_updates(token, offset)
    stats["received"] = len(updates)
    if LAST_FETCH_ERROR:
        raise RuntimeError("Telegram updates could not be read; inbox stopped before replies.")
    handled = 0
    valid = [upd for upd in updates if type(upd.get("update_id")) is int]
    next_offset = int(offset) if offset else None

    # Pre-count occurrences of each message key for owner messages in this batch
    def _message_key(m_text):
        app_id = parse_start_payload(m_text)
        if app_id is not None:
            return ("start_payload", app_id)
        c, c_ids = parse_command(m_text)
        if not c:
            u = re.findall(r"https?://[^\s]+", m_text)
            if u:
                return ("url", u[0])
            if re.match(r"^\s*(?:ee\s*vaaram|eevaaram|this\s*week)\s*$", m_text or "", re.IGNORECASE):
                return ("week_cmd",)
            m_n = parse_marketing_phrase(m_text)
            if m_n is not None:
                return ("marketing_cmd", m_n)
            import ab_results
            if ab_results.parse_and_handle_ab(m_text, db_path=db_path) is not None:
                return ("ab_cmd", m_text.strip().lower())
            import owner_commands
            cmd_info = owner_commands.parse(m_text)
            intent = cmd_info.get("intent", "unknown")
            if intent != "unknown" and not plugins_first(m_text, intent):
                return ("owner_cmd", intent, json.dumps(cmd_info.get("args") or {}, sort_keys=True))
            # Text a plugin can answer is keyed by the text itself: with one shared key, two DIFFERENT
            # plugin commands sent close together ("/ideas" then "/keywords") counted as duplicates and
            # only the first was answered. Chatter nobody understands still shares one key (one hint).
            if plugin_can_answer(m_text):
                return ("text", " ".join((m_text or "").lower().split()))
            return ("unknown_text",)
        return ("command", c, tuple(c_ids))

    key_counts = {}
    for upd in sorted(valid, key=lambda item: item["update_id"]):
        u_id = upd["update_id"]
        if next_offset is not None and u_id < next_offset:
            continue
        msg_candidate = upd.get("message")
        if not msg_candidate or "text" not in msg_candidate:
            continue
        if str((msg_candidate.get("chat") or {}).get("id", "")) != str(owner_chat_id):
            continue
        m_k = _message_key(msg_candidate.get("text", ""))
        key_counts[m_k] = key_counts.get(m_k, 0) + 1

    seen_message_keys = set()

    for upd in sorted(valid, key=lambda item: item["update_id"]):
        update_id = upd["update_id"]
        if next_offset is not None and update_id < next_offset:
            continue
        acknowledge_update(token, update_id)
        next_offset = update_id + 1
        set_state("update_offset", next_offset, db_path)
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
            msg_id = (cb.get("message") or {}).get("message_id")
            if handle_callback(token, owner_chat_id, cb.get("data", ""), db_path, message_id=msg_id) is not None:
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
            plugin_handled, plugin_res = call_plugins(caption, token, owner_chat_id, db_path=db_path, message=msg)
            if plugin_handled:
                if isinstance(plugin_res, dict) and plugin_res.get("text"):
                    _send_text_kw(token, owner_chat_id, plugin_res["text"], keyboard=plugin_res.get("keyboard"))
                handled += 1
                continue
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
            plugin_handled, plugin_res = call_plugins(caption, token, owner_chat_id, db_path=db_path, message=msg)
            if plugin_handled:
                if isinstance(plugin_res, dict) and plugin_res.get("text"):
                    _send_text_kw(token, owner_chat_id, plugin_res["text"], keyboard=plugin_res.get("keyboard"))
                handled += 1
                continue
            reply_text, buttons, _ = media_brain.process_document(token, msg["document"], db_path=db_path)
            send_text(token, owner_chat_id, reply_text, buttons)
            handled += 1
            continue

        message_text = msg.get("text", "")
        m_key = _message_key(message_text)
        is_duplicate = m_key in seen_message_keys
        seen_message_keys.add(m_key)
        total_k_count = key_counts.get(m_key, 1)
        burst_note = f"\n\n(answered {total_k_count} messages)" if total_k_count > 1 else ""

        approval_id = parse_start_payload(message_text)
        if approval_id is not None:
            if not is_duplicate:
                reply = view_text(approval_id, db_path) + burst_note
                send_text(token, owner_chat_id, reply, draft_buttons(approval_id))
            handled += 1
            continue

        cmd, ids = parse_command(message_text)
        if not cmd:
            # Check for URL link in message_text
            urls = re.findall(r"https?://[^\s]+", message_text)
            # A link inside a command ("/listed 3 https://...gumroad.com/l/x") is that command's
            # argument, not a link shared for the media brain: the media brain must not fetch it.
            if urls and _link_belongs_to_command(message_text):
                urls = []
            if urls:
                if not is_duplicate:
                    reply_text, buttons, _ = media_brain.process_url(urls[0], db_path=db_path)
                    send_text(token, owner_chat_id, reply_text + burst_note, buttons)
                handled += 1
                continue

            # Check for "ee vaaram" / "eevaaram" / "this week" trigger
            if re.match(r"^\s*(?:ee\s*vaaram|eevaaram|this\s*week)\s*$", message_text or "", re.IGNORECASE):
                if not is_duplicate:
                    reply = week_summary_text(db_path=db_path) + burst_note
                    send_text(token, owner_chat_id, reply)
                handled += 1
                continue

            # Check for A/B title tracking command / Tenglish message
            import ab_results
            ab_reply = ab_results.parse_and_handle_ab(message_text, db_path=db_path)
            if ab_reply is not None:
                if not is_duplicate:
                    send_text(token, owner_chat_id, ab_reply + burst_note)
                handled += 1
                continue

            # Check for /marketing pack files request (slash, "pack N marketing", "marketing N chupinchu")
            marketing_n = parse_marketing_phrase(message_text)
            if marketing_n is not None:
                if not is_duplicate:
                    reply = marketing_text(marketing_n) + burst_note
                    send_text(token, owner_chat_id, reply)
                handled += 1
                continue

            import owner_commands
            cmd_info = owner_commands.parse(message_text)
            c_intent = cmd_info.get("intent", "unknown")
            c_args = cmd_info.get("args") or {}

            # Plugin sentences first ("pack 3 files pampu", "auto approve status", "/undo"): see plugins_first.
            plugins_tried, early_res = False, None
            if plugins_first(message_text, c_intent):
                plugins_tried = True
                plugin_handled, early_res = call_plugins(message_text, token, owner_chat_id, db_path=db_path)
                is_menu_fallback = (isinstance(early_res, dict)
                                    and str(early_res.get("text", "")).startswith(_MENU_FALLBACK))
                if plugin_handled and not is_menu_fallback:
                    if not is_duplicate and isinstance(early_res, dict) and early_res.get("text"):
                        _send_text_kw(token, owner_chat_id, early_res["text"] + burst_note,
                                      keyboard=early_res.get("keyboard"))
                    handled += 1
                    continue
                if not plugin_handled:
                    early_res = None      # no plugin wanted it: the old intent below answers

            if c_intent in SAFE_OWNER_INTENTS:
                if not is_duplicate:
                    if c_intent == "list_drafts":
                        reply = handle_command("list", [], db_path) + burst_note
                        send_text(token, owner_chat_id, reply)
                        for it in guardrails.pending(db_path)[:10]:
                            send_text(token, owner_chat_id, draft_card(it, db_path=db_path), draft_buttons(it["id"]))
                    elif c_intent == "show_status":
                        reply = status_text(db_path) + burst_note
                        send_text(token, owner_chat_id, reply)
                    elif c_intent in ("daily_report", "what_happened_today"):
                        import daily_report
                        reply = daily_report.report_text(db_path=db_path) + burst_note
                        send_text(token, owner_chat_id, reply)
                    elif c_intent == "list_packs":
                        page = c_args.get("page") or c_args.get("n") or 1
                        reply = ready_packs_text(page=page) + burst_note
                        send_text(token, owner_chat_id, reply)
                    elif c_intent == "show_pack":
                        n = c_args.get("n")
                        reply = (pack_detail_text(n, db_path=db_path) if n is not None
                                 else "Send /pack <number> to view pack details.") + burst_note
                        send_text(token, owner_chat_id, reply)
                    elif c_intent == "post_today":
                        reply = today_posts_text() + burst_note
                        send_text(token, owner_chat_id, reply)
                    elif c_intent == "post_done":
                        n = c_args.get("n")
                        reply = mark_post_done_text(n) + burst_note
                        send_text(token, owner_chat_id, reply)
                    elif c_intent == "help":
                        hint_msg = cmd_info.get("hint") or HELP
                        send_text(token, owner_chat_id, hint_msg + burst_note)
                handled += 1
                continue

            if c_intent in RISKY_OWNER_INTENTS:
                if not is_duplicate:
                    prompt_text, buttons = create_confirmation_prompt(c_intent, c_args, db_path=db_path)
                    send_text(token, owner_chat_id, prompt_text + burst_note, buttons)
                handled += 1
                continue

            if c_intent in OFFICE_INTENTS:
                if not is_duplicate:
                    action_desc = _format_office_action_desc(c_intent, c_args)
                    try:
                        store_office_request(c_intent, c_args, db_path=db_path)
                        reply = f"Office lo marchutha: {action_desc} (Binny Mills app lo apply avutundi)" + burst_note
                    except Exception as exc:    # a queue problem must never stop the other messages
                        print(f"Office request not saved: {type(exc).__name__}", file=sys.stderr)
                        reply = (f"Office request save kaaledu: {action_desc}. Malli try cheyandi leda /doctor chudandi."
                                 + burst_note)
                    send_text(token, owner_chat_id, reply)
                handled += 1
                continue

            # Call plugins for owner plain text before the old 'Ardam kaaledu' fallback
            if plugins_tried:             # already asked above: do not run the plugins twice
                plugin_handled, plugin_res = early_res is not None, early_res
            else:
                plugin_handled, plugin_res = call_plugins(message_text, token, owner_chat_id, db_path=db_path)
            if plugin_handled:
                if not is_duplicate and isinstance(plugin_res, dict) and plugin_res.get("text"):
                    _send_text_kw(token, owner_chat_id, plugin_res["text"] + burst_note, keyboard=plugin_res.get("keyboard"))
                handled += 1
                continue

            # Fallback for unknown text: reply with parser's Tenglish help hint
            if not is_duplicate:
                hint_text = cmd_info.get("hint") or UNKNOWN_TEXT_HINT
                send_text(token, owner_chat_id, hint_text + burst_note)
            handled += 1
            continue

        stats["commands"] += 1
        if not is_duplicate:
            if cmd == "rules":
                import improver_loop
                text, buttons = improver_loop.format_rules_list(db_path)
                send_text(token, owner_chat_id, text + burst_note, buttons)
            elif cmd == "cubes":
                import cubes
                text, buttons = cubes.format_telegram_cubes_list(db_path)
                send_text(token, owner_chat_id, text + burst_note, buttons)
            else:
                plugin_handled = False
                # If command has arguments (like /help word) or is not in builtins, check plugins first:
                if (cmd == "help" and message_text.strip().lower().split("@")[0] not in ("/help", "help", "/start")) or \
                   cmd not in ("status", "packs", "pack", "marketing", "list", "view", "approve", "reject",
                               "pipeline", "pause", "resume", "buttontest", "autofix_drill", "today", "posted", "ab", "week", "help", "start",
                               "doctor"):   # /doctor is built in: without it tg_menu's "ardam kaaledu" fallback answered
                    plugin_handled, plugin_res = call_plugins(message_text, token, owner_chat_id, db_path=db_path)
                    if plugin_handled:
                        if isinstance(plugin_res, dict) and plugin_res.get("text"):
                            _send_text_kw(token, owner_chat_id, plugin_res["text"] + burst_note, keyboard=plugin_res.get("keyboard"))

                if not plugin_handled:
                    reply = handle_command(cmd, ids, db_path) + burst_note
                    send_text(token, owner_chat_id, reply)
            if cmd == "list":       # fresh buttons for every waiting draft (up to 10)
                for it in guardrails.pending(db_path)[:10]:
                    send_text(token, owner_chat_id, draft_card(it, db_path=db_path), draft_buttons(it["id"]))
            elif cmd == "buttontest":
                send_text(token, owner_chat_id, "Press this test button. The reply comes on the next run "
                          "(about 1-2 minutes with the door bell)." + burst_note, [[("Test button", "t:ping")]])
        handled += 1
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
        if not send_text(token, owner_chat_id, list_text(db_path)):
            return 0
        set_state("last_alerted_id", max(it["id"] for it in items), db_path)
        return len(items)
    new = [it for it in items if it["id"] > last_seen]
    if not new:
        return 0
    _ensure_checked(new, db_path)
    if not send_text(token, owner_chat_id,
              f"\U0001f336️ {len(new)} new draft(s) ready. Waiting in total: {len(items)}."
              + (f" Buttons for the first 10; use /list for the rest." if len(new) > 10 else "")):
        return 0
    delivered = 0
    for it in new[:10]:
        if not send_text(token, owner_chat_id, draft_card(it, db_path=db_path), draft_buttons(it["id"])):
            return delivered
        delivered += 1
        set_state("last_alerted_id", it["id"], db_path)
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


def send_morning_post_plan(token, owner_chat_id, db_path=None, now=None, plan_path=None, products_dir=None):
    """Once per day, on the first robot run after 08:00 India time, send today_text to owner.

    Never send twice the same day, never at night (22:00-08:00 India time).
    Remembers last sent date in telegram_state key 'last_morning_post_date'.
    """
    from zoneinfo import ZoneInfo
    ist_zone = ZoneInfo("Asia/Kolkata")
    if now is None:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(now, datetime.datetime):
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    else:
        now_dt = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)

    ist_dt = now_dt.astimezone(ist_zone)
    ist_hour = ist_dt.hour
    ist_date_str = ist_dt.date().isoformat()

    # Never send at night: 22:00 to 08:00 India time (i.e. hour >= 22 or hour < 8)
    if ist_hour < 8 or ist_hour >= 22:
        return False

    last_sent_date = get_state("last_morning_post_date", None, db_path)
    if last_sent_date == ist_date_str:
        return False

    text = today_posts_text(plan_path=plan_path, products_dir=products_dir, date=ist_date_str)
    # Nothing planned for today (no ready packs yet): stay quiet instead of sending an empty plan.
    if text.startswith("No posts scheduled"):
        return False
    sent = send_text(token, owner_chat_id, text)
    if sent:
        set_state("last_morning_post_date", ist_date_str, db_path)
        return True
    return False


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


def run(db_path=None, recovery=False):
    stats = new_stats()
    with diagnostics.session() as state:
        try:
            result = _run(db_path, recovery, stats)
            if state["send_failed"]:
                raise RuntimeError("Telegram message delivery failed; see safe diagnostics.")
            return result
        finally:
            diagnostics.print_summary(state, stats)


def _run(db_path=None, recovery=False, stats=None):
    configured, token, chat_id = notify._telegram_configured()
    diagnostics.configuration(configured)
    # The office is an independent signed inbox. Telegram setup/outages must not block it.
    if not recovery:
        try:
            import control_inbox
            control_inbox.run(db_path)
        except Exception as exc:
            print(f"Control inbox skipped: {type(exc).__name__}")
    if not configured:
        print("Telegram not configured: skipping inbox.")
        return {"configured": False, "commands": 0, "alerts": 0}
    # Verify credentials before consuming an update. A wrong destination previously
    # discarded owner commands and silently failed every outgoing reply.
    for method, params in (("getMe", {}), ("getChat", {"chat_id": chat_id})):
        result = _call(token, method, params)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("Telegram setup check failed; see safe diagnostics.")
    webhook_result = _call(token, "getWebhookInfo", {})
    info = webhook_result.get("result") if isinstance(webhook_result, dict) else None
    diagnostics.webhook(info)
    if isinstance(info, dict) and info.get("url"):
        raise RuntimeError("Telegram webhook blocks polling; see the robot-fix-runbook.")
    stats = stats if stats is not None else new_stats()
    commands = process_updates(token, chat_id, db_path, stats)
    if recovery:
        print(stats_line(stats))
        return {"configured": True, "commands": commands, "alerts": 0,
                "waiting_reminder": False, "daily_report": False}
    import product_pipeline
    product_pipeline.notify_ready(db_path, lambda text: send_text(token, chat_id, text))
    import autofix
    autofix.poll(get_state, set_state,
                 lambda text, buttons: send_text(token, chat_id, text, buttons), db_path)
    try:
        import auto_approve
        auto_approve.run_auto_decisions(db_path=db_path, token=token, owner_chat_id=chat_id)
    except Exception as exc:
        print(f"Auto-approve skipped: {type(exc).__name__}")
    print(stats_line(stats))
    alerts = alert_new_drafts(token, chat_id, db_path)
    waiting_reminder = remind_waiting_drafts(token, chat_id, db_path)
    try:
        # Test runs (CHILLI_TEST=1) must not read the real post plan or send a morning message.
        if os.environ.get("CHILLI_TEST") == "1":
            morning_sent = False
        else:
            morning_sent = send_morning_post_plan(token, chat_id, db_path=db_path)
    except Exception as exc:
        print(f"Morning post plan skipped: {type(exc).__name__}")
        morning_sent = False
    try:  # one short health report a day (first run after 03:30 UTC), weekly letter on Sundays
        import daily_report
        ist_day_str = daily_report.delivery_day()
        last_ist_day = get_state("daily_report_ist_day", None, db_path)

        if last_ist_day == ist_day_str:
            daily = False
        else:
            daily = daily_report.maybe_send(
                lambda text: send_text(token, chat_id, text + "\n" + daily_waiting_line(db_path)),
                get_state, set_state, db_path)
            if daily:
                set_state("daily_report_ist_day", ist_day_str, db_path)
        import improver_loop
        improver_loop.maybe_run_weekly(
            lambda text, buttons=None: send_text(token, chat_id, text, buttons),
            get_state, set_state, db_path)
    except Exception as exc:
        print(f"Daily report / improver loop skipped: {type(exc).__name__}")
        daily = False
    print(f"Telegram inbox: {commands} command(s) handled, {alerts} new draft(s) announced"
          + (", daily report sent." if daily else ".")
          + (" morning post plan sent." if morning_sent else ""))
    return {"configured": True, "commands": commands, "alerts": alerts,
            "waiting_reminder": waiting_reminder, "daily_report": daily,
            "morning_post_plan": morning_sent}


def main(argv):
    if argv and argv[0] == "status":
        configured, _, _ = notify._telegram_configured()
        print(f"Telegram configured: {'yes' if configured else 'no'}")
        return
    result = run(recovery="--recovery" in argv)
    if not result["configured"] and os.environ.get("GITHUB_ACTIONS") == "true":
        raise SystemExit("Telegram is not configured in this workflow job.")


if __name__ == "__main__":
    main(sys.argv[1:])
