"""scheduler.py — Time-based recipe runner for GitHub Actions.

Reads schedule.yaml and runs the recipe whose time-slot matches NOW (UTC).
Called by GitHub Actions every 30 min; each slot runs at most once per day.

Usage:
    python3 scheduler.py               # auto-pick slot for current UTC hour
    python3 scheduler.py --slot morning
    python3 scheduler.py --dry-run
    python3 scheduler.py --list

Schedule config: schedule.yaml (auto-created if missing)
Lock dir: .scheduler_locks/ (prevents double-run; cleared daily by GitHub Actions)
"""
import argparse
import datetime
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_PATH = os.path.join(BASE_DIR, "schedule.yaml")
LOCK_DIR = os.path.join(BASE_DIR, ".scheduler_locks")

# Used only when schedule.yaml is missing: the one night slot with the five niche agents (same as schedule.yaml).
DEFAULT_SCHEDULE = """\
# Chilli Gavva daily schedule (UTC hours)
# One niche agent runs per night, taking turns by UTC weekday.
slots:
  - name: night
    hour: 21
    rotate: niche_budget_sheets, niche_freelancer_trackers, niche_adhd_planners, niche_planners_2027, niche_resume_bundle
    recipe: niche_budget_sheets
"""


def _load_schedule(path=SCHEDULE_PATH):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_SCHEDULE)
    slots = []
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    current = None
    in_slots = False
    for raw in lines:
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent == 0:
            in_slots = stripped.startswith("slots:")
        elif in_slots and indent == 2 and stripped.startswith("- name:"):
            if current:
                slots.append(current)
            current = {"name": stripped[len("- name:"):].strip()}
        elif in_slots and indent == 4 and current and ":" in stripped:
            k, _, v = stripped.partition(":")
            k, v = k.strip(), v.strip()
            if k == "hour":
                try:
                    current["hour"] = int(v)
                except ValueError:
                    pass
            else:
                current[k] = v
    if current:
        slots.append(current)
    return slots


def _lock_path(slot_name, date_str):
    return os.path.join(LOCK_DIR, f"{date_str}_{slot_name}.lock")


def _is_locked(slot_name, date_str):
    return os.path.exists(_lock_path(slot_name, date_str))


def _acquire_lock(slot_name, date_str):
    os.makedirs(LOCK_DIR, exist_ok=True)
    path = _lock_path(slot_name, date_str)
    with open(path, "w") as f:
        f.write(datetime.datetime.utcnow().isoformat())
    return path


def pick_slot(slots, now_hour):
    """Return the slot whose hour is closest to now_hour (±1 hour window)."""
    for slot in slots:
        if abs(slot.get("hour", -99) - now_hour) <= 1:
            return slot
    return None


def resolve_recipe(slot, today=None):
    """Return the recipe name a slot should run today.

    A slot with `rotate: a, b, c` takes turns: one recipe per day, picked by
    UTC weekday (Monday = first name, wrapping round when the list is shorter
    than a week). Slots without `rotate` use their plain `recipe` value.
    """
    names = [n.strip() for n in slot.get("rotate", "").split(",") if n.strip()]
    if not names:
        return slot.get("recipe", "")
    today = today or datetime.datetime.utcnow().date()
    return names[today.weekday() % len(names)]


def _queue_full(db_path=None):
    """True when the human approval queue already holds the workforce limit of drafts."""
    try:
        import guardrails
        import workforce
        return len(guardrails.pending(db_path)) >= workforce.MAX_PENDING_APPROVALS
    except Exception:
        return False


def run_slot(slot, dry_run=False, db_path=None, today=None):
    recipe_name = resolve_recipe(slot, today)
    if not recipe_name:
        return {"error": f"slot '{slot['name']}' has no recipe"}
    if slot.get("rotate") and not dry_run and _queue_full(db_path):
        return {"skipped": True, "name": recipe_name,
                "reason": f"approval queue is full, '{recipe_name}' waits for a later run"}
    import recipe_runner
    path = os.path.join(recipe_runner.RECIPES_DIR, recipe_name + ".yaml")
    if not os.path.exists(path):
        return {"error": f"recipe not found: {path}"}
    return recipe_runner.run_recipe(path, db_path=db_path, dry_run=dry_run)


def run_now(slot_name=None, dry_run=False, db_path=None):
    """Pick or match a slot, check lock, run recipe. Returns result dict."""
    import guardrails
    if guardrails.is_killed():
        return {"skipped": True, "reason": "paused (kill switch or /pause)"}
    import rest_window
    if rest_window.in_rest_window():
        return {"skipped": True, "reason": "daily rest window (6:00-6:06 IST)"}
    slots = _load_schedule()
    now = datetime.datetime.utcnow()
    date_str = now.strftime("%Y-%m-%d")

    if slot_name:
        slot = next((s for s in slots if s["name"] == slot_name), None)
        if not slot:
            return {"error": f"slot '{slot_name}' not found in schedule"}
    else:
        slot = pick_slot(slots, now.hour)
        if not slot:
            return {"skipped": True, "reason": f"no slot for UTC hour {now.hour}"}

    if _is_locked(slot["name"], date_str) and not dry_run:
        return {"skipped": True, "reason": f"slot '{slot['name']}' already ran today"}

    result = run_slot(slot, dry_run=dry_run, db_path=db_path)
    if not dry_run and "error" not in result and not result.get("skipped"):
        _acquire_lock(slot["name"], date_str)
    result["slot"] = slot["name"]
    result["utc_hour"] = now.hour
    return result


def main():
    parser = argparse.ArgumentParser(description="Chilli Gavva scheduler")
    parser.add_argument("--slot", default=None, help="Force a specific slot name")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't enqueue")
    parser.add_argument("--list", action="store_true", help="List all configured slots")
    parser.add_argument("--db", default=None, help="Path to agent_data.db")
    args = parser.parse_args()

    if args.list:
        slots = _load_schedule()
        if not slots:
            print("No slots configured in", SCHEDULE_PATH)
            return
        now_hour = datetime.datetime.utcnow().hour
        date_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        print(f"\nSchedule ({len(slots)} slots, current UTC hour: {now_hour}):\n")
        for s in slots:
            locked = _is_locked(s["name"], date_str)
            tag = "  [ran today]" if locked else ""
            active = " ← NOW" if pick_slot([s], now_hour) else ""
            print(f"  {s['name']:12s}  UTC {s.get('hour','?'):02d}:xx  →  {resolve_recipe(s) or '?'}{tag}{active}")
        print()
        return

    result = run_now(slot_name=args.slot, dry_run=args.dry_run, db_path=args.db)

    if result.get("skipped"):
        print("Skipped:", result["reason"])
        return
    if "error" in result:
        print("Error:", result["error"], file=sys.stderr)
        sys.exit(1)

    tag = " [DRY RUN]" if args.dry_run else ""
    print(f"Slot: {result.get('slot')}{tag}  →  {result.get('name', '?')}")
    print(f"  Enqueued {len(result.get('enqueued', []))} task(s)")
    for e in result.get("errors", []):
        print("  ⚠", e)


if __name__ == "__main__":
    main()
