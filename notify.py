"""Notification module — alerts when approvals pile up or errors occur.

Supports Telegram (free, no paid tier). Zero third-party deps (stdlib only).

Setup (one-time):
  1. Message @BotFather on Telegram → /newbot → copy token
  2. Message your new bot once (so it can find your chat_id)
  3. Add to config.json:
       "telegram_bot_token": "123456:ABC-xyz...",
       "telegram_chat_id":   "987654321"

CLI:
  python3 notify.py test               # send a test message
  python3 notify.py check              # notify if approvals > threshold
  python3 notify.py status             # show config state (no message sent)

Called by worker_daemon.py after each heartbeat if approvals pile up.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_THRESHOLD = 5   # notify when this many approvals are pending
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _cfg():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _telegram_configured(cfg=None):
    c = cfg or _cfg()
    token = c.get("telegram_bot_token", "").strip()
    chat_id = str(c.get("telegram_chat_id", "")).strip()
    return bool(token and chat_id and not token.startswith("YOUR_")), token, chat_id


def send(message: str) -> bool:
    """Send message via Telegram. Returns True on success, False if not configured or fails."""
    cfg = _cfg()
    configured, token, chat_id = _telegram_configured(cfg)
    if not configured:
        return False
    url = TELEGRAM_API.format(token=token)
    body = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return bool(result.get("ok"))
    except Exception:
        return False


def check_and_notify(db_path=None, threshold=None) -> bool:
    """Send a notification if pending approvals exceed threshold. Returns True if sent."""
    import guardrails
    threshold = threshold if threshold is not None else int(_cfg().get("notify_threshold", DEFAULT_THRESHOLD))
    items = guardrails.pending(db_path)
    if len(items) < threshold:
        return False
    lines = []
    for it in items[:5]:
        score_str = ""
        if it.get("review"):
            import re
            m = re.search(r'(\d+)\s*/\s*100', it["review"])
            if m:
                score_str = f" [{m.group(1)}/100]"
        lines.append(f"  #{it['id']} {it['title'][:50]}{score_str}")
    more = f"\n  ...and {len(items)-5} more" if len(items) > 5 else ""
    msg = (f"🌶️ <b>Chilli Gavva — {len(items)} approvals waiting</b>\n\n"
           + "\n".join(lines) + more
           + f"\n\n<code>python3 guardrails.py approve-above 75</code>")
    return send(msg)


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "test":
        ok = send("🌶️ Chilli Gavva notify.py — test message. If you see this, Telegram is working!")
        print("Sent!" if ok else "Not sent — check telegram_bot_token + telegram_chat_id in config.json")
    elif cmd == "check":
        sent = check_and_notify()
        print("Notification sent." if sent else "Nothing to notify (below threshold or Telegram not configured).")
    elif cmd == "status":
        cfg = _cfg()
        configured, _, _ = _telegram_configured(cfg)
        threshold = int(cfg.get("notify_threshold", DEFAULT_THRESHOLD))
        print(f"Telegram configured : {'yes' if configured else 'no — add telegram_bot_token + telegram_chat_id to config.json'}")
        print(f"Notify threshold    : {threshold} pending approvals")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
