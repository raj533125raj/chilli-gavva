"""ab_results.py — A/B title tracking for digital product packs.

Stores owner-entered title test results in the robot state store
(using the same `telegram_state` table as other telegram state).
Supports:
  - Recording clicks for a pack's title:
      Slash command: "/ab <pack> <title number 1-5> <clicks>"
      Tenglish text: "ab 3 lo title 2 ki 120 clicks", "ab 3 title 2 120 clicks"
  - Displaying results table and current leader:
      Slash command: "/ab <pack>"
  - Pure counts only: strictly rejects any amount that looks like money ($ / USD / INR / etc.).
  - Deterministic, atomic writes, outside pack folders.
"""
import html
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_NUMBER = r"[+-]?(?:\d{1,3}(?:[,\s]\d{2,3})+|\d+)(?:[.,]\d+)?"
_CURRENCY = (
    r"(?:USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|JPY|CNY|RMB|AED|CHF|"
    r"ZAR|PKR|BDT|NGN|PHP|KRW|RUB|BRL|MXN|Rs\.?|rupees?|dollars?|"
    r"euros?|pounds?|yen|cents?|paise|paisa)"
)
_MONEY = re.compile(
    r"(?:\b" + _CURRENCY + r"\s*" + _NUMBER + r"|(?<!\w)" + _NUMBER + r"\s*" + _CURRENCY + r"\b)",
    re.IGNORECASE,
)

# Regex patterns for parsing A/B input
# 1. Slash command with update: /ab <pack> <title_num> <clicks>
_SLASH_AB_SET = re.compile(
    r"^/ab(?:@\w+)?\s+(\d+|[A-Za-z0-9_-]+)\s+([1-5])\s+(\d+)\s*$",
    re.IGNORECASE,
)

# 2. Slash command view only: /ab <pack>
_SLASH_AB_VIEW = re.compile(
    r"^/ab(?:@\w+)?\s+(\d+|[A-Za-z0-9_-]+)\s*$",
    re.IGNORECASE,
)

# 3. Plain text / Tenglish commands:
# e.g., "ab 3 lo title 2 ki 120 clicks", "ab 3 title 2 120 clicks", "ab pack-1 lo title 4 ki 50 clicks"
_TENGLISH_AB_SET = re.compile(
    r"\bab\s+(\d+|[A-Za-z0-9_-]+)\s+(?:lo\s+)?title\s*#?([1-5])(?:\s*ki|\s*ku|\s*lo)?\s+(\d+)\s*(?:clicks?|clks?)?\b",
    re.IGNORECASE,
)

# 4. Plain text view: "ab 3", "ab 3 results", "ab 3 stats"
_TENGLISH_AB_VIEW = re.compile(
    r"^ab\s+(\d+|[A-Za-z0-9_-]+)(?:\s+(?:results|stats|choodu|table))?\s*$",
    re.IGNORECASE,
)


def _check_money(text: str) -> None:
    """Raise ValueError if text contains any currency or price symbols."""
    text = unicodedata.normalize("NFKC", html.unescape(text or ""))
    text = re.sub(r"[*`_~]", "", text)
    symbols = "".join(re.escape(c) for c in set(text) if unicodedata.category(c) == "Sc")
    symbol_money = bool(
        symbols
        and re.search(
            r"(?:[" + symbols + r"]\s*" + _NUMBER + r"|" + _NUMBER + r"\s*[" + symbols + r"])",
            text,
        )
    )
    if symbol_money or _MONEY.search(text):
        raise ValueError("Money amount found; A/B tracking only records click counts.")


def _state_key(pack_id: str) -> str:
    """Canonical telegram_state key for a pack's A/B results."""
    clean_id = str(pack_id).strip().lower()
    return f"ab_results:{clean_id}"


def get_ab_data(pack_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve A/B tracking dictionary for a pack."""
    import telegram_inbox as ti

    key = _state_key(pack_id)
    raw = ti.get_state(key, default=None, db_path=db_path)
    if not raw:
        return {"pack": str(pack_id), "titles": {str(i): 0 for i in range(1, 6)}}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Malformed state")
        titles = data.get("titles", {})
        cleaned_titles = {str(i): int(titles.get(str(i), 0)) for i in range(1, 6)}
        return {"pack": str(pack_id), "titles": cleaned_titles}
    except Exception:
        return {"pack": str(pack_id), "titles": {str(i): 0 for i in range(1, 6)}}


def save_ab_data(pack_id: str, data: Dict[str, Any], db_path: Optional[str] = None) -> None:
    """Save A/B tracking dictionary into telegram_state atomically."""
    import telegram_inbox as ti

    key = _state_key(pack_id)
    ti.set_state(key, json.dumps(data, sort_keys=True), db_path=db_path)


def record_clicks(
    pack_id: str,
    title_num: int,
    clicks: int,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Record or update clicks for a title (1-5)."""
    if not (1 <= int(title_num) <= 5):
        raise ValueError("Title number must be between 1 and 5.")
    if int(clicks) < 0:
        raise ValueError("Clicks must be a non-negative integer.")

    data = get_ab_data(pack_id, db_path=db_path)
    data["titles"][str(title_num)] = int(clicks)
    save_ab_data(pack_id, data, db_path=db_path)
    return data


def find_pack_titles(pack_id: str, marketing_dir: Optional[str] = None) -> List[str]:
    """Look up the 5 titles from exported marketing pins ab_titles.txt if present."""
    mkt_base = Path(marketing_dir or "exported_content/marketing")
    clean_id = str(pack_id).strip()
    ab_path = None
    if mkt_base.is_dir():
        for candidate in [
            mkt_base / clean_id / "pins" / "ab_titles.txt",
            mkt_base / f"{clean_id}-*" / "pins" / "ab_titles.txt",
        ]:
            if candidate.is_file():
                ab_path = candidate
                break
        if not ab_path:
            for child in mkt_base.iterdir():
                if child.is_dir() and (child.name == clean_id or child.name.startswith(f"{clean_id}-")):
                    sub = child / "pins" / "ab_titles.txt"
                    if sub.is_file():
                        ab_path = sub
                        break

    titles: List[str] = []
    if ab_path and ab_path.is_file():
        try:
            lines = ab_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines:
                line = line.strip()
                if line:
                    line = re.sub(r"^\d+[.)]\s*", "", line).strip()
                    if line:
                        titles.append(line)
        except Exception:
            pass

    # Ensure list has 5 titles
    while len(titles) < 5:
        titles.append(f"Title {len(titles) + 1}")
    return titles[:5]


def format_ab_table(
    pack_id: str,
    db_path: Optional[str] = None,
    marketing_dir: Optional[str] = None,
) -> str:
    """Format the A/B results table and indicate current leader."""
    data = get_ab_data(pack_id, db_path=db_path)
    titles = find_pack_titles(pack_id, marketing_dir=marketing_dir)
    clicks_dict = data.get("titles", {})

    max_clicks = -1
    leader_num = None
    for i in range(1, 6):
        c = clicks_dict.get(str(i), 0)
        if c > max_clicks and c > 0:
            max_clicks = c
            leader_num = i

    lines = [f"📊 A/B Title Results for Pack #{pack_id}:"]
    for i in range(1, 6):
        c = clicks_dict.get(str(i), 0)
        title_str = titles[i - 1]
        is_leader = (i == leader_num and max_clicks > 0)
        marker = " 👑 (Leader)" if is_leader else ""
        lines.append(f"#{i}: {title_str} — {c} clicks{marker}")

    lines.append("")
    if leader_num and max_clicks > 0:
        lines.append(f"Current Leader: Title #{leader_num} ({titles[leader_num - 1]}) with {max_clicks} clicks.")
    else:
        lines.append("Current Leader: No clicks recorded yet.")

    return "\n".join(lines)


def get_all_ab_leaders(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve leaders across all tracked packs in telegram_state."""
    import telegram_inbox as ti

    leaders = []
    conn = ti._state_conn(db_path)
    try:
        rows = conn.execute("SELECT key, value FROM telegram_state WHERE key LIKE 'ab_results:%'").fetchall()
    finally:
        conn.close()

    for k, val in rows:
        pack_id = k.split("ab_results:", 1)[1]
        try:
            data = json.loads(val)
            titles_dict = data.get("titles", {})
            max_c = -1
            lead_idx = None
            for idx in range(1, 6):
                c = int(titles_dict.get(str(idx), 0))
                if c > max_c and c > 0:
                    max_c = c
                    lead_idx = idx
            if lead_idx and max_c > 0:
                titles = find_pack_titles(pack_id)
                leaders.append({
                    "pack": pack_id,
                    "title_num": lead_idx,
                    "title": titles[lead_idx - 1],
                    "clicks": max_c,
                })
        except Exception:
            continue
    leaders.sort(key=lambda x: str(x["pack"]))
    return leaders


def parse_and_handle_ab(
    text: str,
    db_path: Optional[str] = None,
    marketing_dir: Optional[str] = None,
) -> Optional[str]:
    """Check if message matches any /ab command or Tenglish A/B text, and execute it."""
    if not isinstance(text, str):
        return None

    raw_text = text.strip()

    # Reject anything that looks like money
    try:
        _check_money(raw_text)
    except ValueError as exc:
        return str(exc)

    # 1. Slash command: /ab <pack> <title> <clicks>
    m_set = _SLASH_AB_SET.match(raw_text)
    if m_set:
        pack_id = m_set.group(1)
        t_num = int(m_set.group(2))
        clicks = int(m_set.group(3))
        record_clicks(pack_id, t_num, clicks, db_path=db_path)
        table = format_ab_table(pack_id, db_path=db_path, marketing_dir=marketing_dir)
        return f"Sare! Pack #{pack_id} Title #{t_num} ki {clicks} clicks save chesa.\n\n{table}"

    # 2. Slash command: /ab <pack>
    m_view = _SLASH_AB_VIEW.match(raw_text)
    if m_view:
        pack_id = m_view.group(1)
        return format_ab_table(pack_id, db_path=db_path, marketing_dir=marketing_dir)

    # 3. Tenglish set: e.g. "ab 3 lo title 2 ki 120 clicks"
    m_tset = _TENGLISH_AB_SET.search(raw_text)
    if m_tset:
        pack_id = m_tset.group(1)
        t_num = int(m_tset.group(2))
        clicks = int(m_tset.group(3))
        record_clicks(pack_id, t_num, clicks, db_path=db_path)
        table = format_ab_table(pack_id, db_path=db_path, marketing_dir=marketing_dir)
        return f"Sare! Pack #{pack_id} Title #{t_num} ki {clicks} clicks save chesa.\n\n{table}"

    # 4. Tenglish view: e.g. "ab 3"
    m_tview = _TENGLISH_AB_VIEW.match(raw_text)
    if m_tview:
        pack_id = m_tview.group(1)
        return format_ab_table(pack_id, db_path=db_path, marketing_dir=marketing_dir)

    # 5. Generic /ab without args
    if raw_text.lower() in ("/ab", "/ab@bot"):
        return "A/B Title Tracking:\nUsage: /ab <pack> (to view) or /ab <pack> <title 1-5> <clicks> (to update)."

    return None
