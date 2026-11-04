# Cube Packs

A **Cube Pack** is a self-contained department pack that can be added to the robot simply by dropping a single folder into `cube_packs/`.

Every pack defines its own captain, sub-rooms, agents, allowed tools, schedule hints, and optional recipes.

---

## How to Add a New Cube in 3 Steps

### Step 1: Create a Folder
Create a new directory under `cube_packs/` named with your cube ID (lowercase, alphanumeric, dashes allowed):
```bash
mkdir cube_packs/my-department
```

### Step 2: Add `cube.json`
Inside your folder, create a `cube.json` configuration file:
```json
{
  "id": "my-department",
  "name": "My Department",
  "colour": "#3b82f6",
  "status": "planned",
  "captain": "Department Lead",
  "sub_rooms": [
    {
      "name": "Planning Studio",
      "main_agent": "Planner Agent",
      "sub_agents": ["Research Helper"]
    },
    {
      "name": "Creative Room",
      "main_agent": "Creative Writer",
      "sub_agents": ["Review Helper"]
    }
  ],
  "tools_allowed": ["note_writer", "draft_generator"],
  "schedule_hints": {
    "cadence": "daily",
    "suggested_slot": "afternoon"
  }
}
```

### Step 3: (Optional) Add Recipes
If your cube runs scheduled recipe workflows, add them in an optional `recipes/` subdirectory:
```bash
mkdir cube_packs/my-department/recipes
```
Place YAML recipe files here.

---

## Safety & Validation Rules
- **Safe by Default:** Packs load with status `"planned"` and remain `OFF` until explicitly reviewed and enabled. Planned or off packs never execute tasks or spend money.
- **Strict Validation:** Unique ID, matching folder name, valid sub-rooms and agents, and file size limits (<= 100 KB).
- **No Secrets:** No API keys, credentials, tokens, or passwords may be placed anywhere in pack files.
- **Fail-Safe Isolation:** A broken or invalid pack is safely skipped with a logged error message and never halts or crashes the robot.
