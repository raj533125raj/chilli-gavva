"""Version control helper for the agent: list, mark, and roll back.

Rollback never rewrites history. It makes a NEW commit that restores the
files from an older version, so the full audit trail stays intact.

Usage:
  python3 versions.py list
  python3 versions.py mark <name>                 # tag current HEAD as known-good-<name>
  python3 versions.py rollback <tag-or-commit>    # dry run: shows what would change
  python3 versions.py rollback <tag-or-commit> --apply
"""
import subprocess
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def git(*args, check=True):
    result = subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def list_versions():
    print(git("log", "--oneline", "--decorate", "-n", "30") or "no commits yet")


def mark(name):
    tag = f"known-good-{name}"
    git("tag", "-a", tag, "-m", f"Known good: {name}")
    print(f"tagged HEAD as {tag}")


def rollback(target, apply):
    git("rev-parse", "--verify", target + "^{commit}")  # fails early if unknown
    dirty = git("status", "--porcelain", check=False)
    if apply and dirty:
        raise SystemExit("Refusing to roll back: uncommitted changes exist. Commit or stash them first.")
    changes = git("diff", "--stat", "HEAD", target, check=False)
    if not changes:
        print(f"Nothing to change: current code already matches {target}.")
        return
    print(f"Files that would change to match {target}:\n{changes}")
    if not apply:
        print("\nDry run only. Re-run with --apply to create the rollback commit.")
        return
    # read-tree --reset makes index AND working tree exactly match the target,
    # including removing files that did not exist in that version.
    git("read-tree", "-u", "--reset", target)
    git("commit", "-m", f"Rollback to {target}")
    print(f"Rolled back: new commit created restoring {target}. History kept.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "list":
        list_versions()
    elif args[0] == "mark" and len(args) == 2:
        mark(args[1])
    elif args[0] == "rollback" and len(args) in (2, 3):
        rollback(args[1], apply="--apply" in args)
    else:
        print(__doc__)
