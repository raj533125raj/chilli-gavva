# Office Projection & Link (Binny Mills)

Status: built and tested, NOT connected to the live robot yet.

## Overview

The Office Link protocol (`office_protocol.py`) and projection engine (`office_projection.py`) generate a safe, read-only snapshot of the Chilli Gavva robot operations for consumption by the Binny Mills / Binny-JG office viewer.

The office receives a structured, deterministic projection of the office state without any direct read access to sensitive internals or raw databases.

## Schema Hierarchy

The projection presents the office organized by:
```
Teams (Cubes)
  └── Captain
  └── Sub Rooms
  └── Agents (Main & Sub)
```

Each snapshot contains:
1. **Header Metadata**:
   - `version`: Protocol schema version (e.g., `1.0.0`).
   - `office_id`: Stable identifier (defaults to `binny-mills-office`).
   - `revision`: Monotonically increasing revision number.
   - `generated_at`: ISO 8601 UTC timestamp.

2. **Teams / Cubes**:
   - `id`: Cube identifier (e.g., `executive`, `content`).
   - `name`: Clean display name.
   - `role`: Department role description.
   - `status`: Cube status (`on`, `off`, `archived`).
   - `captain`: Captain agent details.
   - `sub_rooms`: List of sub rooms within the cube.
   - `agents`: Agents assigned to the team.

3. **Agents**:
   - `id`: Unique agent id (e.g., `chilli-gavva-robot-ceo`).
   - `name`: Human-readable name.
   - `level`: `captain`, `main`, or `sub`.
   - `status`: Honest operational status from the approved list:
     - `working`: Actively executing a task.
     - `thinking`: Actively planning or researching.
     - `just finished`: Completed a task within the last 5 minutes.
     - `needs you`: Requires human or owner attention (e.g., Doctor case).
     - `waiting`: Next task is queued and waiting for worker pickup.
     - `off duty with next shift`: Daemon or worker is offline until the next schedule.
     - `idle`: Daemon is alive and waiting for work.
     - `warning`: In automated recovery.
     - `error`: Last task failed.
     - `paused`: Agent or cube is paused / switched off.
     - `not built yet`: Planned cube or unbuilt hire.
   - `current_action`: Clean, generic, scrubbed summary of current work.
   - `progress`: Integer percentage (0–100) or null.
   - `model`: Configured AI model name (scrubbed).
   - `last_seen`: Timestamp when the agent last performed an action.

4. **Office Counters**:
   - `agents`: Total active and recognized workforce agents.
   - `running`: Number of agents currently working or thinking.
   - `needs_you`: Count of items requiring human intervention (approvals + blocked doctor cases).
   - `drafts_waiting`: Number of drafts awaiting owner decision.
   - `products_ready`: Approved products with truth check verdict `READY`.
   - `products_needs_fix`: Products requiring fixes (`NEEDS FIX`).
   - `minutes_used` / `minutes_limit`: Runner action minutes usage.
   - `ai_providers_up` / `ai_providers_total`: Number of active AI providers vs total monitored providers.

5. **Approvals Queue**:
   - `id`: Numeric approval identifier.
   - `title`: Clean short title (no sensitive information, no raw draft text).
   - `score`: Review rubric score (0–100) or null.
   - `waited_hours`: Elapsed hours since submission.
   - `auto_decision_due`: Exact ISO timestamp marking the 24-hour auto-decision deadline.

## Why It Is Safe

1. **No Secrets or Credentials**:
   - All string values pass through strict scrubbing routines (`public_export.scrub_text`, `secret_guard.scrub_text`, and credential regex matching).
   - API keys, tokens, Bearer headers, and authentication keys are redacted to `[redacted]`.

2. **No Email Addresses**:
   - Email addresses are scrubbed and replaced with `[redacted]`.

3. **No Money Amounts or Currency Figures**:
   - Currency symbols and text patterns are scrubbed and replaced with `[hidden]` unless an explicit owner-only parameter is enabled.

4. **No Unapproved Content or Raw Prompts**:
   - Only approved products or generic title placeholders are exposed.
   - System prompts, prompt evolution iterations, and internal debate transcripts are excluded.

5. **Size and Length Caps**:
   - String fields and collections are strictly bounded (e.g. maximum title length of 120 characters, capped team counts, capped approval lists) to eliminate memory or denial-of-service exposure.

6. **Missing / Empty Database Resilience**:
   - If the database file is absent or empty, the projection returns an honest empty snapshot rather than failing or raising uncaught exceptions.
