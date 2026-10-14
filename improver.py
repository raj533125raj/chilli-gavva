"""Safe self-improvement for the agent team.

What may change by itself: PROMPT TEXT only, tracked in the database with versions.
  - Variants are A/B tested (mostly champion, sometimes challenger).
  - Quality = reviewer score mixed with the human approval rate.
  - A challenger is promoted only after enough trials and a clear win.
  - One call reverts to the previous champion.

What may NOT change by itself: Python code. Code changes are written as patch
PROPOSALS, verified in a throwaway copy (tests must pass), and then left for a
human to apply. LLM-generated code is never executed outside that test run.
"""
import datetime
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import guardrails

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROPOSAL_DIR = os.path.join(BASE_DIR, "proposals")

# The draft OUTPUT FORMAT (product spec + store listing sections) is fixed in workforce.DRAFT_FORMAT
# and added after these templates, so evolved prompts can change the wording but never the format.
DEFAULT_PROMPTS = {
    "research": ("Research notes for this digital product: {topic}. As short, honest bullet points list: "
                 "who buys it and why; what it must contain (tabs, pages or fields); 5-8 search phrases "
                 "buyers type on Etsy, Payhip or Gumroad; a typical price range in USD (say unknown if unsure); "
                 "and claims to avoid. No made-up statistics, no income promises."),
    "draft": ("Write the product spec and store listing for this digital product: {topic}. "
              "Make every part concrete and useful for buyers anywhere in the world."),
}
# Older built-in prompts from the landing-page era. A version that is, or grew from, one of these
# is retired on start-up so the defaults above are used. Rows and history stay in the database.
LEGACY_PROMPTS = {
    "research": ["List 5 concrete, honest ideas for: {topic}. One line each. No income promises."],
    "draft": ["Write a short landing-page draft for: {topic}. Include a clear title, 3 benefit lines, "
              "and one call to action. Do not promise income or guaranteed results."],
}

MUTATIONS = [
    "Use short, plain sentences a busy buyer can scan.",
    "Name every tab, page or field in WHAT'S INSIDE.",
    "Write for buyers worldwide: prices in USD, no local slang.",
    "Keep the listing description under 200 words.",
    "Make the 5 search tags phrases that real buyers would type.",
]
MAX_VARIANTS = 12

MIN_TRIALS = 5
WIN_MARGIN = 5.0          # challenger must beat champion by this many quality points
CHALLENGER_SHARE = 0.2    # share of trials given to a challenger


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS prompt_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, text TEXT, active INTEGER DEFAULT 0,
        trials INTEGER DEFAULT 0, score_sum REAL DEFAULT 0, decisions INTEGER DEFAULT 0,
        approvals INTEGER DEFAULT 0, created_ts TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS prompt_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, from_version INTEGER,
        to_version INTEGER, ts TEXT, reason TEXT)""")
    if "retired" not in [r[1] for r in conn.execute("PRAGMA table_info(prompt_versions)").fetchall()]:
        conn.execute("ALTER TABLE prompt_versions ADD COLUMN retired INTEGER DEFAULT 0")
    return conn


def is_legacy(name, text):
    """True for an old built-in prompt or a variant that grew from one (landing-page era)."""
    text = text or ""
    if name == "draft" and ("landing-page" in text.lower() or "landing page" in text.lower()):
        return True
    return any(text == old or text.startswith(old + " ") for old in LEGACY_PROMPTS.get(name, []))


def _retire_legacy(conn, name, new_text):
    """Move a database that still uses an old built-in prompt onto the new default. Nothing is deleted:
    old versions are marked retired (never trialled or promoted again) and the switch is logged."""
    rows = conn.execute("SELECT id, text, active FROM prompt_versions WHERE name = ? AND retired = 0",
                        (name,)).fetchall()
    legacy = [(vid, active) for vid, text, active in rows if is_legacy(name, text)]
    if not legacy:
        return
    now = datetime.datetime.now().isoformat()
    for vid, _ in legacy:
        conn.execute("UPDATE prompt_versions SET retired = 1, active = 0 WHERE id = ?", (vid,))
    was_active = next((vid for vid, active in legacy if active), None)
    if was_active is None:
        return
    row = conn.execute("SELECT id FROM prompt_versions WHERE name = ? AND text = ?", (name, new_text)).fetchone()
    if row:
        new_id = row[0]
        conn.execute("UPDATE prompt_versions SET active = 1, retired = 0 WHERE id = ?", (new_id,))
    else:
        new_id = conn.execute("INSERT INTO prompt_versions (name, text, active, created_ts) VALUES (?, ?, 1, ?)",
                              (name, new_text, now)).lastrowid
    conn.execute("INSERT INTO prompt_history (name, from_version, to_version, ts, reason) VALUES (?, ?, ?, ?, ?)",
                 (name, was_active, new_id, now, "new default: product spec + store listing format"))


def ensure_defaults(db_path=None):
    conn = connect(db_path)
    for name, text in DEFAULT_PROMPTS.items():
        if not conn.execute("SELECT 1 FROM prompt_versions WHERE name = ?", (name,)).fetchone():
            conn.execute("INSERT INTO prompt_versions (name, text, active, created_ts) VALUES (?, ?, 1, ?)",
                         (name, text, datetime.datetime.now().isoformat()))
        else:
            _retire_legacy(conn, name, text)
    conn.commit()
    conn.close()


def get_active(name, db_path=None):
    ensure_defaults(db_path)
    conn = connect(db_path)
    row = conn.execute("SELECT id, text FROM prompt_versions WHERE name = ? AND active = 1", (name,)).fetchone()
    conn.close()
    return row


def add_variant(name, text, db_path=None):
    ensure_defaults(db_path)
    conn = connect(db_path)
    cur = conn.execute("INSERT INTO prompt_versions (name, text, active, created_ts) VALUES (?, ?, 0, ?)",
                       (name, text, datetime.datetime.now().isoformat()))
    conn.commit()
    vid = cur.lastrowid
    conn.close()
    return vid


def pick_for_trial(name, rng=random, db_path=None):
    """A/B choice: usually the champion, sometimes an under-tested challenger."""
    champion = get_active(name, db_path)
    conn = connect(db_path)
    challengers = conn.execute("SELECT id, text FROM prompt_versions WHERE name = ? AND active = 0 "
                               "AND retired = 0 AND trials < ? ORDER BY id", (name, MIN_TRIALS)).fetchall()
    conn.close()
    if challengers and rng.random() < CHALLENGER_SHARE:
        return challengers[0]
    return champion


def record_result(version_id, review_score, db_path=None):
    conn = connect(db_path)
    conn.execute("UPDATE prompt_versions SET trials = trials + 1, score_sum = score_sum + ? WHERE id = ?",
                 (float(review_score), version_id))
    conn.commit()
    conn.close()


def record_human_decision(version_id, approved, db_path=None):
    conn = connect(db_path)
    conn.execute("UPDATE prompt_versions SET decisions = decisions + 1, approvals = approvals + ? WHERE id = ?",
                 (1 if approved else 0, version_id))
    conn.commit()
    conn.close()


def quality(row):
    """row = (trials, score_sum, decisions, approvals). 0..100. Humans count when there are 3+ decisions."""
    trials, score_sum, decisions, approvals = row
    avg_review = (score_sum / trials) if trials else 0.0
    if decisions >= 3:
        return 0.5 * avg_review + 0.5 * (100.0 * approvals / decisions)
    return avg_review


def _stats(conn, version_id):
    return conn.execute("SELECT trials, score_sum, decisions, approvals FROM prompt_versions WHERE id = ?",
                        (version_id,)).fetchone()


def promote_if_better(name, db_path=None):
    """Promote the best well-tested challenger if it clearly beats the champion."""
    champion = get_active(name, db_path)
    conn = connect(db_path)
    champ_q = quality(_stats(conn, champion[0]))
    best = None
    for (vid,) in conn.execute("SELECT id FROM prompt_versions WHERE name = ? AND active = 0 AND retired = 0 "
                               "AND trials >= ?", (name, MIN_TRIALS)).fetchall():
        q = quality(_stats(conn, vid))
        if q >= champ_q + WIN_MARGIN and (best is None or q > best[1]):
            best = (vid, q)
    if not best:
        conn.close()
        return None
    conn.execute("UPDATE prompt_versions SET active = 0 WHERE id = ?", (champion[0],))
    conn.execute("UPDATE prompt_versions SET active = 1 WHERE id = ?", (best[0],))
    conn.execute("INSERT INTO prompt_history (name, from_version, to_version, ts, reason) VALUES (?, ?, ?, ?, ?)",
                 (name, champion[0], best[0], datetime.datetime.now().isoformat(),
                  f"challenger quality {best[1]:.1f} beat champion {champ_q:.1f}"))
    conn.commit()
    conn.close()
    return {"from": champion[0], "to": best[0], "quality": best[1], "champion_quality": champ_q}


def revert(name, db_path=None):
    """One call back to the previous champion."""
    conn = connect(db_path)
    row = conn.execute("SELECT id, from_version, to_version FROM prompt_history WHERE name = ? "
                       "ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    if not row:
        conn.close()
        return None
    _, previous, current = row
    conn.execute("UPDATE prompt_versions SET active = 0 WHERE id = ?", (current,))
    conn.execute("UPDATE prompt_versions SET active = 1 WHERE id = ?", (previous,))
    conn.execute("INSERT INTO prompt_history (name, from_version, to_version, ts, reason) VALUES (?, ?, ?, ?, ?)",
                 (name, current, previous, datetime.datetime.now().isoformat(), "manual revert"))
    conn.commit()
    conn.close()
    return {"active": previous}


def _valid_template(text):
    """A prompt template must use {topic} and nothing else in braces."""
    if not text or "{topic}" not in text:
        return False
    try:
        text.format(topic="x")
        return True
    except (KeyError, IndexError, ValueError):
        return False


def evolve(name, db_path=None, llm=None):
    """Create ONE new challenger prompt when none is waiting for trials.

    With an LLM: ask it to improve the champion. Without: apply the next plain-language
    tweak from MUTATIONS. Returns the new version id, or None if nothing was added.
    """
    champion = get_active(name, db_path)
    conn = connect(db_path)
    waiting = conn.execute("SELECT COUNT(*) FROM prompt_versions WHERE name = ? AND active = 0 AND retired = 0 "
                           "AND trials < ?", (name, MIN_TRIALS)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM prompt_versions WHERE name = ? AND retired = 0",
                         (name,)).fetchone()[0]
    existing = [r[0] for r in conn.execute("SELECT text FROM prompt_versions WHERE name = ?", (name,))]
    conn.close()
    if waiting or total >= MAX_VARIANTS:
        return None
    text = None
    if llm:
        text = llm("Improve this prompt template so the result is clearer and more honest. Keep the "
                   "placeholder {topic} exactly once and use no other curly braces. Return only the new template.\n\n"
                   + champion[1])
        if text:
            text = text.strip()
        if not _valid_template(text) or text in existing:
            text = None
    if not text:
        for tweak in MUTATIONS:
            candidate = champion[1] + " " + tweak
            if candidate not in existing and tweak not in champion[1]:
                text = candidate
                break
    if not text:
        return None
    return add_variant(name, text, db_path)


# ---- code change proposals (never auto-applied) ---------------------------------
def save_proposal(title, diff_text, db_path=None):
    os.makedirs(PROPOSAL_DIR, exist_ok=True)
    approval_id = guardrails.request_approval("code_patch", title, diff_text, db_path=db_path)
    path = os.path.join(PROPOSAL_DIR, f"proposal-{approval_id}.diff")
    with open(path, "w", encoding="utf-8") as f:
        f.write(diff_text)
    return approval_id, path


def verify_proposal(approval_id, db_path=None, source_dir=None, proposal_dir=None):
    """Apply the patch to a throwaway copy and run the tests there. The real tree is never touched."""
    source_dir = source_dir or BASE_DIR
    proposal_dir = proposal_dir or PROPOSAL_DIR
    approval = guardrails.get_approval(approval_id, db_path)
    if not approval or approval["kind"] != "code_patch":
        return {"ok": False, "why": "no such code patch proposal"}
    if approval["status"] != "approved":
        return {"ok": False, "why": "proposal is not approved by a human yet"}
    patch_path = os.path.join(proposal_dir, f"proposal-{approval_id}.diff")
    if not os.path.exists(patch_path):
        return {"ok": False, "why": "patch file missing"}
    tmp = tempfile.mkdtemp(prefix="verify_")
    try:
        work = os.path.join(tmp, "copy")
        shutil.copytree(source_dir, work,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.db", "KILL_SWITCH", "config.json"))
        applied = subprocess.run(["git", "apply", "--check", patch_path], cwd=work, capture_output=True, text=True)
        if applied.returncode != 0:
            return {"ok": False, "why": "patch does not apply: " + applied.stderr.strip()[:300]}
        subprocess.run(["git", "apply", patch_path], cwd=work, check=True, capture_output=True, text=True)
        tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                               cwd=work, capture_output=True, text=True, timeout=300)
        ok = tests.returncode == 0
        return {"ok": ok, "why": "tests passed" if ok else "tests failed: " + tests.stderr.strip()[-400:],
                "next": f"Review {patch_path}, then apply it yourself: git apply {patch_path}" if ok else None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        ensure_defaults()
        conn = connect()
        for row in conn.execute("SELECT id, name, active, trials, score_sum, decisions, approvals "
                                "FROM prompt_versions ORDER BY name, id"):
            vid, name, active, trials, ssum, dec, appr = row
            print(f"v{vid} {name:9s} {'ACTIVE ' if active else 'variant'} trials={trials} "
                  f"quality={quality((trials, ssum, dec, appr)):.1f}")
        conn.close()
    elif cmd == "promote" and len(argv) == 2:
        print(promote_if_better(argv[1]) or "no challenger is clearly better yet")
    elif cmd == "revert" and len(argv) == 2:
        print(revert(argv[1]) or "nothing to revert")
    elif cmd == "verify" and len(argv) == 2:
        print(verify_proposal(int(argv[1])))
    else:
        print("usage: improver.py status | promote <name> | revert <name> | verify <approval-id>")


if __name__ == "__main__":
    main(sys.argv[1:])
