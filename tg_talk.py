"""Telegram plugin: tg_talk.py — CC1-3: talk to the robot in plain Tenglish.

  "ee roju em jarigindi"   -> the daily report          (daily_report.report_text)
  "pending drafts entha"   -> /list                     (telegram_inbox.handle_command)
  "pack 3 files pampu"     -> /files 3                  (tg_files.handle)
  "moodo pack sell chey"   -> /sell 3                   (tg_sell.handle)
  "em cheyali ippudu?"     -> the next thing that needs the owner, with that command's real output

talk_intents.detect() understands the text; this plugin then runs the SAME code the real command
runs and returns its REAL output. There are no canned answers here.

  - Not sure (low confidence)  -> a short question with a 2-button keyboard; each button is a real
    command, so tapping it runs the normal command path.
  - Risky commands (approve / reject / pause / resume) are never run from plain talk: the exact
    command is offered as a button instead.
  - Text starting with "/" is never answered here (built-in and plugin slash commands own it).
  - Hi / menu / help stay with tg_menu.
  - telegram_inbox is imported only inside functions, never at module import time.
  - This plugin has no slash command of its own, so COMMANDS only carries words for the menu.
"""
from __future__ import annotations

import glob
import importlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import talk_intents

REPO_ROOT = Path(__file__).resolve().parent

COMMANDS = [
    {
        "cmd": "/talk",
        "words": ["talk", "maatladu", "matladu"],
        "help_en": "Just type in plain Tenglish or English (no slash needed): 'pack 3 files pampu', "
                   "'pending drafts entha', 'em cheyali ippudu?'",
        "help_te": "Slash lekunda mamulu ga type cheyandi: 'pack 3 files pampu', 'pending drafts entha', "
                   "'em cheyali ippudu?'",
    },
]

# Built-in commands answered by telegram_inbox.handle_command(cmd, ids, db_path).
BUILTIN = {"status", "list", "view", "packs", "pack", "marketing", "pipeline", "doctor", "today", "posted",
           "week", "rules", "cubes"}
_SELF = {"tg_talk"}
_plugin_cache: Dict[str, str] = {}


def plugin_for(command_word: str) -> Optional[Any]:
    """The plugin module that owns a slash command (found from each tg_*.py COMMANDS list)."""
    if not _plugin_cache:
        for path in sorted(glob.glob(str(REPO_ROOT / "tg_*.py"))):
            name = os.path.splitext(os.path.basename(path))[0]
            if name in _SELF:
                continue
            try:
                module = importlib.import_module(name)
            except Exception:
                continue
            for entry in getattr(module, "COMMANDS", []) or []:
                command = str(entry.get("cmd", "")).lower()
                if command and hasattr(module, "handle"):
                    _plugin_cache.setdefault(command, name)
    name = _plugin_cache.get(command_word.lower())
    if not name:
        return None
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _builtin(command: str, ids: List[int], ctx: Dict[str, Any]) -> str:
    import telegram_inbox   # lazy: never at module import time
    return telegram_inbox.handle_command(command, ids, ctx.get("db_path"))


def _text_only(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """ctx copy whose send_text collects text instead of sending (to quote a command's output)."""
    copy = dict(ctx)
    collected: List[str] = []
    copy["send_text"] = lambda text, keyboard=None: collected.append(str(text)) or True
    copy["_collected"] = collected
    return copy


def next_step(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """'Em cheyali ippudu?' — look at the real state and show the first thing that needs the owner."""
    import guardrails
    waiting = guardrails.pending(ctx.get("db_path"))
    if waiting:
        return {"text": f"Mundu idi cheyandi: {len(waiting)} drafts mee approval kosam wait chestunnayi.\n\n"
                        + _builtin("list", [], ctx)}
    sell = plugin_for("/sell")
    if sell is not None:
        quiet = _text_only(ctx)
        reply = sell.handle("/sell", quiet) or {}
        text = "\n".join(quiet["_collected"]) or str(reply.get("text", ""))
        if "/sell " in text:      # at least one pack can be listed now
            return {"text": "Approval kosam drafts levu. Ippudu ee packs ni Gumroad lo pettochu:\n\n" + text}
    return {"text": "Approval kosam drafts levu, sell ki ready packs levu. Ee roju posts:\n\n"
                    + _builtin("today", [], ctx)}


def run_command(command: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Run one understood command with the real code behind it and return its real reply."""
    if command == "daily":
        import daily_report
        return {"text": daily_report.report_text(db_path=ctx.get("db_path"))}
    if command == "next":
        return next_step(ctx)
    word, _, rest = command.partition(" ")
    name = word.lstrip("/").lower()
    ids = [int(number) for number in re.findall(r"\d+", rest)]
    if name in BUILTIN:
        return {"text": _builtin(name, ids, ctx)}
    module = plugin_for(word)
    if module is None:
        return None
    reply = module.handle(command, ctx)
    if isinstance(reply, str):
        reply = {"text": reply}
    return reply if isinstance(reply, dict) else None


def suggestion_reply(found: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Two real commands as buttons; nothing is run until the owner taps one."""
    options = talk_intents.suggestions(found)
    if found.get("risky"):
        lead = "Idi pedda action, nenu mamulu maatala tho cheyanu. Kavalante ee command tap cheyandi:"
    else:
        lead = "Meeru adigindi idena? Okati tap cheyandi:"
    lines = [lead] + [f"{index}. {option['command']} — {option['help']}" for index, option in enumerate(options, 1)]
    text = "\n".join(lines)
    keyboard = [[option["command"]] for option in options]
    send_text = ctx.get("send_text")
    if callable(send_text):
        send_text(text, keyboard=keyboard)
        return {"handled": True}
    return {"text": text, "keyboard": keyboard}


EXAMPLES = ["ee roju em jarigindi", "pending drafts entha", "pack 3 files pampu", "moodo pack sell chey",
            "ee roju sales entha", "robot ela undi", "em cheyali ippudu?"]


def examples_text() -> str:
    """/talk: example sentences with the command each one runs (worked out live by talk_intents)."""
    lines = ["Slash lekunda mamulu ga type cheyandi. Udaharanalu:"]
    for phrase in EXAMPLES:
        found = talk_intents.detect(phrase)
        target = {"daily": "ee roju report", "next": "ippudu cheyalsina pani"}.get(
            found["command"], found["command"]) if found else "?"
        lines.append(f"• {phrase}  ->  {target}")
    lines.append("Ardam kakapothe rendu buttons chupistanu; okati tap cheyandi.")
    return "\n".join(lines)


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Standard Telegram plugin entry point."""
    if not isinstance(text, str):
        return None
    if re.fullmatch(r"/talk(?:@\w+)?|talk|maatladu|matladu", text.strip(), re.IGNORECASE):
        return {"text": examples_text()}
    if text.strip().startswith("/"):
        return None
    found = talk_intents.detect(text)
    if found is None:
        return None
    ctx = ctx if isinstance(ctx, dict) else {}
    if not talk_intents.should_run(found):
        return suggestion_reply(found, ctx)
    try:
        reply = run_command(found["command"], ctx)
    except Exception as error:
        return {"text": f"'{found['command']}' run chesetappudu problem vachindi ({type(error).__name__}). "
                        "/doctor tho chudandi."}
    if not reply or not (reply.get("handled") or str(reply.get("text", "")).strip()):
        return suggestion_reply(found, ctx)
    return reply
