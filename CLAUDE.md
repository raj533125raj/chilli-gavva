# CLAUDE.md — Chilli Gavva Agent Context

> Read STATUS.md first. This file has project-specific rules for AI assistants.

## Project overview
Autonomous agent team that builds income for Sai Prasanna (@raj533125raj).
Language: Python. Talks in Tenglish (Telugu-English mix). Free tools only.

## Key files
| File | Purpose |
|------|---------|
| `workforce.py` | Multi-agent CEO/manager/worker orchestration via Groq LLM |
| `agent_memory.py` | Cross-session SQLite memory |
| `notify.py` | Telegram notifications (reads `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` env vars, falls back to config.json) |
| `worker_daemon.py` | Always-on heartbeat loop |
| `web/` | FastAPI dashboard + HTML frontend |
| `.github/workflows/agent.yml` | GitHub Actions: runs every 6h, notifies via Telegram |
| `STATUS.md` | Honest project state — update this after major changes |
| `config.json` | Local config (gitignored) — Telegram tokens, API keys for local dev |

## GitHub Secrets (all set as of Oct 14 2026)
- `GROQ_API_KEY` — LLM inference (free tier)
- `TELEGRAM_BOT_TOKEN` — @chilligavvabot token (ends in HPDpHc)
- `TELEGRAM_CHAT_ID` — Sai's personal chat id: 746236151
- `GITHUB_TOKEN` — auto-provided by GitHub Actions

## Security rules (MUST follow)
- @chilligavvabot token ends in `HPDpHc` — ONLY for this project
- Do NOT use @FisheryChilliGavvaBot token (Fishery News project)
- Do NOT use Bestdealsmhrbot token (Best Deals project)
- Do NOT mix credentials across projects
- Nothing auto-publishes, auto-posts, or applies patches to production
- Human approval required before any content goes live
- Free tools only — no paid APIs, no paid tiers

## How to run locally
```bash
python workforce.py          # run one agent cycle
python worker_daemon.py      # run continuous loop
python -m pytest tests/      # 80+ tests
python notify.py test        # test Telegram notification
python web/app.py            # dashboard at localhost:8000
```

## GitHub Actions
Workflow: `.github/workflows/agent.yml`
- Triggers: every 6h + manual dispatch
- Steps: checkout → install deps → run one agent cycle → notify via Telegram if approvals pending
- Env vars passed: `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

## notify.py important note
Function `_telegram_configured()` reads env vars first:
```python
token = c.get("telegram_bot_token","").strip() or os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
chat_id = str(c.get("telegram_chat_id","") or os.environ.get("TELEGRAM_CHAT_ID","")).strip()
```
There should be exactly ONE definition of this function in the file.
Bug history: a previous commit accidentally duplicated it — fixed Oct 14 2026.

## Current income: $0
The agent produces drafts that need human approval. No real income stream connected yet.
Next step: pick an income stream and connect it.

## Style guide
- Respond in Tenglish (mix of Telugu + English) in chat
- Be direct, work without asking permission for things AI can decide
- Update STATUS.md after major changes
