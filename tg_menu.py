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
from __future__ import annotations
import glob
import importlib
import os
import re
import sys
import functools
import textwrap


def owner_reply_pages(text, next_step="/menu pampandi; command enchukondi.", limit=3400, allow_money=False):
    """Render private plugin output as short phone pages, keeping all content.

    Only presentation changes here: actions, state, files and keyboards belong
    to the original handler. Apply scrubbing before wrapping so a credential
    cannot be split into otherwise unrecognisable pieces.
    """
    import secret_guard
    from listing_fields import MONEY_PATTERNS

    clean = secret_guard.scrub_text(text)
    if not allow_money:
        for pattern in MONEY_PATTERNS:
            clean = pattern.sub("[amount]", clean)
    # Local machine paths are never useful on the owner's phone. Public URLs
    # and Telegram commands are deliberately left intact.
    clean = re.sub(r"(?<![\w:/])(?:~?/|[A-Za-z]:\\)(?:[^\s/\\]+[/\\])+[^\s,;)`]*",
                   "file vivaralu", clean)
    translations = {
        "None": "vivaralu levu", "True": "avunu", "False": "kaadu",
        "dict": "vivaralu", "traceback": "problem vivaralu",
        "send_document": "File pampadam", "download_file": "File techukovadam",
        "ctx": "setup", "not checked yet": "inka check cheyaledu",
        "counts only": "counts chudandi", "nothing auto-published": "Owner decide chestaru",
    }
    for old, new in translations.items():
        clean = re.sub(r"\b" + re.escape(old) + r"\b", new, clean, flags=re.I)
    clean = re.sub(r"\b\w*(?:Error|Exception)\b", "problem vachindi", clean)
    lines, steps = [], []
    for line in clean.splitlines():
        # Move existing next-step instructions to the final line. Preserve any
        # text before an inline marker, and keep every distinct action.
        marker = re.search(r"(?:^|(?<=[.!?])\s+)Next:\s*", line)
        if marker:
            before, after = line[:marker.start()], line[marker.end():]
            if before.strip():
                lines.append(before.strip())
            if after.strip() and after.strip() not in steps:
                steps.append(after.strip())
        elif line.strip():
            lines.append(line.strip())
    step = "Next: " + ("; ".join(steps) if steps else next_step)
    # Long copied listing fields stay complete; each emitted message has room
    # for its final action and stays below Telegram's character limit.
    step = " ".join(step.split())
    if len(step) > 600:
        lines.extend(textwrap.wrap(step[6:], width=500, break_long_words=True))
        step = "Next: " + next_step
    # Short cards stay one message. Pair neighbouring short rows rather than
    # separating the card heading from its status, links or final action.
    # Large tables still paginate instead of becoming an unreadable wall.
    if 14 < len(lines) <= 42 and len("\n".join(lines + [step])) <= limit:
        while len(lines) > 14:
            candidates = [(len(lines[i]) + len(lines[i + 1]), i)
                          for i in range(1, len(lines) - 1)
                          if len(lines[i]) + len(lines[i + 1]) < 500]
            if not candidates:
                break
            _, index = min(candidates)
            lines[index:index + 2] = [lines[index] + " · " + lines[index + 1]]
    pages, page = [], []
    for line in lines:
        for piece in textwrap.wrap(line, width=min(900, limit - len(step) - 1),
                                   break_long_words=True, break_on_hyphens=False) or [line]:
            if len(page) >= 14 or len("\n".join(page + [piece, step])) > limit:
                pages.append("\n".join(page + [step]))
                page = []
            page.append(piece)
    if page or not pages:
        pages.append("\n".join(page + [step]))
    return pages


def owner_reply(handler):
    """Keep all owned plugins' reply text and media captions phone friendly."""
    @functools.wraps(handler)
    def render(text, ctx):
        original = ctx if isinstance(ctx, dict) else {}
        view = dict(original)
        # Only the private sales ledger confirms owner-provided amounts. This
        # exemption never reaches public listings, files or other plugins.
        allow_money = handler.__module__ == "tg_sales"

        def pages_for(value, next_step, limit=3400):
            return owner_reply_pages(value, next_step, limit, allow_money=allow_money)
        command = str(text or "").strip().split()
        command = command[0].split("@")[0] if command else ""
        if not re.fullmatch(r"/[a-z][a-z0-9_]*", command):
            command = "/menu"
            # An alias has the same next action as its slash command. Derive
            # this from the handler's own declarations without redispatching
            # or running the command a second time.
            raw = str(text or "").strip().casefold()
            aliases = [(str(word).casefold(), entry.get("cmd", "/menu"))
                       for entry in handler.__globals__.get("COMMANDS", [])
                       for word in entry.get("words", [])]
            for alias, target in sorted(aliases, key=lambda pair: len(pair[0]), reverse=True):
                if raw == alias or raw.startswith(alias + " "):
                    command = target
                    break
        next_step = f"{command} pampandi; vivaralu chudandi."
        actions = {
            "/assets": "Photo attach chesi 'idi logo' leda 'idi icon' caption ivvandi.",
            "/asset": "/assets pampandi; kavalsina Asset number enchukondi.",
            "/dbcare": "Clean now leda Check copy button enchukondi.",
            "/decide": "Draft chusi Approve leda Reject button nokkandi.",
            "/drafts": "Draft chusi Approve leda Reject button nokkandi.",
            "/files": "/files <Pack number> pampandi; files techukondi.",
            "/ready": "/ready <Pack number> pampandi; fixes chudandi.",
            "/sell": "/sell <Pack number> pampandi; listing chusi meeru decide cheyandi.",
            "/listed": "/listed <Pack number> <Store link> pampandi; mee link save cheyandi.",
            "/sales": "/sales today pampandi; Sale counts chudandi.",
            "/settings": "Setting marchadaniki 'quiet off' leda 'morning 9' pampandi.",
            "/office": "Office change cheppandi; venakki velladaniki 'office change undo' pampandi.",
            "/launch": "/launch <Pack number> pampandi; ee roju pani chudandi.",
            "/talk": "Mee pani mamulu matallo cheppandi; udaharanaki 'pending drafts entha'.",
            "/reportpdf": "PDF open chesi ee vaaram counts chudandi.",
            "/kit": "/kit <Pack number> gumroad pampandi; Store fields techukondi.",
            "/seokit": "/seokit <Pack number> pampandi; keywords chudandi.",
        }
        next_step = actions.get(command, next_step)
        if handler.__module__ == "tg_menu" and command not in ("/help", "/menu"):
            next_step = "/menu pampandi; command enchukondi."
        if command in ("/menu", "/help"):
            next_step = "/help <command> pampandi; ela vadalo chudandi."

        def send_text(value, keyboard=None):
            pages = pages_for(value, next_step)
            result = None
            for index, page in enumerate(pages):
                # The real keyboard is preserved on the last page only.
                if keyboard is not None and index == len(pages) - 1:
                    result = original["send_text"](page, keyboard=keyboard)
                else:
                    result = original["send_text"](page)
            return result

        if callable(original.get("send_text")):
            view["send_text"] = send_text
        for name in ("send_document", "send_photo", "send_media_group"):
            callback = original.get(name)
            if callable(callback):
                def send_media(path, caption="", _callback=callback):
                    pages = pages_for(caption, next_step, limit=1000)
                    # Keep captions complete without exceeding Telegram's
                    # caption limit; overflow remains ordinary chat text.
                    result = _callback(path, caption=pages[0])
                    if callable(view.get("send_text")):
                        for page in pages[1:]:
                            view["send_text"](page)
                    return result
                view[name] = send_media
        result = handler(text, view)
        if isinstance(result, dict) and "text" in result:
            result = dict(result)
            pages = pages_for(result["text"], next_step)
            if len(pages) > 1 and callable(view.get("send_text")):
                for page in pages[:-1]:
                    view["send_text"](page)
            elif len(pages) > 1:
                # Callers without a transport still receive all the content.
                # Joining short rows keeps the final reply inside 15 lines.
                pages = [" | ".join(" ".join(p.splitlines()[:-1]) for p in pages)
                         + "\n" + pages[-1].splitlines()[-1]]
            result["text"] = pages[-1]
        return result
    return render

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

GROUP_ORDER = ["Sell", "Packs", "Reports", "Settings", "Drafts", "Posts"]

# Plugin commands declare their own category when they have one. Older plugins
# did not, so these names keep the owner-facing menu useful without touching
# every plugin just to add metadata.
CATEGORY_OVERRIDES = {
    "/sell": "Sell",
    "/ready": "Sell",
    "/preflight": "Sell",
    "/upload": "Sell",
    "/bundle": "Sell",
    "/office": "Sell",
    "/decide": "Sell",
    "/assets": "Packs",
    "/asset": "Packs",
    "/files": "Packs",
    "/covers": "Packs",
    "/pins": "Packs",
    "/carousel": "Packs",
    "/reel": "Packs",
    "/blog": "Packs",
    "/landing": "Packs",
    "/launch": "Packs",
    "/kit": "Packs",
    "/listed": "Packs",
    "/seokit": "Packs",
    "/versions": "Packs",
    "/lint": "Packs",
    "/faq": "Packs",
    "/boards": "Packs",
    "/ops": "Reports",
    "/dashboard": "Reports",
    "/seo": "Reports",
    "/dup": "Reports",
    "/keywords": "Reports",
    "/calendar": "Reports",
    "/ideas": "Reports",
    "/sales": "Reports",
    "/health": "Reports",
    "/logs": "Reports",
    "/reportpdf": "Reports",
    "/menu": "Settings",
    "/help": "Settings",
    "/pause": "Settings",
    "/resume": "Settings",
    "/rules": "Settings",
    "/cubes": "Settings",
    "/undo": "Settings",
    "/settings": "Settings",
    "/autoapprove": "Settings",
    "/dbcare": "Settings",
    "/talk": "Settings",
    "/post": "Posts",
    "/posted": "Posts",
}

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
                            entry_copy["category"] = CATEGORY_OVERRIDES.get(
                                cmd_clean, entry_copy.get("category", "Settings")
                            )
                            commands.append(entry_copy)
        except Exception:
            continue
    return commands


def _short_help(text: object, limit: int = 48) -> str:
    """Keep one-line menu entries inside Telegram's message limit."""
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def format_grouped_menu():
    """Build one grouped menu (Sell, Packs, Reports, Settings, plus owner shortcuts)."""
    commands = collect_all_commands()
    groups = {name: [] for name in GROUP_ORDER}

    for item in commands:
        cat = item.get("category", "Settings")
        if cat not in groups:
            groups[cat] = []
        cmd = item.get("cmd", "")
        help_te = item.get("help_te") or item.get("help_en") or ""
        groups[cat].append(f"{cmd} — {_short_help(help_te, 28)}")

    lines = ["📋 *Chilli Gavva Menu* (Mee robot commands):"]
    order = GROUP_ORDER + [cat for cat in groups if cat not in GROUP_ORDER]
    for cat in order:
        cmd_lines = groups.get(cat, [])
        if cmd_lines:
            lines.append(f"*{cat}*: " + " · ".join(cmd_lines))

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


@owner_reply
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
