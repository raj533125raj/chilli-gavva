# CLAUDE.md — notes for AI assistants working on Chilli Gavva

> Read STATUS.md first for the current state. This file holds the working rules.

## Project in one line
Zero-cost AI agent team that drafts low-cost digital products for global (USD) buyers; a human approves
every draft and uploads the finished files to a free store. Python 3.11, standard library first, SQLite.

## Key files
| File | Purpose |
|------|---------|
| `workforce.py` | Task queue and agents: research → draft (fixed listing format) → QA Checker (rules + large model, one rewrite) |
| `improver.py` | Safe self-improvement of prompt wording (A/B tests, rollback). Old landing-page prompts are retired, not deleted |
| `recipes/niche_*.yaml`, `schedule.yaml`, `scheduler.py` | Five niche agents, one per night at 21:00 UTC by weekday |
| `guardrails.py` | Kill switch, daily LLM call cap, human approval queue |
| `telegram_inbox.py`, `notify.py` | Owner-only Telegram commands (/list /view /approve /reject) and alerts |
| `product_builder.py` | Approved niche drafts → xlsx / PDF / Notion zip / docx files in `exported_content/products/` |
| `llm_router.py` | Free LLM providers (keys only from environment variables) |
| `always_on.py`, `deploy/cloudflare_alarm_worker.js`, `docs/ALWAYS_ON.md` | Always-on setup (alarm clock, Mac mode, door bell) |
| `.github/workflows/agent.yml` | Cloud cycle: tests → recipe → agent cycle → Telegram → export → products → save state |
| `config.json` | Local settings and keys, git-ignored. Never commit it |

## Rules (must follow)
- Nothing auto-publishes, posts, sells or applies patches. Human approval is the final step.
- Free tools and free tiers only; no paid APIs.
- Never put tokens, API keys, chat ids or other secrets in the repo, logs, docs or chat.
- No web scraping (search engines included). Research works from the topic and stored memory.
- Product drafts must not promise income, health/medical results or jobs, and must not invent numbers or reviews.
- Off-limits topics for this project: food/recipes and fisheries/aquaculture (the research guard skips them).
- The bot commits `agent_data.db` every run: check a file still matches GitHub's latest version before changing it.

## How to run locally
```bash
python3 -m unittest discover -s tests   # offline tests (no network, LLM stubbed)
python3 workforce.py run                # one agent cycle
python3 scheduler.py --list             # schedule and tonight's niche agent
python3 guardrails.py approvals         # drafts waiting for a human
python3 product_builder.py --list       # built products
python3 guardrails.py kill              # emergency stop
```

## Conventions
- Every code change gets offline unit tests; all tests must pass before committing.
- Keep STATUS.md up to date after major changes.
- The owner chats in Tenglish (Telugu words in English letters mixed with English); explain results simply.
