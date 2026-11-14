"""tg_sales.py — Telegram plugin for private sales notes (BINNY-W3A).

Provides owner commands for recording sales, undoing entries, and viewing sales reports:
  - /sales [today|week|month|all] or 'sales today', 'sales week', etc.
  - Recording phrases:
      "sale pack 3 Gumroad"
      "pack 5 ki 2 sales Etsy"
      "sale pack 3 Gumroad note: great customer"
  - Undo:
      "/sales undo" or "undo last sale" or "sale undo"
  - Pure privacy: amounts are optional and stay only in the private DB and in the owner's Telegram reply.
    They are never written to any file in the repo, never to status.json, never to logs.
  - Feeds: function best_sellers(db, days) available in sales_ledger for office spotlight and scout weights.

Follows Chilli Gavva Telegram Plugin Interface:
  COMMANDS = [{"cmd": "/sales", "words": [...], "help_en": "...", "help_te": "..."}]
  def handle(text: str, ctx: dict) -> dict | None
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

import sales_ledger

COMMANDS = [
    {
        "cmd": "/sales",
        "words": [
            "sales",
            "sale",
            "sales report",
            "sales summary",
            "sales week",
            "sales today",
            "sales month",
            "sales all",
            "sales vivaralu",
            "sales undo",
            "undo sale",
        ],
        "category": "Reports",
        "help_en": "Record a sale or view sales summary: /sales [today|week|month|all|undo]",
        "help_te": "Sale note record cheyadaniki leda summary choodadaniki: /sales [today|week|month|all|undo]",
    }
]


def _get_utc_now(ctx: Dict[str, Any]) -> datetime.datetime:
    now = ctx.get("now")
    if isinstance(now, datetime.datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    elif isinstance(now, (int, float)):
        return datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def handle(text: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle incoming owner Telegram text.

    Returns:
      - None if text is not recognized for this plugin.
      - {"text": "..."} to reply.
      - {"handled": True} if handled via ctx.
    """
    if not text or not isinstance(text, str):
        return None

    raw = text.strip()

    # Built-in command conflict protection
    lower = raw.lower()
    builtins = (
        "/status",
        "/list",
        "/view",
        "/approve",
        "/reject",
        "/packs",
        "/pack",
        "/marketing",
        "/pipeline",
        "/pause",
        "/resume",
        "/buttontest",
        "/rules",
        "/cubes",
        "/doctor",
        "/autofix_drill",
        "/help",
        "/start",
        "/today",
        "/posted",
        "/ab",
        "/week",
    )
    for b in builtins:
        if lower == b or lower.startswith(b + " ") or lower.startswith(b + "@"):
            return None

    # Bare "sale" is a declared word but names no pack, so the ledger parser returned nothing and the
    # owner got "Ardam kaaledu". Say how a sale is recorded instead.
    if lower.rstrip("?.! ") in ("sale", "/sale"):
        return {"text": ("Sale note cheyadaniki pack number cheppandi, udaharanaki: 'sale pack 3 Gumroad' "
                         "leda 'pack 5 ki 2 sales Etsy'.\nSummary kosam: /sales (today | week | month | all).\n"
                         "Chivari sale teeseyadaniki: /sales undo")}

    # Parse via sales_ledger
    parsed = sales_ledger.parse_sales_input(raw)
    if not parsed:
        return None

    db_path = ctx.get("db_path")
    now = _get_utc_now(ctx)
    intent = parsed.get("intent")

    if intent == "undo":
        undone = sales_ledger.undo_last_sale(db_path=db_path)
        reply_text = sales_ledger.format_undo_confirmation(undone)
        return {"text": reply_text}

    if intent == "report":
        period = parsed.get("period", "week")
        report = sales_ledger.generate_sales_report(period=period, now=now, db_path=db_path)
        return {"text": report}

    if intent == "record":
        pack_id = parsed["pack_id"]
        store = parsed.get("store", "Direct")
        count = parsed.get("count", 1)
        note = parsed.get("note", "")
        amt = parsed.get("amount")
        curr = parsed.get("currency")

        sale = sales_ledger.record_sale(
            pack_id=pack_id,
            store=store,
            count=count,
            ts=now,
            note=note,
            amount=amt,
            currency=curr,
            db_path=db_path,
        )
        reply_text = sales_ledger.format_sale_confirmation(sale)
        return {"text": reply_text}

    return None
