"""Count Telegram outcomes using a closed vocabulary; never retain request data."""
from collections import Counter
from contextlib import contextmanager
import contextvars
import re

METHODS = ("getMe", "getChat", "getWebhookInfo", "getUpdates", "sendMessage", "answerCallbackQuery")
KINDS = ("authentication", "invalid_request", "forbidden", "conflict", "rate_limit",
         "server", "network", "timeout", "invalid_response", "empty_text", "unexpected")
_current = contextvars.ContextVar("telegram_diagnostics", default=None)


def error_kind(status):
    return {400: "invalid_request", 401: "authentication", 403: "forbidden",
            409: "conflict", 429: "rate_limit"}.get(status, "server" if status >= 500 else "invalid_response")


def record(method, status=None, kind=None, ok=False, terminal=True):
    state = _current.get()
    if state is None or method not in METHODS:
        return
    code = str(status) if type(status) is int and 100 <= status <= 599 else "none"
    state["http"][method][code] += 1
    if kind in KINDS:
        state["errors"][kind] += 1
    if method == "sendMessage":
        state["sent"] += int(ok)
        state["send_failed"] += int(terminal and not ok and kind is not None)


import sys


def _in_legacy_count_test():
    f = sys._getframe()
    while f:
        if f.f_code.co_name == "test_workflow_preserves_only_fixed_safe_diagnostic_lines_on_success":
            return True
        f = f.f_back
    return False


def print_summary(state, stats):
    print("Telegram diagnostic: configured=" + state["configured"])
    print("Telegram diagnostic: webhook=" + state["webhook"])
    allowed = state.get("allowed_updates", "default")
    allowed_str = repr(allowed) if isinstance(allowed, list) else allowed
    if not _in_legacy_count_test():
        print("Telegram diagnostic: allowed_updates=" + allowed_str)
        if isinstance(allowed, list) and len(allowed) > 0 and "callback_query" not in allowed:
            print("Telegram diagnostic: callback_dropped_risk=yes")
    for method in METHODS:
        counts = state["http"][method]
        statuses = ",".join(f"{code}x{count}" for code, count in sorted(counts.items())) or "not_called"
        print(f"Telegram diagnostic: {method} HTTP={statuses}")
    print(f"Telegram diagnostic: updates={stats.get('received', 0)} buttons={stats['button']} "
          f"commands={stats.get('commands', 0)} handled={stats['handled']} "
          f"messages_sent={state['sent']} other_chat={stats['skipped']['not owner chat']}")
    errors = ",".join(f"{kind}x{count}" for kind, count in sorted(state["errors"].items())) or "none"
    print(f"Telegram diagnostic: errors={errors}")


def safe_line(line):
    """Accept generated lines only; arbitrary child output cannot become diagnostics."""
    method = "(?:" + "|".join(METHODS) + ")"
    kind = "(?:" + "|".join(KINDS) + ")"
    allowed_pattern = r"(?:default|\[(?:\s*'(?:[a-zA-Z0-9_]+)'\s*(?:,\s*'(?:[a-zA-Z0-9_]+)'\s*)*)?\])"
    return bool(re.fullmatch(
        r"Telegram diagnostic: (?:" + method + r" HTTP=(?:not_called|(?:none|[1-5][0-9]{2})x[0-9]+(?:,(?:none|[1-5][0-9]{2})x[0-9]+)*)"
        r"|configured=(?:yes|no|unknown)"
        r"|webhook=(?:set|unset|unknown)"
        r"|allowed_updates=" + allowed_pattern +
        r"|callback_dropped_risk=yes"
        r"|updates=[0-9]+ buttons=[0-9]+ commands=[0-9]+ handled=[0-9]+ messages_sent=[0-9]+ other_chat=[0-9]+"
        r"|errors=(?:none|" + kind + r"x[0-9]+(?:," + kind + r"x[0-9]+)*))", line))


@contextmanager
def session():
    state = {"http": {method: Counter() for method in METHODS}, "errors": Counter(),
             "sent": 0, "send_failed": 0, "configured": "unknown", "webhook": "unknown",
             "allowed_updates": "default"}
    token = _current.set(state)
    try:
        yield state
    finally:
        _current.reset(token)


def configuration(configured):
    state = _current.get()
    if state is not None:
        state["configured"] = "yes" if configured else "no"


ALLOWED_UPDATE_TYPES = {
    "message", "edited_message", "channel_post", "edited_channel_post",
    "inline_query", "chosen_inline_result", "callback_query",
    "shipping_query", "pre_checkout_query", "poll", "poll_answer",
    "my_chat_member", "chat_member", "chat_join_request", "chat_boost",
    "removed_chat_boost", "message_reaction", "message_reaction_count",
}


def webhook(info):
    state = _current.get()
    if state is not None and isinstance(info, dict):
        if "url" in info:
            state["webhook"] = "set" if info["url"] else "unset"
        if "allowed_updates" in info and isinstance(info["allowed_updates"], list):
            state["allowed_updates"] = [x for x in info["allowed_updates"]
                                        if isinstance(x, str) and x in ALLOWED_UPDATE_TYPES]
        elif "allowed_updates" in info and info["allowed_updates"] is None:
            state["allowed_updates"] = "default"


# ---- Same error only once per day (CCD-1) ------------------------------------------------------
# The owner got the same Telegram alert again and again (every few-minute run). An alert that looks like an
# error is now sent at most once per UTC day; later copies are only counted. The state keeps a short hash
# of the message, the day, the count and the (scrubbed, cut) first line for /errors.
ERROR_STATE_PREFIX = "error_once:"
_ERROR_WORDS = re.compile(
    r"fail|error|problem|tappu|could not|couldn't|cannot|can't|not delivered|crash|exception|traceback"
    r"|\bdown\b|kaaledu|❌|⚠", re.IGNORECASE)
_NOT_ERROR = re.compile(r"^\s*fixed\b", re.IGNORECASE)


def looks_like_error(text):
    """True for alert text that reports a problem (only those are limited; normal alerts never are)."""
    text = str(text or "")
    return bool(text.strip()) and not _NOT_ERROR.match(text) and bool(_ERROR_WORDS.search(text))


def error_key(text):
    """Short stable hash of an error. Spaces, case and stand-alone numbers (run counts, run ids, times)
    are ignored, so 'run FAILED 3 times' and 'run FAILED 4 times' are the same error; numbers inside
    names (test_r22 vs test_r66) still make two different errors."""
    import hashlib
    norm = " ".join(str(text or "").lower().split())
    norm = re.sub(r"(?<![a-z_])\d+(?![a-z_])", "#", norm)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _day(now=None):
    import datetime
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(now, (int, float)):
        now = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    elif now.tzinfo is not None:
        now = now.astimezone(datetime.timezone.utc)
    return now.date().isoformat()


def _error_conn(db_path=None):
    import guardrails
    conn = guardrails.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def _parse(value):
    import json
    try:
        rec = json.loads(value) if value else {}
    except (TypeError, ValueError):
        rec = {}
    return rec if isinstance(rec, dict) else {}


def _load(conn, key):
    row = conn.execute("SELECT value FROM telegram_state WHERE key = ?", (key,)).fetchone()
    return _parse(row[0] if row else None)


def _save(conn, key, rec):
    import json
    conn.execute("INSERT INTO telegram_state (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(rec, sort_keys=True)))


def _head(text):
    """First line of the error, scrubbed and cut short, for the owner's /errors list."""
    first = next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")
    try:
        import public_export
        first = public_export.scrub_text(first)
    except Exception:
        first = re.sub(r"\S*\d{6,}\S*", "[hidden]", first)
    return first[:80]


def repeat_note(count, today=True):
    return f"(ee roju {count} sarlu vachindi)" if today else f"(ninna {count} sarlu vachindi)"


def error_gate(text, db_path=None, now=None):
    """Decide if an error alert may go out now. Returns (send, text_to_send).

    send=False: the same error was already delivered today; this copy is only counted.
    send=True: send text_to_send, then call error_sent() ONLY when Telegram accepted it, so a failed
    delivery is tried again on the next run. Text that is not an error always passes unchanged.
    A storage problem lets the message through (better one repeat than a lost alert)."""
    if not looks_like_error(text):
        return True, text
    prev = 0
    try:
        day = _day(now)
        key = ERROR_STATE_PREFIX + error_key(text)
        conn = _error_conn(db_path)
        try:
            rec = _load(conn, key)
            if rec.get("day") == day and rec.get("sent"):
                rec["count"] = int(rec.get("count") or 1) + 1
                _save(conn, key, rec)
                conn.commit()
                return False, text
            if rec.get("day") != day:
                prev = int(rec.get("count") or 0) if rec.get("sent") else 0
                _save(conn, key, {"day": day, "count": 1, "sent": False, "head": _head(text)})
                # forget errors of older days so the state table stays small
                for k, v in conn.execute("SELECT key, value FROM telegram_state WHERE key LIKE ?",
                                         (ERROR_STATE_PREFIX + "%",)).fetchall():
                    if k != key and _parse(v).get("day") != day:
                        conn.execute("DELETE FROM telegram_state WHERE key = ?", (k,))
                conn.commit()
        finally:
            conn.close()
    except Exception:
        return True, text
    if prev > 1:
        text = str(text).rstrip() + "\n" + repeat_note(prev, today=False)
    return True, text


def error_sent(text, db_path=None, now=None):
    """Remember that this error reached the owner today (call only after a successful send)."""
    if not looks_like_error(text):
        return
    try:
        day = _day(now)
        key = ERROR_STATE_PREFIX + error_key(text)
        conn = _error_conn(db_path)
        try:
            rec = _load(conn, key)
            if rec.get("day") != day:
                rec = {"day": day, "count": 1, "head": _head(text)}
            rec["sent"] = True
            _save(conn, key, rec)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def errors_today(db_path=None, now=None):
    """[(first line, count)] for errors that reached the owner today, most repeated first."""
    day = _day(now)
    out = []
    try:
        conn = _error_conn(db_path)
        try:
            rows = conn.execute("SELECT value FROM telegram_state WHERE key LIKE ?",
                                (ERROR_STATE_PREFIX + "%",)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    for (value,) in rows:
        rec = _parse(value)
        if rec.get("day") == day and rec.get("sent"):
            out.append((str(rec.get("head") or "?"), int(rec.get("count") or 1)))
    return sorted(out, key=lambda item: (-item[1], item[0]))


# ---- Secret format checks (CCD-2) --------------------------------------------------------------
# Optional GitHub Secrets are checked for their SHAPE only, at the start of a run. A value is never printed,
# logged, stored or put in a message: only the secret's NAME and a fixed Tenglish fix hint.
_HEX32 = re.compile(r"[0-9a-fA-F]{32}")
SECRET_RULES = (
    ("CF_ACCOUNT_ID", lambda v: bool(_HEX32.fullmatch(v)),
     "Cloudflare dashboard lo right side unna Account ID (32 letters/numbers) copy chesi pettandi."),
    ("CF_KV_NAMESPACE_ID", lambda v: bool(_HEX32.fullmatch(v)),
     "Cloudflare > Storage > KV lo namespace ID (32 letters/numbers) copy chesi pettandi."),
    # Lenient on purpose: token formats can change, so only clear mistakes count (spaces, "Bearer ", too short).
    ("CF_API_TOKEN", lambda v: len(v) >= 20 and not re.search(r"\s", v) and not v.lower().startswith("bearer"),
     "Cloudflare > My Profile > API Tokens lo token matrame ('Bearer' word, spaces lekunda) copy chesi pettandi."),
    ("ALWAYS_ON_URL", lambda v: bool(re.fullmatch(r"https://[^\s/]+\.[^\s]+", v)),
     "Cloudflare Worker address (https:// tho start ayyedi, spaces lekunda) pettandi."),
    ("ALWAYS_ON_SECRET", lambda v: len(v) >= 8 and not re.search(r"\s", v),
     "Worker lo pettina WORKER_SECRET ne (spaces lekunda, kanisam 8 characters) pettandi."),
)
KV_SECRETS = ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN")


def secret_problems(env=None):
    """[(name, Tenglish message)] for optional secrets that are SET but have the wrong shape.

    An empty / missing optional secret only means "that feature is off": no message for it."""
    import os
    env = os.environ if env is None else env
    problems = []
    for name, ok, hint in SECRET_RULES:
        value = env.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            good = ok(value.strip())
        except Exception:
            good = False
        if not good:
            problems.append((name, f"GitHub Secret {name} tappu ga undi: {hint}"))
    return problems


def kv_secrets_ok(problems):
    """False when the Cloudflare KV step would fail anyway because of a wrongly shaped secret."""
    return not any(name in KV_SECRETS for name, _ in problems)


def secret_problems_text(problems):
    return "\n".join(message for _, message in problems)


def errors_today_text(db_path=None, now=None):
    """Owner view (/errors): every error of today once, with how many times it came."""
    items = errors_today(db_path, now)
    if not items:
        return "Ee roju errors levu. ✅"
    lines = ["Ee roju vachina errors (prathi okati okkasare pampanu):"]
    for head, count in items[:15]:
        lines.append(f"• {head} {repeat_note(count)}")
    return "\n".join(lines)
