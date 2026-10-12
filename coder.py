"""Coder agent: asks the LLM for a code change and turns the answer into a PATCH PROPOSAL.

Safety rules (enforced here, not by trusting the LLM):
  - It never edits files. It only writes a .diff proposal and puts it in the human approval queue.
  - Protected files (safety rails, the improver, the web server, the worker, CI, secrets) can never be touched.
  - No file deletions, no absolute or '..' paths, only .py/.md/.html/.js/.css files, size limits.
  - Existing tests cannot be modified (new test files are fine).
  - After a human approves a proposal, the worker verifies it in a throwaway copy (tests must pass).
  - Applying the patch is always done by the human: git apply proposals/proposal-<id>.diff

CLI:
  python3 coder.py request "goal text" file1.py [file2.py]   # queue a coding task for the Coder Worker
  python3 coder.py verify                                     # verify approved patches now
"""
import json
import os
import re
import subprocess
import sys

import guardrails
import improver

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROTECTED = {"guardrails.py", "improver.py", "serve.py", "coder.py", "worker_daemon.py", "start.py",
             "config.json", ".gitignore", "KILL_SWITCH", "worker.lock"}
PROTECTED_PREFIXES = (".github/", ".git/", "deploy/", "proposals/", "backups/")
ALLOWED_EXT = (".py", ".md", ".html", ".js", ".css")
MAX_PATCH_CHARS = 8000
MAX_CHANGED_LINES = 200
MAX_FILE_CHARS = 6000

PROMPT = ("You are a careful Python developer. Goal: {goal}\n\n"
          "Current files:\n{files}\n\n"
          "Reply with ONLY a unified diff (git format, paths like a/foo.py and b/foo.py). "
          "Smallest possible change. Python standard library only. Do not touch tests that already exist; "
          "you may add a new test file under tests/. No explanations.")


def _clean_path(p):
    p = p.strip().split("\t")[0]
    if p == "/dev/null":
        return p
    return re.sub(r"^[ab]/", "", p, count=1)


def path_problem(path):
    if path.startswith("/") or ".." in path.split("/") or "\\" in path or ":" in path:
        return f"unsafe path: {path}"
    if path in PROTECTED or path.startswith(PROTECTED_PREFIXES):
        return f"protected file: {path}"
    if not path.endswith(ALLOWED_EXT):
        return f"file type not allowed: {path}"
    return None


def strip_fences(text):
    text = (text or "").strip()
    m = re.search(r"```(?:diff|patch)?\n(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("diff --git")
    if start == -1:
        start = text.find("--- ")
    return (text[start:] if start != -1 else text).strip() + "\n"


def validate_patch(diff_text, source_dir=None):
    """Return (ok, reason). Pure checks on the diff text; nothing is applied."""
    source_dir = source_dir or BASE_DIR
    if not diff_text or "+++ " not in diff_text:
        return False, "not a unified diff"
    if len(diff_text) > MAX_PATCH_CHARS:
        return False, f"patch too large (> {MAX_PATCH_CHARS} chars)"
    old, new = None, None
    changed = 0
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            old = _clean_path(line[4:])
        elif line.startswith("+++ "):
            new = _clean_path(line[4:])
            if new == "/dev/null":
                return False, "deleting files is not allowed"
            problem = path_problem(new) or (path_problem(old) if old != "/dev/null" else None)
            if problem:
                return False, problem
            if old != "/dev/null" and new.startswith("tests/"):
                return False, f"existing tests cannot be modified: {new}"
            if old == "/dev/null" and os.path.exists(os.path.join(source_dir, new)):
                return False, f"file already exists: {new}"
        elif line.startswith(("rename ", "copy ", "deleted file", "old mode", "new mode", "Binary files", "GIT binary")):
            return False, "renames, mode changes and binary changes are not allowed"
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            changed += 1
    if new is None:
        return False, "no target file found"
    if changed > MAX_CHANGED_LINES:
        return False, f"too many changed lines ({changed} > {MAX_CHANGED_LINES})"
    return True, "ok"


def _read_files(files, source_dir):
    parts = []
    for name in files:
        problem = path_problem(name)
        if problem:
            raise ValueError(problem)
        with open(os.path.join(source_dir, name), encoding="utf-8") as f:
            parts.append(f"--- {name} ---\n{f.read()[:MAX_FILE_CHARS]}")
    return "\n\n".join(parts)


def propose(goal, files, llm, db_path=None, source_dir=None):
    """Ask the LLM for a patch, validate it, and queue it for human approval."""
    source_dir = source_dir or BASE_DIR
    try:
        context = _read_files(files, source_dir)
    except (ValueError, OSError) as exc:
        return {"ok": False, "why": str(exc)}
    answer = llm(PROMPT.format(goal=goal, files=context))
    if not answer:
        return {"ok": False, "why": "no LLM answer (no key, kill switch, or daily cap reached)"}
    diff = strip_fences(answer)
    ok, why = validate_patch(diff, source_dir)
    if not ok:
        return {"ok": False, "why": "rejected: " + why}
    check = subprocess.run(["git", "apply", "--check", "-"], input=diff, cwd=source_dir,
                           capture_output=True, text=True)
    if check.returncode != 0:
        return {"ok": False, "why": "rejected: patch does not apply: " + check.stderr.strip()[:200]}
    approval_id, path = improver.save_proposal(f"Code: {goal[:70]}", diff, db_path)
    return {"ok": True, "approval_id": approval_id, "path": path}


# ---- verification of approved patches -------------------------------------------
def _conn(db_path):
    conn = improver.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS code_verification ("
                 "approval_id INTEGER PRIMARY KEY, ts TEXT, ok INTEGER, why TEXT)")
    return conn


def verify_approved(db_path=None, verifier=None, limit=2):
    """Verify approved code patches that have not been checked yet. Returns the list of results."""
    import datetime
    verifier = verifier or (lambda aid: improver.verify_proposal(aid, db_path))
    conn = _conn(db_path)
    rows = conn.execute("SELECT id FROM approvals WHERE kind = 'code_patch' AND status = 'approved' "
                        "AND id NOT IN (SELECT approval_id FROM code_verification) ORDER BY id LIMIT ?",
                        (limit,)).fetchall()
    results = []
    for (aid,) in rows:
        res = verifier(aid)
        conn.execute("INSERT OR REPLACE INTO code_verification VALUES (?, ?, ?, ?)",
                     (aid, datetime.datetime.now().isoformat(), 1 if res.get("ok") else 0,
                      (res.get("why") or "")[:500] + (" | " + res["next"] if res.get("next") else "")))
        conn.commit()
        results.append((aid, res))
    conn.close()
    return results


def step_code_proposal(task, ctx):
    p = task["payload"]
    res = propose(p.get("goal", ""), p.get("files", []), ctx["llm"], ctx["db_path"])
    return res


def main(argv):
    cmd = argv[0] if argv else "help"
    if cmd == "request" and len(argv) >= 3:
        import workforce
        tid = workforce.enqueue("code_proposal", {"goal": argv[1], "files": argv[2:]})
        print(f"Queued coding task #{tid}. The worker will propose a patch for your approval.")
        return 0
    if cmd == "verify":
        for aid, res in verify_approved():
            print(aid, res)
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
