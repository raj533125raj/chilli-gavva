# Robot Map (in plain English)

This file is for the owner. It explains what the robot does, when it does it,
and where you step in. Every section ends with the same meaning in Tenglish.

## The robot in one sentence

The robot researches a niche, writes a product draft, waits for your approval,
builds the approved product, checks it honestly, fixes what it can, assembles a
hand-upload pack, and prepares a posting plan — while you watch everything from
your phone and a local dashboard.

Tenglish: "Ee robot niche research chesi draft tayaru chesthundi, mee approval
vachaka build chesthundi, nijam ga check chesthundi, thappulu sari chesthundi,
upload pack ready chesthundi, post plan unchisthundi."

## What runs when

- `schedule.yaml` fires one nightly slot at 21:00 UTC. A niche recipe runs one
  night per rotation; new recipe runs are skipped when the approval queue is
  full (ten drafts waiting).
- `.github/workflows/agent.yml` (name: agent-cycle) runs every 3 hours on
  GitHub Actions, after the previous cycle finishes, or manually. It drives
  one work cycle and also reports any failure safely.
- `.github/workflows/publish.yml` (name: publish-content) runs on pushes and
  republishes the approved public content site.
- `.github/workflows/autofix.yml` (name: Robot auto-fix) handles a failed
  run: it asks a code assistant for a safe test fix and opens a pull request.
- `.github/workflows/autofix-check.yml` (name: Robot auto-fix checks) runs on
  the robot's own auto-fix pull requests so they prove themselves before
  the owner reviews them.
- On the laptop, `start.py` starts the always-on worker (`worker_daemon.py`)
  plus the local dashboard (`serve.py`, http://127.0.0.1:8787).

Tenglish: "Robot raatri 21:00 UTC ki okka niche recipe nadipisthundi; GitHub
prati mudda-della tho agent-cycle nadapisthundi; push jarigite public site
malli publish cheyyisthundi; test fail aithe robot auto-fix workflow
nadapisthundi."

## The path of a draft

1. **Idea.** `scout.py` looks for new earning categories, `trend.py` collects
   rising tools, and `topic_pool.py` / `niche_topics.py` keep a per-niche pool
   so no topic repeats.
2. **Draft.** The engine turns a chosen topic into a draft proposal, and the
   `product_council.py` multi-stage debate either kills it or queues it.
3. **Approval gate.** `guardrails.py` keeps drafts in a human approval queue,
   and `telegram_inbox.py` sends them to your phone for a yes or no button.
4. **Build.** After approval, `product_builder.py` turns the draft into real
   files, with `pdf_design.py` pages, `mockups.py` images taken from the real
   files, and `listing_fields.py` helping you write listing text by hand.
5. **Truth check.** `truth_check.py` and `qa_checklist.py` verify the built
   product delivers every promise its listing makes.
6. **Deep QA.** `product_qa_deep.py` runs a stricter check before a pack can
   be marked ready.
7. **Auto-repair.** `product_qa_fix.py` repairs simple problems that deep QA
   found, and `product_repair.py` fixes older NEEDS FIX products cheaply.
8. **Upload pack.** `launch_pack.py` / `upload_pack.py` assemble a private
   pack (files, listing, checklist) that `scripts/check_upload_packs.py` can
   verify; you upload it yourself, with no accounts and no automation.
9. **Post plan.** `post_planner.py` writes a 30-day plan, `publish_schedule.py`
   picks fair daily posting times, and `publisher.py` runs a safe
   owner-approved posting queue; approved text also flows to
   `content_export.py` and `site_builder.py` for the public site.
10. **Honest records.** `record_earning.py` keeps only real, proof-backed
    records, and `daily_report.py` summarizes the day for you.

Tenglish: "Scout, trend, topic pool nunchi kotha ideas vasthayi; engine draft
tayaru chesi council debate chesthundi; mee ok vachaka product_builder daanni
files ga tayaru chesthundi; truth_check, deep QA, qa_checklist parishodhisthayi;
chinna thappulu product_qa_fix, product_repair sari chesthayi; upload_pack
private pack isthundi, meeru chetha upload chesukuntaru; post planner vaade
rojulakki plan unchisthundi."

## Where the owner acts (Telegram)

- `/list` lists waiting drafts, `/view <n>` shows one, `/approve <n>` or
  `/reject <n>` decides it — only the owner chat can press these.
- `/packs` lists ready packs, `/pack <n>` previews one listing.
- `/pipeline` shows the delivery pipeline, `/status` shows robot health.
- `/pause` and `/resume` stop or restart work safely.
- `/doctor` shows diagnosed failures, `/autofix_drill` tests the auto-fix loop.
- `/rules`, `/cubes` show runtime controls, `/today` and `/posted` help the
  daily posting plan, `/help` prints the catalog.
- Typed or voice natural language also works: `owner_commands.py` parses free
  text into intents and asks for confirmation on anything risky.
- The office side (agents, rooms, controls) is documented in
  `docs/office-controls.md`.

Tenglish: "Mee phone lo Telegram dwara drafts aamodinchavachhu leda
tirooskaravachhu; packs chudavachhu, robot aapavachhu, doctor report,
rules chudavachhu — anni mee owner chat lo matrame pani chesthayi."

## What is private and what is public

- **Private:** the encrypted product packs (`*.enc`), the local database
  (`agent_data.db` and its encrypted copy), `.env` and any credentials, the
  exported product folders, and approval payloads. Products never
  auto-publish; every upload is a hand upload.
- **Public:** this repository's code and docs, the safe status snapshot built
  by `public_export.py` for the dashboard, the published content site from
  `publish.yml`, and secret-free office receipts via `control_inbox.py`.
- Before anything leaves the robot, `secret_guard.py` scans for credentials
  and `injection_guard.py` filters untrusted text.
- Money is never printed in this map; the ledger module keeps real records
  with their proof references and nothing else.

Tenglish: "Mee encrypted packs, database, keys anni private; repository code,
public status, published site, office receipts matrame public. Robot bayataki
em pampina secret_guard mariyu injection_guard parishodhisthayi."

## Safety rails, in short

- `guardrails.py` owns the kill switch, the daily call cap, and the approval
  queue.
- `rest_window.py` gives the robot two short daily rest pauses.
- `maintenance.py` and `doctor.py` keep the database healthy and failures
  diagnosed.
- `improver_loop.py` self-improves only with your explicit approval.
- If a run fails, `run_failure.py` writes a summary and the auto-fix chain
  (with `autofix_retry.py`) attempts a safe repair; nothing merges without
  your decision.

Tenglish: "Guardrails robot nu control lo unchisthayi; doctor mariyu
maintenance database ni healthy ga unchisthayi; robot tana thotthi
podupu pencha ledu — meeru anumathisthe matrame."
