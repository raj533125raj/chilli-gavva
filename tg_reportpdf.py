"""Telegram plugin: tg_reportpdf.py

Commands:
  /reportpdf   build the weekly PDF report and send it as a document
  /weeklypdf   alias for /reportpdf
  weekly pdf   /  weekly report  /  report pdf  (plain Tenglish-friendly words)

The plugin never calls the Telegram API directly. It uses the shared ctx
callbacks from telegram_inbox (send_document) and never imports telegram_inbox
at module import time. The report itself is built by report_pdf.py from the
robot's own database with safe fallbacks. Owner-only access is enforced by the
inbox before plugins run.
"""

from __future__ import annotations

import re
import tempfile

import report_pdf

COMMANDS = [
    {
        "cmd": "/reportpdf",
        "words": ["reportpdf", "report pdf", "weekly pdf", "weekly report",
                  "weekly report pdf", "report pampu", "weekly pdf pampu"],
        "help_en": "Build the weekly PDF report (counts, charts, wins, problems, next plan) and send it.",
        "help_te": "Weekly PDF report (counts, charts, wins, problems, next plan) build chesi pampisthundi.",
    },
]

_TRIGGERS = (
    "reportpdf", "report pdf", "weekly pdf", "weekly report", "weekly report pdf",
    "report pampu", "weekly pdf pampu",
)
_PATTERN = re.compile(
    r"^\s*(?:/(?:reportpdf|weeklypdf)(?:@\w+)?|(?:" +
    "|".join(re.escape(word) for word in _TRIGGERS) + r"))\s*$",
    re.IGNORECASE,
)


def is_report_command(text):
    return bool(_PATTERN.match(str(text or "").strip()))


def handle(text, ctx):
    if not is_report_command(text):
        return None
    send_document = ctx.get("send_document")
    if not callable(send_document):
        return {"text": "Report PDF pampadaniki send_document ledu; owner setup check cheyali."}
    try:
        result = report_pdf.build_weekly_report(
            db_path=ctx.get("db_path"),
            now=ctx.get("now"),
            out_dir=tempfile.gettempdir(),
        )
    except Exception as exc:
        return {"text": f"Report PDF build fail ayyindi ({type(exc).__name__}). Owner check cheyali."}
    data = result.get("data", {})
    caption = (f"Weekly report PDF · {data.get('week_key', data.get('week', ''))} · counts only. "
               "Owner review cheyandi; nothing auto-published.")
    send_document(result["path"], caption=caption[:1000])
    return {"handled": True}
