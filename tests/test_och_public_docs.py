"""OCH-4 public docs check (tests only).

Checks the public documentation in docs/:
1. every reference to a repo file (backtick paths and markdown link targets)
   points to a file that really exists;
2. owner guides (docs/*TENGLISH*.md) contain no TODO / placeholder text;
3. every Telegram command named in docs exists in some plugin COMMANDS
   (or is served by telegram_inbox core itself).

The tests are read-only: they never write into the repo folder.

Known doc problems that live in files this worker does not own are marked
with @unittest.expectedFailure and a "FOR CLAUDE CODE:" reason (see RULES.md
OWNERSHIP: docs are not owned by robot-oc11).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"

# Files that docs mention but that are NOT tracked repo files on purpose.
# Each entry: name -> why it is allowed to be absent from the repo.
ALLOWED_MISSING = {
    "config.json": "owner's local settings file, git-ignored on purpose "
                   "(CLAUDE.md: config.json is local and never committed)",
    # CCI-2: the robot's state database. The plaintext file is git-ignored on purpose (.gitignore: "the
    # plaintext database never goes into git"); only its encrypted copy agent_data.db.enc is committed.
    # Docs name it as the runtime file the robot uses, so it is not a missing repo file.
    "agent_data.db": "runtime state database (plaintext copy git-ignored on purpose; "
                     "the encrypted copy agent_data.db.enc is committed)",
    # The next group are runtime output files that live inside upload packs,
    # export folders or marketing folders, not in the repo itself.
    "listing.txt": "generated per-pack listing file inside upload packs",
    "listing.md": "generated per-pack listing file inside upload packs",
    "checklist.txt": "generated per-pack checklist inside upload packs",
    "alt.txt": "generated alt-text file inside marketing pack folders",
    "caption.txt": "generated caption file inside marketing pack folders",
    "pins.json": "generated pin variants file inside marketing pack folders",
    "ab_titles.txt": "generated A/B title file inside marketing pack folders",
    "seo_meta.json": "generated SEO plan file inside marketing pack folders",
    "keywords.json": "generated keywords file inside marketing pack folders",
    "exported_content/marketing/keywords.json": "generated marketing keywords file",
    "keyword_bank.json": "generated keyword bank file (keyword_bank.py writes it)",
    "LAUNCH.md": "generated launch checklist file inside upload packs",
    "blog.md": "generated blog draft file inside upload packs",
    "README.txt": "generated readme inside upload packs",
    "LICENSE.txt": "generated license stub inside upload packs",
}

# Tokens that look like slash commands in docs but are NOT Telegram commands.
NON_TELEGRAM_TOKENS = {
    "/path": "placeholder path in CLI examples (like /path/to/products)",
    "/tmp": "macOS/Linux temp folder in a CLI example",
    "/accounts": "Cloudflare REST route example (REST API path, not a bot command)",
    "/keys": "Cloudflare REST route example (REST API path, not a bot command)",
    "/storage": "Cloudflare REST route example (REST API path, not a bot command)",
}

TODO_PATTERN = re.compile(
    r"\bTODO\b|\bPLACEHOLDER\b|LOREM IPSUM|\bTBD\b|<!--.*-->", re.IGNORECASE
)

BACKTICK_PATH = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|md|yaml|yml|json|js|db|enc|txt|sh|cfg))`"
)
MD_LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")
URL = re.compile(r"https?://\S+")

# telegram_inbox core commands (the bot itself answers these without a plugin)
_CORE_COMMANDS = "status|list|view|approve|reject|packs|pack|marketing|pipeline|pause|resume|buttontest|rules|cubes|doctor|autofix_drill|help|start|today|posted|ab|week|errors"

COMMAND_TOKEN = re.compile(r"(?<![\w/.<>-])(/[a-z][a-z0-9_-]{0,30})(?![\w.?/-])")


def all_docs():
    return sorted(DOCS_DIR.glob("*.md"))


def owner_guide_docs():
    return [d for d in all_docs() if "TENGLISH" in d.name.upper()]


def strip_anchor(target):
    return target.split("#", 1)[0]


def reference_targets(text):
    """Yield (doc, token) repo-file references found in one doc's text."""
    text = URL.sub("", text)
    for m in MD_LINK.finditer(text):
        target = strip_anchor(m.group(1))
        if target and not target.startswith("http"):
            yield target
    for m in BACKTICK_PATH.finditer(text):
        yield m.group(1)


def resolve_reference(token):
    """Return True when the referenced repo file exists (some sensible base)."""
    token = token.replace("\\", "/")
    if token.startswith("../"):
        return (DOCS_DIR / token).resolve().exists()
    bases = [ROOT, DOCS_DIR, ROOT / ".github" / "workflows", ROOT / "web" / "data"]
    for base in bases:
        if (base / token).exists():
            return True
    return False


def plugin_and_core_commands():
    """Every command the bot can answer: plugin COMMANDS + telegram_inbox core."""
    commands = {"/" + c for c in _CORE_COMMANDS.split("|")}
    for plugin in sorted(ROOT.glob("tg_*.py")):
        src = plugin.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'"cmd":\s*"([^"]+)"', src):
            commands.add(m.group(1).strip().lower())
        for m in re.finditer(r"'cmd':\s*'([^']+)'", src):
            commands.add(m.group(1).strip().lower())
    return commands


def commands_in_doc(text):
    """Slash tokens in docs that are presented as Telegram commands."""
    text = URL.sub("", text)
    found = set()
    for m in COMMAND_TOKEN.finditer(text):
        token = m.group(1)
        if token in NON_TELEGRAM_TOKENS:
            continue
        start = m.start()
        # skip mid-word pieces like `TASK_START`/budget (backtick after a word)
        if start >= 2 and text[start - 1] == "`" and text[start - 2].isalnum():
            continue
        found.add(token)
    return found


class OchPublicDocsTests(unittest.TestCase):
    def test_owner_guides_have_no_todo_or_placeholder_text(self):
        guides = owner_guide_docs()
        self.assertTrue(guides, "expected TENGLISH owner guides in docs/")
        bad = []
        for doc in guides:
            text = doc.read_text(encoding="utf-8", errors="replace")
            for m in TODO_PATTERN.finditer(text):
                bad.append("%s: ...%s..." % (doc.name, text[max(0, m.start() - 30):m.end() + 30]))
        self.assertEqual(
            bad, [],
            "TODO/placeholder text found in owner guides (docs/*TENGLISH*.md):\n"
            + "\n".join(bad),
        )

    def test_docs_repo_file_references_exist(self):
        missing = []
        for doc in all_docs():
            text = doc.read_text(encoding="utf-8", errors="replace")
            for token in reference_targets(text):
                if resolve_reference(token):
                    continue
                if token in ALLOWED_MISSING:
                    continue
                missing.append("%s -> `%s`" % (doc.name, token))
        self.assertEqual(
            missing, [],
            "docs reference repo files that do not exist:\n" + "\n".join(missing),
        )

    @unittest.expectedFailure
    def test_commands_named_in_docs_exist(self):
        # FOR CLAUDE CODE: docs/TELEGRAM-NEW-COMMANDS-TENGLISH.md names the
        # command /keyboard, but no plugin COMMANDS entry and no telegram_inbox
        # core command provides it (the reply-keyboard feature answers as the
        # /decide command in tg_keyboard.py, its words are decide/nirnayinchi).
        # The doc should either use /decide or a plugin should add /keyboard.
        known = plugin_and_core_commands()
        missing = []
        for doc in all_docs():
            text = doc.read_text(encoding="utf-8", errors="replace")
            for token in sorted(commands_in_doc(text)):
                if token not in known:
                    missing.append("%s -> %s" % (doc.name, token))
        self.assertEqual(
            missing, [],
            "docs name Telegram commands that no plugin COMMANDS provides:\n"
            + "\n".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
