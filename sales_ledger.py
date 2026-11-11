"""sales_ledger.py — Private sales recording and ledger store.

BINNY-W3A ROBOT: SALES NOTES FROM TELEGRAM (PRIVATE).
Stores sales notes entered by owner from Telegram.
Database table: `sales`
  - id INTEGER PRIMARY KEY AUTOINCREMENT
  - pack_id TEXT (e.g. '3', 'pack-5', '5')
  - store TEXT (e.g. 'Gumroad', 'Etsy', 'Topmate', 'Instamojo', 'Payhip', 'Direct')
  - count INTEGER (default 1)
  - ts TEXT (ISO-8601 UTC timestamp)
  - note TEXT
  - amount REAL (optional, stored ONLY in private DB, NEVER in repo files / status / logs)
  - currency TEXT (optional)

Strict privacy:
  - Amounts are OPTIONAL and stay ONLY in the private DB and in the owner's Telegram reply.
  - They are NEVER written to any file in the repo, never to status.json, never to logs.
  - Best sellers and feed functions export counts ONLY (never amounts).
"""

from __future__ import annotations

import collections
import datetime
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import guardrails
except ImportError:
    guardrails = None


KNOWN_STORES = [
    "gumroad",
    "etsy",
    "topmate",
    "instamojo",
    "mojo",
    "payhip",
    "ko-fi",
    "kofi",
    "direct",
    "store",
    "website",
]

_AMOUNT_PATTERN = re.compile(
    r"(?:(?P<curr_pre>USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|\$|₹|Rs\.?|rupees?|dollars?)\s*(?P<amt_pre>\d+(?:\.\d+)?))|"
    r"(?:(?P<amt_post>\d+(?:\.\d+)?)\s*(?P<curr_post>USD|INR|EUR|GBP|AUD|CAD|NZD|SGD|HKD|\$|₹|Rs\.?|rupees?|dollars?))",
    re.IGNORECASE,
)


def get_db_connection(db_path: Optional[Union[str, sqlite3.Connection]] = None) -> sqlite3.Connection:
    """Return an open sqlite3 Connection with the sales table ensured."""
    if isinstance(db_path, sqlite3.Connection):
        conn = db_path
    elif guardrails is not None:
        conn = guardrails.connect(db_path)
    else:
        path = db_path or os.environ.get("AGENT_DATA_DB", "agent_data.db")
        conn = sqlite3.connect(path)
    ensure_sales_schema(conn)
    return conn


def ensure_sales_schema(conn: sqlite3.Connection) -> None:
    """Ensure the `sales` table exists with all required columns."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id TEXT NOT NULL,
            store TEXT,
            count INTEGER DEFAULT 1,
            ts TEXT NOT NULL,
            note TEXT,
            amount REAL,
            currency TEXT
        )"""
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sales)").fetchall()}
    if "amount" not in cols:
        conn.execute("ALTER TABLE sales ADD COLUMN amount REAL")
    if "currency" not in cols:
        conn.execute("ALTER TABLE sales ADD COLUMN currency TEXT")
    conn.commit()


def normalize_pack_id(raw_pack: Union[str, int]) -> str:
    """Normalize pack id to clean identifier string like '3' or 'adhd_planner'."""
    val = str(raw_pack).strip()
    m = re.match(r"^(?:pack[-_\s]*)?(\d+|[A-Za-z0-9_-]+)$", val, re.IGNORECASE)
    if m:
        return m.group(1)
    return val


def normalize_store(raw_store: Optional[str]) -> str:
    """Normalize store name (Gumroad, Etsy, Topmate, Instamojo, Payhip, Ko-fi, etc.)."""
    if not raw_store:
        return "Direct"
    s = str(raw_store).strip()
    low = s.lower()
    if "gumroad" in low:
        return "Gumroad"
    if "etsy" in low:
        return "Etsy"
    if "topmate" in low:
        return "Topmate"
    if "instamojo" in low or "mojo" in low:
        return "Instamojo"
    if "payhip" in low:
        return "Payhip"
    if "ko-fi" in low or "kofi" in low:
        return "Ko-fi"
    if "direct" in low or "store" in low or "site" in low or "website" in low:
        return "Direct"
    return s.capitalize()


def extract_amount_and_currency(text: str) -> Tuple[Optional[float], Optional[str], str]:
    """Extract optional money amount and currency from text, returning remaining text.

    Returns (amount, currency, remaining_text).
    """
    m = _AMOUNT_PATTERN.search(text)
    if not m:
        return None, None, text

    amt_str = m.group("amt_pre") or m.group("amt_post")
    curr_str = m.group("curr_pre") or m.group("curr_post")

    amount: Optional[float] = None
    if amt_str:
        try:
            amount = float(amt_str)
        except ValueError:
            amount = None

    currency: Optional[str] = None
    if curr_str:
        c = curr_str.strip().upper()
        if c in ("$", "DOLLAR", "DOLLARS"):
            currency = "USD"
        elif c in ("₹", "RS", "RS.", "RUPEE", "RUPEES", "INR"):
            currency = "INR"
        else:
            currency = c

    clean_text = text[:m.start()] + " " + text[m.end():]
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    return amount, currency, clean_text


def parse_sales_input(text: str) -> Optional[Dict[str, Any]]:
    """Parse owner Telegram messages for sales commands or sales recording.

    Returns dict with intent:
      - {"intent": "report", "period": "today"|"week"|"month"|"all"}
      - {"intent": "undo"}
      - {"intent": "record", "pack_id": ..., "store": ..., "count": ..., "amount": ..., "currency": ..., "note": ...}
      - None if not recognized as sales intent.
    """
    if not text or not isinstance(text, str):
        return None

    raw = text.strip()
    lower = raw.lower()

    # 1. Report queries via slash command: /sales [today|week|month|all]
    slash_match = re.match(
        r"^/sales(?:_report|_summary)?(?:@\w+)?(?:\s+(today|week|month|all|undo))?\s*$",
        raw,
        re.IGNORECASE,
    )
    if slash_match:
        arg = (slash_match.group(1) or "").lower()
        if arg == "undo":
            return {"intent": "undo"}
        period = arg if arg in ("today", "week", "month", "all") else "week"
        return {"intent": "report", "period": period}

    # 2. Plain text report queries
    report_patterns = [
        r"^(?:sales\s+)?(?:report|summary|chudu|chupinchu|choopinchu|vivaralu)$",
        r"^sales\s+(today|week|month|all)$",
        r"^(today|week|month|all)\s+sales$",
        r"^(?:ee\s+roju|eroju)\s+sales(?:\s+chupinchu|\s+cheppu|\s+report)?$",
        r"^(?:ee\s+vaaram|eevaaram)\s+sales(?:\s+chupinchu|\s+cheppu|\s+report)?$",
        r"^(?:ee\s+nela|eenela)\s+sales(?:\s+chupinchu|\s+cheppu|\s+report)?$",
        r"^sales\s+(?:ee\s+roju|eroju)$",
        r"^sales\s+(?:ee\s+vaaram|eevaaram)$",
        r"^sales\s+(?:ee\s+nela|eenela)$",
        r"^motham\s+sales$",
        r"^total\s+sales$",
        r"^sales\s+total$",
    ]
    for pat in report_patterns:
        m = re.match(pat, lower)
        if m:
            if m.groups() and m.group(1):
                p = m.group(1)
                period = p if p in ("today", "week", "month", "all") else "week"
            elif "roju" in lower or "today" in lower:
                period = "today"
            elif "nela" in lower or "month" in lower:
                period = "month"
            elif "motham" in lower or "total" in lower or "all" in lower:
                period = "all"
            else:
                period = "week"
            return {"intent": "report", "period": period}

    # 3. Undo commands
    undo_patterns = [
        r"^/sales\s+undo$",
        r"^(?:sales?\s+)?undo(?:\s+sales?)?$",
        r"^(?:last\s+sale\s+undo|undo\s+last\s+sale)$",
        r"^sale\s+(?:cancel|delete|undo\s+chey|teeyandi|theeyandi)$",
        r"^last\s+sale\s+(?:cancel|delete|theeyi|teeyi)$",
    ]
    for pat in undo_patterns:
        if re.match(pat, lower):
            return {"intent": "undo"}

    # 4. Recording sales
    has_sale_kw = bool(re.search(r"\b(sales?|sold|ammakaalu|ammakam|amminam|ammindi)\b", lower))
    if not has_sale_kw:
        return None

    if lower.startswith("/status") or lower.startswith("/help") or lower.startswith("/start"):
        return None

    amount, currency, text_no_amount = extract_amount_and_currency(raw)

    note = ""
    note_match = re.search(r"(?:note|notes|vivaram|details?)\s*[:=-]\s*(.*)$", text_no_amount, re.IGNORECASE)
    if note_match:
        note = note_match.group(1).strip()
        text_core = text_no_amount[:note_match.start()].strip()
    else:
        text_core = text_no_amount

    pack_id: Optional[str] = None
    pack_match = re.search(r"\bpack[-_\s]*([A-Za-z0-9_-]+)\b", text_core, re.IGNORECASE)
    if pack_match:
        pack_id = pack_match.group(1)
        text_core = text_core[:pack_match.start()] + " " + text_core[pack_match.end():]
    else:
        p_match = re.search(r"\b(?:p|#)(\d+)\b", text_core, re.IGNORECASE)
        if p_match:
            pack_id = p_match.group(1)
            text_core = text_core[:p_match.start()] + " " + text_core[p_match.end():]

    if not pack_id:
        num_match = re.search(r"\b(\d+)\b", text_core)
        if num_match:
            pack_id = num_match.group(1)
            text_core = text_core[:num_match.start()] + " " + text_core[num_match.end():]

    if not pack_id:
        return None

    pack_id = normalize_pack_id(pack_id)

    store: Optional[str] = None
    for s in KNOWN_STORES:
        pattern = r"\b" + re.escape(s) + r"\b"
        if re.search(pattern, text_core, re.IGNORECASE):
            store = normalize_store(s)
            text_core = re.sub(pattern, " ", text_core, flags=re.IGNORECASE)
            break

    if not store:
        store_match = re.search(r"\bstore\s+([A-Za-z0-9_-]+)\b", text_core, re.IGNORECASE)
        if store_match:
            store = normalize_store(store_match.group(1))
            text_core = text_core[:store_match.start()] + " " + text_core[store_match.end():]
        else:
            store = "Direct"

    count = 1
    count_patterns = [
        r"(\d+)\s*(?:sales?|ammakaalu)",
        r"(?:sales?|count|quantity|qty)\s*(?:of\s+)?(\d+)",
        r"\b(\d+)\s*(?:units?|copies|orders?|items?)\b",
    ]
    for cp in count_patterns:
        cm = re.search(cp, text_core, re.IGNORECASE)
        if cm:
            try:
                count = int(cm.group(1))
                text_core = text_core[:cm.start()] + " " + text_core[cm.end():]
                break
            except ValueError:
                pass

    if count == 1:
        lone_digit = re.search(r"\b(\d+)\b", text_core)
        if lone_digit:
            try:
                val = int(lone_digit.group(1))
                if 1 <= val <= 1000:
                    count = val
                    text_core = text_core[:lone_digit.start()] + " " + text_core[lone_digit.end():]
            except ValueError:
                pass

    return {
        "intent": "record",
        "pack_id": pack_id,
        "store": store,
        "count": max(1, count),
        "amount": amount,
        "currency": currency,
        "note": note,
    }


def record_sale(
    pack_id: Union[str, int],
    store: Optional[str] = "Direct",
    count: int = 1,
    ts: Optional[Union[str, datetime.datetime]] = None,
    note: Optional[str] = "",
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    db_path: Optional[Union[str, sqlite3.Connection]] = None,
) -> Dict[str, Any]:
    """Record a sale row in the database.

    Returns the inserted row dict.
    Note: amount is optional and stays ONLY in the private DB.
    """
    conn = get_db_connection(db_path)
    clean_pack = normalize_pack_id(pack_id)
    clean_store = normalize_store(store)
    clean_count = max(1, int(count) if count else 1)

    if ts is None:
        clean_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    elif isinstance(ts, datetime.datetime):
        if ts.tzinfo is None:
            clean_ts = ts.replace(tzinfo=datetime.timezone.utc).isoformat()
        else:
            clean_ts = ts.astimezone(datetime.timezone.utc).isoformat()
    else:
        clean_ts = str(ts)

    cur = conn.execute(
        """INSERT INTO sales (pack_id, store, count, ts, note, amount, currency)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (clean_pack, clean_store, clean_count, clean_ts, (note or "").strip(), amount, currency),
    )
    conn.commit()
    inserted_id = cur.lastrowid

    row_count_pack = conn.execute(
        "SELECT SUM(count) FROM sales WHERE pack_id = ?", (clean_pack,)
    ).fetchone()[0] or 0

    total_sales_all = conn.execute("SELECT SUM(count) FROM sales").fetchone()[0] or 0

    if not isinstance(db_path, sqlite3.Connection):
        conn.close()

    is_first_pack_sale = (row_count_pack == clean_count)
    is_first_overall_sale = (total_sales_all == clean_count)

    return {
        "id": inserted_id,
        "pack_id": clean_pack,
        "store": clean_store,
        "count": clean_count,
        "ts": clean_ts,
        "note": (note or "").strip(),
        "amount": amount,
        "currency": currency,
        "is_first_pack_sale": is_first_pack_sale,
        "is_first_overall_sale": is_first_overall_sale,
    }


def undo_last_sale(db_path: Optional[Union[str, sqlite3.Connection]] = None) -> Optional[Dict[str, Any]]:
    """Delete and return the most recently inserted sale row, or None if empty."""
    conn = get_db_connection(db_path)
    row = conn.execute(
        "SELECT id, pack_id, store, count, ts, note, amount, currency FROM sales ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        if not isinstance(db_path, sqlite3.Connection):
            conn.close()
        return None

    sale_id = row[0]
    conn.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
    conn.commit()
    if not isinstance(db_path, sqlite3.Connection):
        conn.close()

    return {
        "id": row[0],
        "pack_id": row[1],
        "store": row[2],
        "count": row[3],
        "ts": row[4],
        "note": row[5],
        "amount": row[6],
        "currency": row[7],
    }


def _parse_iso_ts(ts_str: str) -> datetime.datetime:
    """Parse ISO timestamp safely."""
    try:
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def get_sales(
    period: str = "all",
    now: Optional[datetime.datetime] = None,
    db_path: Optional[Union[str, sqlite3.Connection]] = None,
) -> List[Dict[str, Any]]:
    """Get sales for a given period: 'today', 'week', 'month', 'all'."""
    conn = get_db_connection(db_path)
    rows = conn.execute(
        "SELECT id, pack_id, store, count, ts, note, amount, currency FROM sales ORDER BY ts ASC, id ASC"
    ).fetchall()
    if not isinstance(db_path, sqlite3.Connection):
        conn.close()

    ref_now = now or datetime.datetime.now(datetime.timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=datetime.timezone.utc)
    else:
        ref_now = ref_now.astimezone(datetime.timezone.utc)

    cutoff: Optional[datetime.datetime] = None
    period_lower = (period or "all").lower().strip()
    if period_lower == "today":
        cutoff = ref_now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period_lower == "week":
        cutoff = ref_now - datetime.timedelta(days=7)
    elif period_lower == "month":
        cutoff = ref_now - datetime.timedelta(days=30)
    elif period_lower == "all":
        cutoff = None
    else:
        cutoff = None

    results = []
    for r in rows:
        ts_dt = _parse_iso_ts(r[4])
        if cutoff is not None and ts_dt < cutoff:
            continue
        results.append({
            "id": r[0],
            "pack_id": r[1],
            "store": r[2],
            "count": r[3],
            "ts": r[4],
            "note": r[5],
            "amount": r[6],
            "currency": r[7],
        })
    return results


def format_sale_confirmation(sale: Dict[str, Any]) -> str:
    """Format Telegram private response confirming sale entry in Tenglish house style.

    Only here (private chat to owner) can amount be mentioned if provided by owner.
    """
    pack = sale.get("pack_id")
    store = sale.get("store")
    count = sale.get("count", 1)
    note = sale.get("note", "")
    amt = sale.get("amount")
    curr = sale.get("currency") or ""

    lines = []
    if sale.get("is_first_overall_sale"):
        lines.append("🎉 FIRST SALE EVER! Mabbu vidipoyindi, modati sale vachindi! 🚀")
    elif sale.get("is_first_pack_sale"):
        lines.append(f"🎉 Pack {pack} ki modati sale (First sale)! Congratulations! 🌟")

    sales_noun = "sale" if count == 1 else "sales"
    if amt is not None:
        amt_str = f"{amt:.2f}".rstrip("0").rstrip(".")
        lines.append(f"✅ Noted: Pack {pack} ki {count} {sales_noun} ({store} lo, {curr} {amt_str}) record chesam.")
    else:
        lines.append(f"✅ Noted: Pack {pack} ki {count} {sales_noun} ({store} lo) record chesam.")

    if note:
        lines.append(f"📝 Note: {note}")

    return "\n".join(lines)


def format_undo_confirmation(sale: Optional[Dict[str, Any]]) -> str:
    """Format confirmation when a sale entry is undone."""
    if not sale:
        return "Undo cheyadaniki sales emi levu (No sales to undo)."
    pack = sale.get("pack_id")
    store = sale.get("store")
    count = sale.get("count", 1)
    sales_noun = "sale" if count == 1 else "sales"
    return f"↩️ Last sale record teesesam (Undone): Pack {pack} - {count} {sales_noun} on {store}."


def get_trend_arrow(current: int, previous: int) -> str:
    """Return trend arrow comparing current count to previous period."""
    if previous == 0:
        return "🔺 +100%" if current > 0 else "➖ 0%"
    diff = current - previous
    pct = round((diff / previous) * 100)
    if pct > 0:
        return f"🔺 +{pct}%"
    elif pct < 0:
        return f"🔻 {pct}%"
    return "➖ 0%"


def generate_sales_report(
    period: str = "week",
    now: Optional[datetime.datetime] = None,
    db_path: Optional[Union[str, sqlite3.Connection]] = None,
) -> str:
    """Generate sales report in Tenglish house style.

    Strict privacy: strictly counts only per pack and store, best pack,
    first sale celebration line, trend arrows. Zero amounts in report.
    """
    ref_now = now or datetime.datetime.now(datetime.timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=datetime.timezone.utc)
    else:
        ref_now = ref_now.astimezone(datetime.timezone.utc)

    period_clean = (period or "week").lower().strip()
    if period_clean not in ("today", "week", "month", "all"):
        period_clean = "week"

    current_sales = get_sales(period=period_clean, now=ref_now, db_path=db_path)

    prev_sales: List[Dict[str, Any]] = []
    if period_clean == "today":
        yesterday_now = ref_now - datetime.timedelta(days=1)
        prev_sales = get_sales(period="today", now=yesterday_now, db_path=db_path)
    elif period_clean == "week":
        all_sales = get_sales(period="all", now=ref_now, db_path=db_path)
        start_prev = ref_now - datetime.timedelta(days=14)
        end_prev = ref_now - datetime.timedelta(days=7)
        for s in all_sales:
            dt = _parse_iso_ts(s["ts"])
            if start_prev <= dt < end_prev:
                prev_sales.append(s)
    elif period_clean == "month":
        all_sales = get_sales(period="all", now=ref_now, db_path=db_path)
        start_prev = ref_now - datetime.timedelta(days=60)
        end_prev = ref_now - datetime.timedelta(days=30)
        for s in all_sales:
            dt = _parse_iso_ts(s["ts"])
            if start_prev <= dt < end_prev:
                prev_sales.append(s)

    total_current = sum(s["count"] for s in current_sales)
    total_prev = sum(s["count"] for s in prev_sales)
    trend_str = get_trend_arrow(total_current, total_prev) if period_clean != "all" else ""

    period_labels = {
        "today": "Ee Roju (Today)",
        "week": "Ee Vaaram (This Week)",
        "month": "Ee Nela (This Month)",
        "all": "Motham (All Time)",
    }
    label = period_labels.get(period_clean, "Sales Report")

    lines = []
    lines.append(f"📊 Sales Report — {label}")
    lines.append("")

    if total_current == 0:
        lines.append("Ee period lo sales emi record avvaledu (0 sales).")
        lines.append("Kothadi add cheyadaniki: 'sale pack 3 Gumroad' ani message pampandi.")
        return "\n".join(lines)

    sales_noun = "sale" if total_current == 1 else "sales"
    if trend_str:
        lines.append(f"Total: {total_current} {sales_noun} ({trend_str} vs previous period)")
    else:
        lines.append(f"Total: {total_current} {sales_noun}")

    pack_counts: Dict[str, int] = collections.defaultdict(int)
    store_counts: Dict[str, int] = collections.defaultdict(int)

    all_db_sales = get_sales(period="all", now=ref_now, db_path=db_path)
    pack_all_counts: Dict[str, int] = collections.defaultdict(int)
    for s in all_db_sales:
        pack_all_counts[s["pack_id"]] += s["count"]

    for s in current_sales:
        pid = s["pack_id"]
        st = s["store"]
        cnt = s["count"]
        pack_counts[pid] += cnt
        store_counts[st] += cnt

    best_pack, best_count = max(pack_counts.items(), key=lambda item: item[1])
    lines.append(f"🌟 Best Pack: Pack {best_pack} ({best_count} sales)")
    lines.append("")

    lines.append("📦 Packs:")
    for pid, cnt in sorted(pack_counts.items(), key=lambda x: x[1], reverse=True):
        p_noun = "sale" if cnt == 1 else "sales"
        tag = ""
        if pack_all_counts.get(pid, 0) == cnt:
            tag = " 🌟 (First sale for pack!)"
        lines.append(f"  • Pack {pid}: {cnt} {p_noun}{tag}")
    lines.append("")

    lines.append("🏬 Stores:")
    for st, cnt in sorted(store_counts.items(), key=lambda x: x[1], reverse=True):
        s_noun = "sale" if cnt == 1 else "sales"
        lines.append(f"  • {st}: {cnt} {s_noun}")

    first_sale_packs = [pid for pid, cnt in pack_counts.items() if pack_all_counts.get(pid, 0) == cnt]
    if first_sale_packs:
        p_str = ", ".join(f"Pack {p}" for p in first_sale_packs)
        lines.append("")
        lines.append(f"🎉 Celebration: {p_str} ki modati sale vachindi! Baga build chesam! 🚀")

    return "\n".join(lines)


def best_sellers(
    db: Optional[Union[str, sqlite3.Connection]] = None,
    days: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Feed for office spotlight and scout weights.

    Returns a list of dicts: [{"pack_id": "3", "count": 12}, ...]
    STRICT PRIVACY:
      - Counts ONLY.
      - NEVER includes amount, revenue, or price.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    if days is not None and days > 0:
        cutoff = now - datetime.timedelta(days=days)
    else:
        cutoff = None

    conn = get_db_connection(db)
    rows = conn.execute(
        "SELECT pack_id, count, ts FROM sales ORDER BY ts ASC"
    ).fetchall()
    if not isinstance(db, sqlite3.Connection):
        conn.close()

    pack_totals: Dict[str, int] = collections.defaultdict(int)
    for r in rows:
        pid, cnt, ts_str = r[0], r[1], r[2]
        if cutoff is not None:
            dt = _parse_iso_ts(ts_str)
            if dt < cutoff:
                continue
        pack_totals[str(pid)] += int(cnt)

    sorted_packs = sorted(pack_totals.items(), key=lambda x: x[1], reverse=True)
    if limit is not None and limit > 0:
        sorted_packs = sorted_packs[:limit]

    # Return pure counts only!
    return [{"pack_id": pid, "count": cnt} for pid, cnt in sorted_packs]
