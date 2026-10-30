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
