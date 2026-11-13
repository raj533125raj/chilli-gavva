# Telegram New Commands — Tenglish Guide

This page explains the OWNER-ONLY Telegram commands for this bot. It is written in Tenglish house style for the owner. Nothing here is public. If a command touches a money amount, the example uses `[amount]` and no real number is written.

Use `/menu` to see the live menu, and keep this page for the full meaning of each command.

---

## First 10 minutes

When you first open Telegram with the bot, do these in order:

1. Send `/start` or `/menu` to wake the bot and see the menu.
2. Send `/help` if you want the short command list as a reminder.
3. Send `/status` to see today's draft count and where the robot is.
4. Send `/health` to see the system self-check card (green / amber / red).
5. Send `/logs` if you want to read the last run summary.
6. Send `/list` to see drafts waiting for your approval.
7. Send `/packs` to see which packs are ready for upload.
8. Send `/pipeline` to see today's plan versus done.
9. Send `/rules` if you want the house rules again.
10. Send `/cubes` if you want the office workspace list.

After this, use the grouped commands below whenever you need a specific action.

---

## Drafts

These commands are about product drafts waiting for your approval.

### /list

Shows drafts waiting for a human approval.

Example:
- `/list`
- `list`

What the reply looks like:
- A numbered list of drafts with their title, niche, current status, and how long they have been waiting.

When to use it:
- When you want to approve or reject something and you do not remember what is waiting.

### /view

Shows the full draft you choose.

Example:
- `/view 2`
- `view 2`

What the reply looks like:
- The draft title, niche, full description, and the current review notes for that draft.

When to use it:
- Before you approve or reject, when you want to read the full draft first.

### /approve

Approves a draft so the robot can continue building it.

Example:
- `/approve 2`
- `approve 2`

What the reply looks like:
- A confirmation that the draft is approved and the next step it will trigger.

When to use it:
- After you read the draft with `/view` and you are happy with it.

### /reject

Rejects a draft so the robot stops working on it.

Example:
- `/reject 2`
- `reject 2`

What the reply looks like:
- A confirmation that the draft was rejected, with a short reason line if you added one.

When to use it:
- When a draft is off-topic, weak, or not worth continuing.

### /today

Shows the draft plan for today.

Example:
- `/today`
- `today`

What the reply looks like:
- Today's draft plan, usually with the count of planned drafts and what stage they are in.

When to use it:
- At the start of the day, to see what the robot is working on today.

### /pipeline

Shows the plan versus done for the current pipeline.

Example:
- `/pipeline`
- `pipeline`

What the reply looks like:
- A small plan versus done summary for the stages today.

When to use it:
- When you want a quick sense of whether today's work is on track.

---

## Packs

These commands are about ready product packs.

### /packs

Lists the packs that are ready for upload.

Example:
- `/packs`
- `packs`

What the reply looks like:
- A list of ready packs, usually with their pack number and short name.

When to use it:
- When you want to know which packs are ready to upload or share.

### /pack

Shows one ready pack in detail.

Example:
- `/pack 3`
- `pack 3`

What the reply looks like:
- The pack folder detail for that pack number, including what files exist and what is still missing.

When to use it:
- When you want details for one pack before you upload it or check its files.

---

## Marketing

These commands are about marketing copy, schedules, and reports.

### /marketing

Shows the marketing summary for a pack.

Example:
- `/marketing 3`
- `marketing 3`

What the reply looks like:
- A marketing summary for the pack, such as the post plan, channels, and asset status.

When to use it:
- When you want the marketing picture for one pack.

### /post

Controls posts from the phone. This is the posting command, not an auto-post command.

Example:
- `/post today`
- `/post tomorrow`
- `/post week`

What the reply looks like:
- `/post today`: sends the posts ready for today with photos, captions, and a reply keyboard for each post.
- `/post tomorrow`: gives a text preview of tomorrow's posts only.
- `/post week`: gives the weekly post schedule.

When to use it:
- `/post today` when you are ready to post today's content from your phone.
- `/post tomorrow` when you want to preview tomorrow before posting.
- `/post week` when you want the weekly schedule.

### /posted

Marks a post as done after you posted it.

Example:
- `/posted 2`
- `posted 2`

What the reply looks like:
- A short confirmation that post N is marked done, plus the updated week summary.

When to use it:
- After you actually posted a post on your phone and want the robot to mark it done.

### /ab

Opens the A/B test control for a pack.

Example:
- `/ab 3`
- `ab 3`

What the reply looks like:
- The current A/B status for that pack and what can be changed.

When to use it:
- When you want to check or adjust an A/B test on a pack.

### /week

Shows the week view for posts or planning.

Example:
- `/week`
- `week`

What the reply looks like:
- A weekly summary view, depending on the current context.

When to use it:
- When you want the week-level view instead of just today.

### /ops

Shows the weekly operations summary.

Example:
- `/ops`
- `ops report`

What the reply looks like:
- A text ops summary with runs, failures, retries, and provider notes.

When to use it:
- When you want the weekly ops summary in text.

### /dashboard

Sends the self-contained HTML ops dashboard as a document.

Example:
- `/dashboard`
- `dashboard`

What the reply looks like:
- A document message with the HTML ops dashboard.

When to use it:
- When you want the full dashboard file instead of the text report.

### /seo

Shows the SEO score and top fixes for one pack.

Example:
- `/seo 1`
- `seo 1`

What the reply looks like:
- The SEO score out of 100, a short summary, and the top 3 fixes for that pack.

When to use it:
- When you want to know how SEO-ready a pack is.

### /lint

Checks the listing format rules for one pack.

Example:
- `/lint 1`
- `lint 1`

What the reply looks like:
- PASS / WARN / FAIL counts and the first 5 problem lines for that pack.

When to use it:
- When you want to check listing format problems before upload.

### /faq

Shows the buyer FAQ for one pack.

Example:
- `/faq 1`
- `faq 1`

What the reply looks like:
- The FAQ markdown text for the pack, or a document if it is long.

When to use it:
- When you want to read or send the buyer FAQ for a pack.

### /dup

Checks duplicate content between packs.

Example:
- `/dup`
- `dup`

What the reply looks like:
- A duplicate content report with any pairs above 0.5 similarity and why they matched.

When to use it:
- When you want to see whether two packs are too similar.

### /keywords

Lists the top keywords from the keyword bank.

Example:
- `/keywords`
- `keywords`

What the reply looks like:
- The top 20 keywords from the keyword bank.

When to use it:
- When you want the strongest keywords for planning or checking a pack.

### /calendar

Sends the post plan calendar file as a document.

Example:
- `/calendar`
- `calendar`

What the reply looks like:
- A document message with the `.ics` calendar file for the post plan.

When to use it:
- When you want the posting schedule as a calendar file.

### /versions

Shows the latest version and file changes for one pack.

Example:
- `/versions 1`
- `versions 1`

What the reply looks like:
- The latest semantic version, the previous version if any, and the added, modified, and removed files.

When to use it:
- When you want to know what changed in a pack.

### /ideas

Scores and ranks the latest scout ideas.

Example:
- `/ideas`
- `ideas`

What the reply looks like:
- A scored list of the latest scout ideas on a 0–100 scale with short reasons.

When to use it:
- When you want to compare new product ideas the scout found.

### /boards

Shows the Pinterest board plan summary.

Example:
- `/boards`
- `boards`

What the reply looks like:
- Board names, pin counts, and a short summary of the Pinterest board plan.

When to use it:
- When you want the Pinterest board plan at a glance.

---

## Selling

These commands are about sellability and sales notes.

### /ready

Shows which packs are sellable now.

Example:
- `/ready`
- `/ready 2`
- `ready`
- `pack 2 ready aa`

What the reply looks like:
- Without a number: ready packs grouped by sellability status with the top 3 fixes for each.
- With a number: the full sellability card for that pack with Kit, Files, and Marketing buttons.

When to use it:
- When you want to know what can be sold now and what still needs fixing.

### /sales

Records a sale note or shows a sales summary.

Example:
- `/sales today`
- `/sales week`
- `sale pack 3 Gumroad`
- `pack 5 ki 2 sales Etsy`
- `sale pack 3 Gumroad note: great customer`

What the reply looks like:
- A recording confirmation for a sale note, or a sales summary for the time period you asked for.

When to use it:
- When you want to log a sale, or when you want the sales summary for today, this week, this month, or all.

### /autoapprove

Shows or changes the safe auto-approve settings.

Example:
- `/autoapprove`
- `autoapprove`
- `autoapprove on`
- `autoapprove off`
- `autoapprove 80`
- `autoapprove limit 1`

What the reply looks like:
- Status view: current on/off state, score threshold, daily limit, today's count, and the available commands.
- Change commands: a Yes/No confirmation keyboard first, then a confirmation reply after you answer.

When to use it:
- When you want to let the robot auto-approve safe drafts, or change the threshold or daily limit.
- Note: changing a setting asks for a Yes/No confirmation before it is applied.

---

## Office

These commands control the Binny Mills office layout and agent appearances.

### /office

Opens the office menu.

Example:
- `/office`
- `office`
- `office menu`

What the reply looks like:
- The current rooms list, the recent changes, and example phrases you can use.

When to use it:
- When you want to see the office layout or start an office change.

Common office phrases:
- `office lo Pinterest room pedda chey`
- `QA room ni Products pakkana pettu`
- `kotha room Research add chey`
- `Marketing room remove chey`
- `Pin Designer ki red cap pettu`
- `Doctor ki blue theme pettu`
- `CEO ki star icon pettu`
- `office change undo`

What the reply looks like for a change:
- A confirmation that the request was queued, with a preview line and an Undo / Office menu keyboard.

When to use them:
- When you want to resize, move, add, remove, rename, or restyle a room or agent in the office.

### Undo from office

Example:
- `office change undo`
- `undo`
- `↩️ Undo` from the keyboard

What the reply looks like:
- A confirmation that the last office change was undone, or a note that there is nothing recent to undo.

When to use it:
- When you want to roll back the last office change.

---

## Files

These commands send pack files to your phone.

### /files

Sends the files for a pack.

Example:
- `/files 1`
- `files 1`

What the reply looks like:
- The pack files sent to your chat, if they exist.

When to use it:
- When you want the pack files on your phone.

### /covers

Sends or handles cover files for a pack.

Example:
- `/covers 1`
- `covers 1`

What the reply looks like:
- The cover images for the pack, if they exist.

When to use it:
- When you want the pack covers.

### /pins

Sends or handles Pinterest pin files for a pack.

Example:
- `/pins 1`
- `pins 1`

What the reply looks like:
- The pin images for the pack, if they exist.

When to use it:
- When you want the pack pins.

### /carousel

Sends or handles carousel slides for a pack.

Example:
- `/carousel 1`
- `carousel 1`

What the reply looks like:
- The carousel slides for the pack, if they exist.

When to use it:
- When you want the pack carousel slides.

### /reel

Sends or handles reel frames for a pack.

Example:
- `/reel 1`
- `reel 1`

What the reply looks like:
- The reel frames for the pack, if they exist.

When to use it:
- When you want the pack reel frames.

### /blog

Sends or handles the blog post for a pack.

Example:
- `/blog 1`
- `blog 1`

What the reply looks like:
- The blog post for the pack, if it exists.

When to use it:
- When you want the pack blog post.

### /landing

Sends or handles the landing page content for a pack.

Example:
- `/landing 1`
- `landing 1`

What the reply looks like:
- The landing page content for the pack, if it exists.

When to use it:
- When you want the pack landing page content.

### /kit

Sends the store listing kit file for a pack.

Example:
- `/kit 3`
- `/kit 3 etsy`
- `kit 3 pampu`
- `kit 3 etsy pampu`

What the reply looks like:
- The kit file sent as a document, plus a short text with the first 3 fields.

When to use it:
- When you want the store listing kit for a pack.

Supported stores include gumroad, payhip, etsy, kofi, topmate, and instamojo.

### /launch

Shows or sends the launch checklist for a pack.

Example:
- `/launch 1`
- `launch 1`

What the reply looks like:
- The launch checklist for the pack, if it exists.

When to use it:
- When you are about to launch a pack and want the checklist.

### /decide

Opens the decide flow for a pack.

Example:
- `/decide 1`
- `decide 1`

What the reply looks like:
- The decide view for the pack, where you can choose the next action.

When to use it:
- When you want to make a decision on a pack from the phone.

---

## Settings

These commands control bot settings and state.

### /settings

Shows or changes your bot settings.

Example:
- `/settings`
- `settings`

What the reply looks like:
- Your current settings and the available settings commands.

When to use it:
- When you want to check or change your settings.

### /rules

Shows the bot rules.

Example:
- `/rules`
- `rules`

What the reply looks like:
- The house rules in short form.

When to use it:
- When you want a reminder of the rules.

### /cubes

Shows the office workspace cubes.

Example:
- `/cubes`
- `cubes`

What the reply looks like:
- The list of workspace cubes.

When to use it:
- When you want the workspace list.

### /doctor

Runs the doctor self-check.

Example:
- `/doctor`
- `doctor`

What the reply looks like:
- The doctor check result with any issues and hints.

When to use it:
- When something feels stuck and you want a deeper self-check.

### /pause

Pauses the robot.

Example:
- `/pause`
- `pause`

What the reply looks like:
- A confirmation that the robot is paused.

When to use it:
- When you want the robot to stop working for now.

### /resume

Resumes the robot.

Example:
- `/resume`
- `resume`

What the reply looks like:
- A confirmation that the robot resumed.

When to use it:
- After a pause, when you want the robot to start again.

### /keyboard

Controls the reply keyboard.

Example:
- `/keyboard`
- `keyboard`

What the reply looks like:
- The keyboard control reply or current keyboard state.

When to use it:
- When you want to change the reply keyboard behavior.

---

## Health

These commands are about system health and undo.

### /health

Runs the system self-check.

Example:
- `/health`
- `health`
- `health check`
- `arogyam`

What the reply looks like:
- A single health card with green, amber, or red status for each part, plus a short Tenglish fix hint for any red line.

When to use it:
- When you want a quick look at whether the system is healthy.

### /logs

Shows the last run summary.

Example:
- `/logs`
- `logs`
- `last run logs`

What the reply looks like:
- The last run summary with safe lines only.

When to use it:
- When you want the most recent run summary without secrets.

### /undo

Undoes your last Telegram action when it is safe.

Example:
- `/undo`
- `undo`
- `undo action`

What the reply looks like:
- A confirmation prompt with a Yes/No keyboard describing the action that would be undone.

When to use it:
- When you want to reverse a recent approval, rejection, settings change, or office request.

### /logs and /undo together

If you are unsure what happened, use `/logs` first, then `/undo` if you need to reverse something recent.

---

## Built-in commands you will see often

These are the basic commands the bot recognises everywhere:

- `/start` — start the bot
- `/help` — short help
- `/menu` — the main menu
- `/status` — current status
- `/list` — drafts waiting
- `/view` — view one draft
- `/approve` — approve a draft
- `/reject` — reject a draft

---

## A small note on examples

If you ever see an example with a money amount in another file, this bot replaces it with `[amount]` in owner-facing text. This page follows that rule too, so no real money amount appears here.

---

Owner-only guide. No secrets. No public amounts. Tenglish house style.
