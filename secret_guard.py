"""secret_guard.py — stop secrets and private data from being saved to the repository.

Finds: API keys and tokens (Groq, Google, GitHub, OpenRouter/OpenAI, Telegram bot, Cloudflare-style),
private keys, password-like settings, Telegram chat ids, and personal email addresses. It looks inside
normal files AND inside the rows of SQLite databases (agent_data.db) and zip-based files (xlsx, docx, zip).

When the real secrets are available as environment variables (in the cloud run), it also looks for their
EXACT values, which catches a private chat id or a token in any shape. Values are only compared in memory.

It NEVER prints a secret: only the file (or database table), the line, and the kind of secret.

Usage:
    python3 secret_guard.py                 # scan the whole repository (exit 1 if anything is found)
    python3 secret_guard.py --save          # scan only what "Save state" commits
    python3 secret_guard.py path1 path2     # scan given files or folders
Known test-only fake values are listed by file and kind in .secret_guard_allow.
"""
import fnmatch
import os
import re
import sqlite3
import sys
import zipfile
from urllib.parse import quote, quote_plus

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALLOW_FILE = os.path.join(BASE_DIR, ".secret_guard_allow")
SAVE_PATHS = ["agent_data.db", "web/data/status.json", "exported_content", ".scheduler_locks"]
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
# .enc = the encrypted database (random bytes; the plaintext copy is scanned before it is encrypted).
BINARY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".webp", ".woff", ".woff2", ".ttf", ".enc")
ZIP_EXT = (".zip", ".xlsx", ".docx", ".pptx")
SECRET_ENV = ("GROQ_API_KEY", "GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_AUTOFIX_KEY", "OPENROUTER_API_KEY", "CF_API_TOKEN", "CF_ACCOUNT_ID",
              "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_TOKEN", "GITHUB_MODELS_TOKEN", "ALWAYS_ON_SECRET",
              "ALWAYS_ON_URL", "FREELLMAPI_KEY", "WORKER_SECRET", "NVIDIA_API_KEY", "DB_KEY",
              "CONTROL_SIGNING_KEY", "CF_KV_NAMESPACE_ID")

PATTERNS = [
    ("NVIDIA API key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{20,}\b")),
    ("Groq API key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("OpenRouter/OpenAI key", re.compile(r"\bsk-(?:or-v1-)?[A-Za-z0-9\-]{32,}")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password or secret setting", re.compile(
        r"(?i)\b(?:password|passwd|api[_-]?key|secret|token)\b[\"']?\s*[:=]\s*[\"']([^\"'\s]{12,})[\"']")),
    ("Telegram chat id", re.compile(r"(?i)\b(?:chat_id|telegram_chat_id)\b[\"']?\s*[:=]\s*[\"']?-?\d{6,}")),
]
EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
SAFE_EMAIL = re.compile(r"(?i)(noreply|no-reply|example\.(com|org)|\.invalid$|@test\.|@localhost|"
                        r"users\.noreply\.github\.com|@anthropic\.com$|@email\.com$|@domain\.com$|@x$|@t$)")


def scrub_text(text, replacement="[hidden]", env=None):
    """Shared scrubber for diagnostics, stored text and provider prompts. Never logs values."""
    env = os.environ if env is None else env
    out = str(text) if text is not None else ""
    values = set()
    for name, value in env.items():
        if value and (name in SECRET_ENV or re.search(
                r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|ACCOUNT|NAMESPACE|CHAT_ID", name, re.I)):
            # Also cover URL-encoded credentials in request errors.
            values.update((value, quote(value, safe=""), quote_plus(value, safe="")))
    for value in sorted(values, key=len, reverse=True):
        out = out.replace(value, replacement)
    out = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                 replacement, out, flags=re.S)
    # Remove arbitrary query keys as well as recognizable provider key formats.
    out = re.sub(r"(?i)((?:[?&]|&amp;)(?:key|api[_-]?key|token|access_token)=)[^\s&#\"'<>\\]+",
                 lambda m: m.group(1) + replacement, out)
    for _, rx in PATTERNS:
        out = rx.sub(replacement, out)
    out = re.sub(r"(?i)(\bAuthorization\s*[:=]\s*(?:Bearer|Basic)\s+)[^\s\"'<>]+",
                 lambda m: m.group(1) + replacement, out)
    return EMAIL.sub(lambda m: m.group(0) if SAFE_EMAIL.search(m.group(0)) else "name@example.com", out)


def clean_ai_text(text):
    """Make AI-written text safe to store: sample emails become name@example.com and anything shaped like
    a key, token or private key is removed. Used on every AI answer before it is saved."""
    if not text:
        return text
    out = EMAIL.sub(lambda m: m.group(0) if SAFE_EMAIL.search(m.group(0)) else "name@example.com", text)
    for kind, rx in PATTERNS:
        if kind not in ("password or secret setting", "Telegram chat id"):
            out = rx.sub("[removed]", out)
    return out


def _env_secrets():
    out = []
    for name in SECRET_ENV:
        value = os.environ.get(name, "").strip()
        if len(value) >= 6:
            out.append((f"exact value of {name}", value))
    return out


def scan_text(text, env_secrets=None):
    """Return [(kind, line_no)] for one text. No values."""
    found = []
    env_secrets = _env_secrets() if env_secrets is None else env_secrets
    for no, line in enumerate(text.splitlines(), 1):
        for kind, rx in PATTERNS:
            matches = list(rx.finditer(line))
            if kind == "password or secret setting":
                matches = [match for match in matches
                           if not re.fullmatch(r"\{env:[A-Z][A-Z0-9_]*\}", match.group(1))]
            if matches:
                found.append((kind, no))
        for m in EMAIL.finditer(line):
            if not SAFE_EMAIL.search(m.group(0)):
                found.append(("personal email", no))
                break
        for kind, value in env_secrets:
            if re.search(r"(?<![0-9A-Za-z])" + re.escape(value) + r"(?![0-9A-Za-z])", line):
                found.append((kind, no))
    return found


def _scan_db(path, env_secrets):
    found = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            for row_no, row in enumerate(conn.execute(f'SELECT * FROM "{table}"'), 1):
                text = "\n".join(str(v) for v in row if isinstance(v, (str, int)))
                for kind, _ in scan_text(text, env_secrets):
                    found.append((kind, f"table {table}, row {row_no}"))
        conn.close()
    except sqlite3.Error:
        found.append(("unreadable database", "-"))
    return found


def _scan_zip(path, env_secrets):
    found = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith((".xml", ".md", ".csv", ".txt", ".json", ".rels")):
                    for kind, no in scan_text(z.read(name).decode("utf-8", "ignore"), env_secrets):
                        found.append((kind, f"{name} line {no}"))
    except (zipfile.BadZipFile, OSError):
        pass
    return found


def scan_file(path, env_secrets=None):
    """Return [(kind, where)] for one file."""
    env_secrets = _env_secrets() if env_secrets is None else env_secrets
    lower = path.lower()
    if lower.endswith(BINARY_EXT):
        return []
    if lower.endswith(".db") or lower.endswith(".sqlite"):
        return _scan_db(path, env_secrets)
    if lower.endswith(ZIP_EXT):
        return _scan_zip(path, env_secrets)
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return []
    return [(kind, f"line {no}") for kind, no in scan_text(text, env_secrets)]


def _walk(paths, root):
    for p in paths:
        full = os.path.join(root, p)
        if os.path.isdir(full):
            for d, dirs, files in os.walk(full):
                dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
                for name in files:
                    yield os.path.relpath(os.path.join(d, name), root)
        elif os.path.exists(full):
            yield os.path.relpath(full, root)


def load_allow(path=ALLOW_FILE):
    rules = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "|" in line:
                    pattern, kind = (x.strip() for x in line.split("|", 1))
                    rules.append((pattern, kind))
    except OSError:
        pass
    return rules


def _allowed(rel, kind, rules):
    return any(fnmatch.fnmatch(rel, pattern) and (k == "*" or k == kind) for pattern, k in rules
               if not kind.startswith("exact value"))   # a real secret value is never allowed


def scan(paths=None, root=None, allow=None):
    """Return [(file, where, kind)] that are NOT allowed."""
    root = root or BASE_DIR
    allow = load_allow(os.path.join(root, ".secret_guard_allow")) if allow is None else allow
    env_secrets = _env_secrets()
    out = []
    for rel in sorted(set(_walk(paths or ["."], root))):
        if rel.startswith(".git" + os.sep) or rel == ".secret_guard_allow":
            continue
        for kind, where in scan_file(os.path.join(root, rel), env_secrets):
            if not _allowed(rel, kind, allow):
                out.append((rel, where, kind))
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    paths = SAVE_PATHS if argv[:1] == ["--save"] else (argv or None)
    findings = scan(paths)
    if not findings:
        print("Secret guard: clean (no tokens, keys, passwords, chat ids or personal emails found).")
        return 0
    print(f"Secret guard: BLOCKED - {len(findings)} possible secret(s). Values are not shown.")
    for rel, where, kind in findings[:50]:
        print(f"  {rel} ({where}): {kind}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
