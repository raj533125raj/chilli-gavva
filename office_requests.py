"""office_requests.py — Safe Office Requests Queue & Natural Language Parser for Binny Mills.

Parses plain English and Tenglish phrases for Binny Mills office layout & agent customisations,
such as:
  - Room resizing (bigger/smaller): "office lo Pinterest room pedda chey", "QA room chinna chey", "make Research room bigger"
  - Room movement: "QA room ni Products pakkana pettu", "move Studio next to Reception"
  - Room addition: "kotha room Research add chey", "add room Analytics", "create room Lab"
  - Room removal: "Marketing room remove chey", "room Lab teeyyi", "delete room QA"
  - Room renaming: "Research room peru Discovery ga marchu", "rename room QA to Testing"
  - Agent styling: "Pin Designer ki red cap pettu", "Doctor ki blue theme pettu", "CEO ki star icon pettu"
  - Global office theme: "office theme dark pettu", "office dark mode", "festival theme pettu"
  - Undo: "office change undo", "undo office change", "undo last office change", "office lo undo chey", "venakki teesko"

Requests are persisted in the `office_requests` database table (auto-created if missing)
with status ('queued' | 'applied' | 'failed'). The table preserves compatibility with
existing systems (receipt/nonce/digest columns) and keeps the last 50 entries.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

import guardrails

MAX_REQUESTS_KEPT = 50

# Canonical kinds:
# - "room.update": resize, move, rename
# - "room.add": create/add room
# - "room.remove": delete/remove room
# - "agent.update": theme/color/cap/icon for agent
# - "office.theme": global office theme
# - "undo": rollback last office request


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Ensure the office_requests table exists and has all required columns."""
    cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='office_requests'").fetchone()
    if not cur:
        conn.execute("""CREATE TABLE office_requests (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            target TEXT,
            value TEXT,
            label_te TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            nonce TEXT,
            digest TEXT,
            receipt TEXT,
            intent TEXT,
            args TEXT
        )""")
    else:
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(office_requests)").fetchall()}
        col_defs = {
            "id": "TEXT",
            "ts": "TEXT",
            "kind": "TEXT",
            "target": "TEXT",
            "value": "TEXT",
            "label_te": "TEXT",
            "status": "TEXT DEFAULT 'queued'",
            "nonce": "TEXT",
            "digest": "TEXT",
            "receipt": "TEXT",
            "intent": "TEXT",
            "args": "TEXT",
        }
        for col, col_type in col_defs.items():
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE office_requests ADD COLUMN {col} {col_type}")


def _trim_history(conn: sqlite3.Connection) -> None:
    """Keep only the latest MAX_REQUESTS_KEPT records."""
    row_count = conn.execute("SELECT COUNT(*) FROM office_requests").fetchone()[0]
    if row_count > MAX_REQUESTS_KEPT:
        conn.execute("""
            DELETE FROM office_requests
            WHERE rowid NOT IN (
                SELECT rowid FROM office_requests
                ORDER BY rowid DESC
                LIMIT ?
            )
        """, (MAX_REQUESTS_KEPT,))


def store_request(
    kind: str,
    target: Optional[str] = None,
    value: Optional[Dict[str, Any] | str] = None,
    label_te: Optional[str] = None,
    db_path: Optional[str] = None,
    status: str = "queued",
    now_dt: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Create and store an office request record into the database."""
    now = now_dt or datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    req_id = f"off_{uuid.uuid4().hex[:12]}"
    nonce = uuid.uuid4().hex
    digest = "telegram_office_ui"

    val_str = json.dumps(value) if isinstance(value, (dict, list)) else (str(value) if value is not None else "")
    args_dict = {"target": target, "value": value}
    args_json = json.dumps(args_dict)
    receipt_dict = {
        "at": ts,
        "state": "held" if status == "queued" else ("applied" if status == "applied" else "rejected"),
        "type": kind,
        "target": target,
        "reason": label_te or "Office UI request",
    }
    receipt_json = json.dumps(receipt_dict)

    conn = guardrails.connect(db_path)
    try:
        _ensure_table(conn)
        conn.execute("""
            INSERT INTO office_requests (
                id, ts, kind, target, value, label_te, status,
                nonce, digest, receipt, intent, args
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            req_id, ts, kind, target or "", val_str, label_te or "", status,
            nonce, digest, receipt_json, kind, args_json
        ))
        _trim_history(conn)
        conn.commit()
    finally:
        conn.close()

    return {
        "id": req_id,
        "ts": ts,
        "kind": kind,
        "target": target or "",
        "value": value,
        "label_te": label_te or "",
        "status": status,
    }


def list_office_requests(
    db_path: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Retrieve office requests from the database, newest first."""
    db_file = db_path or guardrails.DB_PATH
    if not os.path.exists(db_file):
        return []

    conn = guardrails.connect(db_path)
    try:
        _ensure_table(conn)
        if status:
            rows = conn.execute("""
                SELECT id, ts, kind, target, value, label_te, status
                FROM office_requests
                WHERE status = ?
                ORDER BY rowid DESC
                LIMIT ?
            """, (status, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, ts, kind, target, value, label_te, status
                FROM office_requests
                ORDER BY rowid DESC
                LIMIT ?
            """, (limit,)).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        rid, rts, rkind, rtarget, rval, rlabel, rstatus = r
        parsed_val: Any = rval
        if rval:
            try:
                parsed_val = json.loads(rval)
            except Exception:
                parsed_val = rval
        result.append({
            "id": rid,
            "ts": rts,
            "kind": rkind,
            "target": rtarget,
            "value": parsed_val,
            "label_te": rlabel,
            "status": rstatus,
        })
    return result


def undo_last_request(db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Find the most recent non-undo request, mark it cancelled/failed, and record undo."""
    db_file = db_path or guardrails.DB_PATH
    if not os.path.exists(db_file):
        return None

    conn = guardrails.connect(db_path)
    last_req = None
    try:
        _ensure_table(conn)
        row = conn.execute("""
            SELECT id, kind, target, value, label_te, status
            FROM office_requests
            WHERE kind != 'undo' AND status != 'failed'
            ORDER BY rowid DESC
            LIMIT 1
        """).fetchone()
        if row:
            last_id, lkind, ltarget, lval, llabel, lstatus = row
            conn.execute("UPDATE office_requests SET status = 'failed' WHERE id = ?", (last_id,))
            conn.commit()
            last_req = {
                "id": last_id,
                "kind": lkind,
                "target": ltarget,
                "value": lval,
                "label_te": llabel,
                "status": "failed",
            }
    finally:
        conn.close()

    if last_req:
        target_name = last_req.get("target") or "last change"
        undo_label = f"Mundu chesina change '{target_name}' undo chesam"
        store_request(
            kind="undo",
            target=last_req.get("id"),
            value={"undone_request": last_req},
            label_te=undo_label,
            db_path=db_path,
            status="applied",
        )
    return last_req


# ==============================================================================
# Natural Language Parser for Binny Mills Office Phrases
# ==============================================================================

def _clean_str(s: Optional[str]) -> str:
    if not s:
        return ""
    # Strip punctuation and extra spaces
    s = re.sub(r"^[^\w\s]+|[^\w\s]+$", "", s.strip())
    return s.strip()


def parse_phrase(text: str) -> Optional[Dict[str, Any]]:
    """Parse a plain English or Tenglish phrase into an office request specification.

    Returns dict with keys:
      - kind: "room.update" | "room.add" | "room.remove" | "agent.update" | "office.theme" | "undo"
      - target: name of room or agent, or empty
      - value: dict containing parsed parameters (e.g. {"action": "resize", "size": "bigger"}, {"cap": "red"}, etc.)
      - label_te: Tenglish explanation for owner confirmation

    Returns None if the text does not match an office request phrase.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None

    lower = raw.lower()
    norm = re.sub(r"[,;:\?!]+", " ", lower).strip()
    norm = re.sub(r"\s+", " ", norm)

    # 1. UNDO PHRASES
    if re.search(r"\b(office\s+change\s+undo|undo\s+office\s+change|undo\s+last\s+(?:office\s+)?change|office\s+lo\s+undo|venakki\s+teesko|undo\s+office|rollback\s+office)\b", norm) or \
       re.match(r"^(?:undo|rollback)(?:\s+chey|\s+cheyyi)?$", norm) or \
       (norm in ("undo", "office undo", "undo last", "undo chey", "undo cheyyi")):
        return {
            "kind": "undo",
            "target": "",
            "value": {"action": "undo"},
            "label_te": "Office lo last change undo cheyandi",
        }

    # 2. GLOBAL OFFICE THEME
    m_theme = re.search(r"\b(?:office\s+)?(?:theme|mode)\s+(dark|light|festival|system)\b", norm) or \
              re.search(r"\boffice\s+(?:lo\s+)?(dark|light|festival)\s+(?:theme|mode)(?:\s+(?:pettu|marchu|set))?\b", norm) or \
              re.search(r"\boffice\s+(dark|light|festival)\s+mode\b", norm)
    if m_theme:
        th = m_theme.group(1)
        return {
            "kind": "office.theme",
            "target": "office",
            "value": {"theme": th},
            "label_te": f"Office theme {th} mode ga marcham",
        }

    # 3. AGENT STYLING (cap, theme, color, icon)
    # Match cap:
    m_cap_te = re.search(r"\b([A-Za-z0-9_\s]+?)\s+(?:agent\s+)?ki\s+([A-Za-z0-9_-]+)\s+cap(?:\s+(?:pettu|ivvu|marchu|veeyi))?\b", norm)
    if m_cap_te:
        agent_raw = _clean_str(m_cap_te.group(1))
        agent = re.sub(r"^(?:office\s+lo\s+)?", "", agent_raw).strip()
        color = _clean_str(m_cap_te.group(2))
        return {
            "kind": "agent.update",
            "target": agent.title(),
            "value": {"cap": color.lower()},
            "label_te": f"{agent.title()} agent ki {color.lower()} cap pettam",
        }

    m_cap_en = re.search(r"\b(?:put|set|give|add)\s+([A-Za-z0-9_-]+)\s+cap\s+(?:on|to|for)\s+([A-Za-z0-9_\s]+?)(?:\s+agent)?$", norm) or \
               re.search(r"\bchange\s+([A-Za-z0-9_\s]+?)(?:\s+agent)?\s+cap\s+to\s+([A-Za-z0-9_-]+)\b", norm)
    if m_cap_en:
        if "to" in norm and norm.startswith("change"):
            agent = _clean_str(m_cap_en.group(1))
            color = _clean_str(m_cap_en.group(2))
        else:
            color = _clean_str(m_cap_en.group(1))
            agent = _clean_str(m_cap_en.group(2))
        return {
            "kind": "agent.update",
            "target": agent.title(),
            "value": {"cap": color.lower()},
            "label_te": f"{agent.title()} agent ki {color.lower()} cap pettam",
        }

    # Match agent icon:
    m_icon_te = re.search(r"\b([A-Za-z0-9_\s]+?)\s+(?:agent\s+)?ki\s+([A-Za-z0-9_-]+)\s+icon(?:\s+(?:pettu|ivvu|marchu|veeyi))?\b", norm)
    if m_icon_te:
        agent_raw = _clean_str(m_icon_te.group(1))
        agent = re.sub(r"^(?:office\s+lo\s+)?", "", agent_raw).strip()
        icon = _clean_str(m_icon_te.group(2))
        return {
            "kind": "agent.update",
            "target": agent.title(),
            "value": {"icon": icon.lower()},
            "label_te": f"{agent.title()} agent ki {icon.lower()} icon pettam",
        }

    m_icon_en = re.search(r"\b(?:put|set|give|add)\s+([A-Za-z0-9_-]+)\s+icon\s+(?:on|to|for)\s+([A-Za-z0-9_\s]+?)(?:\s+agent)?$", norm) or \
                re.search(r"\bchange\s+([A-Za-z0-9_\s]+?)(?:\s+agent)?\s+icon\s+to\s+([A-Za-z0-9_-]+)\b", norm)
    if m_icon_en:
        if "to" in norm and norm.startswith("change"):
            agent = _clean_str(m_icon_en.group(1))
            icon = _clean_str(m_icon_en.group(2))
        else:
            icon = _clean_str(m_icon_en.group(1))
            agent = _clean_str(m_icon_en.group(2))
        return {
            "kind": "agent.update",
            "target": agent.title(),
            "value": {"icon": icon.lower()},
            "label_te": f"{agent.title()} agent ki {icon.lower()} icon pettam",
        }

    # Match agent theme / color:
    m_ag_th_te = re.search(r"\b([A-Za-z0-9_\s]+?)\s+(?:agent\s+)?ki\s+([A-Za-z0-9_-]+)\s+(?:theme|colour|color)(?:\s+(?:pettu|ivvu|marchu))?\b", norm)
    if m_ag_th_te:
        agent_raw = _clean_str(m_ag_th_te.group(1))
        agent = re.sub(r"^(?:office\s+lo\s+)?", "", agent_raw).strip()
        theme_val = _clean_str(m_ag_th_te.group(2))
        return {
            "kind": "agent.update",
            "target": agent.title(),
            "value": {"theme": theme_val.lower()},
            "label_te": f"{agent.title()} agent ki {theme_val.lower()} theme pettam",
        }

    m_ag_th_en1 = re.search(r"\b(?:set|give)\s+([A-Za-z0-9_-]+)\s+(?:theme|color|colour)\s+(?:for|to)\s+([A-Za-z0-9_\s]+?)(?:\s+agent)?$", norm)
    if m_ag_th_en1:
        th_val = _clean_str(m_ag_th_en1.group(1))
        agent = _clean_str(m_ag_th_en1.group(2))
        return {
            "kind": "agent.update",
            "target": agent.title(),
            "value": {"theme": th_val.lower()},
            "label_te": f"{agent.title()} agent ki {th_val.lower()} theme pettam",
        }

    m_ag_th_en2 = re.search(r"\b(?:set|give)\s+([A-Za-z0-9_\s]+?)(?:\s+agent)?\s+([A-Za-z0-9_-]+)\s+(?:theme|color|colour)\b", norm)
    if m_ag_th_en2:
        agent = _clean_str(m_ag_th_en2.group(1))
        th_val = _clean_str(m_ag_th_en2.group(2))
        return {
            "kind": "agent.update",
            "target": agent.title(),
            "value": {"theme": th_val.lower()},
            "label_te": f"{agent.title()} agent ki {th_val.lower()} theme pettam",
        }

    m_ag_th_en3 = re.search(r"\bchange\s+([A-Za-z0-9_\s]+?)(?:\s+agent)?\s+(?:theme|color|colour)\s+to\s+([A-Za-z0-9_-]+)\b", norm)
    if m_ag_th_en3:
        agent = _clean_str(m_ag_th_en3.group(1))
        th_val = _clean_str(m_ag_th_en3.group(2))
        return {
            "kind": "agent.update",
            "target": agent.title(),
            "value": {"theme": th_val.lower()},
            "label_te": f"{agent.title()} agent ki {th_val.lower()} theme pettam",
        }

    # 4. ROOM ADD
    m_add_te = re.search(r"\b(?:office\s+lo\s+)?(?:kotha|new)\s+room\s+([A-Za-z0-9_&-]+)(?:\s+add\s+chey|\s+add\s+cheyyi|\s+pettu|\s+create\s+chey)?\b", norm)
    if m_add_te:
        room_name = _clean_str(m_add_te.group(1))
        room_name = re.sub(r"\b(add|create|pettu|chey|cheyyi)\b", "", room_name).strip()
        if room_name:
            return {
                "kind": "room.add",
                "target": room_name.title(),
                "value": {"action": "add"},
                "label_te": f"Kotha room '{room_name.title()}' add chesam",
            }

    m_add_en = re.search(r"\b(?:add|create)\s+(?:new\s+)?room\s+([A-Za-z0-9_&-]+)\b", norm) or \
               re.search(r"\b(?:add|create)\s+([A-Za-z0-9_&-]+)\s+room\b", norm)
    if m_add_en:
        room_name = _clean_str(m_add_en.group(1))
        if room_name not in ("the", "a", "an", "new"):
            return {
                "kind": "room.add",
                "target": room_name.title(),
                "value": {"action": "add"},
                "label_te": f"Kotha room '{room_name.title()}' add chesam",
            }

    # 5. ROOM REMOVE / DELETE
    m_rem_te = re.search(r"\b(?:office\s+lo\s+)?([A-Za-z0-9_&-]+)\s+room(?:\s+ni)?\s+(?:remove\s+chey|delete\s+chey|teeyyi|teesei|teesey)\b", norm) or \
               re.search(r"\b(?:office\s+lo\s+)?room\s+([A-Za-z0-9_&-]+)(?:\s+ni)?\s+(?:remove\s+chey|delete\s+chey|teeyyi|teesei|teesey)\b", norm)
    if m_rem_te:
        room_name = _clean_str(m_rem_te.group(1))
        return {
            "kind": "room.remove",
            "target": room_name.title(),
            "value": {"action": "remove"},
            "label_te": f"'{room_name.title()}' room remove chesam",
        }

    m_rem_en = re.search(r"\b(?:remove|delete)\s+(?:room\s+)?([A-Za-z0-9_&-]+)(?:\s+room)?\b", norm)
    if m_rem_en:
        room_name = _clean_str(m_rem_en.group(1))
        if room_name not in ("the", "a", "room", "last", "office"):
            return {
                "kind": "room.remove",
                "target": room_name.title(),
                "value": {"action": "remove"},
                "label_te": f"'{room_name.title()}' room remove chesam",
            }

    # 6. ROOM RENAME
    m_ren_te = re.search(r"\b(?:office\s+lo\s+)?([A-Za-z0-9_&-]+)\s+room(?:\s+peru|\s+name)?\s+([A-Za-z0-9_&-]+)\s*(?:ga|ani)\s+marchu\b", norm) or \
               re.search(r"\b(?:office\s+lo\s+)?room\s+([A-Za-z0-9_&-]+)\s+(?:peru|name)\s+([A-Za-z0-9_&-]+)\s*(?:ga|ani)\s+marchu\b", norm)
    if m_ren_te:
        old_name = _clean_str(m_ren_te.group(1))
        new_name = _clean_str(m_ren_te.group(2))
        return {
            "kind": "room.update",
            "target": old_name.title(),
            "value": {"action": "rename", "new_name": new_name.title()},
            "label_te": f"'{old_name.title()}' room peru '{new_name.title()}' ga marcham",
        }

    m_ren_en = re.search(r"\brename\s+(?:room\s+)?([A-Za-z0-9_&-]+)(?:\s+room)?\s+(?:to|as)\s+([A-Za-z0-9_&-]+)(?:\s+room)?\b", norm)
    if m_ren_en:
        old_name = _clean_str(m_ren_en.group(1))
        new_name = _clean_str(m_ren_en.group(2))
        return {
            "kind": "room.update",
            "target": old_name.title(),
            "value": {"action": "rename", "new_name": new_name.title()},
            "label_te": f"'{old_name.title()}' room peru '{new_name.title()}' ga marcham",
        }

    # 7. ROOM MOVE NEXT TO / BESIDE
    m_move_te = re.search(r"\b(?:office\s+lo\s+)?([A-Za-z0-9_&-]+)\s*(?:room)?(?:\s+ni)?\s+([A-Za-z0-9_&-]+)\s*(?:room)?\s+(?:pakkana|daggaraki|daggarlo)\s+(?:pettu|jarupu|marchu)\b", norm)
    if m_move_te:
        room_a = _clean_str(m_move_te.group(1))
        room_b = _clean_str(m_move_te.group(2))
        if room_a and room_b and room_a != room_b and room_a not in ("office", "room", "ni"):
            return {
                "kind": "room.update",
                "target": room_a.title(),
                "value": {"action": "move", "near": room_b.title()},
                "label_te": f"'{room_a.title()}' room ni '{room_b.title()}' pakkana pettam",
            }

    m_move_en = re.search(r"\b(?:move|put|place)\s+(?:room\s+)?([A-Za-z0-9_&-]+)(?:\s+room)?\s+(?:next\s+to|beside|near|close\s+to)\s+(?:room\s+)?([A-Za-z0-9_&-]+)(?:\s+room)?\b", norm)
    if m_move_en:
        room_a = _clean_str(m_move_en.group(1))
        room_b = _clean_str(m_move_en.group(2))
        return {
            "kind": "room.update",
            "target": room_a.title(),
            "value": {"action": "move", "near": room_b.title()},
            "label_te": f"'{room_a.title()}' room ni '{room_b.title()}' pakkana pettam",
        }

    # 8. ROOM RESIZE (bigger / smaller)
    size_action = None
    if re.search(r"\b(bigger|large|larger|expand|enlarge|pedda|peddadi)\b", norm):
        size_action = "bigger"
    elif re.search(r"\b(smaller|small|shrink|chinna|chinnadi)\b", norm):
        size_action = "smaller"

    if size_action:
        m_sz_te = re.search(r"\b(?:office\s+lo\s+)?([A-Za-z0-9_&-]+)(?:\s+room)?(?:\s+ni)?\s+(?:pedda|chinna|peddadi|chinnadi)\s+(?:chey|cheyyi|pettu|marchu)\b", norm)
        if m_sz_te:
            room_name = _clean_str(m_sz_te.group(1))
            if room_name not in ("office", "room", "ni"):
                adj = "peddadi" if size_action == "bigger" else "chinnadi"
                return {
                    "kind": "room.update",
                    "target": room_name.title(),
                    "value": {"action": "resize", "size": size_action},
                    "label_te": f"'{room_name.title()}' room ni {adj} ga chesam",
                }

        m_sz_en1 = re.search(r"\b(?:make|resize)\s+(?:room\s+)?([A-Za-z0-9_&-]+)(?:\s+room)?\s+(?:bigger|smaller|larger)\b", norm)
        if m_sz_en1:
            room_name = _clean_str(m_sz_en1.group(1))
            adj = "peddadi" if size_action == "bigger" else "chinnadi"
            return {
                "kind": "room.update",
                "target": room_name.title(),
                "value": {"action": "resize", "size": size_action},
                "label_te": f"'{room_name.title()}' room ni {adj} ga chesam",
            }

        m_sz_en2 = re.search(r"\b(?:expand|enlarge|shrink)\s+(?:room\s+)?([A-Za-z0-9_&-]+)(?:\s+room)?\b", norm)
        if m_sz_en2:
            room_name = _clean_str(m_sz_en2.group(1))
            adj = "peddadi" if size_action == "bigger" else "chinnadi"
            return {
                "kind": "room.update",
                "target": room_name.title(),
                "value": {"action": "resize", "size": size_action},
                "label_te": f"'{room_name.title()}' room ni {adj} ga chesam",
            }

        m_sz_en3 = re.search(r"\b([A-Za-z0-9_&-]+)\s+room\s+(?:bigger|smaller|larger)\b", norm)
        if m_sz_en3:
            room_name = _clean_str(m_sz_en3.group(1))
            if room_name not in ("make", "resize", "office"):
                adj = "peddadi" if size_action == "bigger" else "chinnadi"
                return {
                    "kind": "room.update",
                    "target": room_name.title(),
                    "value": {"action": "resize", "size": size_action},
                    "label_te": f"'{room_name.title()}' room ni {adj} ga chesam",
                }

    return None
