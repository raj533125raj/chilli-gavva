# STATUS (read this first)

Last updated: 2026-10-15.

## Goal
A zero-cost team of AI agents that researches and drafts low-cost digital products (Google Sheets
trackers, printable planners, Notion templates, resume bundles) for buyers worldwide paying in USD.
A human approves every draft; approved drafts become ready-to-sell files that the human uploads to a
free store (Payhip or Gumroad). Nothing is published, posted or sold automatically.

## Honest state
- Real money earned so far: **$0.00**. No product has been listed or sold yet.
- No real draft has been produced by the niche agents yet; the pipeline below is built and tested.
- `engine.py` is an old simulation; its numbers are labelled `simulated` in the database and are not income.

## How it works today
1. **Five niche agents** (`recipes/niche_*.yaml`): budget sheets, freelancer trackers, ADHD planners,
   Notion templates, resume bundle. One niche runs per night at 21:00 UTC, taking turns by weekday
   (`schedule.yaml`, `scheduler.py`). Each run queues up to 3 product ideas; it skips the night when
   10 drafts already wait for approval.
2. **Research → draft** (`workforce.py`): the research step works from the topic and the agents'
   memory only (no web scraping, no news or weather feeds). Topics about food or fisheries are skipped.
   Every draft uses one fixed format: PRODUCT NAME, BUYER, PRICE (USD), PLATFORM, WHAT'S INSIDE,
   HOW TO USE, LISTING TITLE, LISTING DESCRIPTION, SEARCH TAGS (5).
3. **QA Checker** (`workforce.py`): plain rules (format complete, USD price, 5 tags, banned risky
   claims) plus a large-model check. Verdict pass / revise (one rewrite) / reject. It never approves.
4. **Human approval**: by Telegram (`telegram_inbox.py`: /list, /view, /approve, /reject), only from the
   owner's chat, or `python3 guardrails.py approve <id>`.
5. **Product Builder** (`product_builder.py`): turns approved niche drafts into files under
   `exported_content/products/` (xlsx, printable PDFs, Notion import zip, docx + listing text).
6. **Self-improvement** (`improver.py`): A/B tests prompt wording only; the draft format is fixed in code.
7. **AI providers** (`llm_router.py`): Gemini first, then Groq, GitHub Models (off until the repo variable
   `GITHUB_MODELS` is `on`), Cloudflare Workers AI, OpenRouter. Prompts are scrubbed of keys, tokens and the
   Telegram chat id. If no AI answers, no placeholder draft is made: the task retries on a later run (max 3).
   Manual health check: run the workflow with `health=yes`.
8. **Telegram** (`telegram_inbox.py`): new drafts come with Approve / Reject / View buttons; Reject asks for a
   reason (saved with the decision). `/pause` and `/resume` are the emergency brake. Answered on the next run
   (about every 30 minutes).
9. **Listing images** (`mockups.py`): every built product gets cover.png, preview-1..3.png, pinterest.png and
   LICENSE.txt (personal use only).
10. **Daily report** (`daily_report.py`): one short Telegram message on the first run after 03:30 UTC, plus a
    weekly letter on Sundays (real money only).

## Where it runs
- **GitHub Actions** (`.github/workflows/agent.yml`): tests first, then one agent cycle, Telegram inbox,
  export, product builder, and "Save state" (commits the database and new product files).
  GitHub's own timer is a backup every 3 hours; it fires rarely on its own.
- **Planned / in setup** (`docs/ALWAYS_ON.md`): a free Cloudflare Worker "alarm clock" that starts a run
  every 30 minutes within the 2,000 free Actions minutes, a Mac mode for nonstop work while the owner's
  Mac is on, and a Telegram "door bell" for fast replies. Phase A code is merged; account setup is pending.

## Quality
- About 390 offline unit tests: `python3 -m unittest discover -s tests` (CI runs them with `CHILLI_TEST=1`
  before every cycle). Tests never call real AI models or websites.

## Known gaps / next steps
- Finish the always-on setup (alarm clock, Mac mode, door bell).
- Old food and fishery recipes are still listed in `schedule.yaml` (02, 07, 12, 17 UTC). Their topics are
  now skipped by the research guard, so they cost nothing, but the schedule should be cleaned up.
- `feeds.py` and `web_fetcher.py` are no longer used by the agents and can be removed later.
- Pick prices and upload the first approved products by hand, then measure real sales.

## Safety rules
- Nothing auto-publishes; a human approves every draft and uploads every product.
- Free tools and free tiers only. Kill switch: `python3 guardrails.py kill`.
- Secrets live only in GitHub Actions secrets, the Cloudflare dashboard, or the git-ignored `config.json`.
  Never commit tokens, keys or chat ids.
