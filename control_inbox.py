"""Consume signed office controls and publish secret-free runtime receipts to Cloudflare KV."""
import datetime
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import guardrails
import publisher
import secret_guard
import telegram_inbox

ACTION_NAMES = {"approve", "reject", "pause", "resume", "set_switch", "add_mission",
                "edit_mission", "archive_mission", "add_trigger", "edit_trigger", "set_trigger", "delete_trigger",
                "send_message", "chat", "ask", "route_request", "set_policy", "set_autonomy", "set_budget"}
PREFIX = "control:request:"
RECEIPT_PREFIX = "runtime:receipt:"
SIGNED_FIELDS = ("id", "action", "target", "value", "created_at", "nonce")
MAX_REQUESTS = 50
_not_configured_logged = False


def _config(env=None):
    env = os.environ if env is None else env
    values = [env.get(name) for name in ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN", "CONTROL_SIGNING_KEY")]
    return values if all(isinstance(value, str) and value for value in values) else None


def _url(account, namespace, suffix):
    part = lambda value: urllib.parse.quote(value, safe="")
    return f"https://api.cloudflare.com/client/v4/accounts/{part(account)}/storage/kv/namespaces/{part(namespace)}/{suffix}"


def _http(url, token, method="GET", data=None, http_request=None):
    if http_request:
        return http_request(url, token, method, data)
    request = urllib.request.Request(url, data=data.encode() if isinstance(data, str) else data, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(256 * 1024 + 1).decode()


def _logger():
    logged = False
    def log(operation, error):
        nonlocal logged
        if logged:
            return
        logged = True
        reason = type(error).__name__
        if isinstance(error, urllib.error.HTTPError) and isinstance(error.code, int):
            reason = f"HTTP {error.code}"
            if error.code == 403:
                reason += " - check API token has Workers KV Storage Edit"
        print(f"Control inbox: KV {operation} failed ({reason})")
    return log


def fetch(env=None, http_request=None, log=None):
    """Bounded, paginated reads. A failed KV request logs at most once per run."""
    global _not_configured_logged
    config = _config(env)
    if not config:
        if not _not_configured_logged:
            print("control inbox not configured")
            _not_configured_logged = True
        return []
    account, namespace, token, _ = config
    log = log or _logger()
    result, cursor, seen = [], "", set()
    try:
        for _ in range(10):
            query = "keys?prefix=" + PREFIX + "&limit=100"
            if cursor:
                query += "&cursor=" + urllib.parse.quote(cursor, safe="")
            page = json.loads(_http(_url(account, namespace, query), token, http_request=http_request))
            if page.get("success") is False or not isinstance(page.get("result"), list):
                raise ValueError("invalid KV response")
            for item in page["result"]:
                name = item.get("name") if isinstance(item, dict) else None
                if not isinstance(name, str) or not name.startswith(PREFIX) or name in seen:
                    continue
                seen.add(name)
                try:
                    raw = _http(_url(account, namespace, "values/" + urllib.parse.quote(name, safe="")), token,
                                http_request=http_request)
                    record = json.loads(raw) if len(raw.encode()) <= 16384 else None
                except (ValueError, UnicodeError):
                    record = None
                except Exception as error:
                    log("read", error)
                    return result
                result.append((name, record))
                if len(result) == MAX_REQUESTS:
                    return result
            next_cursor = page.get("result_info", {}).get("cursor", "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return result
    except Exception as error:
        log("read", error)
        return result


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", value) is not None


def _signed(record):
    return {field: record[field] for field in SIGNED_FIELDS}


def _digest(value, key):
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()


def _reason(record, key, now):
    if not isinstance(record, dict) or not all(field in record for field in SIGNED_FIELDS):
        return "invalid request"
    try:
        expected = _digest(_signed(record), key)
        signature = record.get("signature")
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature) or not hmac.compare_digest(expected, signature):
            return "bad signature"
    except (TypeError, ValueError, OverflowError, RecursionError):
        return "invalid request"
    if not _valid_id(record["id"]) or not _valid_id(record["target"]):
        return "invalid identifier"
    if not isinstance(record["nonce"], str) or not 1 <= len(record["nonce"]) <= 200:
        return "invalid nonce"
    try:
        stamp = record["created_at"]
        if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", stamp):
            return "invalid time"
        created = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return "invalid time"
    if now - created > 86400 or created - now > 300:
        return "expired"
    if not isinstance(record["action"], str) or record["action"] not in ACTION_NAMES:
        return "unknown action"
    return None


@contextmanager
def _connect(db_path):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS office_requests (
        id TEXT PRIMARY KEY, nonce TEXT UNIQUE NOT NULL, digest TEXT NOT NULL, receipt TEXT NOT NULL)""")
    conn.commit()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _receipt(ident, state, reason, now, reply=None, source_signature=None):
    result = {"id": ident, "state": state, "reason": reason,
              "at": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat().replace("+00:00", "Z")}
    if reply is not None:
        result["reply"] = secret_guard.scrub_text(reply)[:4000]
    if isinstance(source_signature, str) and re.fullmatch(r"[0-9a-f]{64}", source_signature):
        result["request_signature"] = source_signature
    return result


def _put_receipt(receipt, config, http_request, log):
    account, namespace, token, key = config
    signed = dict(receipt, signature=_digest(receipt, key))
    try:
        response = _http(_url(account, namespace, "values/" + urllib.parse.quote(RECEIPT_PREFIX + receipt["id"], safe="")),
                         token, "PUT", json.dumps(signed, ensure_ascii=False), http_request)
        if response and json.loads(response).get("success") is False:
            raise ValueError("KV write refused")
        return True
    except Exception as error:
        log("write", error)
        return False


def _remote_receipt(ident, config, http_request, log):
    account, namespace, token, key = config
    try:
        raw = _http(_url(account, namespace, "values/" + urllib.parse.quote(RECEIPT_PREFIX + ident, safe="")), token,
                    http_request=http_request)
        data = json.loads(raw)
        signature = data.pop("signature", None)
        if (data.get("id") == ident and data.get("state") in ("applied", "held", "rejected")
                and isinstance(signature, str) and hmac.compare_digest(_digest(data, key), signature)):
            return data, True
        return None, True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None, True
        log("read", error)
    except (ValueError, TypeError, AttributeError):
        return None, True
    except Exception as error:
        log("read", error)
    return None, False


def _apply(record, db_path, now=None):
    action = record["action"]
    if action == "add_mission":
        import office_tasks
        return office_tasks.add_mission(record, db_path, now)
    if action in ("edit_mission", "archive_mission"):
        import office_tasks
        return office_tasks.change_mission(record, db_path, now)
    if action in ("add_trigger", "edit_trigger", "set_trigger", "delete_trigger"):
        import office_triggers
        return office_triggers.apply(record, db_path, now)
    if action in ("set_policy", "set_autonomy", "set_budget"):
        import office_policy
        return office_policy.apply(record, db_path)
    if action in ("send_message", "chat", "ask", "route_request"):
        import chat_brain
        import office_tasks
        value = record["value"]
        if action in ("chat", "ask") and isinstance(value, str):
            recipient, text = record["target"], value
        else:
            fields = {"text", "thread_id", "role", "cube_id"} if action == "route_request" else {"text", "thread_id"}
            if not isinstance(value, dict) or set(value) != fields:
                raise ValueError("invalid agent message")
            recipient, text = value["thread_id"], value["text"]
            resolved = office_tasks.agent(recipient)
            if action == "route_request":
                if (record["target"] != "orchestrator" or not isinstance(value["role"], str)
                        or value["role"].casefold() not in (resolved["name"].casefold(), resolved["role"].casefold())):
                    return "held", "agent role no longer matches", None
                import cubes
                cube = cubes.get_cube_for_agent(resolved["name"])
                if value["cube_id"] is not None and value["cube_id"] not in (cube["id"] if cube else None, "chilli-gavva-robot"):
                    return "held", "agent cube no longer matches", None
            elif office_tasks.agent(record["target"])["id"] != resolved["id"]:
                raise ValueError("message thread does not match agent")
        return chat_brain.office_reply(recipient, text, db_path)
    if action in ("approve", "reject"):
        if record["value"] is not None or not re.fullmatch(r"[1-9][0-9]{0,9}", record["target"]):
            return "rejected", "invalid draft identifier", None
        number = int(record["target"])
        telegram_inbox.handle_command(action, [number], db_path)
        return "applied", "draft decision processed", None
    if action in ("pause", "resume"):
        if record["target"] != "robot" or record["value"] is not None:
            return "rejected", "invalid robot control", None
        telegram_inbox.handle_command(action, [], db_path)
        return "applied", "robot pause state updated", None
    if action == "set_switch" and record["target"] in publisher.PUBLISH_TYPES and type(record["value"]) is bool:
        publisher.set_enabled(record["target"], record["value"], db_path)
        return "applied", "publication switch updated", None
    return "rejected", "invalid action", None


def run(db_path=None, env=None, http_request=None, now=None, send=None):
    config = _config(env)
    if not config:
        fetch(env, http_request)
        return {"applied": 0, "refused": 0}
    now = time.time() if now is None else now
    log = _logger()
    applied = refused = 0
    for name, record in fetch(env, http_request, log):
        reason = _reason(record, config[3], now)
        ident = record.get("id") if isinstance(record, dict) and _valid_id(record.get("id")) else "invalid:" + hashlib.sha256(name.encode()).hexdigest()[:40]
        if reason:
            _put_receipt(_receipt(ident, "rejected", reason, now, source_signature=record.get("signature")
                                 if isinstance(record, dict) else None), config, http_request, log)
            refused += 1
            continue
        digest = _digest(_signed(record), config[3])
        with _connect(db_path) as conn:
            saved = conn.execute("SELECT nonce, digest, receipt FROM office_requests WHERE id = ?", (ident,)).fetchone()
            if saved and saved[:2] == (record["nonce"], digest):
                _put_receipt(json.loads(saved[2]), config, http_request, log)
                refused += 1
                continue
            reused = saved or conn.execute("SELECT 1 FROM office_requests WHERE nonce = ?", (record["nonce"],)).fetchone()
            if reused or telegram_inbox.get_state("control_nonce:" + record["nonce"], None, db_path):
                _put_receipt(_receipt(ident, "rejected", "replayed nonce or request id", now,
                                     source_signature=record["signature"]), config, http_request, log)
                refused += 1
                continue
            remote, reachable = _remote_receipt(ident, config, http_request, log)
            if remote and remote.get("reason") in ("bad signature", "invalid request"):
                # An unauthenticated submission cannot reserve another request's identity.
                remote = None
            if remote:
                if remote.get("request_signature") not in (None, record["signature"]):
                    refused += 1
                    continue
                conn.execute("INSERT INTO office_requests VALUES (?, ?, ?, ?)", (ident, record["nonce"], digest, json.dumps(remote)))
                refused += 1
                continue
            receipt = _receipt(ident, "held", "request reserved; outcome unconfirmed", now,
                               source_signature=record["signature"])
            if not reachable or not _put_receipt(receipt, config, http_request, log):
                refused += 1
                break
            conn.execute("INSERT INTO office_requests VALUES (?, ?, ?, ?)", (ident, record["nonce"], digest, json.dumps(receipt)))
        try:
            state, reason, reply = _apply(record, db_path, now)
        except ValueError:
            state, reason, reply = "rejected", "invalid request value", None
        except Exception:
            state, reason, reply = "held", "runtime could not complete the request", None
        receipt = _receipt(ident, state, reason, now, reply, record["signature"])
        with _connect(db_path) as conn:
            conn.execute("UPDATE office_requests SET receipt = ? WHERE id = ?", (json.dumps(receipt), ident))
        _put_receipt(receipt, config, http_request, log)
        if state == "applied":
            applied += 1
            if send:
                try:
                    send("Office: " + reason)
                except Exception:
                    pass
        else:
            refused += 1
    return {"applied": applied, "refused": refused}
