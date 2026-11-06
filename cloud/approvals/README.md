# Cloud Approval Ledger

This directory contains code for an optional zero-cost Cloudflare Worker backed by a SQLite Durable Object.
It is designed to receive and store owner approval and rejection decisions via Telegram webhook so that the Chilli Gavva / Binny robot can read them on its scheduled runs.

**Status: Code only. Nothing here is deployed or enabled yet.**
The robot currently continues to use `getUpdates` polling via `telegram_inbox.py`. Switching to webhooks or deploying this worker is a future owner decision. No Cloudflare APIs or Telegram `setWebhook` calls are made by this codebase.

---

## Architecture

- **`worker.js`**:
  - Handles incoming Telegram webhooks at `POST /telegram`.
    - Authenticates the request via `X-Telegram-Bot-Api-Secret-Token` matching `env.WEBHOOK_SECRET`.
    - Verifies that `callback_query` originates from `env.OWNER_CHAT_ID`.
    - Parses callback actions: `approve:<id>` or `reject:<id>:<reason>`.
    - Forwards to the Durable Object for persistent storage.
    - Responds immediately with HTTP 200 (CPU execution time < 10 ms).
  - Handles robot reading at `GET /decisions?since=<n>`.
    - Authenticates via header `Authorization: Bearer <env.ROBOT_READ_TOKEN>`.
    - Returns decisions recorded after sequence number `n` in JSON format.
  - Returns HTTP 404 for all other paths and methods.

- **`ledger.js`**:
  - Cloudflare Durable Object class (`ApprovalLedger`) with SQLite storage (`this.ctx.storage.sql`).
  - Table: `decisions(seq INTEGER PRIMARY KEY, draft_id, action, reason, ts, update_id UNIQUE)`.
  - Enforces idempotency:
    - Same `update_id` delivered twice is stored only once (`duplicate_update`).
    - First decision for a given `draft_id` wins; subsequent decisions are recorded as `already_decided`.

---

## Environment Variables & Secrets

All secrets are managed through Cloudflare Worker environment variables or secrets. No real tokens, secrets, or chat IDs are stored in repository files.

1. `WEBHOOK_SECRET`:
   - A high-entropy secret string configured when setting up the Telegram webhook (`setWebhook` parameter `secret_token`).
   - Telegram sends this in the `X-Telegram-Bot-Api-Secret-Token` header.
   - If the header does not match, the worker returns `401 Unauthorized`.

2. `OWNER_CHAT_ID`:
   - The Telegram chat ID of the authorized owner.
   - Only callback queries originating from this chat ID are accepted. Other messages or callbacks from non-owners are safely ignored.

3. `ROBOT_READ_TOKEN`:
   - A bearer token used by the robot engine when fetching new decisions from `GET /decisions`.
   - Requests without `Authorization: Bearer <env.ROBOT_READ_TOKEN>` return `401 Unauthorized`.

---

## Testing

Tests are written using Node.js built-in test runner (`node:test`) and require no third-party npm dependencies:

```bash
node --test cloud/approvals/test/ledger.test.mjs cloud/approvals/test/worker.test.mjs
```
