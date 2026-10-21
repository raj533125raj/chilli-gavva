# Chilli Gavva Agent

Zero-cost autonomous agent project (Python + SQLite + web dashboard).

**Start here:** read `STATUS.md` first. It holds the current state, goals, known issues and next steps.

## Layout
Read `STATUS.md` for the full table. Short version:
- `workforce.py` (agent team), `guardrails.py` (kill switch, budget cap, approvals), `improver.py` (safe self-improvement), `engine.py` (old simulation + LLM call)
- `serve.py` + `web/index.html`: local 2D office dashboard
- `cloud_runner.py` + `.github/workflows/agent.yml`: 24/7 cloud cycle (optional)
- `versions.py` (rollback), `log_validation.py` (14-day test), `tests/` (run: `python3 -m unittest discover -s tests`)
- `agent_data.db`: SQLite database. `config.json`: local settings, git-ignored, never commit.
- `docs/`: research, plans, setup guides. `docs/memory_2026-10-02/` and `archive/` are historical and outdated.

## Run
- Everything (worker + dashboard): `python3 start.py` then http://127.0.0.1:8787. See docs/AUTOSTART.md
- Agent cycle: `python3 workforce.py run`
- Emergency stop: `python3 guardrails.py kill`


## More tools
- `python3 record_earning.py add|list|void|total` real earnings with proof
- `python3 maintenance.py` backup/prune/integrity
- `python3 site_builder.py` build static pages from approved drafts
- `python3 coder.py request "goal" file.py` ask the coder agent for a patch proposal (human approves and applies)
- `python3 report.py` daily report (worker writes it automatically)

## Public repo safety

This repo is **public**. Anything committed can be read by anyone, and git keeps old copies in history even after a file is deleted.

**What is public**
- The code, the docs and the tests.
- `web/data/status.json`: the dashboard snapshot. It has the kill switch, LLM call counts, agent names and roles, task counts, recent task kinds, pending approval titles, and whether the worker is running. It has no money, prompts, logs, validation numbers, service requests, built tools, approval review text, keys or tokens.
- `exported_content/`: the product files for approved drafts, which are meant to be public listings.

**What is never public**
- The database. The working copy `agent_data.db` is git-ignored. The copy in git is `agent_data.db.enc`, encrypted with AES-256 (openssl, PBKDF2, 200 000 iterations). The key is the GitHub secret `DB_KEY` and the owner's password manager. Nothing else.
- API keys, tokens, passwords, chat IDs and personal emails. The secret guard (`secret_guard.py`) runs on every save and blocks a save that contains one.

**If the key is missing:** the cloud job stops at its first step with `DB_KEY not set`. Nothing is read, written or committed.

**Rotating `DB_KEY`** (type the key in the terminal prompt, never in chat or a file):
1. Create a new key: `openssl rand -base64 32`. Save it in the password manager.
2. In the repo folder, decrypt with the old key:
   `read -s -p "old DB_KEY: " DB_KEY; export DB_KEY; python3 scripts/db_crypt.py decrypt; unset DB_KEY`
3. Encrypt with the new key:
   `read -s -p "new DB_KEY: " DB_KEY; export DB_KEY; python3 scripts/db_crypt.py encrypt; unset DB_KEY`
4. Replace the `DB_KEY` secret on GitHub (Settings > Secrets and variables > Actions).
5. Commit `agent_data.db.enc` and push. Run the workflow once to check it is green.

**Old copies are still in history.** Before this change, `agent_data.db` (105 commits) and `archive/agent_data_backup_2026-10-13.db` (2 commits) were committed in plain form. Encrypting from now on does not remove those copies. Removing them needs a history rewrite and a force push. That is an owner decision and has not been done.
