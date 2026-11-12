# Weekly Routine — Tenglish

Simple weekly rhythm for the owner. Every step is one command from the existing tools; nothing auto-publishes or posts. Human approval stays in the middle.

## Daily

1. `python3 telegram_inbox.py today` — open `/today` in Telegram and read the day's starting plan.
2. `python3 telegram_inbox.py post` — post the prepared pin/reel/blog item for the day.
3. `python3 telegram_inbox.py view` — check what got posted today (`posted N` line).
4. `python3 guardrails.py approvals` — see drafts waiting for your approval.

## Monday

1. `python3 workforce.py run` — let one agent cycle finish a fresh draft.
2. `python3 product_builder.py --list` — see what ready packs exist this week.
3. `python3 guardrails.py pack_report` — read the pack report for the week.
4. `python3 guardrails.py listing_lint` — run listing lint on the latest drafts.

## Wednesday

1. `python3 guardrails.py uploads` — check upload-ready listings for the day.
2. `python3 product_builder.py upload` — upload the approved listing files to the store folder.
3. `python3 telegram_inbox.py view` — confirm what got uploaded today.

## Friday

1. `python3 guardrails.py seo_score` — check SEO score for the week's listings.
2. `python3 guardrails.py keyword_bank` — review the keyword bank for the niche.
3. `python3 guardrails.py edit` — fix any SEO gaps in the listing text.

## Sunday

1. `python3 telegram_inbox.py letter` — write the Sunday letter in Tenglish.
2. `python3 telegram_inbox.py newsletter` — draft the weekly newsletter from the letter.
3. `python3 guardrails.py calendar` — open the calendar and plan next week.
4. `python3 telegram_inbox.py view` — confirm what got sent this week (`posted N` line).
