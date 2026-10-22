# STATUS (read this first)

Last updated: 2026-10-17.

## Goal
A zero-cost team of AI agents that researches and drafts low-cost digital products (Google Sheets
trackers, printable planners, Notion templates, resume bundles) for buyers worldwide paying in USD.
A human approves every draft; approved drafts become ready-to-sell files that the human uploads to a
free store (Gumroad). Nothing is published, posted or sold automatically.

## Honest state
- First product LIVE (2026-10-17, done by the owner): the ADHD Printable Planner Kit is on Gumroad;
  payout connected, downloads tested (roadmap #59 DONE). No sale recorded yet.
- Drafts 1-3 (ADHD planners) were written while the AI was down and approved; products #1 and #3 are built,
  #2 is retried by the Product Builder now that the AI works.
- `engine.py` is an old simulation; its numbers are labelled `simulated` in the database and are not income.

## How it works today
1. **Five niche agents** (`recipes/niche_*.yaml`): budget sheets, freelancer trackers, ADHD planners,
   Notion templates, resume bundle. One niche runs per night (slot hour 21 UTC with a +/-1 hour window,
   so the first run after 20:00 UTC starts it, about 20:16), taking turns by weekday
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
7. **AI providers** (`llm_router.py`): Groq first, then Gemini, GitHub Models (off until the repo variable
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
11. **Topic pool** (`niche_topics.py`): each niche's 3 starter topics seed a pool; when it runs low the AI
    suggests new specific topics. A done-topics list (plus every past task) blocks repeats and near-repeats.
    If no fresh topic is left and the AI is down, nothing is queued that night.
12. **Save state** saves only when something real changed (`save_check.py`), not just timestamps.
13. **Truth Checker** (`truth_check.py`): after every build, every promise in listing.md, README and the product
    pages is checked against the REAL files. Default answer "needs fix"; 95+ needs extra proof (rules from the
    MIT "Reality Checker" agent in msitarzewski/agency-agents). Also checks the files: empty pages, broken
    characters, cut-off text (pages and images), raw ** in images, lost apostrophes. Telegram gets
    "READY" or "NEEDS FIX" with the top 3 problems. Each folder gets TRUTH_CHECK.md.
14. **Rotation (2026-10-16):** Notion templates PAUSED (recipe kept). Thursday = 2027 dated planners
    (`recipes/niche_planners_2027.yaml`): all dates and month grids are made by code; one clear buyer each.
    Products #4 and #6 are NOT FOR SALE (files do not match listing); their files are kept.
15. **Approved design (2026-10-16)** (`pdf_design.py`): Style A "Clean Accent" (teal) for every niche, Style B
    "Soft Pastel" for ADHD planners. Every planner: cover + How to use page, footer "name · size · for personal
    use · Page X of N", last box in each column grows to the footer, nothing cut. ADHD/time-blindness planners
    can get the code-made "Today" page. 2027 month pages: Sunday-first 6-row grid, faded other days, shaded
    weekends, boxes This month's focus / Key dates / Notes.
16. **Listing images** (`mockups.py`): Inter font (`fonts/`, SIL OFL), Pillow + poppler. Cover, 3 previews and a
    pin show REAL pages of the product; every fact on them comes from `truth_check.image_facts`.
17. **New versions** (`specs/rebuild/`): #1, #2, #3 rebuilt as v2 in Style B (old folders kept, listed as
    "older versions"). Nothing is for sale until the owner decides; #4 and #6 stay NOT FOR SALE.
18. **Fixes after the v2 review:** a growing box gets a small "Notes" label and writing lines, never extra
    checkboxes (v2 rebuilt as revision 2 in the same folders). Compact cover title box. Short preview bullets.
    Dated planners can be a school year: "us-academic" (Aug-Jul, Sunday start) or "uk-academic" (Sep-Aug,
    Monday start); samples in `specs/samples/`.
19. **Bundle** (`specs/bundles/`): "ADHD Printable Planner Kit" = the real pages of #1 v2 + #2 v2 + #3 v2 in one
    Style B PDF, built into `bundle-adhd-printable-planner-kit/`. LIVE on Gumroad since 2026-10-17
    (listed by the owner; payout connected, downloads tested). Roadmap #59 done.

## Where it runs
- **GitHub Actions** (`.github/workflows/agent.yml`): tests first, then one agent cycle, Telegram inbox,
  export, product builder, and "Save state" (commits the database and new product files).
  GitHub's own timer is a backup every 3 hours; it fires rarely on its own.
- **Alarm clock (live)** (`docs/ALWAYS_ON.md`): a free Cloudflare Worker starts a run about every 30 minutes
  within the 2,000 free Actions minutes. Still planned: a Mac mode for nonstop work while the owner's Mac is
  on, and a Telegram "door bell" for fast replies.

## Quality
- About 600 offline unit tests: `python3 -m unittest discover -s tests` (CI runs them with `CHILLI_TEST=1`
  before every cycle). Tests never call real AI models or websites.

## Known gaps / next steps
- Finish the always-on setup (Mac mode, door bell).
- The schedule has only the night niche slot. Old food/fishery recipe files remain in `recipes/` but are
  not scheduled, and their topics are blocked by the research guard.
- `feeds.py` and `web_fetcher.py` are no longer used by the agents and can be removed later.
- First product is live (ADHD Printable Planner Kit). Next: measure real sales; list more products by hand.

## Safety rules
- Nothing auto-publishes; a human approves every draft and uploads every product.
- Free tools and free tiers only. Kill switch: `python3 guardrails.py kill`.
- Secrets live only in GitHub Actions secrets, the Cloudflare dashboard, or the git-ignored `config.json`.
  Never commit tokens, keys or chat ids.
