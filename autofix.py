"""Auto-fix requests contain only the robot's safe failure vocabulary."""
import os
from pathlib import Path
import re
import sys
import uuid

import run_failure


def safe_summary(raw):
    lines = str(raw).splitlines()
    step = next((line[6:] for line in lines if line.startswith("Step: ")
                 and line[6:] in run_failure.STEPS.values()), run_failure.STEPS["workflow"])
    detail = next((line[9:] for line in lines if line.startswith("Problem: ")), run_failure.UNKNOWN)
    if detail.startswith("Failed tests: "):
        names = run_failure.safe_test_names(detail[14:].split(", "))
        detail = "Failed tests: " + ", ".join(names) if names else run_failure.UNKNOWN
    elif detail not in {*run_failure.ERRORS.values(), run_failure.PENDING, run_failure.UNKNOWN,
                        run_failure.KEY_MISSING, "A command could not complete its work."}:
        detail = run_failure.UNKNOWN
    return f"Step: {step}\nProblem: {detail}"


def build_prompt(raw, run_id=""):
    summary = safe_summary(raw)
    step = summary.splitlines()[0][6:]
    marker = f"Robot auto-fix run: {run_id}" if re.fullmatch(r"[0-9]+", run_id) else "Robot auto-fix"
    return (summary + "\n\nFind the real cause, fix it, and add a regression test. "
            "Run python3 -m unittest discover -s tests until it ends OK. "
            f'Open a PR titled "Auto-fix: {step}" targeting main. '
            f'Include this exact line in its body: "{marker}". '
            "Never touch .github/workflows, secrets, encrypted files, or private products. "
            "Do not read credentials or print environment variables. Never print or log secrets. "
            "Do not include money amounts or product ideas in code, comments, messages, or PR text. "
            "If a password, token, key, or login is requested, stop. "
            "Do not merge the PR; the owner must approve it in Telegram.")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["prompt"]:
        return 2
    prompt = build_prompt(os.environ.get("FAILURE_SUMMARY", ""), os.environ.get("GITHUB_RUN_ID", ""))
    delimiter = "robot_" + uuid.uuid4().hex
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"prompt<<{delimiter}\n{prompt}\n{delimiter}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
