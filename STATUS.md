# STATUS (read this first)

Last updated: 2026-10-13. Owner: Sai Prasanna (user). Write-up language: English for AI readers; the user chats in Tenglish.

## Goal
A zero-cost, 24/7 autonomous agent system that builds real income. Sub-agents and bots handle small tasks. A dashboard shows the system like a 2D office (desks, status lights). Layout is a pyramid: CEO orchestrator at top, department managers in the middle, worker bots at the bottom. Future direction: a self-improving agent with one-click rollback to any previous version.

## Current state (honest)
- The engine is still a SIMULATION. Clicks are random and revenue values are estimates.
- Real money so far: $0.00.
- Every campaign and discovery row now has a `source` column: `real` or `simulated`. Existing rows were migrated to `simulated`.
- The dashboard is connected to the engine: "Run Engine Cycle" runs the real cycle, and stats come from the same DB. Real and simulated revenue are shown separately.
- Groq live mode uses model `llama-3.1-8b-instant`. No key is set yet, so the engine runs in fallback mode until the user sets GROQ_API_KEY.
- Affiliate tags (chilligavva-20, chilliai, chilligavva.gumroad.com) are placeholders. No real affiliate tracking exists yet.
- Config is read only from `config.json` next to `engine.py`. The DB path is next to `engine.py`.

## Changes log
- 2026-10-13 cleanup: fixed `chilli_app.py` syntax error; moved stale copies to `archive/`; memory docs to `docs/memory_2026-10-02/`; added `.gitignore`.
- 2026-10-13 names: `engine.py`, `dashboard.py`, `run_engine.py`, `agent_data.db`.
- 2026-10-13 fixes: model name updated; cost unified to $0.00; `source` ledger added with migration; dashboard connected to engine; config path made local-only.
- Backup of the DB before schema change: `archive/agent_data_backup_2026-10-13.db`.
- Tested on a throwaway copy: engine run OK, migration OK, summary OK. The real DB has NOT been run yet, so its migration happens on first engine or dashboard stats use.

## Added 2026-10-13 (continued work)
- `versions.py`: list versions, mark known-good tags, roll back. Rollback is a dry run by default. `--apply` makes a NEW commit (history kept) and refuses if the tree has uncommitted changes. Tested in a throwaway repo only.
- `docs/VALIDATION_PLAN.md`: two-week $0 validation plan with success criteria written before starting.
- `log_validation.py`: daily measured numbers (visitors, signups, engaged) into agent_data.db; `report` shows targets met and verdict on day 14.
- `docs/GROQ_SETUP.md`: free Groq key setup, done by the user.
- Engine output labels now say SIMULATION / estimated, no more 'LIVE' wording.

## Known issues (still open)
0. Needs user action: (a) pick the income stream to validate (docs/VALIDATION_PLAN.md), (b) create the Groq key and set GROQ_API_KEY (docs/GROQ_SETUP.md), (c) accounts/hosting (later discussion), (d) approve git commit when ready.
1. Uncommitted changes exist. Git sees the renames as delete + new file. Commit only when the user asks.
2. Revenue numbers in the engine are still hardcoded estimates (ROI values). They are labelled `simulated`.
3. Dashboard runs the engine on the GUI thread, so the window freezes during a cycle. Fine for now; move to a thread later.
4. Real earnings source is not connected. Nothing writes `source = 'real'` yet.
5. `docs/memory_2026-10-02/` and `archive/` use old names. Historical; not updated.

## Decisions (user-stated)
- Do not mix this project's data into other projects. The campaign project memory may hold Chilli work notes; the user has asked to keep them there.
- Accounts, email, domain, hosting: discuss later, not now.
- User wants: a real-vs-simulated ledger, a 2-week $0 validation test before building more, and a self-improving agent with one-click rollback (git tags + revert commits).
- Recommended free stack (research dated 2026-10): Groq (Llama 3.1 8B Instant, no card) as primary LLM, Cloudflare Workers AI as backup, GitHub Actions cron for scheduled runs (near-24/7, not exact), GitHub Pages for the dashboard, SQLite for storage, API keys in GitHub Secrets only.

## Next steps (suggested order)
1. Confirm the one income stream to validate first.
2. Set up a free LLM key via environment variable only (never in files).
3. Run the engine once on the real DB and check the migration.
4. Two-week validation test with success criteria written down before starting.
5. Only when a real earning is recorded, set `source = 'real'` for it.

## How to work in this folder (for any AI)
- Do not delete files. Move stale files into `archive/`.
- Do not print or copy `config.json` contents into chats or commits.
- Update this STATUS.md whenever the state changes, with the date.

## Rename log (2026-10-13)
- `ai_enterprise_corp.py` -> `engine.py`
- `chilli_app.py` -> `dashboard.py`
- `start_agent.py` -> `run_engine.py`
- `enterprise_data.db` -> `agent_data.db` (code paths updated in engine.py and dashboard.py)
- Removed `__pycache__` folders and the empty leftover memory folder.
- `docs/memory_2026-10-02/` and `archive/` still use the OLD names. They are historical; do not update them.
