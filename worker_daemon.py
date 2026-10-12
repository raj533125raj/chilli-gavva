"""Always-on worker: keeps picking up queued work automatically.

  python3 worker_daemon.py            # run until Ctrl+C
  python3 worker_daemon.py --once     # one pass, then exit (good for cron)

Every pass it: honours the kill switch, recovers tasks stuck after a crash, makes the
daily plan (once per day), runs a few tasks, refreshes the dashboard snapshot, and writes a
heartbeat so the dashboard can show whether the worker is alive. Only one worker can run at
a time (lock file). When the queue is empty it slows down to save resources.
"""
import json
import os
import signal
import sys
import time

import coder
import export_status
import guardrails
import improver
import maintenance
import site_builder
import workforce

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_PATH = os.path.join(BASE_DIR, "worker.lock")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_INTERVAL = 30
MAX_IDLE_INTERVAL = 120


def interval_from_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return max(5, int(json.load(f).get("worker_interval_seconds", DEFAULT_INTERVAL)))
    except Exception:
        return DEFAULT_INTERVAL


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_lock(path=LOCK_PATH):
    """Return True if this process now owns the lock. A lock left by a dead process is taken over."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if pid_alive(int(f.read().strip())):
                    return False
        except ValueError:
            pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock(path=LOCK_PATH):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def one_pass(db_path=None, write_snapshot=True):
    """One worker pass. Returns (result dict, did_work)."""
    if guardrails.is_killed():
        workforce.heartbeat("paused: kill switch is ON", db_path)
        result = {"status": "killed", "ran": 0, "failed": 0}
    else:
        workforce.ensure_daily_plan(db_path)
        result = workforce.run_once(max_tasks=3, db_path=db_path)
        notes = []
        promoted = improver.promote_if_better("draft", db_path)
        if promoted:
            notes.append(f"promoted prompt v{promoted['to']}")
        added = improver.evolve("draft", db_path, llm=workforce.default_llm(db_path))
        if added:
            notes.append(f"new challenger v{added}")
        if db_path is None:     # the real project only; tests pass a temp database and must not write site/
            built = site_builder.build()
            if built:
                notes.append(f"built {len(built)} page(s) in site/")
        for aid, res in coder.verify_approved(db_path):
            notes.append(f"patch #{aid} {'passes' if res.get('ok') else 'FAILS'} tests")
        cleaned = maintenance.run_if_due(db_path)
        if cleaned:
            notes.append("maintenance done" if "stopped" not in cleaned else "maintenance STOPPED: integrity check failed")
        workforce.heartbeat(f"ok: ran {result['ran']}, failed {result['failed']}"
                            + ("; " + ", ".join(notes) if notes else ""), db_path)
    if write_snapshot:
        export_status.write_status(db_path=db_path)
    return result, bool(result.get("ran") or result.get("failed"))


def loop(db_path=None, interval=None, max_loops=None, sleep=time.sleep, should_stop=lambda: False,
         write_snapshot=True):
    interval = interval or interval_from_config()
    delay, loops = interval, 0
    while not should_stop() and (max_loops is None or loops < max_loops):
        loops += 1
        try:
            _, did_work = one_pass(db_path, write_snapshot)
            delay = interval if did_work else min(MAX_IDLE_INTERVAL, max(interval, delay * 1.5))
        except Exception as exc:                      # never let the worker die on one bad pass
            workforce.heartbeat(f"error: {str(exc)[:120]}", db_path)
            delay = min(MAX_IDLE_INTERVAL, max(interval, delay * 2))
        if max_loops is None or loops < max_loops:
            sleep(delay)
    return loops


def main(argv):
    if "--once" in argv:
        print(one_pass()[0])
        return 0
    if not acquire_lock():
        print("A worker is already running (worker.lock). Not starting a second one.")
        return 1
    stop = {"flag": False}

    def handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)
    print(f"Worker started (pid {os.getpid()}). Checking for work every {interval_from_config()}s. Ctrl+C to stop.")
    try:
        loop(should_stop=lambda: stop["flag"], sleep=lambda s: _interruptible_sleep(s, stop))
    finally:
        workforce.heartbeat("stopped")
        release_lock()
        print("Worker stopped.")
    return 0


def _interruptible_sleep(seconds, stop):
    end = time.time() + seconds
    while time.time() < end and not stop["flag"]:
        time.sleep(min(1.0, end - time.time()))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
