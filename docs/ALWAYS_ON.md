# Keep Chilli Gavva running all the time (free)

GitHub's own hourly timer almost never fires, so the agent slept. Three free pieces fix that:

| Piece | Where it runs | What it does |
|---|---|---|
| **A. Alarm clock** | Cloudflare Worker (free plan) | Starts the GitHub workflow every 30 minutes. Paces itself so GitHub's free minutes last the month. |
| **B. Mac mode** | Your Mac (after login) | Works nonstop while the Mac is on; replies to Telegram in ~15 s. GitHub runs stand down meanwhile. |
| **C. Door bell** | Same Cloudflare Worker | When the Mac is off, your Telegram message starts a GitHub run so you get a reply in ~1-2 minutes. |

Nothing here publishes, posts or sells anything. Tokens live only in Cloudflare / GitHub secrets
(or `config.json` on your Mac, which git ignores). Never paste a token into a chat, an issue or a file in the repo.

---

## Phase A — Alarm clock (one-time setup, about 15 minutes)

You will make two secrets. Keep them in a password manager or a private note on your Mac:

* **Worker secret**: a long random password shared by the Worker, GitHub and your Mac.
  Make one in the Mac Terminal: `openssl rand -hex 32` and copy the output.
* **GitHub token**: lets the Worker press "Run workflow" for you, and nothing else.

### A1. GitHub token (fine-grained, this repo only)
1. GitHub → your photo → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Name: `chilli-gavva-alarm-clock`. Expiration: 1 year (put a reminder in your calendar).
3. Repository access: **Only select repositories** → `raj533125raj/chilli-gavva`.
4. Permissions → Repository permissions → **Actions: Read and write**. Leave everything else as "No access".
5. **Generate token** and copy it. You will paste it into Cloudflare in step A3 and nowhere else.

### A2. Cloudflare Worker
1. Make a free account at dash.cloudflare.com (no card needed).
2. **Storage & Databases → KV → Create** a namespace named `chilli-gavva-state`.
3. **Workers & Pages → Create → Worker** ("Hello World"), name it `chilli-gavva-clock`, **Deploy**.
4. **Edit code**: delete everything, paste the whole file `deploy/cloudflare_alarm_worker.js` from this repo, **Deploy**.
5. Worker → **Settings → Bindings → Add → KV namespace**: variable name `STATE`, namespace `chilli-gavva-state`.

### A3. Worker secrets and timer
1. Worker → **Settings → Variables and Secrets → Add**, type **Secret**:
   * `GITHUB_TOKEN` = the GitHub token from A1
   * `WORKER_SECRET` = the Worker secret
2. Worker → **Settings → Trigger Events → Add → Cron Triggers**: `* * * * *` (every minute; the Worker
   itself decides when a run is due, so this does not mean a run every minute).
3. Copy the Worker address shown on its page, e.g. `https://chilli-gavva-clock.<your-name>.workers.dev`.

### A4. GitHub repo secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
* `ALWAYS_ON_URL` = the Worker address from A3
* `ALWAYS_ON_SECRET` = the Worker secret

### Check it works
* Within 1 minute a new run appears in **Actions → agent-cycle** with the run name started by the token.
* The run's first step says `Gate: RUN (ok)` and the last step says `Reported about N billed minute(s)`.
* About 30 minutes later the next one starts by itself.

---

## How the minutes are kept inside the free plan

GitHub gives a private repo **2,000 Actions minutes a month** and bills every run as at least 1 minute
(rounded up). Most Chilli Gavva runs take 20-60 seconds, so they bill 1 minute; busy runs bill 2.

* Every 30 minutes, all month, with the Mac off: 48 runs a day × 31 days = **1,488 runs**.
  At 1 billed minute each that is 1,488 minutes; if some runs bill 2 it could pass 1,700.
* So every run reports its billed minutes to the Worker, and the Worker **paces the alarm**: it only
  starts a run when the month's minutes are not ahead of schedule (budget 1,700 spread evenly over
  the month). Busy days are balanced by skipped alarms later. At 1,700 the alarm stops for the month.
* Door bell runs count too and reset the 30-minute timer (no double runs). They stop at 1,900.
* GitHub's own timer is kept only as a backup every 3 hours (at most 8 runs a day, and it rarely fires).
* While the Mac worker is alive the Worker starts nothing, so a Mac that is on most of the day saves
  most of the minutes.

## Cloudflare free-plan limits (and what we use)

| Limit (free plan) | Allowed per day | We use per day (worst case) |
|---|---|---|
| Worker requests (incl. cron) | 100,000 | ~1,440 cron ticks + ~300 heartbeats + ~150 run reports ≈ 1,900 |
| CPU time | 10 ms per request | a few ms (only small JSON) |
| Cron triggers | 5 per account | 1 |
| KV reads | 100,000 | ~3 per tick ≈ 4,400 |
| KV writes | 1,000 | heartbeats 288 + about 3 per run (≤ 150) ≈ 450. Idle ticks write nothing. |
| KV storage | 1 GB | under 1 KB |
