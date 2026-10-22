# Automatic publishing

This is an owner-approved plan for sending already approved work to Gumroad,
Pinterest, and a Telegram channel. It starts with every publishing switch off.
Nothing is sent until the owner turns on the matching switch and puts an
approved item in the publish queue.

## What it does

- Gumroad changes can run when an approved change is waiting.
- Pinterest has three planned daytime slots in India.
- Telegram channel posts have two planned daytime slots in India.
- A planned item is sent only once for each place.
- A temporary problem is tried one more time. Missing setup is simply skipped.
- The daily owner report says how many posts were sent or failed.

## Switches and stopping

There is one separate switch for Gumroad, Pinterest, and Telegram. All three
are off at first. The owner can pause all publishing at once by sending
`/pause` to the robot. Send `/resume` to allow the enabled switches to work
again. Pause does not approve, publish, or delete any draft.

## Setup to do later

Add these names as GitHub secrets only after the related account is ready:

- `GUMROAD_TOKEN`
- `PINTEREST_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`

Do not put these values in this public repository. Until they are added, the
matching publishing job is skipped safely.
