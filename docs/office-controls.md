# Signed office controls

The robot consumes `control:request:*` in the configured Cloudflare KV namespace.
It verifies HMAC-SHA256 over the existing JavaScript-compatible ordered JSON fields
`id, action, target, value, created_at, nonce`. Signatures cover nested values too.
Requests expire after 24 hours; clocks may lead by at most five minutes. IDs are bounded
ASCII identifiers. A nonce may contain Unicode for compatibility with existing vectors.
Unknown actions and invalid payloads never dispatch work or commands.

Receipts live at `runtime:receipt:<id>` and contain `id`, `state` (`applied`, `held`,
`rejected`), `reason`, `at` in UTC, and a signature. Agent answers also include `reply`.
The signature protects a receipt used for replay suppression after a failed git save.
A held reservation is written before mutation. If the worker stops mid-request, its
outcome stays held rather than repeating a possibly completed action. Resubmit held
work with a new request ID and nonce after investigating or correcting the condition.
Local receipts and nonce uniqueness also survive ordinary restarts in SQLite.

KV reads are paginated and bounded. Read/write permission or transport errors emit
at most one safe diagnostic line per invocation. Values, tokens and exception messages
are never included in those diagnostics. No request can supply an executable command.

## Missions

`add_mission`: target is a stable mission ID; value is exactly
`{"title":"Review ideas","agent_id":"chilli-gavva-robot-research-worker","every":"1h"}`.
Intervals are `1h`, `1d`, `1w`; `hourly`, `daily`, `weekly` are also accepted.
Real workforce names and their short slugs resolve to the same reported agent ID.
Unreported agents, extra executable fields and unsupported intervals are rejected.

Definitions live in `office_missions`. The scheduler and workforce queue due work as
`office_task`. Missed intervals coalesce into one run; at most one queued/running task
per mission exists. There are at most 50 active missions, 20 pending office tasks and
10 dispatches per tick. Pause, the kill switch, daily rest and cube Off suspend dispatch.
Answers are advisory; they do not publish or perform the actions mentioned in a title.

`edit_mission`: same target; value is a nonempty subset of `title, agent_id, every`.
A valid edit resets the next due time and cancels queued superseded work atomically.
`archive_mission`: same target, null value. Archiving cancels queued runs and prevents
future dispatch. Running work is not interrupted. Archived missions cannot be edited.
Receipts bind to the original request signature as well as the ID; invalid signatures
cannot poison the replay record of a later authenticated request.

## Triggers

`set_trigger` adds or replaces a definition. `add_trigger` requires an unused ID;
`edit_trigger` accepts a nonempty patch for an active definition. `delete_trigger`
requires null and archives/cancels pending work. Targets are stable trigger IDs.

Office payloads have `label, agent_id, enabled, kind, at, watch_id, match_state`.
Supported kinds are `at` (timezone-qualified ISO time), `agent_state` (a real agent,
`idle/working/error`) and `task_done` (an existing `task:<number>`).
Other condition fields must be null. Optional `action` is `ask_agent` or `summary`.
For numeric state conditions use kind `state` and
`condition: {metric: "drafts_waiting", op: ">", value: 3}`. Supported metrics are
`drafts_waiting`, `queued_tasks`, `failed_tasks`; operators are `>, >=, <, <=, ==`.
Thresholds are nonnegative integers at most 10,000. No arbitrary expressions are accepted.

Time triggers fire once. State triggers fire on a false-to-true edge, at most once an
hour, and never add a second pending task for the same trigger. There are at most 100
active triggers. Missions and triggers share the pending and per-tick dispatch caps.
`summary` uses local database facts and sends through the existing notifier; it does
not spend an LLM call. Disabled cubes, pause/rest, and archived triggers prevent execution.

## Agent messages

`send_message`: target is the real agent ID; value is exactly `{text, thread_id}`,
with a matching thread and 1–500 characters. `chat` and `ask` also accept a text string.
`route_request` uses target `orchestrator`, with `{text, thread_id, role, cube_id}`;
its role and optional cube must still match the selected real workforce agent.

`chat_brain.office_reply` answers factual queries locally or uses the guarded LLM
for an advisory answer attributed to the requested agent. Replies are written into
the runtime receipt and scrubbed chat memory. This path sends no Telegram message.
Action requests expressed in chat are held for their own explicit signed control;
chat cannot approve, pause, execute tools, publish, or change budgets. Freeform work
is held while paused, its cube is Off, the budget is exhausted, or no provider answers.

## Autonomy and budgets

`set_policy` targets `robot`; value is exactly `{autonomy, budget_limit, budget_unit}`.
Modes are `manual`, `supervised`, `autonomous`. Manual stops automatic scheduler and
workforce dispatch; supervised/autonomous never bypass human approval or allow tools.
`set_autonomy` also accepts a mode string. `set_budget` accepts an integer call limit,
or `{limit, unit}`. Zero is valid and blocks AI calls. Booleans are not numeric budgets.

Call limits cannot exceed the lower configured ceiling or the hard 500-call daily cap.
Requests below today's recorded usage are held without partial changes. Token budgets
are held explicitly because this runtime has no authoritative token accounting; use
`calls`. Effective budgets apply to prompts, vision/audio, retries and provider fallbacks.
Each provider attempt atomically reserves a call before network IO, scoped to its database;
parallel callers cannot overshoot. Failed attempts still consume a call. Chat facts and
local trigger summaries do not consume AI calls. Changing autonomy never clears pause.

## Cube On/Off

`set_cube` targets a registered cube ID (optionally `cube:<id>`). Values may be a
boolean, `on`/`off`, or exactly `{enabled: boolean}`. `cube_on`/`cube_off` require null.
The office's existing `set_switch` boolean form also accepts registered cube targets;
publication switch targets retain their existing behavior. Unknown cubes are rejected,
and enabling a planned/unimplemented cube is held. Enabling never clears global pause
or changes human approval. Workforce, recurring work and the automatic product builder
respect disabled cubes.

Receipt write failures are kept in a SQLite outbox and retried before new work. Only
the receipt is retried, never the completed action or agent call. Completed source keys
are excluded from the bounded work batch so retained history does not starve new controls;
old authenticated outcomes remain applied/held/rejected after request expiry.

The main inbox processes signed office controls independently of Telegram setup and
before Telegram polling, so a Telegram outage cannot block the office. CI supplies the
existing workforce provider configuration to this step for agent questions. Recovery
mode remains an update-only fallback. Both KV read and write diagnostics stay visible
through the safe workflow log wrapper.
