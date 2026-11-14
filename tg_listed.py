"""Telegram plugin: tg_listed.py — CC2-3: remember which packs are already on Gumroad.

  /listed N https://name.gumroad.com/l/slug   save the Gumroad link of pack N (the owner listed it by hand)
  /listed N                                   show the saved link of pack N
  /listed N remove                            forget the link (wrong link typed, product taken down)
  /listed                                     all listed packs with their links

Also plain words: "listed", "listed packs", "pack 3 listed https://...".

Where the link is kept
  - Table listed_packs (approval_id, store_url, listed_ts) in the robot database. This is the record
    /status and /ready read (status_line / ready_note below).
  - When the approval's payload is already a JSON object, store_url is also written into it, which is
    the field public_export reads. A plain-text payload (the draft text itself, read by many other
    modules) is never rewritten.

Rules kept
  - Plugin interface: COMMANDS + handle(text, ctx); replies only through the return value.
  - telegram_inbox is never imported here.
  - Only https links on gumroad.com (or a *.gumroad.com shop) with a /l/<product> path are accepted.
  - The robot never opens the link and never lists, publishes or sells anything.
"""
from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

COMMANDS = [
    {
        "cmd": "/listed",
        "words": ["listed", "listed packs", "listed list", "gumroad lo unnavi"],
        "help_en": "Remember a pack's Gumroad link: /listed N <link>. /listed alone shows all listed packs",
        "help_te": "Pack N Gumroad link gurthu pettukuntundi: /listed N <link>. /listed ante listed packs anni",
    },
]

MAX_LINK_CHARS = 200
EXAMPLE = "/listed 3 https://yourshop.gumroad.com/l/abcde"
_HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)?gumroad\.com$")
_PATH_RE = re.compile(r"^/l/([A-Za-z0-9][A-Za-z0-9_-]{0,79})/?$")
_REMOVE_WORDS = {"remove", "delete", "clear", "undo", "teesey", "teeseyi", "teesesey", "tiseyi", "vaddu"}
_LIST_TEXTS = {"/listed", "listed", "listed packs", "listed list", "gumroad lo unnavi", "listed packs chupinchu"}
_HEAD_RE = re.compile(r"^(?:/listed(?:@\w+)?|listed)\s+(?:pack\s+)?#?(\d{1,9})(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_PACK_FIRST_RE = re.compile(r"^pack\s+#?(\d{1,9})\s+listed(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


# ---- link check --------------------------------------------------------------------
def validate_url(raw: Any) -> Tuple[Optional[str], str]:
    """(clean link, "") for a real Gumroad product link, else (None, reason in Tenglish).

    The clean link is always https://<host>/l/<product>: query strings and fragments are dropped.
    """
    if not isinstance(raw, str):
        return None, "Link text kaadu."
    link = raw.strip().strip("<>")
    if not link:
        return None, "Link ivvaledu."
    if len(link) > MAX_LINK_CHARS:
        return None, "Link chala pedda ga undi."
    if any(ch.isspace() for ch in link) or any(ord(ch) < 33 or ord(ch) > 126 for ch in link):
        return None, "Link lo space leda vintha letters unnayi."
    if "\\" in link:
        return None, "Link lo '\\' undakudadu."
    if not link.lower().startswith("https://"):
        return None, "Link https:// tho start avvali (http leda vere type kaadu)."
    try:
        parts = urlsplit(link)
        port = parts.port
    except ValueError:
        return None, "Link sarigga ledu."
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None, "Link lo user/password undakudadu."
    if port is not None:
        return None, "Link lo port number undakudadu."
    host = (parts.hostname or "").lower()
    if not _HOST_RE.match(host):
        return None, "Idi Gumroad link kaadu (gumroad.com leda meeshop.gumroad.com undali)."
    match = _PATH_RE.match(parts.path or "")
    if not match:
        return None, "Product link kavali: .../l/<product> la undali (Gumroad product page link)."
    return f"https://{host}/l/{match.group(1)}", ""


# ---- storage -------------------------------------------------------------------------
def _connect(db_path=None):
    import guardrails
    conn = guardrails.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS listed_packs ("
                 "approval_id INTEGER PRIMARY KEY, store_url TEXT NOT NULL, listed_ts TEXT NOT NULL)")
    return conn


def _approval(conn, approval_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT id, kind, title, payload, status FROM approvals WHERE id = ?",
                       (approval_id,)).fetchone()
    if not row:
        return None
    return dict(zip(("id", "kind", "title", "payload", "status"), row))


def _json_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """The payload as a dict when it really is a JSON object, else None (plain draft text)."""
    if not isinstance(payload, str) or not payload.strip().startswith("{"):
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _sync_payload(conn, approval: Dict[str, Any], url: Optional[str]) -> bool:
    """Write (or remove) store_url inside a JSON-object payload. Plain-text payloads stay untouched."""
    data = _json_payload(approval.get("payload"))
    if data is None:
        return False
    if url is None:
        if "store_url" not in data:
            return False
        data.pop("store_url")
    else:
        data["store_url"] = url
    conn.execute("UPDATE approvals SET payload = ? WHERE id = ?",
                 (json.dumps(data, ensure_ascii=False), approval["id"]))
    return True


def _stamp(now: Any = None) -> str:
    if isinstance(now, datetime.datetime):
        moment = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    else:
        moment = datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_title(title: Any) -> str:
    """'Draft: ADHD planner (v3, llm)' -> 'ADHD planner'."""
    text = re.sub(r"^\s*Draft:\s*", "", str(title or ""))
    text = re.sub(r"\s*\(v\d+[^)]*\)\s*$", "", text).strip()
    return text[:80] or "Pack"


def set_listed(approval_id: int, url: str, db_path=None, now=None) -> Dict[str, Any]:
    """Save the link of an approved pack. Returns {"ok", "reason", "url", "old_url", "title", "payload_updated"}."""
    clean, reason = validate_url(url)
    if clean is None:
        return {"ok": False, "reason": reason}
    conn = _connect(db_path)
    try:
        approval = _approval(conn, int(approval_id))
        if approval is None:
            return {"ok": False, "reason": "not_found"}
        if approval["kind"] != "public_draft" or approval["status"] != "approved":
            return {"ok": False, "reason": "not_approved", "status": approval["status"]}
        row = conn.execute("SELECT store_url FROM listed_packs WHERE approval_id = ?", (approval["id"],)).fetchone()
        old_url = row[0] if row else None
        conn.execute("INSERT INTO listed_packs (approval_id, store_url, listed_ts) VALUES (?, ?, ?) "
                     "ON CONFLICT(approval_id) DO UPDATE SET store_url = excluded.store_url, "
                     "listed_ts = excluded.listed_ts", (approval["id"], clean, _stamp(now)))
        payload_updated = _sync_payload(conn, approval, clean)
        conn.commit()
        return {"ok": True, "reason": "", "url": clean, "old_url": old_url,
                "title": clean_title(approval["title"]), "payload_updated": payload_updated}
    finally:
        conn.close()


def remove_listed(approval_id: int, db_path=None) -> Optional[str]:
    """Forget the link of a pack. Returns the removed link, or None when the pack was not listed."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT store_url FROM listed_packs WHERE approval_id = ?", (int(approval_id),)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM listed_packs WHERE approval_id = ?", (int(approval_id),))
        approval = _approval(conn, int(approval_id))
        if approval is not None:
            data = _json_payload(approval.get("payload"))
            if data is not None and data.get("store_url") == row[0]:
                _sync_payload(conn, approval, None)
        conn.commit()
        return row[0]
    finally:
        conn.close()


def all_listed(db_path=None) -> List[Dict[str, Any]]:
    """Every listed pack, lowest number first: {"id", "url", "listed_ts", "title"}."""
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT l.approval_id, l.store_url, l.listed_ts, a.title FROM listed_packs l "
                            "LEFT JOIN approvals a ON a.id = l.approval_id ORDER BY l.approval_id").fetchall()
    finally:
        conn.close()
    return [{"id": row[0], "url": row[1], "listed_ts": row[2], "title": clean_title(row[3])} for row in rows]


def get_listed(approval_id: int, db_path=None) -> Optional[Dict[str, Any]]:
    for item in all_listed(db_path):
        if item["id"] == int(approval_id):
            return item
    return None


def is_listed(approval_id: int, db_path=None) -> bool:
    return get_listed(approval_id, db_path) is not None


def listed_ids(db_path=None) -> List[int]:
    return [item["id"] for item in all_listed(db_path)]


# ---- lines other commands show (/status, /ready) -------------------------------------
def status_line(db_path=None) -> str:
    """One line for /status; empty text when nothing is listed yet."""
    ids = listed_ids(db_path)
    if not ids:
        return ""
    shown = ", ".join(f"#{number}" for number in ids[:10])
    more = f" +{len(ids) - 10}" if len(ids) > 10 else ""
    return f"Gumroad lo listed: {len(ids)} packs ({shown}{more}) — /listed"


def ready_note(pack_id: int, db_path=None) -> str:
    """One line for the /ready N card; empty text when pack N is not listed."""
    item = get_listed(pack_id, db_path)
    if item is None:
        return ""
    return f"✅ LISTED — Pack #{item['id']} already Gumroad lo undi: {item['url']}"


def ready_summary_note(db_path=None) -> str:
    """One line for the /ready list; empty text when nothing is listed."""
    ids = listed_ids(db_path)
    if not ids:
        return ""
    return "✅ Already Gumroad lo listed: " + ", ".join(f"#{number}" for number in ids[:20]) + " (/listed)"


# ---- replies ---------------------------------------------------------------------------
def list_text(db_path=None) -> str:
    items = all_listed(db_path)
    if not items:
        return ("Inka e pack Gumroad lo listed ani gurthu pettaledu.\n"
                "Gumroad lo pettaka link ila pampandi: " + EXAMPLE)
    lines = [f"Gumroad lo listed packs ({len(items)}):", ""]
    for item in items[:30]:
        lines.append(f"#{item['id']} — {item['title']}")
        lines.append(f"   {item['url']}  (listed {item['listed_ts'][:10]})")
    if len(items) > 30:
        lines.append(f"... inko {len(items) - 30} packs")
    lines += ["", "Kotha link: /listed N <link>   |   Link teeseyadaniki: /listed N remove"]
    return "\n".join(lines)


def show_text(pack_id: int, db_path=None) -> str:
    item = get_listed(pack_id, db_path)
    if item is None:
        return (f"Pack #{pack_id} inka listed ani gurthu pettaledu.\n"
                f"Gumroad lo pettaka: /listed {pack_id} <Gumroad link>")
    return (f"Pack #{item['id']} — {item['title']}\nGumroad link: {item['url']}\n"
            f"Listed: {item['listed_ts'][:10]}\nLink teeseyadaniki: /listed {item['id']} remove")


def save_text(pack_id: int, link: str, db_path=None, now=None) -> str:
    result = set_listed(pack_id, link, db_path=db_path, now=now)
    if result["ok"]:
        lines = [f"✅ Pack #{pack_id} ({result['title']}) Gumroad lo listed ani gurthu pettukunna.",
                 f"Link: {result['url']}"]
        if result["old_url"] and result["old_url"] != result["url"]:
            lines.append(f"Paatha link teesesanu: {result['old_url']}")
        elif result["old_url"]:
            lines.append("(Ide link mundu kuda undi.)")
        lines.append("Ippudu /status, /ready lo idi listed ga kanipistundi. Anni listed packs: /listed")
        return "\n".join(lines)
    if result["reason"] == "not_found":
        return f"Pack #{pack_id} dorakaledu. Ready packs kosam /ready, listed packs kosam /listed."
    if result["reason"] == "not_approved":
        return (f"Pack #{pack_id} approved Draft kaadu (status: {result.get('status')}). "
                "Approve ayina, build ayina pack ni matrame listed ani mark cheyagalanu.")
    return (f"Link save cheyaledu: {result['reason']}\n"
            f"Sari ayina format: {EXAMPLE}")


def remove_text(pack_id: int, db_path=None) -> str:
    removed = remove_listed(pack_id, db_path)
    if removed is None:
        return f"Pack #{pack_id} listed list lo ledu, teeseyadaniki emi ledu. Listed packs: /listed"
    return f"Pack #{pack_id} listed link teesesanu ({removed}). Malli pettadaniki: /listed {pack_id} <link>"


# ---- parsing -----------------------------------------------------------------------------
def parse(text: Any) -> Optional[Tuple[str, Optional[int], str]]:
    """("list", None, ""), ("show", N, ""), ("remove", N, ""), ("save", N, link), ("help", N, rest) or None."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw or len(raw) > 400:
        return None
    low = " ".join(raw.lower().split()).rstrip("?.! ")
    if low in _LIST_TEXTS or re.fullmatch(r"/listed@\w+", low):
        return "list", None, ""
    match = _HEAD_RE.match(raw) or _PACK_FIRST_RE.match(raw)
    if not match:
        if re.match(r"^/listed(?:@\w+)?(?:\s|$)", raw, re.IGNORECASE):
            return "help", None, raw
        return None
    pack_id = int(match.group(1))
    rest = (match.group(2) or "").strip()
    if not rest:
        return "show", pack_id, ""
    if rest.lower().rstrip(".! ") in _REMOVE_WORDS:
        return "remove", pack_id, ""
    words = rest.split()
    if len(words) == 1:
        return "save", pack_id, words[0]
    return "help", pack_id, rest


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Standard Telegram plugin entry point."""
    parsed = parse(text)
    if parsed is None:
        return None
    ctx = ctx if isinstance(ctx, dict) else {}
    db_path = ctx.get("db_path")
    action, pack_id, rest = parsed
    try:
        if action == "list":
            return {"text": list_text(db_path)}
        if action == "show":
            return {"text": show_text(pack_id, db_path)}
        if action == "remove":
            return {"text": remove_text(pack_id, db_path)}
        if action == "save":
            return {"text": save_text(pack_id, rest, db_path, ctx.get("now"))}
        return {"text": "Ila pampandi (pack number, tarvata okka Gumroad link):\n" + EXAMPLE
                        + "\nListed packs chudadaniki: /listed"}
    except Exception as error:
        return {"text": f"/listed lo problem vachindi ({type(error).__name__}). /doctor tho chudandi."}
