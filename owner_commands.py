"""owner_commands.py - Offline natural language command parser for the owner.

The owner controls the robot and office environment via typed prompts or voice
transcripts in English or Tenglish (Telugu transcribed in Latin script).

This module is strictly an offline parser:
- Never executes any actions or calls network APIs.
- Returns a structured dictionary:
    {
        "intent": str,
        "args": dict,
        "confidence": float (0.0 .. 1.0),
        "needs_confirm": bool,
        "hint": str (optional / included for unknown or help)
    }
- Risky intents require explicit owner confirmation (`needs_confirm=True`).
- Unknown commands return `intent="unknown"` with a helpful short Tenglish hint.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# Set of intents considered risky that require explicit owner confirmation.
RISKY_INTENTS = {
    "approve_draft",
    "reject_draft",
    "pause_all",
    "resume_all",
    "run_now",
    "rename_agent",
    "rename_room",
    "move_room",
    "resize_room",
}

# Default hint returned when input is unrecognized.
TENGLISH_UNKNOWN_HINT = "Ardam kaaledu. 'help' ani type cheyandi or command malli cheppandi."

# Map word representations of numbers (common in speech transcripts)
WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "okati": 1, "rendu": 2, "moodu": 3, "naalugu": 4, "nalugu": 4, "aidu": 5,
    "aaru": 6, "yedu": 7, "enimidi": 8, "tommidi": 9, "padi": 10,
}


def _extract_int(text: str) -> Optional[int]:
    """Extract an integer from digits or simple number words."""
    m = re.search(r"\b(\d+)\b", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    for word, val in WORD_TO_NUM.items():
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            return val
    return None


def _clean_str(text: str) -> str:
    """Normalize whitespace and strip punctuation from ends."""
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(".,;:?!'\"")


def _match_approve(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # approve draft 59, approve 59, 59 approve chey, draft 59 ni aamodinchu, approve cheyyi 59
    if re.search(r"\b(approve|accept|aamodinchu|ok chey|approve chey|approve cheyyi)\b", lower):
        # make sure it's not reject
        if re.search(r"\b(reject|tirooskarinchu|vaddu)\b", lower):
            return None
        n = _extract_int(lower)
        if n is not None:
            return "approve_draft", {"n": n}, 0.95
        return "approve_draft", {}, 0.70
    return None


def _match_reject(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # reject 59, draft 61 reject chey, too generic, 61 vaddu reject cheyyi reason
    if re.search(r"\b(reject|tirooskarinchu|reject chey|reject cheyyi|tappu|vaddu)\b", lower):
        if re.search(r"\b(approve|accept|aamodinchu)\b", lower) and not re.search(r"\breject\b", lower):
            return None
        n = _extract_int(lower)
        # Extract reason if provided
        # e.g., "draft 61 reject chey, too generic" or "reject 12 reason weak niche" or "reject 44 because duplicate"
        reason = ""
        # Check after comma
        if "," in text:
            parts = text.split(",", 1)
            reason = _clean_str(parts[1])
        else:
            m_reason = re.search(r"\b(?:reason|because|endukante|endukante|ani|for)\s+(.+)$", text, re.IGNORECASE)
            if m_reason:
                reason = _clean_str(m_reason.group(1))
            else:
                # check common reject phrases at end
                m_common = re.search(r"\b(too generic|duplicate|weak niche|promises too much|low quality|not good|waste)\b", lower)
                if m_common:
                    reason = m_common.group(1)

        args: Dict[str, Any] = {}
        if n is not None:
            args["n"] = n
        if reason:
            args["reason"] = reason

        conf = 0.95 if (n is not None) else 0.75
        return "reject_draft", args, conf
    return None


def _match_list_drafts(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # list drafts, show drafts, drafts chupinchu, pending drafts, drafts list chey, ennii drafts unnay
    patterns = [
        r"\b(list\s+drafts?|show\s+drafts?|drafts?\s+list|view\s+drafts?)\b",
        r"\b(drafts?\s+(chupi|chupinchu|choopinchu|chudu|choodu|list\s+chey))\b",
        r"\b(pending\s+drafts?|drafts\s+enni|enni\s+drafts)\b",
        r"^drafts?$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "list_drafts", {}, 0.95
    return None


def _match_show_status(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # show status, status chupinchu, robot status, system status enti, ela nadustondi
    patterns = [
        r"\b(show\s+status|system\s+status|robot\s+status|current\s+status)\b",
        r"\b(status\s+(chupinchu|choopinchu|chupi|cheppu|enti|ela\s+undi))\b",
        r"\b(ela\s+nadustondi|office\s+status|status\s+kavalenu)\b",
        r"^status$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "show_status", {}, 0.95
    return None


def _match_show_money(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # show money, earnings, balance, paisalu chupinchu, entha vachindi, revenue
    # Distinguish between show_money (private read) and show_money_on_screen
    if re.search(r"\b(screen|dashboard|display|tv|wall|monitor)\b", lower):
        return None
    patterns = [
        r"\b(show\s+(money|earnings|revenue|balance|profit|sales))\b",
        r"\b(money|earnings|revenue|balance)\s+(chupinchu|choopinchu|cheppu|enti)\b",
        r"\b(paisalu\s+chupinchu|entha\s+vachindi|entha\s+sampadincham|aadaayam\s+chupinchu)\b",
        r"\b(private\s+money|show\s+my\s+money|check\s+earnings)\b",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "show_money", {}, 0.90
    return None


def _match_hide_money(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # hide money, screen meeda money daayi, mask money, money hide chey, paisalu daachipettu
    patterns = [
        r"\b(hide\s+(money|earnings|revenue|balance|amounts?))\b",
        r"\b(money\s+(hide\s+chey|daayi|daachipettu|kanapada|kanapadakunda))\b",
        r"\b(paisalu\s+(daayi|daachu|daachipettu|hide\s+chey))\b",
        r"\b(screen\s+meeda\s+(?:money|paisalu)\s+(?:daayi|daachu|daachipettu|hide\s+chey))\b",
        r"\b(mask\s+(money|earnings)|don't\s+show\s+money|dont\s+show\s+money)\b",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "hide_money", {}, 0.95
    return None


def _match_show_money_on_screen(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # show money on screen, screen meeda paisalu chupinchu, display money, unhide money on screen
    # Do not match if asking to hide money on screen
    if re.search(r"\b(daayi|daachu|daachipettu|hide|mask)\b", lower):
        return None
    patterns = [
        r"\b(show\s+money\s+on\s+screen|display\s+money|screen\s+meeda\s+(paisalu|money))\b",
        r"\b(screen\s+lo\s+money\s+chupinchu|show\s+earnings\s+on\s+screen)\b",
        r"\b(unhide\s+money|reveal\s+money\s+on\s+screen)\b",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "show_money_on_screen", {}, 0.95
    return None


def _match_pause_all(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # pause all, stop all, anni aapu, anni pause chey, pause everything, mottam aapandi
    patterns = [
        r"\b(pause\s+all|stop\s+all|pause\s+everything|emergency\s+pause|kill\s+all)\b",
        r"\b(anni\s+(aapu|aapandi|pause\s+chey|nilipiveyyi))\b",
        r"\b(mottam\s+(aapu|aapandi|pause\s+chey))\b",
        r"^pause$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "pause_all", {}, 0.95
    return None


def _match_resume_all(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # resume all, start all, anni modalu pettu, unpause, anni restart chey, malli start chey
    patterns = [
        r"\b(resume\s+all|resume\s+everything|unpause\s+all|start\s+all)\b",
        r"\b(anni\s+(start\s+chey|modalu\s+pettu|nadipinchu|resume\s+chey))\b",
        r"\b(malli\s+(start\s+chey|nadipinchu|modalu\s+pettu)|unpause)\b",
        r"^resume$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "resume_all", {}, 0.95
    return None


def _match_run_now(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # run now, execute now, ippude run chey, cycle ippude start chey, trigger run now
    patterns = [
        r"\b(run\s+now|execute\s+now|trigger\s+run|run\s+immediately|start\s+cycle\s+now)\b",
        r"\b(ippude\s+(run\s+chey|nadipinchu|start\s+chey|cycle\s+start))\b",
        r"\b(ventane\s+(run\s+chey|start\s+chey))\b",
        r"^run$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "run_now", {}, 0.95
    return None


def _match_list_packs(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # list packs, show upload packs, packs chupinchu, ready packs, upload packs list chey
    # ensure it's not show_pack(n)
    if _extract_int(lower) is not None and re.search(r"\b(pack|upload)\b", lower) and not re.search(r"\blist\b", lower):
        return None
    patterns = [
        r"\b(list\s+(?:upload\s+)?packs?|show\s+(?:upload\s+)?packs?|upload\s+packs?\s+list|ready\s+packs?)\b",
        r"\b(packs?\s+(chupinchu|choopinchu|list\s+chey|anni|enni\s+unnay))\b",
        r"\b(upload\s+packs?\s+chupinchu|packs\s+list)\b",
        r"^/packs$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "list_packs", {}, 0.95
    return None


def _match_show_pack(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # show pack 3, pack 3 chupinchu, view pack 2, pack 5 details, /pack 4
    if re.search(r"\b(pack|upload\s+pack)\b", lower):
        n = _extract_int(lower)
        if n is not None:
            return "show_pack", {"n": n}, 0.95
    return None


def _match_rename_agent(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # rename agent Scout to Finder, agent Scout peru Finder ga marchu, change agent Scout to Finder
    # rename agent <old> as <new>
    m = re.search(r"\b(?:rename\s+agent|change\s+agent)\s+([A-Za-z0-9_-]+)\s+(?:to|as|into)\s+([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if m:
        return "rename_agent", {"old": _clean_str(m.group(1)), "new": _clean_str(m.group(2))}, 0.95
    # Tenglish: agent Scout peru Finder ga marchu / agent Scout ni Finder ga marchu
    m_te = re.search(r"\bagent\s+([A-Za-z0-9_-]+)(?:\s+peru|\s+ni)?\s+([A-Za-z0-9_-]+)\s+(?:ga\s+marchu|ga\s+pettu|ani\s+marchu)", text, re.IGNORECASE)
    if m_te:
        return "rename_agent", {"old": _clean_str(m_te.group(1)), "new": _clean_str(m_te.group(2))}, 0.95
    return None


def _match_rename_room(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # rename room Studio to Workshop, room Studio peru Workshop ga marchu, rename Pinterest room to PinLab, change room Reception to FrontDesk
    m = re.search(r"\b(?:rename|change)\s+room\s+([A-Za-z0-9_-]+)\s+(?:to|as|into)\s+([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if m:
        return "rename_room", {"old": _clean_str(m.group(1)), "new": _clean_str(m.group(2))}, 0.95
    m2 = re.search(r"\b(?:rename|change)\s+([A-Za-z0-9_-]+)\s+room\s+(?:to|as|into)\s+([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if m2:
        return "rename_room", {"old": _clean_str(m2.group(1)), "new": _clean_str(m2.group(2))}, 0.95
    # Tenglish: room Studio peru Workshop ga marchu / Studio room ni Workshop ga marchu
    m_te = re.search(r"\b(?:room\s+([A-Za-z0-9_-]+)|([A-Za-z0-9_-]+)\s+room)(?:\s+peru|\s+ni)?\s+([A-Za-z0-9_-]+)\s+(?:ga\s+marchu|ga\s+pettu|ani\s+marchu)", text, re.IGNORECASE)
    if m_te:
        old_val = m_te.group(1) or m_te.group(2)
        new_val = m_te.group(3)
        return "rename_room", {"old": _clean_str(old_val), "new": _clean_str(new_val)}, 0.95
    return None


def _match_move_room(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # move room Studio near Reception, move Pinterest room close to Council, Studio room ni Reception daggaraki jhappu
    m = re.search(r"\bmove\s+(?:room\s+)?([A-Za-z0-9_-]+)(?:\s+room)?\s+(?:near|next\s+to|beside|close\s+to|towards)\s+([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if m:
        return "move_room", {"name": _clean_str(m.group(1)), "near": _clean_str(m.group(2))}, 0.95
    # Tenglish: Studio room ni Reception daggaraki jarupu / Pinterest room ni Council daggaraki pettu
    m_te = re.search(r"\b([A-Za-z0-9_-]+)(?:\s+room)?(?:\s+ni)?\s+([A-Za-z0-9_-]+)(?:\s+room)?\s+(?:daggaraki|pakkana|daggarlo)\s+(?:jarupu|pettu|marchu)", text, re.IGNORECASE)
    if m_te:
        return "move_room", {"name": _clean_str(m_te.group(1)), "near": _clean_str(m_te.group(2))}, 0.95
    return None


def _match_resize_room(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # resize room Studio bigger, make Pinterest room smaller, Pinterest room ni pedda chey, Council room chinna chey
    action = None
    if re.search(r"\b(bigger|large|larger|expand|pedda|peddadi)\b", lower):
        action = "bigger"
    elif re.search(r"\b(smaller|small|shrink|chinna|chinnadi)\b", lower):
        action = "smaller"

    if action:
        # Tenglish first: Pinterest room ni pedda chey / Pinterest ni pedda chey / Pinterest pedda chey
        m_te = re.search(r"\b([A-Za-z0-9_-]+)(?:\s+room)?\s+(?:ni\s+)?(?:pedda|chinna|peddadi|chinnadi)\s+(?:chey|cheyyi|pettu|marchu)", text, re.IGNORECASE)
        if m_te:
            room_name = _clean_str(m_te.group(1))
            if room_name.lower() not in {"room", "ni"}:
                return "resize_room", {"name": room_name, "size": action}, 0.95

        # English: resize room <name> bigger, make <name> room smaller, enlarge <name> room
        m_en1 = re.search(r"\b(?:resize|make)\s+(?:room\s+)?([A-Za-z0-9_-]+)(?:\s+room)?\s+(?:bigger|smaller|large)", text, re.IGNORECASE)
        if m_en1:
            room_name = _clean_str(m_en1.group(1))
            return "resize_room", {"name": room_name, "size": action}, 0.95

        m_en2 = re.search(r"\b([A-Za-z0-9_-]+)(?:\s+room)?\s+(?:bigger|smaller)", text, re.IGNORECASE)
        if m_en2:
            room_name = _clean_str(m_en2.group(1))
            if room_name.lower() not in {"make", "resize", "room", "please"}:
                return "resize_room", {"name": room_name, "size": action}, 0.95
    return None


def _match_theme(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # theme dark, dark mode pettu, light theme marchu, festival mode, theme light
    # choices: dark | light | festival
    target = None
    if re.search(r"\b(dark|raatri|night)\b", lower):
        target = "dark"
    elif re.search(r"\b(light|pagalu|day)\b", lower):
        target = "light"
    elif re.search(r"\b(festival|pandaga|celebration)\b", lower):
        target = "festival"

    if target:
        if re.search(r"\b(theme|mode|color|pettu|marchu|set)\b", lower):
            return "theme", {"theme": target}, 0.95
    return None


def _match_language(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # language english, tenglish lo matladu, switch language to english, bhasha tenglish pettu
    target = None
    if re.search(r"\b(tenglish|telugu)\b", lower):
        target = "tenglish"
    elif re.search(r"\b(english|aanglam)\b", lower):
        target = "english"

    if target:
        if re.search(r"\b(language|bhasha|matladu|speak|switch|set|pettu|change)\b", lower) or lower.strip() in {"tenglish", "english"}:
            return "language", {"language": target}, 0.95
    return None


def _match_focus_room(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # focus room Council, zoom into Studio room, Council room meeda focus pettu, focus on Pinterest
    # Tenglish first: Council room meeda focus pettu / Pinterest ni focus chey / Council focus pettu / Studio room meeda zoom chey
    m_te = re.search(r"\b([A-Za-z0-9_-]+)(?:\s+room)?(?:\s+(?:meeda|ni))?\s+(?:focus|zoom)\s+(?:pettu|chey|cheyyi)", text, re.IGNORECASE)
    if m_te:
        name = _clean_str(m_te.group(1))
        if name.lower() not in {"focus", "room", "meeda", "ni", "zoom"}:
            return "focus_room", {"name": name}, 0.95

    # English: focus on Pinterest, focus room Council, zoom into Pinterest room, focus Council
    m_en = re.search(r"\b(?:focus\s+on|focus\s+room|focus|zoom\s+into|zoom\s+in|zoom)\s+(?:room\s+)?([A-Za-z0-9_-]+)(?:\s+room)?\b", text, re.IGNORECASE)
    if m_en:
        room = _clean_str(m_en.group(1))
        if room.lower() not in {"on", "in", "into", "room", "the", "meeda", "pettu", "chey"}:
            return "focus_room", {"name": room}, 0.95
    return None


def _match_mute(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # mute, silent mode, silent ga undu, sound aapu, shabdam aapu
    # make sure not unmute
    if re.search(r"\bunmute\b", lower):
        return None
    patterns = [
        r"\b(mute|silent\s+mode|turn\s+off\s+sound|quiet)\b",
        r"\b(sound\s+aapu|silent\s+ga\s+undu|shabdam\s+aapu|matladaku)\b",
        r"^mute$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "mute", {}, 0.95
    return None


def _match_unmute(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # unmute, sound on chey, turn on sound, shabdam on chey, unmute chey
    patterns = [
        r"\b(unmute|turn\s+on\s+sound|sound\s+on|enable\s+audio)\b",
        r"\b(sound\s+(on\s+chey|petu|pettu)|shabdam\s+on\s+chey|unmute\s+chey)\b",
        r"^unmute$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "unmute", {}, 0.95
    return None


def _match_undo_last_ui_change(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # undo, undo last change, undo ui change, mundu laaga chey, venakki teesko, rollback ui
    patterns = [
        r"\b(undo(\s+last(\s+ui)?\s+change)?|rollback(\s+ui)?|revert(\s+ui)?)\b",
        r"\b(mundu\s+laaga\s+chey|venakki\s+teesko|undo\s+chey|malli\s+venakki)\b",
        r"^undo$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "undo_last_ui_change", {}, 0.95
    return None


def _match_daily_report(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # daily report, show daily report, roju report chupinchu, roju report enti, give me daily report
    # distinguish from what_happened_today
    if re.search(r"\b(jarigindi|what\s+happened|summary)\b", lower) and not re.search(r"\breport\b", lower):
        return None
    patterns = [
        r"\b(daily\s+report|today('s)?\s+report|day\s+report)\b",
        r"\b(roju\s+report|daily\s+report\s+(chupinchu|choopinchu|kavali|pettu))\b",
        r"\b(report\s+chupinchu|report\s+kavalenu)\b",
        r"^report$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "daily_report", {}, 0.95
    return None


def _match_what_happened_today(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # what happened today, ee roju em jarigindi, today summary, what did robot do today, ee roju updates enti
    patterns = [
        r"\b(what\s+happened\s+today|what\s+did\s+(?:you|robot)\s+do\s+today|today('s)?\s+summary)\b",
        r"\b(ee\s+roju\s+(?:em\s+jarigindi|emi\s+jarigindi|updates\s+enti|panulu\s+enti))\b",
        r"\b(eroju\s+(?:em\s+jarigindi|emi\s+jarigindi|updates\s+enti|panulu\s+enti))\b",
        r"\b(today\s+activity|activities\s+today)\b",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "what_happened_today", {}, 0.95
    return None


def _match_help(text: str, lower: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    # help, sahaayam, commands list, emit cheyagalanu, how to use, /help
    patterns = [
        r"\b(help|commands|how\s+to\s+use|command\s+list)\b",
        r"\b(sahaayam|sahayam|em\s+cheyagalanu|commands\s+chupinchu)\b",
        r"^help$",
        r"^/help$",
    ]
    for pat in patterns:
        if re.search(pat, lower):
            return "help", {}, 0.95
    return None


def parse(text: str) -> Dict[str, Any]:
    """Parse an owner voice or typed text prompt into a structured command dict.

    Returns:
        {
            "intent": str,
            "args": dict,
            "confidence": float,
            "needs_confirm": bool,
            "hint": str (optional)
        }
    """
    if not text or not isinstance(text, str) or not text.strip():
        return {
            "intent": "unknown",
            "args": {},
            "confidence": 0.0,
            "needs_confirm": False,
            "hint": TENGLISH_UNKNOWN_HINT,
        }

    raw = text.strip()
    lower = raw.lower()

    # Matchers ordered logically:
    # 1. Draft actions (approve / reject / list)
    # 2. Packs (show_pack / list_packs)
    # 3. Status and reporting (status, report, today)
    # 4. Money actions (show_money_on_screen, hide_money, show_money)
    # 5. Global controls (pause_all, resume_all, run_now)
    # 6. Room/Agent alterations (rename_agent, rename_room, move_room, resize_room, focus_room)
    # 7. UI controls (theme, language, mute, unmute, undo)
    # 8. Help

    matchers = [
        _match_reject,
        _match_approve,
        _match_list_drafts,
        _match_show_pack,
        _match_list_packs,
        _match_show_money_on_screen,
        _match_hide_money,
        _match_show_money,
        _match_show_status,
        _match_what_happened_today,
        _match_daily_report,
        _match_pause_all,
        _match_resume_all,
        _match_run_now,
        _match_rename_agent,
        _match_rename_room,
        _match_move_room,
        _match_resize_room,
        _match_focus_room,
        _match_theme,
        _match_language,
        _match_unmute,
        _match_mute,
        _match_undo_last_ui_change,
        _match_help,
    ]

    for matcher in matchers:
        res = matcher(raw, lower)
        if res is not None:
            intent, args, confidence = res
            needs_confirm = intent in RISKY_INTENTS
            result = {
                "intent": intent,
                "args": args,
                "confidence": confidence,
                "needs_confirm": needs_confirm,
            }
            if intent == "help":
                result["hint"] = "Commands: approve <n>, reject <n>, list drafts, status, pause all, resume, theme dark/light, help."
            return result

    # Fallback to unknown
    return {
        "intent": "unknown",
        "args": {"raw": raw},
        "confidence": 0.0,
        "needs_confirm": False,
        "hint": TENGLISH_UNKNOWN_HINT,
    }
