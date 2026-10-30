"""Reserve a daily send before contacting Telegram, independently of the state-save commit."""
import datetime
import os
from pathlib import Path

import guardrails


def claim(day, db_path=None, env=None, api=None):
    """At most one sender wins. An uncertain reservation always suppresses the send.

    CI atomically creates a date-only GitHub receipt tag using the existing workflow token.
    Local runs use an exclusive, fsynced receipt beside their database. Neither holds chat data.
    Receipts are retained even if Telegram fails: delivery may have succeeded before a timeout.
    """
    env = os.environ if env is None else env
    if datetime.date.fromisoformat(day).isoformat() != day:
        raise ValueError("Invalid daily report date")
    if api is not None or env.get("GITHUB_ACTIONS") == "true" or env.get("GITHUB_TOKEN"):
        try:
            if api is None:
                from autofix import GitHub
                api = GitHub(env)
            head = api.call("GET", "/git/ref/heads/main")
            reference = "refs/tags/robot-daily-report/" + day
            result = api.call("POST", "/git/refs", {"ref": reference, "sha": head["object"]["sha"]})
            return result.get("ref") == reference
        except Exception:
            # Existing tag, permissions error or ambiguous timeout: do not send.
            return False
    directory = Path(str(db_path or guardrails.DB_PATH) + ".daily-receipts")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / day).open("x", encoding="utf-8") as handle:
            handle.write(day + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Make the new directory entry durable before the outbound request, too.
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return True
    except OSError:
        return False
