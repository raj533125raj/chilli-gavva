"""Owner control requests queued by the office.  This never runs without all configuration."""
import hashlib
import hmac
import json
import os
import time
import calendar
import datetime
import re
import urllib.request

import publisher
import telegram_inbox

ACTION_NAMES = {"approve", "reject", "pause", "resume", "set_switch"}
PREFIX = "control:request:"
_not_configured_logged = False


def _config(env=None):
    env = os.environ if env is None else env
    values = [env.get(name) for name in ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN", "CONTROL_SIGNING_KEY")]
    return values if all(isinstance(value, str) and value for value in values) else None


def _url(account, namespace, suffix):
    return f"https://api.cloudflare.com/client/v4/accounts/{account}/storage/kv/namespaces/{namespace}/{suffix}"


def _http(url, token, method="GET", data=None, http_request=None):
    if http_request:
        return http_request(url, token, method, data)
    request = urllib.request.Request(url, data=data.encode() if isinstance(data, str) else data, method=method,
                                     headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode()


def fetch(env=None, http_request=None):
    """Return KV request pairs. Configuration and transport failure are harmless."""
    global _not_configured_logged
    config = _config(env)
    if not config:
        if not _not_configured_logged:
            print("control inbox not configured")
            _not_configured_logged = True
        return []
    account, namespace, token, _ = config
    try:
        raw = _http(_url(account, namespace, f"keys?prefix={PREFIX}"), token, http_request=http_request)
        keys = json.loads(raw).get("result", [])
        result = []
        for item in keys:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name.startswith(PREFIX):
                continue
            value = _http(_url(account, namespace, f"values/{name}"), token, http_request=http_request)
            result.append((name, json.loads(value)))
        return result
    except Exception:
        return []


def _state(nonce, db_path):
    return telegram_inbox.get_state(f"control_nonce:{nonce}", None, db_path)


def _mark(nonce, db_path):
    telegram_inbox.set_state(f"control_nonce:{nonce}", "1", db_path)


def _reason(record, key, now):
    fields = ("id", "action", "target", "value", "created_at", "nonce")
    if not isinstance(record, dict) or not all(field in record for field in fields): return "invalid request"
    signed = {field: record[field] for field in fields}
    signature = record.get("signature")
    expected = hmac.new(key.encode(), json.dumps(signed, separators=(",", ":"), ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
    # JavaScript JSON.stringify has no spaces and leaves non-ASCII characters intact.
    if not isinstance(signature, str) or not hmac.compare_digest(expected, signature): return "bad signature"
    try:
        stamp = record["created_at"]
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z", stamp or "")
        if not match:
            raise ValueError
        fraction = (match.group(2) or "").ljust(6, "0")[:6]
        created = datetime.datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp() + (int(fraction) / 1000000 if fraction else 0)
    except (TypeError, ValueError): return "invalid time"
    if now - created > 24 * 3600 or created - now > 300: return "expired"
    if record["action"] not in ACTION_NAMES: return "unknown action"
    if not isinstance(record["nonce"], str) or not record["nonce"]: return "invalid nonce"
    return None


def _apply(record, db_path):
    action = record["action"]
    if action in ("approve", "reject"):
        try: number = int(record["target"])
        except (TypeError, ValueError): return None
        telegram_inbox.handle_command(action, [number], db_path)
        return f"Office: {action}d draft #{number}"
    if action in ("pause", "resume"):
        telegram_inbox.handle_command(action, [], db_path)
        return f"Office: {action}d robot"
    if action == "set_switch" and record["target"] in publisher.PUBLISH_TYPES and isinstance(record["value"], bool):
        publisher.set_enabled(record["target"], record["value"], db_path)
        return f"Office: changed {record['target']} switch"
    return None


def run(db_path=None, env=None, http_request=None, now=None, send=None):
    config = _config(env)
    if not config:
        fetch(env, http_request)
        return {"applied": 0, "refused": 0}
    account, namespace, token, key = config
    now = time.time() if now is None else now
    send = send or (lambda text: None)
    applied = refused = 0
    for name, record in fetch(env, http_request):
        reason = _reason(record, key, now)
        if not reason and _state(record["nonce"], db_path): reason = "replayed nonce"
        message = None if reason else _apply(record, db_path)
        if not message: reason = reason or "invalid action"
        if reason:
            result, refused = f"refused: {reason}", refused + 1
        else:
            _mark(record["nonce"], db_path); result, applied = "applied", applied + 1; send(message)
        try:
            updated = dict(record); updated["state"] = result
            _http(_url(account, namespace, f"values/{name}"), token, "PUT", json.dumps(updated), http_request)
        except Exception:
            pass
    return {"applied": applied, "refused": refused}
