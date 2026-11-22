"""state_guard.py — start-of-run checks for two quiet bad-day failures (CCE-3).

1. Half-written state file: a run killed while writing leaves a JSON state file cut in the middle. Every
   reader treats that as "no data" in its own way and nobody tells the owner. Here the damaged file is removed
   (a missing file is the normal fresh-install state: every reader handles it and the writers make a new one)
   and the owner gets ONE Tenglish line.
2. Clock jump: the time of the last run is kept in the DB. A clock that went back, or a gap of a day or more
   (clock jumped forward, or the robot really did not run), gives ONE Tenglish line; the robot keeps working.

Never raises: a problem inside a check is printed and the run goes on.
"""
import json
import os
import time
from pathlib import Path
import post_planner

ROOT = Path(__file__).resolve().parent

# JSON state files the robot rewrites itself (path relative to the robot folder).
STATE_FILES = (
    post_planner.PLAN_PATH,
    "exported_content/marketing/dup_report.json",
    "web/data/status.json",
)

DAMAGED_STATE_TEXT = ("Robot state file sagam matrame raasi undi ({names}) — mundu run madhyalo aagipoyindi. "
                      "Paadaina file teesesanu, robot kotha file malli tayaru chestundi. Meeru emi cheyakkarledu.")
CLOCK_BACK_TEXT = ("Robot clock venakki velindi (sumaru {hours} ganta(lu)). Robot mamulu ga pani chestondi; "
                   "daily report, build slots konchem late ga ravachu. Meeru emi cheyakkarledu.")
CLOCK_GAP_TEXT = ("Robot sumaru {days} roju(lu) run avvaledu (leda clock munduku jump ayyindi). Ippudu malli pani "
                  "chestondi; aagina pani next runs lo chestundi. Idi malli malli vaste alarm clock / GitHub "
                  "Actions check cheyandi.")

CLOCK_KEY = "state_guard_last_run"
BACK_SECONDS = 10 * 60          # smaller steps back are normal clock corrections
GAP_SECONDS = 24 * 3600         # the robot runs many times a day


def damaged_json(path):
    """True when the file exists but is empty or not complete JSON."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return False
    try:
        json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return True
    return False


def _send(text, send):
    try:
        if send is None:
            import notify
            send = notify.send
        return bool(send(text))
    except Exception as exc:
        print(f"State guard: owner message not sent ({type(exc).__name__}).")
        return False


def check_state_files(root=None, send=None, files=None):
    """Remove half-written state files, tell the owner once. Returns the names removed."""
    root = Path(root) if root else ROOT
    if files is None:
        files = (post_planner.PLAN_PATH, *STATE_FILES[1:])
    removed = []
    for rel in files:
        path = root / rel
        if not damaged_json(path):
            continue
        try:
            os.remove(path)
        except OSError as exc:
            print(f"State guard: could not remove damaged {rel} ({type(exc).__name__}).")
            continue
        removed.append(os.path.basename(rel))
        print(f"State guard: {rel} was half-written; removed, it will be made again.")
    if removed:
        _send(DAMAGED_STATE_TEXT.format(names=", ".join(removed)), send)
    return removed


def _state(db_path):
    """get/put on the robot's small key-value table (the same table telegram_inbox keeps its state in)."""
    import guardrails

    def conn():
        db = guardrails.connect(db_path)
        db.execute("CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT)")
        return db

    def get():
        db = conn()
        try:
            row = db.execute("SELECT value FROM telegram_state WHERE key = ?", (CLOCK_KEY,)).fetchone()
        finally:
            db.close()
        return row[0] if row else None

    def put(value):
        db = conn()
        try:
            db.execute("INSERT INTO telegram_state (key, value) VALUES (?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (CLOCK_KEY, str(value)))
            db.commit()
        finally:
            db.close()

    return get, put


def check_clock(db_path=None, now=None, send=None):
    """Compare now with the last run's time. Returns 'back', 'gap' or None. The new time is always stored,
    so one jump gives one line."""
    now = time.time() if now is None else float(now)
    get, put = _state(db_path)
    try:
        last = float(get() or 0)
    except (TypeError, ValueError):
        last = 0.0
    put(repr(now))
    if not last:
        return None
    if now < last - BACK_SECONDS:
        hours = max(1, int(round((last - now) / 3600.0)))
        print(f"State guard: clock went back about {hours} hour(s).")
        _send(CLOCK_BACK_TEXT.format(hours=hours), send)
        return "back"
    if now - last >= GAP_SECONDS:
        days = max(1, int(round((now - last) / 86400.0)))
        print(f"State guard: about {days} day(s) since the last run.")
        _send(CLOCK_GAP_TEXT.format(days=days), send)
        return "gap"
    return None


def run(db_path=None, root=None, now=None, send=None):
    """Both checks; never raises."""
    result = {"removed": [], "clock": None}
    try:
        result["removed"] = check_state_files(root=root, send=send)
    except Exception as exc:
        print(f"State guard file check skipped: {type(exc).__name__}")
    try:
        result["clock"] = check_clock(db_path=db_path, now=now, send=send)
    except Exception as exc:
        print(f"State guard clock check skipped: {type(exc).__name__}")
    return result


if __name__ == "__main__":
    print(run())
