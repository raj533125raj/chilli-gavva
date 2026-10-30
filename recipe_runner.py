"""YAML recipe runner — Goose-style one-command job launcher.

Usage:
    python3 recipe_runner.py recipes/daily_chilli_content.yaml
    python3 recipe_runner.py recipes/niche_budget_sheets.yaml
    python3 recipe_runner.py --list          # show all available recipes

A recipe is a YAML file with:
    name: str
    description: str
    tasks:
      - kind: research | draft | plan | code_proposal
        payload: {key: value, ...}
        priority: 1-10  (optional, default 5)

Each task is enqueued immediately; the worker picks them up on its next cycle.
Nothing is auto-published — all output goes through the approval queue as usual.
"""
import argparse
import glob
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECIPES_DIR = os.path.join(BASE_DIR, "recipes")

VALID_KINDS = {"research", "draft", "plan", "review", "code_proposal"}


def _load_yaml(path: str) -> dict:
    """Load YAML without third-party deps using a minimal line parser.

    Supports only the flat structure our recipes use:
        key: value
        tasks:
          - kind: foo
            payload:
              topic: bar text
              other: value
    """
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    result: dict = {}
    tasks: list = []
    current_task: dict | None = None
    in_tasks = False
    in_payload = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)

        # top-level key: value
        if indent == 0:
            in_tasks = False
            in_payload = False
            current_task = None
            if stripped.startswith("tasks:"):
                in_tasks = True
            elif ":" in stripped:
                k, _, v = stripped.partition(":")
                result[k.strip()] = v.strip()

        elif in_tasks and indent == 2 and stripped.startswith("- kind:"):
            # New task list item
            in_payload = False
            if current_task is not None:
                tasks.append(current_task)
            current_task = {"kind": stripped[len("- kind:"):].strip(), "payload": {}, "priority": 5}

        elif in_tasks and indent == 4 and current_task is not None:
            # Task-level keys (payload:, priority:, etc.) — indent 4 inside list item
            in_payload = False
            if stripped.startswith("payload:"):
                in_payload = True
            elif ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                if k == "priority":
                    try:
                        current_task["priority"] = int(v.strip())
                    except ValueError:
                        pass
                else:
                    current_task[k] = v.strip()

        elif in_tasks and in_payload and indent >= 6 and current_task is not None:
            # Payload sub-keys — indent 6+ inside payload block
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                current_task["payload"][k.strip()] = v.strip().strip('"')

    if current_task is not None:
        tasks.append(current_task)

    result["tasks"] = tasks
    return result


def list_recipes() -> list[dict]:
    """Return all .yaml files in recipes/ with their name and description."""
    out = []
    for path in sorted(glob.glob(os.path.join(RECIPES_DIR, "*.yaml"))):
        try:
            data = _load_yaml(path)
            out.append({
                "file": os.path.basename(path),
                "name": data.get("name", "?"),
                "description": data.get("description", ""),
                "task_count": len(data.get("tasks", [])),
            })
        except Exception as e:
            out.append({"file": os.path.basename(path), "error": str(e)})
    return out


def run_recipe(path: str, db_path=None, dry_run: bool = False, llm=None) -> dict:
    """Load recipe from path and enqueue its tasks. Returns summary dict.

    Niche recipes (name starts with "niche_") take fresh, never-used topics from the niche topic pool
    (niche_topics.py) instead of repeating their 3 starter topics every time.
    """
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    if not os.path.exists(path):
        return {"error": f"Recipe not found: {path}"}

    try:
        recipe = _load_yaml(path)
    except Exception as e:
        return {"error": f"Failed to parse {path}: {e}"}

    tasks = recipe.get("tasks", [])
    if not tasks:
        return {"error": "Recipe has no tasks", "name": recipe.get("name")}

    note = ""
    if not dry_run and str(recipe.get("name", "")).startswith("niche_"):
        import niche_topics
        import workforce
        fresh = niche_topics.fresh_tasks(recipe, db_path=db_path,
                                         llm=llm if llm is not None else workforce.default_llm(db_path))
        if fresh is not None:
            tasks = fresh
            if not tasks:
                note = "no new topics left and the AI could not suggest more; nothing repeated tonight"

    enqueued = []
    errors = []
    if not dry_run:
        import workforce
        for t in tasks:
            kind = t.get("kind", "")
            if kind not in VALID_KINDS:
                errors.append(f"Unknown kind '{kind}' — skipped")
                continue
            try:
                task_id = workforce.enqueue(kind, t.get("payload", {}),
                                            priority=t.get("priority", 5), db_path=db_path)
                enqueued.append({"id": task_id, "kind": kind})
            except Exception as e:
                errors.append(f"{kind}: {e}")
    else:
        for t in tasks:
            enqueued.append({"id": None, "kind": t.get("kind", "?"), "dry_run": True})

    return {
        "name": recipe.get("name", os.path.basename(path)),
        "description": recipe.get("description", ""),
        "enqueued": enqueued,
        "errors": errors + ([note] if note else []),
    }


def main():
    parser = argparse.ArgumentParser(description="Chilli Gavva recipe runner")
    parser.add_argument("recipe", nargs="?", help="Path to a .yaml recipe file")
    parser.add_argument("--list", action="store_true", help="List all available recipes")
    parser.add_argument("--dry-run", action="store_true", help="Parse recipe but don't enqueue")
    parser.add_argument("--db", default=None, help="Path to agent_data.db (default: auto)")
    args = parser.parse_args()

    if args.list or not args.recipe:
        recipes = list_recipes()
        if not recipes:
            print("No recipes found in", RECIPES_DIR)
            return
        print(f"\nAvailable recipes ({len(recipes)}):\n")
        for r in recipes:
            if "error" in r:
                print(f"  ⚠  {r['file']}: {r['error']}")
            else:
                print(f"  {r['file']}")
                print(f"     {r['description']}  ({r['task_count']} task(s))")
        print()
        return

    result = run_recipe(args.recipe, db_path=args.db, dry_run=args.dry_run)
    if "error" in result:
        print("Error:", result["error"], file=sys.stderr)
        sys.exit(1)

    tag = " [DRY RUN]" if args.dry_run else ""
    print(f"\nRecipe: {result['name']}{tag}")
    print(f"  {result['description']}")
    print(f"  Enqueued {len(result['enqueued'])} task(s):")
    for t in result["enqueued"]:
        tid = f"#{t['id']}" if t.get("id") else "(not enqueued)"
        print(f"    {tid}  {t['kind']}")
    if result["errors"]:
        for e in result["errors"]:
            print("  ⚠ ", e)
    print()


if __name__ == "__main__":
    main()
