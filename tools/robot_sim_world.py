"""tools/robot_sim_world.py — the fake outside world for tools/robot_day_sim.py (CCE-1).

This module is loaded ONLY inside robot processes started by the day simulator, through a tiny
sitecustomize.py in the sandbox (environment variable ROBOT_SIM_DIR must be set). It never runs in a
normal robot run, in CI steps or in the owner's terminal.

What it replaces, for that one process:
  * the clock: time.time / datetime.now / date.today move to the simulated time (ROBOT_SIM_NOW),
    then keep ticking normally. A "clock jump" is just a new ROBOT_SIM_NOW for the next process.
  * ALL network: urllib.request.urlopen answers like Telegram, the free AI providers, Cloudflare KV,
    the alarm clock Worker and the GitHub API would (fake data, same JSON shapes). Every other host
    is refused, and a raw socket connect to a non-local address is refused too.
  * Telegram checks real Telegram also makes: empty or too long text, too long captions, button
    data over 64 bytes, a wrong chat id -> HTTP 400 with Telegram's own error wording.
  * faults (CCE-3): rules from ROBOT_SIM_DIR/faults.json, e.g. Telegram 429/500, all AI down,
    Cloudflare KV namespace not found.
  * error capture: uncaught exceptions and exceptions that the robot catches and prints
    ("... skipped: KeyError") are written with file:line to ROBOT_SIM_DIR/errors.jsonl.

Everything it records goes to files inside ROBOT_SIM_DIR (a temp folder). Tokens are never written:
Telegram URLs are logged by method name only.
"""
from __future__ import annotations

import builtins
import datetime as _dt
import email.message
import io
import json
import os
import re
import socket
import sys
import time as _time
import traceback
import urllib.error
import urllib.parse
import urllib.request

SIM_DIR = os.environ.get("ROBOT_SIM_DIR", "")
STEP = os.environ.get("ROBOT_SIM_STEP", "")
RUN = os.environ.get("ROBOT_SIM_RUN", "")
OWNER_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
REPO_MARK = os.environ.get("ROBOT_SIM_SANDBOX", "")

_REAL_TIME = _time.time
_REAL_TIME_NS = _time.time_ns
_REAL_GMTIME = _time.gmtime
_REAL_LOCALTIME = _time.localtime
_REAL_STRFTIME = _time.strftime
_REAL_CTIME = _time.ctime
_REAL_DATETIME = _dt.datetime
_REAL_DATE = _dt.date
_REAL_URLOPEN = urllib.request.urlopen
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_PRINT = builtins.print
_OFFSET = 0.0
_INSTALLED = False


# ---- files in the simulation folder ---------------------------------------------------------
def _path(name):
    return os.path.join(SIM_DIR, name)


def _load(name, default):
    try:
        with open(_path(name), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _save(name, data):
    tmp = _path(name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, _path(name))


def _append(name, record):
    try:
        record.setdefault("run", RUN)   # clock jumps make times repeat: reports group by run number
        with open(_path(name), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


def sim_now_iso():
    return _REAL_DATETIME.utcfromtimestamp(_REAL_TIME() + _OFFSET).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- clock ------------------------------------------------------------------------------------
class _ClockMeta(type):
    """isinstance(x, datetime.datetime) stays true for real datetime objects."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, cls._real)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, cls._real)


class FakeDatetime(_REAL_DATETIME, metaclass=_ClockMeta):
    _real = _REAL_DATETIME

    @classmethod
    def now(cls, tz=None):
        return _REAL_DATETIME.fromtimestamp(_time.time(), tz)

    @classmethod
    def utcnow(cls):
        return _REAL_DATETIME.utcfromtimestamp(_time.time())

    @classmethod
    def today(cls):
        return _REAL_DATETIME.fromtimestamp(_time.time())


class FakeDate(_REAL_DATE, metaclass=_ClockMeta):
    _real = _REAL_DATE

    @classmethod
    def today(cls):
        return _REAL_DATE.fromtimestamp(_time.time())


def _sim_time():
    return _REAL_TIME() + _OFFSET


def _with_now(real):
    def wrapper(*args):
        if not args or args[0] is None:
            return real(_sim_time())
        return real(*args)
    return wrapper


def _strftime(fmt, *args):
    if not args:
        return _REAL_STRFTIME(fmt, _REAL_LOCALTIME(_sim_time()))
    return _REAL_STRFTIME(fmt, *args)


def install_clock(sim_epoch):
    global _OFFSET
    _OFFSET = float(sim_epoch) - _REAL_TIME()
    _time.time = _sim_time
    _time.time_ns = lambda: int(_sim_time() * 1e9)
    _time.gmtime = _with_now(_REAL_GMTIME)
    _time.localtime = _with_now(_REAL_LOCALTIME)
    _time.ctime = _with_now(_REAL_CTIME)
    _time.strftime = _strftime
    _dt.datetime = FakeDatetime
    _dt.date = FakeDate
    try:
        import sqlite3
        sqlite3.register_adapter(FakeDatetime, lambda value: value.isoformat(" "))
        sqlite3.register_adapter(FakeDate, lambda value: value.isoformat())
    except ImportError:
        pass


# ---- error capture ---------------------------------------------------------------------------
def _where(tb):
    """file:line of the deepest robot frame (skips this module and the standard library)."""
    frames = traceback.extract_tb(tb)
    best = frames[-1] if frames else None
    for frame in frames:
        name = frame.filename
        if REPO_MARK and name.startswith(REPO_MARK) and "robot_sim_world" not in name:
            best = frame
    if best is None:
        return "?", 0
    name = best.filename
    if REPO_MARK and name.startswith(REPO_MARK):
        name = os.path.relpath(name, REPO_MARK)
    return name, best.lineno


def record_error(kind, exc_type, exc, tb, note=""):
    filename, line = _where(tb)
    _append("errors.jsonl", {
        "t": sim_now_iso(), "run": RUN, "step": STEP, "kind": kind, "type": getattr(exc_type, "__name__", str(exc_type)),
        "message": str(exc)[:300], "file": filename, "line": line, "note": str(note)[:200],
        "traceback": "".join(traceback.format_exception(exc_type, exc, tb))[-4000:]})


def _excepthook(exc_type, exc, tb):
    if not issubclass(exc_type, SystemExit):
        record_error("crash", exc_type, exc, tb)
    sys.__excepthook__(exc_type, exc, tb)


def _print(*args, **kwargs):
    """A robot `print` inside an except block that names the exception = a caught crash."""
    exc_type, exc, tb = sys.exc_info()
    if exc_type is not None and not issubclass(exc_type, (SystemExit, KeyboardInterrupt)):
        text = " ".join(str(a) for a in args)
        if exc_type.__name__ in text:
            caller = sys._getframe(1).f_code.co_filename
            if "robot_sim_world" not in caller:
                record_error("caught", exc_type, exc, tb, note=text)
    return _REAL_PRINT(*args, **kwargs)


# ---- HTTP helpers ------------------------------------------------------------------------------
class FakeResponse(io.BytesIO):
    def __init__(self, payload, status=200, url="", headers=None):
        if not isinstance(payload, (bytes, bytearray)):
            payload = json.dumps(payload).encode("utf-8")
        super().__init__(payload)
        self.status = self.code = status
        self.reason = "OK"
        self.url = url
        self.headers = email.message.Message()
        self.headers["Content-Type"] = "application/json"
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def info(self):
        return self.headers

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


def _http_error(url, code, body=None, headers=None):
    hdrs = email.message.Message()
    hdrs["Content-Type"] = "application/json"
    for key, value in (headers or {}).items():
        hdrs[key] = str(value)
    payload = json.dumps(body if body is not None else {"ok": False, "error_code": code}).encode("utf-8")
    return urllib.error.HTTPError(url, code, f"HTTP Error {code}", hdrs, io.BytesIO(payload))


def _body(req):
    data = req.data
    if data is None:
        return b""
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    try:
        return b"".join(data)
    except TypeError:
        return b""


def _params(req):
    """Telegram parameters from a JSON, form or multipart body (file bytes are not kept)."""
    raw = _body(req)
    ctype = (req.get_header("Content-type") or req.get_header("Content-Type") or "").lower()
    params = {}
    if "multipart/form-data" in ctype:
        files = []
        for part in raw.split(b"\r\n--"):
            head, _, value = part.partition(b"\r\n\r\n")
            name = re.search(rb'name="([^"]+)"', head)
            if not name:
                continue
            filename = re.search(rb'filename="([^"]*)"', head)
            key = name.group(1).decode("utf-8", "replace")
            if filename:
                files.append({"field": key, "name": filename.group(1).decode("utf-8", "replace"),
                              "bytes": len(value)})
                continue
            params[key] = value.rstrip(b"\r\n").decode("utf-8", "replace")
        params["_files"] = files
        for key in ("reply_markup", "media"):
            if isinstance(params.get(key), str):
                try:
                    params[key] = json.loads(params[key])
                except ValueError:
                    pass
        return params
    if raw[:1] in (b"{", b"["):
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {"_list": data}
        except ValueError:
            return {}
    if raw:
        parsed = dict(urllib.parse.parse_qsl(raw.decode("utf-8", "replace")))
        for key in ("reply_markup", "media", "allowed_updates"):
            if isinstance(parsed.get(key), str):
                try:
                    parsed[key] = json.loads(parsed[key])
                except ValueError:
                    pass
        return parsed
    query = urllib.parse.urlsplit(req.full_url).query
    return dict(urllib.parse.parse_qsl(query))


# ---- faults (CCE-3) ----------------------------------------------------------------------------
def _service(host, path):
    if host == "api.telegram.org":
        return "telegram"
    if host == "api.cloudflare.com" and "/storage/kv/" in path:
        return "kv"
    if host in ("api.groq.com", "generativelanguage.googleapis.com", "openrouter.ai", "models.github.ai",
                "models.inference.ai.azure.com") or (host == "api.cloudflare.com" and "/ai/" in path):
        return "llm"
    if host.endswith(".sim.invalid"):
        return "alarm"
    if host == "api.github.com":
        return "github"
    return "other"


def _fault_for(service, method):
    rules = _load("faults.json", [])
    if not rules:
        return None
    counts = _load("fault_counts.json", {})
    for index, rule in enumerate(rules):
        if rule.get("service") not in (service, "*"):
            continue
        if rule.get("method") and rule.get("method") != method:
            continue
        times = rule.get("times")
        used = int(counts.get(str(index), 0))
        if times is not None and used >= int(times):
            continue
        counts[str(index)] = used + 1
        _save("fault_counts.json", counts)
        return rule
    return None


def _raise_fault(rule, url):
    _append("faults_hit.jsonl", {"t": sim_now_iso(), "step": STEP, "service": rule.get("service"),
                                 "status": rule.get("status"), "error": rule.get("error")})
    if rule.get("error") == "timeout":
        raise socket.timeout("timed out")
    if rule.get("error") == "refused":
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    code = int(rule.get("status") or 500)
    headers = {}
    body = rule.get("body")
    if code == 429:
        headers["Retry-After"] = str(rule.get("retry_after", 1))
        if body is None and rule.get("service") == "telegram":
            body = {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 1",
                    "parameters": {"retry_after": rule.get("retry_after", 1)}}
    if body is None and rule.get("service") == "telegram":
        body = {"ok": False, "error_code": code, "description": "Internal Server Error"}
    if body is None and rule.get("service") == "kv":
        body = {"success": False, "errors": [{"code": 10013, "message": "namespace not found"}]}
    raise _http_error(url, code, body, headers)


# ---- Telegram ------------------------------------------------------------------------------------
TG_LIMIT_TEXT = 4096
TG_LIMIT_CAPTION = 1024


def _tg_bad(url, description, method, params):
    _append("telegram_out.jsonl", {"t": sim_now_iso(), "step": STEP, "method": method, "ok": False,
                                   "error": description, "text": str(params.get("text") or
                                                                     params.get("caption") or "")[:300]})
    return _http_error(url, 400, {"ok": False, "error_code": 400, "description": description})


def _buttons(markup):
    rows = (markup or {}).get("inline_keyboard") if isinstance(markup, dict) else None
    out = []
    for row in rows or []:
        for button in row or []:
            if isinstance(button, dict):
                out.append({"text": button.get("text"), "data": button.get("callback_data"),
                            "url": button.get("url")})
    return out


def _telegram(req, url, method):
    params = _params(req)
    state = _load("telegram_state.json", {"message_id": 100})
    bot_id = int(os.environ.get("TELEGRAM_BOT_TOKEN", "1:x").split(":", 1)[0] or 1)

    if method == "getMe":
        return {"ok": True, "result": {"id": bot_id, "is_bot": True, "first_name": "Sim Robot",
                                       "username": "sim_robot_bot", "can_join_groups": False}}
    if method == "getWebhookInfo":
        return {"ok": True, "result": {"url": "", "has_custom_certificate": False, "pending_update_count": 0}}
    if method in ("deleteWebhook", "setMyCommands", "answerCallbackQuery", "deleteMessage",
                  "sendChatAction", "setChatMenuButton", "deleteMyCommands", "pinChatMessage"):
        if method == "answerCallbackQuery":
            _append("telegram_out.jsonl", {"t": sim_now_iso(), "step": STEP, "method": method, "ok": True,
                                           "text": str(params.get("text") or "")[:200]})
        return {"ok": True, "result": True}
    if method == "getUpdates":
        queue = _load("tg_updates.json", [])
        try:
            offset = int(params.get("offset") or 0)
        except (TypeError, ValueError):
            offset = 0
        if offset:
            queue = [u for u in queue if int(u.get("update_id", 0)) >= offset]
            _save("tg_updates.json", queue)
        limit = int(params.get("limit") or 100)
        return {"ok": True, "result": queue[:limit]}

    chat = str(params.get("chat_id", ""))
    if method == "getChat":
        if chat != OWNER_CHAT:
            raise _tg_bad(url, "Bad Request: chat not found", method, params)
        return {"ok": True, "result": {"id": int(OWNER_CHAT), "type": "private", "first_name": "Owner"}}
    if method == "getFile":
        raise _tg_bad(url, "Bad Request: invalid file_id", method, params)

    if chat and chat != OWNER_CHAT:
        raise _tg_bad(url, "Bad Request: chat not found", method, params)
    markup = params.get("reply_markup")
    for button in _buttons(markup):
        data = button.get("data")
        if data is not None and (not data or len(str(data).encode("utf-8")) > 64):
            raise _tg_bad(url, "Bad Request: BUTTON_DATA_INVALID", method, params)
    if method in ("sendMessage", "editMessageText"):
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _tg_bad(url, "Bad Request: message text is empty", method, params)
        if len(text) > TG_LIMIT_TEXT:
            raise _tg_bad(url, "Bad Request: message is too long", method, params)
    caption = params.get("caption")
    if isinstance(caption, str) and len(caption) > TG_LIMIT_CAPTION:
        raise _tg_bad(url, "Bad Request: message caption is too long", method, params)
    if method == "sendMediaGroup":
        media = params.get("media") if isinstance(params.get("media"), list) else []
        if not 2 <= len(media) <= 10:
            raise _tg_bad(url, "Bad Request: wrong number of media in the album", method, params)
    if method in ("sendDocument", "sendPhoto") and not params.get("_files") and not params.get(
            "document") and not params.get("photo"):
        raise _tg_bad(url, "Bad Request: there is no file in the request", method, params)

    state["message_id"] = int(state.get("message_id", 100)) + 1
    _save("telegram_state.json", state)
    _append("telegram_out.jsonl", {
        "t": sim_now_iso(), "step": STEP, "method": method, "ok": True, "message_id": state["message_id"],
        "text": str(params.get("text") or params.get("caption") or "")[:3000],
        "buttons": _buttons(markup), "files": [f.get("name") for f in params.get("_files", [])]})
    message = {"message_id": state["message_id"], "date": int(_sim_time()),
               "chat": {"id": int(OWNER_CHAT or 0), "type": "private"}, "text": params.get("text", "")}
    if method == "sendMediaGroup":
        return {"ok": True, "result": [dict(message, message_id=state["message_id"] + i)
                                       for i in range(len(params.get("media") or []))]}
    if method in ("editMessageText", "editMessageReplyMarkup", "editMessageCaption"):
        return {"ok": True, "result": message}
    return {"ok": True, "result": message}


# ---- AI providers ------------------------------------------------------------------------------
def _prompt_of(payload):
    if not isinstance(payload, dict):
        return ""
    parts = []
    for message in payload.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, list):
            content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
        parts.append(str(content or ""))
    for item in payload.get("contents") or []:
        for part in (item.get("parts") or []) if isinstance(item, dict) else []:
            parts.append(str(part.get("text", "")) if isinstance(part, dict) else "")
    if payload.get("prompt"):
        parts.append(str(payload["prompt"]))
    return "\n".join(parts)


def _sell_proof():
    import importlib.util
    path = os.path.join(REPO_MARK, "tools", "sell_proof.py")
    spec = importlib.util.spec_from_file_location("_sim_sell_proof", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRAFT = """PRODUCT NAME: {name}
BUYER: {buyer}
PRICE: 9 USD
PLATFORM: Gumroad
WHAT'S INSIDE: {inside}
HOW TO USE: Step 1 download the file. Step 2 open it in your usual app. Step 3 replace the sample rows with your own items and keep it open during the week.
LISTING TITLE: {name} Digital Download
LISTING DESCRIPTION: A calm, clean {kind} that keeps everything for one task in one place. It opens in common free apps, uses plain labels and comes with sample rows you can replace. Personal use only.
SEARCH TAGS: {tags}
"""
DRAFT_KINDS = [
    ("budget", "Monthly Budget Spreadsheet", "people who want a simple monthly money overview", "spreadsheet",
     "Tab 1 Monthly Budget (Columns: Category, Planned, Actual, Difference), Tab 2 Bills (Columns: Bill, Due Date, Status)",
     "budget spreadsheet, monthly budget, expense tracker, bill tracker, money planner"),
    ("freelancer", "Freelancer Invoice Tracker", "freelancers who send invoices every month", "tracker",
     "Tab 1 Invoices (Columns: Client, Amount, Due Date, Status), Tab 2 Clients (Columns: Name, Email, Notes)",
     "invoice tracker, freelancer template, client tracker, payment log, small business"),
    ("adhd", "Gentle Focus Daily Planner", "adults who like short, visual daily plans", "printable planner",
     "Page 1 Daily Plan (top 3 tasks, time blocks), Page 2 Brain Dump, Page 3 Weekly Review",
     "daily planner, focus planner, printable planner, adhd planner, to do list"),
    ("2027", "2027 Teacher Planner", "teachers who plan the school year on paper", "dated planner",
     "Year at a glance, 12 monthly calendar pages for 2027, class roster page, lesson notes page",
     "2027 planner, teacher planner, monthly planner, printable calendar, dated planner"),
    ("resume", "Simple ATS Resume Bundle", "job seekers who want a clean single-column resume", "resume bundle",
     "Word resume with standard headings, Word cover letter, Excel job application tracker",
     "ats resume, resume template, cover letter, job tracker, word resume"),
    ("notion", "Client Workspace Notion Template", "freelancers who keep client notes in Notion", "Notion template",
     "Start Here page, Clients database, Projects database, Weekly Review page",
     "notion template, client tracker, notion dashboard, freelancer, productivity"),
]


def _draft_for(prompt):
    low = prompt.lower()
    for key, name, buyer, kind, inside, tags in DRAFT_KINDS:
        if key in low:
            return DRAFT.format(name=name, buyer=buyer, kind=kind, inside=inside, tags=tags)
    key, name, buyer, kind, inside, tags = DRAFT_KINDS[0]
    return DRAFT.format(name=name, buyer=buyer, kind=kind, inside=inside, tags=tags)


def fake_llm_reply(prompt):
    """A plausible answer for each robot prompt type (labels match the robot's own prompt wording)."""
    low = prompt.lower()
    if "you are the product builder" in low:
        match = re.search(r"product type:\s*([a-z_]+)", low)
        kind = match.group(1) if match else "sheets"
        try:
            specs = _sell_proof().builtin_specs()
            raw = specs.get(kind, specs["sheets"])[0]
            return "kind", json.dumps(raw)
        except Exception as exc:  # the fake must not hide the robot's own behaviour
            return "kind", json.dumps({"error": type(exc).__name__})
    if "new, specific product ideas" in low:
        return "ideas", "\n".join([
            "Shared household budget sheet for roommates splitting monthly bills",
            "Wedding savings tracker sheet for couples planning a small ceremony",
            "Sinking funds spreadsheet for parents saving for school supplies",
            "Holiday gift budget sheet for families with a fixed December list",
            "Side project expense log for hobby crafters who sell at weekend fairs",
            "Moving house budget checklist sheet for first-time renters"])
    if "qa checker" in low:
        return "qa", json.dumps({"score": 88, "verdict": "pass", "issues": []})
    if "product council critic" in low:
        return "council", json.dumps({"weaknesses": [], "verdict": "OK"})
    if "buyer's voice" in low:
        return "buyer", json.dumps({"answer": "Keeps my monthly numbers in one clear place", "conviction": 8})
    if "judge agent" in low:
        return "judge", json.dumps({"verdict": "PASS", "reasons": ["Clear layout and buyer value"],
                                    "top_fix": "None"})
    if "scout" in low:
        return "scout", json.dumps([{"category": "Planners", "idea": "Weekly Meal-Free Focus Planner",
                                     "demand_score": 20, "competition_score": 18, "fit_score": 22,
                                     "effort_score": 21, "reason": "Simple printable for busy adults"}] * 5)
    if "trend" in low:
        return "trend", json.dumps([{"title": "Printable weekly planners", "score": 70,
                                     "why": "Steady interest in simple paper planning"}])
    if "json" in low and ("pin" in low or "pinterest" in low):
        return "pins", json.dumps([{"title": "Simple Monthly Budget Sheet", "description":
                                    "A calm budget spreadsheet for your month.", "board": "Budget Planners",
                                    "keywords": ["budget", "spreadsheet"]}])
    if any(word in low for word in ("pinterest", "instagram", "shorts", "seo", "caption", "hashtag")):
        return "marketing", ("Pin 1 Title: Simple Monthly Budget Sheet\nPin 1 Description: A calm budget "
                             "spreadsheet for your month.\nKeywords: budget, spreadsheet\nBoard Name: Budget "
                             "Planners")
    if "product name" in low or "listing title" in low or "search tags" in low or "rewrite the product draft" in low:
        return "draft", _draft_for(prompt)
    if "research" in low or "notes" in low:
        return "research", ("Buyers want one clear page, sample rows and plain labels. Keep the layout simple, "
                            "printable and easy to edit. Avoid promises about money or health.")
    if "telugu" in low or "tenglish" in low or "owner" in low:
        return "chat", "Robot baagane undi. Drafts /list lo choodandi."
    return "other", "Okay. Here is a short, honest answer for the owner."


def _llm(req, url, host, path):
    try:
        payload = json.loads(_body(req).decode("utf-8") or "{}")
    except ValueError:
        payload = {}
    method = req.get_method()
    if method == "GET" or path.endswith("/models") or "/catalog/models" in path:
        return {"data": [{"id": "sim-model"}], "models": [{"name": "models/sim-model"}],
                "success": True, "result": []}
    prompt = _prompt_of(payload)
    label, text = fake_llm_reply(prompt)
    _append("llm_log.jsonl", {"t": sim_now_iso(), "step": STEP, "host": host, "label": label,
                              "prompt": prompt[:160].replace("\n", " ")})
    if host == "api.cloudflare.com":
        return {"success": True, "result": {"response": text}, "errors": []}
    if ":generatecontent" in path.lower():
        return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
    return {"id": "sim", "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                      "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": len(text) // 4}}


# ---- Cloudflare KV, alarm clock, GitHub ------------------------------------------------------------
def _kv(req, url, path):
    store = _load("kv.json", {})
    method = req.get_method()
    if "/keys" in path and method == "GET":
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        prefix = query.get("prefix", "")
        return {"success": True, "errors": [], "result": [{"name": k} for k in sorted(store) if k.startswith(prefix)],
                "result_info": {"count": len(store), "cursor": ""}}
    match = re.search(r"/values/([^?]+)", path)
    if not match:
        return {"success": True, "errors": [], "result": None}
    key = urllib.parse.unquote(match.group(1))
    if method == "GET":
        if key not in store:
            raise _http_error(url, 404, {"success": False, "errors": [{"code": 10009, "message": "key not found"}]})
        return store[key].encode("utf-8")
    if method == "DELETE":
        store.pop(key, None)
        _save("kv.json", store)
        return {"success": True, "errors": [], "result": None}
    store[key] = _body(req).decode("utf-8", "replace")
    _save("kv.json", store)
    return {"success": True, "errors": [], "result": None}


def _alarm(path):
    if path.endswith("/run") or path.endswith("/heartbeat"):
        return {"ok": True, "mac_alive": False, "minutes_month": 120}
    return {"ok": True, "mac_alive": False, "minutes_month": 120, "last_run": sim_now_iso()}


MAIN_SHA = "5" * 40


# ---- GitHub pull requests for the auto-fix drill (tools/autofix_drill.py, CCF-1) -------------------
# Only active when ROBOT_SIM_DIR/github_hub.json exists. The pull requests are REAL branches in a bare
# git repo inside the sandbox (hub["git_dir"]): head SHAs, changed files and patches come from git.
def _hub_git(hub, *args):
    import subprocess
    try:
        done = subprocess.run(["git", "--git-dir", str(hub.get("git_dir", "")), *args], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, errors="replace", check=False)
    except OSError:
        return ""
    return done.stdout if done.returncode == 0 else ""


def _hub_pull(hub, pr, repo):
    ref = str(pr.get("head_ref", ""))
    sha = _hub_git(hub, "rev-parse", "--verify", "-q", "refs/heads/" + ref).strip() or pr.get("head_sha", "")
    return {"number": pr["number"], "title": pr.get("title", ""), "body": pr.get("body", ""),
            "state": pr.get("state", "open"), "draft": False, "merged": pr.get("merged") is True,
            "user": {"login": pr.get("user", "github-actions[bot]")}, "base": {"ref": pr.get("base", "main")},
            "mergeable_state": pr.get("mergeable_state", "clean"),
            "head": {"ref": ref, "sha": sha, "repo": {"full_name": repo}}}


def _hub_files(hub, pr):
    span = "main..." + str(pr.get("head_ref", ""))
    rows = []
    for line in _hub_git(hub, "diff", "--name-status", "-M", span).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        row = {"filename": parts[-1], "status": parts[0][:1],
               "patch": _hub_git(hub, "diff", span, "--", parts[-1])}
        if parts[0].startswith("R") and len(parts) == 3:
            row["previous_filename"] = parts[1]
        rows.append(row)
    return rows


def _hub(hub, req, url, method, path, repo):
    """Answer one GitHub call from the hub file. Returns None when the call is not a hub call."""
    def body():
        try:
            data = json.loads(_body(req).decode("utf-8") or "{}")
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def note(call, **more):
        _append("github_log.jsonl", dict({"t": sim_now_iso(), "step": STEP, "call": call}, **more))

    pulls = hub.setdefault("pulls", {})
    if path == "/pulls" and method == "GET":
        return [_hub_pull(hub, pr, repo) for pr in pulls.values() if pr.get("state") == "open"]
    if path == "/pulls" and method == "POST":
        data = body()
        ref = str(data.get("head", ""))
        sha = _hub_git(hub, "rev-parse", "--verify", "-q", "refs/heads/" + ref).strip()
        if not sha or ref == "main" or data.get("base") != "main":
            raise _http_error(url, 422, {"message": "Validation Failed"})
        number = int(hub.get("next_pull", 1))
        hub["next_pull"] = number + 1
        # the workflow token always acts as github-actions[bot]
        pulls[str(number)] = {"number": number, "title": str(data.get("title", "")), "state": "open",
                              "body": str(data.get("body", "")), "head_ref": ref, "head_sha": sha, "base": "main",
                              "user": "github-actions[bot]"}
        _save("github_hub.json", hub)
        note("open pull request", number=number, ref=ref)
        return _hub_pull(hub, pulls[str(number)], repo)
    match = re.fullmatch(r"/pulls/(\d+)(/files|/merge)?", path)
    if match:
        pr = pulls.get(match.group(1))
        if pr is None:
            raise _http_error(url, 404, {"message": "Not Found"})
        if match.group(2) == "/files" and method == "GET":
            return _hub_files(hub, pr)
        if match.group(2) == "/merge" and method == "PUT":
            current = _hub_pull(hub, pr, repo)
            if pr.get("state") != "open" or body().get("sha") != current["head"]["sha"]:
                raise _http_error(url, 409, {"message": "Head branch was modified. Review and try the merge again."})
            pr.update(state="closed", merged=True, head_sha=current["head"]["sha"])
            hub.setdefault("merged", []).append({"number": pr["number"], "sha": current["head"]["sha"],
                                                 "ref": pr.get("head_ref", ""), "done": False})
            _save("github_hub.json", hub)
            note("merge pull request", number=pr["number"])
            return {"merged": True, "sha": current["head"]["sha"]}
        if match.group(2) is None and method == "PATCH":
            if body().get("state") == "closed":
                pr["state"] = "closed"
                hub.setdefault("closed", []).append(pr["number"])
                _save("github_hub.json", hub)
                note("close pull request", number=pr["number"])
            return _hub_pull(hub, pr, repo)
        if match.group(2) is None and method == "GET":
            return _hub_pull(hub, pr, repo)
    match = re.fullmatch(r"/commits/([0-9a-f]{40})/(check-runs|status)", path)
    if match and method == "GET":
        if match.group(2) == "status":
            return {"state": "success", "statuses": list((hub.get("statuses") or {}).get(match.group(1), []))}
        runs = list((hub.get("check_runs") or {}).get(match.group(1), []))
        return {"total_count": len(runs), "check_runs": runs}
    match = re.fullmatch(r"/actions/workflows/([\w.-]+)/dispatches", path)
    if match and method == "POST":
        data = body()
        hub.setdefault("dispatches", []).append({"workflow": match.group(1), "ref": data.get("ref", ""),
                                                 "inputs": data.get("inputs") or {}, "t": sim_now_iso()})
        _save("github_hub.json", hub)
        note("dispatch", path=path)
        return b""
    match = re.fullmatch(r"/actions/runs/(\d+)(/artifacts)?", path)
    if match and method == "GET" and match.group(1) in (hub.get("runs") or {}):
        if match.group(2):
            rows = list((hub.get("artifacts") or {}).get(match.group(1), []))
            return {"total_count": len(rows), "artifacts": rows}
        return dict(hub["runs"][match.group(1)])
    match = re.fullmatch(r"/actions/artifacts/(\d+)/zip", path)
    if match and method == "GET":
        try:
            with open(str((hub.get("artifact_files") or {})[match.group(1)]), "rb") as handle:
                return handle.read()
        except (KeyError, OSError):
            raise _http_error(url, 404, {"message": "Not Found"})
    return None


def _github(req, url, path):
    """The few GitHub REST calls the robot makes: refs (daily report receipt), workflow dispatch, runs, PRs."""
    method = req.get_method()
    repo = "/".join(path.split("/")[2:4]) if path.startswith("/repos/") else ""
    path = re.sub(r"^/repos/[^/]+/[^/]+", "", path)
    hub = _load("github_hub.json", None)
    if isinstance(hub, dict):
        reply = _hub(hub, req, url, method, path, repo)
        if reply is not None:
            return reply
    if path == "/git/ref/heads/main" and method == "GET":
        return {"ref": "refs/heads/main", "object": {"sha": MAIN_SHA, "type": "commit"}}
    if path == "/git/refs" and method == "POST":
        try:
            ref = json.loads(_body(req).decode("utf-8") or "{}").get("ref", "")
        except ValueError:
            ref = ""
        refs = _load("github_refs.json", [])
        if not ref.startswith("refs/") or ref in refs:
            raise _http_error(url, 422, {"message": "Reference already exists"})
        refs.append(ref)
        _save("github_refs.json", refs)
        _append("github_log.jsonl", {"t": sim_now_iso(), "step": STEP, "call": "create ref", "ref": ref})
        return {"ref": ref, "object": {"sha": MAIN_SHA}}
    if path.endswith("/dispatches") and method == "POST":
        _append("github_log.jsonl", {"t": sim_now_iso(), "step": STEP, "call": "dispatch", "path": path})
        return b""
    if re.fullmatch(r"/actions/runs/\d+", path):
        return {"id": int(path.rsplit("/", 1)[-1]), "status": "completed", "conclusion": "success",
                "head_sha": MAIN_SHA, "event": "schedule"}
    if "/runs" in path:
        return {"total_count": 0, "workflow_runs": []}
    if re.fullmatch(r"/pulls/\d+", path):
        raise _http_error(url, 404, {"message": "Not Found"})
    if "/pulls" in path or "/issues" in path:
        return []
    raise _http_error(url, 404, {"message": "Not Found"})


# ---- urlopen -------------------------------------------------------------------------------------
def fake_urlopen(req, data=None, timeout=None, *args, **kwargs):
    if isinstance(req, str):
        req = urllib.request.Request(req, data=data)
    elif data is not None:
        req.data = data
    url = req.full_url
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path
    service = _service(host, path)
    method = path.rsplit("/", 1)[-1] if service == "telegram" else ""
    if service == "other":
        _append("blocked.jsonl", {"t": sim_now_iso(), "step": STEP, "host": host})
        raise urllib.error.URLError(OSError(101, f"simulator: no internet for {host}"))
    rule = _fault_for(service, method)
    if rule:
        _raise_fault(rule, url)
    if service == "telegram":
        if "/file/bot" in path:
            raise _http_error(url, 404)
        reply = _telegram(req, url, method)
    elif service == "llm":
        reply = _llm(req, url, host, path)
    elif service == "kv":
        reply = _kv(req, url, path)
    elif service == "alarm":
        reply = _alarm(path)
    else:
        reply = _github(req, url, path)
    return FakeResponse(reply, 200 if reply != b"" else 204, url)


def _opener_open(self, fullurl, data=None, timeout=None):
    return fake_urlopen(fullurl, data, timeout)


def _guard_connect(real):
    def connect(self, address):
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost") and self.family in (
                socket.AF_INET, socket.AF_INET6):
            _append("blocked.jsonl", {"t": sim_now_iso(), "step": STEP, "host": str(host), "raw_socket": True})
            raise OSError(101, "simulator: network is unreachable")
        return real(self, address)
    return connect


class _Tee:
    """Copies everything a robot process prints into ROBOT_SIM_DIR/proc/ (run_failure.py hides
    successful step output, so the simulator keeps its own copy)."""

    def __init__(self, stream, log):
        self._stream, self._log = stream, log

    def write(self, text):
        try:
            self._log.write(text)
            self._log.flush()
        except (OSError, ValueError):
            pass
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _tee_output():
    try:
        folder = _path("proc")
        os.makedirs(folder, exist_ok=True)
        step = re.sub(r"[^a-z0-9_.-]+", "_", STEP.lower())
        name = f"run{int(RUN or 0):02d}-{step}-{os.getpid()}.txt"
        log = open(os.path.join(folder, name), "a", encoding="utf-8")
        log.write("$ " + " ".join(sys.argv)[:300] + "\n")
        sys.stdout = _Tee(sys.stdout, log)
        sys.stderr = _Tee(sys.stderr, log)
    except (OSError, ValueError):
        pass


_REAL_OPEN = builtins.open
_REAL_OS_OPEN = os.open


def _install_disk_full():
    """faults.json rule {"service": "disk", "path": "exported_content/products"}: writes there fail with
    ENOSPC for this whole process (the robot sees a full disk; reads still work)."""
    rules = [r for r in _load("faults.json", []) if r.get("service") == "disk"]
    if not rules:
        return
    marks = [str(r.get("path") or "") for r in rules]

    def blocked(file, writing):
        if not writing or isinstance(file, int):
            return False
        name = os.path.abspath(os.fsdecode(file))
        return any(mark and mark in name for mark in marks)

    def full():
        _append("faults_hit.jsonl", {"t": sim_now_iso(), "step": STEP, "service": "disk", "error": "ENOSPC"})
        return OSError(28, "No space left on device")

    def fake_open(file, mode="r", *args, **kwargs):
        if blocked(file, any(c in str(mode) for c in "wax+")):
            raise full()
        return _REAL_OPEN(file, mode, *args, **kwargs)

    def fake_os_open(path, flags, *args, **kwargs):
        if blocked(path, bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))):
            raise full()
        return _REAL_OS_OPEN(path, flags, *args, **kwargs)

    builtins.open = fake_open
    io.open = fake_open
    os.open = fake_os_open


def install():
    """Called by the sandbox sitecustomize.py. Does nothing unless ROBOT_SIM_DIR is set."""
    global _INSTALLED
    if _INSTALLED or not SIM_DIR:
        return
    _INSTALLED = True
    install_clock(float(os.environ.get("ROBOT_SIM_NOW") or _REAL_TIME()))
    urllib.request.urlopen = fake_urlopen
    urllib.request.OpenerDirector.open = _opener_open
    socket.socket.connect = _guard_connect(_REAL_CONNECT)
    socket.socket.connect_ex = _guard_connect(_REAL_CONNECT_EX)
    sys.excepthook = _excepthook
    _tee_output()
    _install_disk_full()
    builtins.print = _print
