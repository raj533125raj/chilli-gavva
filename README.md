# Chilli Gavva Agent

A small Python robot that drafts digital products — and **waits for you**.
It researches a niche, writes a product draft, builds the approved files,
prepares a hand-upload pack with marketing assets, and stops there.
Nothing auto-publishes. The owner posts every item by hand.

- Plain tour of the whole robot: [docs/ROBOT-MAP.md](docs/ROBOT-MAP.md)
- Owner commands in Telegram (English / Tenglish): [docs/OWNER-GUIDE-TENGLISH.md](docs/OWNER-GUIDE-TENGLISH.md)
- Our word style for every message: [docs/TENGLISH-STYLE.md](docs/TENGLISH-STYLE.md)

## What the robot does

1. **Research.** Every cycle the robot studies its niche topic.
2. **Draft.** It writes a small digital product draft (title, page text, files).
3. **Approve.** The draft waits in a queue. The owner reviews it and says yes or
   no from Telegram or the local dashboard. Unapproved drafts never become files.
4. **Build.** Approved drafts are built into real product files.
5. **Check.** A separate reality-check pass reads the built files honestly and
   reports problems: empty pages, broken links, weak content.
6. **Pack.** The robot assembles a hand-upload pack: listing text, product
   carousel, pins with A/B titles, reel frames, a blog post, a launch checklist
   and SEO metadata — all saved under `exported_content/`.
7. **Post plan.** It proposes when and where to post. The owner posts by hand.
8. **Nothing publishes itself.** There is no auto-post path from the robot to
   any store or social network.

## How it runs

- **GitHub Actions** (`.github/workflows/agent.yml`) drives one work cycle
  every 3 hours. A failed run can open an auto-fix pull request
  (`autofix.yml`) that must prove itself on `autofix-check.yml` before merge.
- **publish-content** (`.github/workflows/publish.yml`) republishes the
  approved public content website on pushes.
- **Local mode.** `python3 start.py` starts the worker plus the local office
  dashboard at http://127.0.0.1:8787 — a 3D office `index.html` where every
  agent is drawn with its own name and team logo.
- **Tests.** `python3 -m unittest discover -s tests` (no network, LLM stubbed).
  CI runs Python 3.11 with only Pillow available; tests that need other
  libraries skip themselves.

## Main folders and modules

- `workforce.py` — the agent team cycle (research → draft → build → pack).
- `guardrails.py` — approvals queue, rate limits, integrity checks and the
  owner-only emergency stop.
- `product_builder.py` — turns an approved draft into product files.
- `upload_pack.py`, `pin_variants.py`, `carousel_maker.py`, `blog_post.py` —
  the marketing-asset builders that fill `exported_content/marketing/<pack>/`.
- `telegram_inbox.py` — the owner's Telegram commands (draft review, pack
  status, A/B title tracking), Tenglish phrases included.
- `secret_guard.py` — privacy scrub that runs on every save (see below).
- `exported_content/` — the product files and marketing packs, meant to be
  public.
- `cloud_runner.py` + `.github/workflows/` — the scheduled cloud cycle.
- `docs/` — guides, research notes and the robot map. `archive/` and dated
  memory folders are historical.

## Safety

This repository is **public**. The robot is written so that its mistakes
cannot leak private data:

- **Secret guard.** `secret_guard.py` scrubs and blocks every save that
  contains keys, tokens, emails or other credentials.
- **Encrypted database.** The working database `agent_data.db` is git-ignored.
  The copy stored in git is `agent_data.db.enc`, encrypted with AES-256
  (PBKDF2 with 200 000 iterations). The key lives only in GitHub Actions
  secrets and the owner's password manager.
- **No money in public.** No earnings, prices or amounts are stored in this
  public repo; the private copy lives outside it.
- **Owner gates everything.** Drafts need approval, publishing needs the
  owner, and the emergency stop is an owner-only command kept out of public
  docs.
- **Git history is forever.** Anything once committed stays readable in
  history, so the guard runs *before* the save, not after.

## Licence

This project is released under the **MIT licence**. The office theme keeps its
credit to [LimeZu](https://limezu.itch.io/modernoffice) — thank you for the
Modern Office art style the visuals draw on.

More guides live in [`docs/`](docs/): the robot map, the owner guide, the
Tenglish style guide, upload-pack details, bundle notes and setup.
