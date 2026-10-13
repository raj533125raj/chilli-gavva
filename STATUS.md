# STATUS (read this first)

Last updated: 2026-10-14. Owner: Sai Prasanna. Written in English for AI readers; the user chats in Tenglish and wants continuous work without being asked "what next" for things the AI can do itself.

## Goal
Zero-cost, 24/7 autonomous agent team that builds real income. Office-style dashboard (pyramid: CEO / managers / workers). Self-improving, with one-call rollback. Runs in the cloud even when the computer is off.

## Honest state
- Real money earned so far: **$0.00**. The old engine (`engine.py`) is a SIMULATION. Its numbers are labelled `simulated` in the DB.
- The new agent team (`workforce.py`) works, is tested, and produces DRAFTS that wait for human approval. Nothing publishes by itself. No real earning source is connected.
- Cloud 24/7 workflow (`.github/workflows/agent.yml`) is LIVE on GitHub — secrets set, notify.py fixed.
- The Groq key IS set as GitHub secret `GROQ_API_KEY`. Team runs with LLM in GitHub Actions.

## What's done (as of Oct 14 2026)
- ✅ GitHub Actions workflow (`agent.yml`) — runs agent cycle every 6h, notifies via Telegram if approvals pending
- ✅ Telegram bot (@chilligavvabot) — token and chat_id set as GitHub secrets
- ✅ 4 GitHub secrets set: GROQ_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GITHUB_TOKEN (auto)
- ✅ notify.py fixed — single `_telegram_configured()` that reads env vars (was duplicated, second old def was winning)
- ✅ End-to-end Telegram test passed — bot sends message to Sai's Telegram (chat_id 746236151)
- ✅ 80+ tests pass locally

## Open items (need human decision or action)
- ❓ Income stream: pick one (Fishery News articles, Substack, affiliate, service) to start earning real $
- ❓ Accounts: decide which accounts to connect for publishing
- ❌ CLAUDE.md not created yet — needed for AI context handoff (add to repo root)

## Architecture quick ref
- `workforce.py` — multi-agent orchestration (CEO/managers/workers with Groq LLM)
- `agent_memory.py` — cross-session memory (SQLite)
- `notify.py` — Telegram notifications (config.json first, env vars fallback)
- `worker_daemon.py` — heartbeat loop calling workforce every N minutes
- `web/` — FastAPI dashboard
- `.github/workflows/agent.yml` — cloud 24/7 runner

## Security rules (for any AI reading this)
- @chilligavvabot token ends in `HPDpHc` — ONLY for Chilli Gavva project
- Do NOT use @FisheryChilliGavvaBot (Fishery News project)
- Do NOT use Bestdealsmhrbot (Best Deals project)
- Nothing auto-publishes — human approval required
- Free tools only
