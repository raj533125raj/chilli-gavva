"""always_on.py — talk to the Chilli Gavva "alarm clock" (a free Cloudflare Worker).

The Worker (deploy/cloudflare_alarm_worker.js, setup in docs/ALWAYS_ON.md) starts a GitHub run
every 30 minutes, wakes the agent when the owner messages the Telegram bot, and stays quiet
while the Mac worker is alive. This module is the Python side, used by the GitHub workflow and
by mac_worker.py:

    python3 always_on.py gate --reason alarm   # workflow: should this run do work?
    python3 always_on.py report                # workflow: tell the clock how many minutes ran
    python3 always_on.py status                # show what the clock knows (no secrets printed)

Settings: env vars ALWAYS_ON_URL / ALWAYS_ON_SECRET (GitHub secrets) or config.json keys
always_on_url / always_on_secret (the Mac). Without them every call is a harmless no-op and
the workflow behaves exactly as before. The secret is only ever sent in a request header.
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TIMEOUT = 10


def settings(config_path=CONFIG_PATH, env=None):
    """Return (url, secret). Env vars win over config.json; empty strings mean "not set up"."""
    env = os.environ if env is None else env
    cfg = {}
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        pass
    url = str(env.get("ALWAYS_ON_URL") or cfg.get("always_on_url") or "").strip().rstrip("/")
    secret = str(env.get("ALWAYS_ON_SECRET") or cfg.get("always_on_secret") or "").strip()
    return url, secret


def call(path, payload=None, url=None, secret=None, opener=None):
    """GET (no payload) or POST JSON to the Worker. Returns the JSON reply, or None on any problem.

    Never raises and never prints the secret, so callers can treat None as "clock unavailable".
    """
    if url is None:
        url, secret = settings()
    if not url or not secret:
        return None
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Authorization": f"Bearer {secret}", "User-Agent": "chilli-gavva",
                                          "Content-Type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as resp:
            reply = json.loads(resp.read().decode() or "{}")
        return reply if isinstance(reply, dict) else None
    except Exception:
        return None


def heartbeat(stopping=False, **kw):
    """The Mac worker is alive (or, with stopping=True, going away so the cloud takes over now)."""
    return call("/heartbeat", {"stopping": bool(stopping)}, **kw)


def status(**kw):
    return call("/status", **kw)


def run_started(reason, **kw):
    return call("/run", {"phase": "start", "reason": str(reason)[:20]}, **kw)


def run_finished(minutes, **kw):
    return call("/run", {"phase": "end", "minutes": int(minutes)}, **kw)


def billed_minutes(started, now=None, setup_seconds=20):
    """GitHub bills every job in whole minutes (at least 1). setup_seconds covers runner start-up."""
    now = time.time() if now is None else now
    return max(1, math.ceil((max(0.0, now - started) + setup_seconds) / 60))


def gate(reason="", force=False, start=None):
    """Return (should_run, why). Fails open: no clock or no answer means the run goes ahead."""
    if force:
        return True, "forced by the person who started the run"
    reply = (start or run_started)(reason or "unknown")
    if reply is None:
        return True, "alarm clock not set up or not reachable"
    if reply.get("mac_alive"):
        return False, "the Mac worker is running, so this cloud run stands down"
    return True, "ok"


def _append(path, line):
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def main(argv=None, env=None):
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(description="Chilli Gavva alarm clock client")
    parser.add_argument("command", choices=["gate", "report", "status"])
    parser.add_argument("--reason", default="")
    parser.add_argument("--force", default="no")
    args = parser.parse_args(argv)

    if args.command == "gate":
        run, why = gate(args.reason, str(args.force).strip().lower() in ("yes", "true", "1"))
        _append(env.get("GITHUB_OUTPUT"), f"run={'true' if run else 'false'}")
        _append(env.get("GITHUB_ENV"), f"RUN_STARTED={int(time.time())}")
        print(f"Gate: {'RUN' if run else 'SKIP'} ({why}).")
        return 0
    if args.command == "report":
        try:
            started = float(env.get("RUN_STARTED") or time.time())
        except ValueError:
            started = time.time()
        minutes = billed_minutes(started)
        reply = run_finished(minutes)
        print(f"Reported about {minutes} billed minute(s) to the alarm clock"
              + ("." if reply else " (clock not set up or not reachable)."))
        return 0
    reply = status()
    print(json.dumps(reply, indent=2) if reply else "Alarm clock not set up or not reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
