"""tg_menu.py - Telegram Plugin for Greetings, Menu, and Help.

Follows Chilli Gavva Telegram Plugin Interface:
  COMMANDS = [{"cmd": "/menu", "words": ["menu"], "help_en": "...", "help_te": "..."}, ...]
  def handle(text: str, ctx: dict) -> dict | None

Provides:
  - Greetings ('hi', 'hello', 'namaste', 'hii', 'em chestunnav') -> Friendly Tenglish greeting + status + menu
  - /menu and 'menu' -> Grouped menu (Drafts, Packs, Posts, Reports, Settings) built from plugins + built-in commands
  - /help <word> -> Specific command help
  - Typo tolerance (edit distance 1-2) for command words
  - Unknown text fallback -> 3 closest suggestions instead of raw 'Ardam kaaledu'
"""
import glob
import importlib
import os
import re
import sys

COMMANDS = [
    {
        "cmd": "/menu",
        "words": ["menu", "options", "commands"],
        "category": "Settings",
        "help_en": "Show the grouped command menu",
        "help_te": "Command menu chupisthundi",
    },
    {
        "cmd": "/help",
        "words": ["help", "sahayam", "ela cheyali"],
        "category": "Settings",
        "help_en": "Show help for a command: /help <command>",
        "help_te": "Command gurinchi vivarana: /help <command>",
    },
]

BUILTIN_COMMANDS = [
    # Drafts
    {"cmd": "/list", "words": ["list", "drafts", "pending"], "category": "Drafts", "help_en": "Show drafts waiting for approval", "help_te": "Waiting drafts chupisthundi"},
    {"cmd": "/view", "words": ["view", "chudu"], "category": "Drafts", "help_en": "Read full draft text: /view <ID>", "help_te": "Draft motham chadavadaniki: /view <ID>"},
    {"cmd": "/approve", "words": ["approve", "aamodinchu"], "category": "Drafts", "help_en": "Approve draft: /approve <ID>", "help_te": "Draft approve cheyadaniki: /approve <ID>"},
    {"cmd": "/reject", "words": ["reject", "vaddu"], "category": "Drafts", "help_en": "Reject draft: /reject <ID>", "help_te": "Draft reject cheyadaniki: /reject <ID>"},

    # Packs
    {"cmd": "/packs", "words": ["packs", "ready packs"], "category": "Packs", "help_en": "List ready upload packs", "help_te": "Ready ga unna packs list"},
    {"cmd": "/pack", "words": ["pack"], "category": "Packs", "help_en": "View pack details: /pack <ID>", "help_te": "Pack details choodandi: /pack <ID>"},
    {"cmd": "/marketing", "words": ["marketing", "mkt"], "category": "Packs", "help_en": "Marketing assets for pack: /marketing <ID>", "help_te": "Pack marketing files choodandi: /marketing <ID>"},

    # Posts
    {"cmd": "/today", "words": ["today", "ee roju"], "category": "Posts", "help_en": "Show scheduled posts for today", "help_te": "Ee roju post cheyalsina content"},
    {"cmd": "/posted", "words": ["posted", "chesanu"], "category": "Posts", "help_en": "Mark post done: /posted <N>", "help_te": "Post complete ayyindi ani mark cheyandi: /posted <N>"},
    {"cmd": "/ab", "words": ["ab", "ab test"], "category": "Posts", "help_en": "Track A/B title clicks: /ab <pack> <title> <clicks>", "help_te": "A/B title test clicks track cheyandi"},

    # Reports
    {"cmd": "/status", "words": ["status", "paristhithi"], "category": "Reports", "help_en": "Compact system status", "help_te": "System status chupisthundi"},
    {"cmd": "/pipeline", "words": ["pipeline", "progress"], "category": "Reports", "help_en": "Pipeline plan vs done per stage", "help_te": "Pipeline progress choodandi"},
    {"cmd": "/week", "words": ["week", "ee vaaram"], "category": "Reports", "help_en": "Weekly performance summary", "help_te": "Ee vaaram summary choodandi"},
    {"cmd": "/doctor", "words": ["doctor", "health"], "category": "Reports", "help_en": "Show last doctor health cases", "help_te": "Doctor checks report choodandi"},

    # Settings
    {"cmd": "/menu", "words": ["menu"], "category": "Settings", "help_en": "Show main menu", "help_te": "Menu chupisthundi"},
    {"cmd": "/pause", "words": ["pause", "aapu"], "category": "Settings", "help_en": "Emergency pause: stop AI work", "help_te": "AI panulu aapadaniki emergency pause"},
    {"cmd": "/resume", "words": ["resume", "start"], "category": "Settings", "help_en": "Resume AI work", "help_te": "AI panulu malli start cheyadaniki"},
    {"cmd": "/rules", "words": ["rules", "niyamalu"], "category": "Settings", "help_en": "List active learned rules", "help_te": "Active rules list choodandi"},
    {"cmd": "/cubes", "words": ["cubes"], "category": "Settings", "help_en": "Department cubes status", "help_te": "Department cubes status choodandi"},
]

GREETING_WORDS = {"hi", "hello", "namaste", "hii", "em chestunnav"}


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    s1, s2 = s1.lower(), s2.lower()
    if s1 == s2:
        return 0
    if len(s1) == 0:
        return len(s2)
    if len(s2) == 0:
        return len(s1)

    v0 = list(range(len(s2) + 1))
    v1 = [0] * (len(s2) + 1)

    for i in range(len(s1)):
        v1[0] = i + 1
        for j in range(len(s2)):
            cost = 0 if s1[i] == s2[j] else 1
            v1[j + 1] = min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost)
        v0 = list(v1)

    return v0[len(s2)]


def collect_all_commands():
    """Discover all commands across plugins and builtins."""
    commands = list(BUILTIN_COMMANDS)
    seen_cmds = {c["cmd"].lower() for c in commands}

    root = os.path.dirname(os.path.abspath(__file__))
    pattern = os.path.join(root, "tg_*.py")
    for ppath in sorted(glob.glob(pattern)):
        mod_name = os.path.splitext(os.path.basename(ppath))[0]
        try:
            if mod_name in sys.modules:
                mod = sys.modules[mod_name]
            else:
                mod = importlib.import_module(mod_name)
            p_cmds = getattr(mod, "COMMANDS", None)
            if isinstance(p_cmds, list):
                for cmd_entry in p_cmds:
                    if isinstance(cmd_entry, dict) and cmd_entry.get("cmd"):
                        cmd_clean = cmd_entry["cmd"].strip().lower()
                        if cmd_clean not in seen_cmds:
                            seen_cmds.add(cmd_clean)
                            entry_copy = dict(cmd_entry)
                            if "category" not in entry_copy:
                                entry_copy["category"] = "Settings"
                            commands.append(entry_copy)
        except Exception:
            continue
    return commands


def format_grouped_menu():
    """Build grouped menu (Drafts, Packs, Posts, Reports, Settings)."""
    commands = collect_all_commands()
    groups = {
        "Drafts": [],
        "Packs": [],
        "Posts": [],
        "Reports": [],
        "Settings": [],
    }

    for item in commands:
        cat = item.get("category", "Settings")
        if cat not in groups:
            groups[cat] = []
        cmd = item.get("cmd", "")
        help_te = item.get("help_te") or item.get("help_en") or ""
        groups[cat].append(f"  {cmd} — {help_te}")

    lines = ["📋 *Chilli Gavva Menu* (Mee robot commands):\n"]
    order = ["Drafts", "Packs", "Posts", "Reports", "Settings"]
    for cat in order:
        cmd_lines = groups.get(cat, [])
        if cmd_lines:
            lines.append(f"*{cat}*:")
            lines.extend(cmd_lines)
            lines.append("")

    lines.append("Tip: Edhaina command vivarana kosam `/help <command>` type cheyandi.")
    return "\n".join(lines).strip()


def get_short_status_line(ctx: dict) -> str:
    """Get a short 1-line Tenglish status for greetings."""
    db_path = ctx.get("db_path")
    try:
        import guardrails
        pending_cnt = len(guardrails.pending(db_path))
    except Exception:
        pending_cnt = 0

    try:
        from telegram_inbox import _find_ready_packs
        packs_cnt = len(_find_ready_packs())
    except Exception:
        packs_cnt = 0

    return f"Status: {pending_cnt} drafts waiting approval, {packs_cnt} packs ready ga unnayi."


def find_command_match(word: str, commands=None):
    """Find exact match or closest typo matches (edit distance 1-2)."""
    if commands is None:
        commands = collect_all_commands()

    clean_word = word.strip().lower()
    if clean_word.startswith("/"):
        clean_word = clean_word[1:]

    # Check exact match first
    for item in commands:
        c_name = item.get("cmd", "").lstrip("/").lower()
        if clean_word == c_name:
            return item, 0
        for w in item.get("words", []):
            if clean_word == w.lower():
                return item, 0

    # Look for typo distance 1 or 2
    best_item = None
    min_dist = 999

    for item in commands:
        candidates = [item.get("cmd", "").lstrip("/").lower()] + [w.lower() for w in item.get("words", [])]
        for cand in candidates:
            dist = levenshtein_distance(clean_word, cand)
            if dist < min_dist:
                min_dist = dist
                best_item = item

    if min_dist <= 2:
        return best_item, min_dist

    return None, min_dist


def find_suggestions(text: str, max_suggestions=3, commands=None):
    """Return top max_suggestions closest commands for unknown text."""
    if commands is None:
        commands = collect_all_commands()

    words = text.strip().lower().split()
    target = words[0].lstrip("/") if words else ""

    scored = []
    seen = set()
    for item in commands:
        cmd = item.get("cmd", "")
        if cmd in seen:
            continue
        seen.add(cmd)
        c_name = cmd.lstrip("/").lower()
        candidates = [c_name] + [w.lower() for w in item.get("words", [])]
        dists = [levenshtein_distance(target, cand) for cand in candidates]
        best_d = min(dists) if dists else 999
        scored.append((best_d, cmd, item.get("help_te") or item.get("help_en") or ""))

    scored.sort(key=lambda x: x[0])
    return scored[:max_suggestions]


def format_unknown_suggestions(text: str, max_suggestions=3, commands=None) -> str:
    """Format 3 closest suggestions for unknown text instead of only 'Ardam kaaledu'."""
    suggs = find_suggestions(text, max_suggestions=max_suggestions, commands=commands)
    s_lines = [f"• {cmd} — {desc}" for _, cmd, desc in suggs]
    return (
        f"Meeru cheppindi ardam kaaledu: '{text[:50]}'.\n"
        f"Ivi try cheyandi:\n"
        + "\n".join(s_lines)
        + "\n\nMothaniki `/menu` ani pampithe commands anni kanipisthayi."
    )


def handle(text: str, ctx: dict) -> dict | None:
    """Plugin handle entry point."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    lower = raw.lower()

    # 1. Greetings: 'hi', 'hello', 'namaste', 'hii', 'em chestunnav'
    norm_greeting = re.sub(r"[^\w\s]", "", lower).strip()
    if norm_greeting in GREETING_WORDS:
        greeting_line = "Namaste andi! 🙏 Chilli Gavva robot ikkada."
        status_line = get_short_status_line(ctx)
        menu_text = format_grouped_menu()
        full_text = f"{greeting_line}\n{status_line}\n\n{menu_text}"
        return {"text": full_text}

    # 2. /menu or 'menu'
    if lower in ("/menu", "menu", "options", "commands"):
        return {"text": format_grouped_menu()}

    # 3. /help or /help <word> or help <word>
    # "sahayam" and "ela cheyali" are declared /help words; before they were not matched here and
    # ended in "Ardam kaaledu".
    help_match = re.match(r"^(?:/?help|sahayam|ela\s+cheyali)(?:\s+(.*))?$", raw.rstrip("?.! "), re.IGNORECASE)
    if help_match:
        target = (help_match.group(1) or "").strip()
        if not target:
            return {"text": format_grouped_menu()}
        matched, dist = find_command_match(target)
        if matched:
            cmd = matched.get("cmd", "")
            cat = matched.get("category", "General")
            help_te = matched.get("help_te", "")
            help_en = matched.get("help_en", "")
            words = ", ".join(matched.get("words", []))
            reply = [
                f"ℹ️ *Command Help: {cmd}*",
                f"Category: {cat}",
                f"Details (Telugu): {help_te}",
                f"Details (English): {help_en}",
                f"Keywords: {words}" if words else ""
            ]
            return {"text": "\n".join([r for r in reply if r]).strip()}
        else:
            suggs = find_suggestions(target, max_suggestions=3)
            s_lines = [f"• {cmd} ({desc})" for _, cmd, desc in suggs]
            return {
                "text": f"'{target}' command dorakaledu. Ivi try cheyandi:\n" + "\n".join(s_lines)
            }

    # 4. Check typo tolerance for single command words with edit distance 1-2
    # e.g. "/stats", "/pakcs", "/todey", "paks", "drafts"
    words = raw.split()
    if len(words) == 1 and len(words[0]) >= 3:
        word = words[0]
        matched, dist = find_command_match(word)
        if matched and 1 <= dist <= 2:
            cmd = matched.get("cmd", "")
            help_te = matched.get("help_te") or matched.get("help_en") or ""
            return {
                "text": f"Meeru '{cmd}' anukunnara? ({help_te})\nCommand execute cheyadaniki `{cmd}` ani pampandi, leda `/menu` choodandi."
            }

    # If the text starts with a slash or question word or typo word, or explicitly unknown fallback requested:
    if raw.startswith("/"):
        return {"text": format_unknown_suggestions(raw)}

    return None
