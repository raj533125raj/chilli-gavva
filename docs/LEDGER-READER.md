# Cloud Approvals Ledger Reader

## Overview

The `approvals_ledger.py` module reads owner approval and rejection decisions from a remote Cloudflare Worker backed by a SQLite Durable Object (`cloud/approvals/`).

When the owner reviews draft proposals in Telegram using one-tap buttons (`[Approve]` or `[Reject]`), the decisions can be stored persistently in the cloud ledger. `approvals_ledger.py` fetches these decisions via HTTP GET (`/decisions?since=<seq>`) and applies them safely to the local SQLite database (`agent_data.db`) via `guardrails.decide()` and `guardrails.set_reason()`.

### Safety and Invariants
- **First decision wins**: If a draft has already been approved or rejected, subsequent decisions for that draft are skipped.
- **Idempotent sequence tracking**: The highest applied sequence number (`seq`) is stored in `agent_data.db`. Re-running the reader does not duplicate decisions.
- **Credential safety**: Authentication tokens and sensitive parameters are never printed to logs or standard output. Errors only report short, safe summaries.
- **Module only**: Nothing calls or executes this reader automatically yet.

---

## How to Turn It On Later (3 Owner Steps)

To activate cloud ledger reading in the robot pipeline when ready, perform the following 3 steps:

### 1. Deploy the Worker
Deploy the Cloudflare Worker in `cloud/approvals/` to Cloudflare using Wrangler:
```bash
cd cloud/approvals
wrangler deploy
```
Set the worker secrets in Cloudflare (`wrangler secret put <NAME>`):
- `WEBHOOK_SECRET`
- `OWNER_CHAT_ID`
- `ROBOT_READ_TOKEN`

Then set Telegram's webhook to point to `https://<your-worker>.<subdomain>.workers.dev/telegram`.

### 2. Set Robot Secrets
In your robot environment or GitHub repository secrets, add:
- `LEDGER_URL`: The base URL of the deployed Cloudflare Worker (e.g. `https://my-approvals-worker.subdomain.workers.dev`).
- `LEDGER_READ_TOKEN`: The bearer token matching `ROBOT_READ_TOKEN` set on the Cloudflare Worker.

### 3. Add One Line to the Workflow
In your scheduled GitHub Actions workflow (or runner loop), add a step before product builds to sync owner decisions:
```bash
python3 approvals_ledger.py
```
If the environment variables are not configured, the script safely prints `Ledger: not configured` and exits without error.
