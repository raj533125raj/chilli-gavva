# STATUS (read this first)

Last updated: 2026-10-13. Owner: Sai Prasanna. Written in English for AI readers; the user chats in Tenglish and wants continuous work without being asked "what next" for things the AI can do itself.

## Goal
Zero-cost, 24/7 autonomous agent team that builds real income. Office-style dashboard (pyramid: CEO / managers / workers). Self-improving, with one-call rollback. Runs in the cloud even when the computer is off.

## Honest state
- Real money earned so far: **$0.00**. The old engine (`engine.py`) is a SIMULATION. Its numbers are labelled `simulated` in the DB.
- The new agent team (`workforce.py`) works, is tested, and produces DRAFTS that wait for human approval. Nothing publishes by itself. No real earning source is connected.
- Cloud 24/7 workflow file exists but has NOT been run on real GitHub yet (cannot be tested from here).
- The Groq key is not set, so the team runs in `fallback` mode (templates, no LLM) until the user sets `GROQ_API_KEY`.

## What exists (all tested, 45 unit tests pass: `python3 -m unittest discover -s tests`)
| File | Purpose |
|---|---|
| `engine.py` | Old simulated cycle + Groq call (`llama-3.1-8b-instant`, 429 retry, respects kill switch and daily cap) |
| `workforce.py` | SQLite task queue, 7 agents by level, plan -> research -> draft -> review, retries 3x, crash recovery, heartbeat, plans 2 drafts/day (config `daily_drafts`) and stops at 10 waiting approvals |
| `worker_daemon.py` | ALWAYS-ON worker: picks up queued work automatically, obeys kill switch, single-instance lock, backoff when idle, promotes winning prompts and creates new challengers by itself |
| `start.py` | One command: worker in the background + dashboard |
| `guardrails.py` | Kill switch (`KILL_SWITCH` file or env `AGENT_PAUSED=1`), daily LLM call cap (config `daily_llm_call_cap`, default 100), approval queue |
| `improver.py` | Prompt A/B testing with human approval signal, automatic challenger creation (`evolve`: LLM rewrite or plain tweaks), promote/revert in one call; code changes only as verified patch PROPOSALS, never auto-applied |
| `export_status.py` | Builds the dashboard snapshot (counts and titles only, no secrets) into `web/data/status.json` |
| `chat.py`, `serve.py` | Chat with the CEO (honest answers from the DB; LLM for open questions) and local dashboard server on 127.0.0.1:8787 |
| `web/index.html` | 2D office dashboard: desks and status lights, money (real vs simulated), approvals, task queue, prompts, validation progress, kill switch, chat |
| `cloud_runner.py` + `.github/workflows/agent.yml` | One cycle every 30 min in GitHub Actions: tests, run, save state |
| `archive/dashboard_tkinter_retired.py` | Old Tkinter dashboard, retired 2026-10-13 (web dashboard replaces it) |
| `versions.py` | List / tag / roll back project versions via git (dry run by default) |
| `log_validation.py` | Daily numbers for the 14-day validation test |
| `docs/` | RESEARCH_2026-10-13.md (platform facts and corrections), VALIDATION_PLAN.md, GROQ_SETUP.md, CLOUD_SETUP.md |

## How to use (local)
- Everything at once: `python3 start.py` (worker + dashboard). Details and start-at-login: docs/AUTOSTART.md
- Dashboard only: `python3 serve.py` then open http://127.0.0.1:8787
- One manual agent cycle: `python3 workforce.py run`
- Approvals: dashboard buttons, or `python3 guardrails.py approvals | approve <id> | reject <id>`
- Emergency stop: dashboard Kill switch, or `python3 guardrails.py kill` (resume with `resume`)
- Prompt learning: `python3 improver.py status | promote <name> | revert <name>`
- Version rollback: `python3 versions.py list`, then `rollback <tag>` (add `--apply`)

## Research corrections (see docs/RESEARCH_2026-10-13.md)
- GitHub Models was retired 2026-07-30: no free GitHub 8B token.
- Hugging Face Inference Providers: free users get no monthly credits.
- GitHub Pages must not host commercial sites. Use it only for the non-commercial test; use Cloudflare Pages later for monetised pages.
- Free Hugging Face Spaces sleep when idle: not an always-on host.
- Groq: model is listed as production; exact free limits for the 8B model are only in the user's console.

## Added 2026-10-13 (late)
- `record_earning.py`: proof-required real earnings ledger (add/list/void/total). Real and simulated are never mixed.
- `maintenance.py`: daily backup (keep 7, in git-ignored `backups/`), prune old logs/tasks, integrity check; run by the worker.
- `site_builder.py`: approved drafts -> static pages in `site/` (escaped, no scripts/trackers). Never auto-published anywhere.
- `coder.py` + "Coder Worker": `python3 coder.py request "goal" file.py` -> LLM proposes a patch -> strict validation (protected files, no deletes, size limits) -> approval queue. After approval the worker tests it in a throwaway copy. A human applies it: `git apply proposals/proposal-<id>.diff`. Never auto-applied.
- `report.py`: daily plain-text report in `reports/` (git-ignored), written by the worker once a day.
- Ollama: set `"ollama_model": "llama3"` in config.json to use a free local model when Groq has no key/answer (needs Ollama running locally; untested on a real Ollama).
- Tkinter dashboard retired into `archive/`.
- 80 tests pass (`python3 -m unittest discover -s tests`).

## Open items
1. USER ACTION: pick the income stream to validate (docs/VALIDATION_PLAN.md).
2. USER ACTION: create a free Groq key, set GROQ_API_KEY (docs/GROQ_SETUP.md). Check Groq terms before earning from the free tier.
3. USER ACTION (optional, for 24/7 cloud): create a GitHub repo, push, add the secret (docs/CLOUD_SETUP.md).
4. USER ACTION: accounts/email/domain/hosting discussion (deferred by the user).
5. Real earnings: only by hand with proof: `python3 record_earning.py add <usd> <proof-ref> <title>` (no automatic source).
6. GitHub workflow and the macOS start-at-login file are not exercised on real hardware/GitHub. Tested for real in the cloud sandbox: worker + dashboard together (new work added while running was picked up and finished automatically, clean Ctrl+C shutdown), web UI in Chromium.
7. Old docs in `docs/memory_2026-10-02/` and `archive/` use outdated names and paths. Historical only.
8. Nothing is pushed anywhere. Work is committed locally; see `python3 versions.py list` for tags.

## Rules for any AI working here
- Do not delete files; move stale ones into `archive/`.
- Never print or commit `config.json` or any API key.
- Never execute LLM-generated code outside the throwaway-copy test in `improver.verify_proposal`.
- Nothing may publish or post publicly without a human approval in the approvals table.
- Update this file with the date whenever the state changes.
- End-of-day: when the user says delete, list exactly what will be removed and delete the project and its traces (local folder and notes) as they asked; remote copies (GitHub etc.) the user must remove.

## Decisions (user-stated)
- Keep this project's data inside this project; the campaign project memory may hold Chilli notes (user asked to keep them there).
- Accounts, email, domain, hosting: discuss later.
- Prefers Tenglish replies, structured, brutally honest, confidence levels, no unnecessary questions.
