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


def print_summary(state, stats):
    print("Telegram diagnostic: configured=" + state["configured"])
    print("Telegram diagnostic: webhook=" + state["webhook"])
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
    return bool(re.fullmatch(
        r"Telegram diagnostic: (?:" + method + r" HTTP=(?:not_called|(?:none|[1-5][0-9]{2})x[0-9]+(?:,(?:none|[1-5][0-9]{2})x[0-9]+)*)"
        r"|configured=(?:yes|no|unknown)"
        r"|webhook=(?:set|unset|unknown)"
        r"|updates=[0-9]+ buttons=[0-9]+ commands=[0-9]+ handled=[0-9]+ messages_sent=[0-9]+ other_chat=[0-9]+"
        r"|errors=(?:none|" + kind + r"x[0-9]+(?:," + kind + r"x[0-9]+)*))", line))


@contextmanager
def session():
    state = {"http": {method: Counter() for method in METHODS}, "errors": Counter(),
             "sent": 0, "send_failed": 0, "configured": "unknown", "webhook": "unknown"}
    token = _current.set(state)
    try:
        yield state
    finally:
        _current.reset(token)


def configuration(configured):
    state = _current.get()
    if state is not None:
        state["configured"] = "yes" if configured else "no"


def webhook(info):
    state = _current.get()
    if state is not None and isinstance(info, dict) and "url" in info:
        state["webhook"] = "set" if info["url"] else "unset"
