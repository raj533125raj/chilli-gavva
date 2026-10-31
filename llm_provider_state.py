"""Private router counters; public diagnostics use only the fixed vocabulary below.

Model caps count every reserved HTTP attempt, including retries and failed requests.
SQLite transactions share caps and half-open probes across workers/cloud runs. Tests
without an explicit database use an isolated in-memory store, never the state file.
"""
import datetime
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


REASONS = {
    "quota": "Daily provider allocation exhausted",
    "rate_limit": "Provider request rate exceeded",
    "capacity": "Provider temporarily out of capacity",
    "invalid_model": "Model unavailable or retired",
    "request_too_large": "Request exceeds model context or payload limit",
    "authentication": "Provider credentials or permissions rejected",
    "timeout": "Provider request timed out",
    "network": "Provider connection failed",
    "response_parse": "Provider returned an invalid response",
    "empty_response": "Provider returned no answer",
    "provider_error": "Provider request failed",
}
BREAKER_FAILURES = 3
BREAKER_SECONDS = 300
PROBE_SECONDS = 120
RECENT_SECONDS = 86400
RECENT_LIMIT = 20

_memory = sqlite3.connect(":memory:", check_same_thread=False)
_lock = threading.RLock()


@contextmanager
def _connection(path):
    # Serialize the shared in-memory connection. File databases additionally use
    # BEGIN IMMEDIATE to serialize reservations across processes.
    with _lock:
        memory = isinstance(path, sqlite3.Connection) or not path
        conn = (path if path else _memory) if memory else sqlite3.connect(path, timeout=5)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS llm_model_health ("
                         "provider TEXT, model TEXT, data TEXT NOT NULL, PRIMARY KEY(provider, model))")
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
        finally:
            if not memory:
                conn.close()


def _read(conn, provider, model):
    row = conn.execute("SELECT data FROM llm_model_health WHERE provider=? AND model=?",
                       (provider, model)).fetchone()
    return json.loads(row[0]) if row else {
        "day": "", "calls": 0, "kind": "", "streak": 0, "until": 0,
        "probe_until": 0, "last_kind": "", "failures": {}, "recent": {},
    }


def _write(conn, provider, model, state):
    conn.execute("INSERT INTO llm_model_health VALUES (?, ?, ?) "
                 "ON CONFLICT(provider, model) DO UPDATE SET data=excluded.data",
                 (provider, model, json.dumps(state)))


def reserve(provider, model, cap, path=None):
    """Reserve before IO. A broken state store fails closed, rather than ignoring caps."""
    now = time.time()
    day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date().isoformat()
    try:
        with _connection(path) as conn:
            state = _read(conn, provider, model)
            if state["day"] != day:
                state.update(day=day, calls=0)
            if state["calls"] >= cap or max(state["until"], state["probe_until"]) > now:
                return False
            if state["until"]:
                state["probe_until"] = now + PROBE_SECONDS
            state["calls"] += 1
            _write(conn, provider, model, state)
            return True
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return False


def observe(provider, model, ok, kind="", task="general", path=None):
    """Persist outcomes only; callers must never pass exception or prompt text."""
    kind = kind if kind in REASONS else "provider_error"
    now = time.time()
    try:
        with _connection(path) as conn:
            state = _read(conn, provider, model)
            state["probe_until"] = 0
            state.update(last_ok=bool(ok), observed_at=now)
            if ok:
                state.update(kind="", streak=0, until=0)
            else:
                state["streak"] = state["streak"] + 1 if state["kind"] == kind else 1
                state.update(kind=kind, last_kind=kind)
                state["failures"][kind] = state["failures"].get(kind, 0) + 1
                if state["streak"] >= BREAKER_FAILURES or state["until"]:
                    state["until"] = now + BREAKER_SECONDS
            recent = state["recent"]
            recent[task] = ([item for item in recent.get(task, [])
                             if item[0] > now - RECENT_SECONDS] + [[now, bool(ok)]])[-RECENT_LIMIT:]
            _write(conn, provider, model, state)
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        pass


def snapshots(path=None):
    conn = None
    try:
        with _lock:
            if path and not isinstance(path, sqlite3.Connection):
                conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
                source = conn
            else:
                source = path if path else _memory
            return [(provider, model, json.loads(data)) for provider, model, data in
                    source.execute("SELECT provider, model, data FROM llm_model_health")]
    except (sqlite3.Error, ValueError, TypeError):
        return []
    finally:
        if conn is not None:
            conn.close()


def order(providers, task, path=None):
    """Best success fraction in the last day/20 attempts; original order breaks ties.

    Unobserved providers have a neutral prior, so a failing incumbent cannot block
    an untried fallback. Only bounded task labels supplied by the router are stored.
    """
    recent = {}
    now = time.time()
    for provider, _, state in snapshots(path):
        recent.setdefault(provider, []).extend(
            item for item in state["recent"].get(task, []) if item[0] > now - RECENT_SECONDS)
    scores = {}
    for provider, items in recent.items():
        items = sorted(items, key=lambda item: item[0])[-RECENT_LIMIT:]
        if items:
            scores[provider] = sum(bool(item[1]) for item in items) / len(items)
    return sorted(providers, key=lambda item: -scores.get(item[0], 0.5))


def health(provider, model, cap, path=None):
    state = next((state for p, m, state in snapshots(path) if (p, m) == (provider, model)), None)
    state = state or {"day": "", "calls": 0, "until": 0, "probe_until": 0,
                      "streak": 0, "last_kind": "", "failures": {}}
    now = time.time()
    day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date().isoformat()
    calls = state["calls"] if state["day"] == day else 0
    breaker = ("open" if state["until"] > now else
               "half_open" if state["until"] else "closed")
    kind = state["last_kind"]
    return {"model": model, "calls_today": calls, "daily_cap": cap,
            "capped": calls >= cap, "breaker": breaker,
            "consecutive_failures": state["streak"],
            "failure_kind": kind, "reason": REASONS.get(kind, ""),
            "last_ok": state.get("last_ok"), "observed_at": state.get("observed_at", 0),
            "failure_reasons": {k: {"count": count, "reason": REASONS[k]}
                                for k, count in state["failures"].items() if k in REASONS}}
