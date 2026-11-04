# Robot fix runbook

## Second Gemini key is empty

`GEMINI_API_KEY_2: EMPTY` means the optional backup Gemini credential was empty
in that run's environment. It does **not** mean the primary Gemini credential
is empty, and it does not by itself stop the robot. This checkout maps
`${{ secrets.GEMINI_API_KEY_2 }}` into the workflow environment; GitHub returns
an empty string when a referenced secret is unset. An old run cannot establish
the secret's current value or whether a later run can access it.
See [GitHub's secret setup and availability rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

### Exact owner steps

1. Open the [chilli-gavva repository](https://github.com/raj533125raj/chilli-gavva)
   using the owner's GitHub account. If GitHub requests a password, passkey,
   two-factor code, token, or login, the owner completes that privately; an
   assistant stops at that step. No secret value belongs in this runbook or chat.
2. Open **Settings → Secrets and variables → Actions → Secrets**. The direct
   [Actions secrets page](https://github.com/raj533125raj/chilli-gavva/settings/secrets/actions)
   opens the same repository settings. Use a repository **secret**, not an
   Actions variable, and use this repository rather than a fork.
3. Find the exact name **`GEMINI_API_KEY_2`**. If it is absent, choose
   **New repository secret**, enter `GEMINI_API_KEY_2` in **Name**, paste the
   owner's valid second Gemini key into **Secret**, then choose **Add secret**.
   If it exists, open its edit control, replace its value privately, and choose
   **Update secret**. GitHub does not display the stored value for comparison.
4. Paste only the key value: no quotes, spaces, `GEMINI_API_KEY_2=` prefix, or
   `YOUR_...` placeholder. Leave `GEMINI_API_KEY` and `GEMINI_AUTOFIX_KEY` alone.
   If the owner does not have a valid second key, leaving this optional secret
   absent is supported; adding a placeholder does not create working failover.
5. If a newly started run still reports EMPTY, verify the name and repository,
   that it is under **Secrets**, and that any organization-level secret grants
   this repository access. Environment secrets require the job to select that
   environment; a repository secret avoids that mismatch here. Fork-triggered
   and Dependabot workflows do not normally receive these secrets. These are
   checks to make, not a claim about the cause of the observed run.
6. Let the next scheduled `agent-cycle` run start, or, when the owner wants a
   manual run, open **Actions → agent-cycle → Run workflow**, select **main**,
   leave `force` at **no**, and choose **Run workflow**. The optional `health`
   input can be **yes** for a general provider check. Inspect the **new run**;
   changing a secret does not alter the environment or logs of an existing run.

Only check that the value is present/masked rather than EMPTY; never print,
echo, or copy it to prove configuration. A green run or a successful Gemini
health line is not proof that the second key was exercised. The health target
builder currently probes only `GEMINI_API_KEY`; it does not independently test
`GEMINI_API_KEY_2`. Normal text routing uses the backup only after the primary
path fails. No live run or secret change was performed for this documentation.

### Confirmed fallback behavior

The following describes the code in S1 commit `c4b6927`, not a live provider test.

| Code | Behavior with an empty second key |
| --- | --- |
| [Workflow environment](../.github/workflows/agent.yml#L114) | Passes the two secrets separately. An empty second value does not replace the primary value. |
| [`_gemini`](../llm_router.py#L455) | Builds `keys_to_try` from primary, then secondary, filtering out empty values and `YOUR_` placeholders. With a valid primary and empty secondary, only the primary is attempted. If neither is configured, it returns `None` before any Gemini request. |
| [`_gemini` model loop](../llm_router.py#L463) | Tries configured Gemini models for each available key. Missing/retired models, rate limits, server errors and timeouts can advance to another model; an authentication failure moves to the next available key. An empty backup is skipped, never sent as a credential. If all attempts fail, it returns `None`. |
| [`chat`](../llm_router.py#L623) | A Gemini `None` result or provider exception continues to another provider in the router's task-health order. The default list is Groq, Gemini, Cloudflare, OpenRouter, FreeLLMAPI; recent task results can reorder it. Missing credentials skip that provider. If all providers fail or the global guard blocks calls, the result is `None`, not fabricated AI text. |
| [`_request_json`](../llm_router.py#L192) | Actual attempts still reserve model capacity and check the global call budget. Key rotation does not bypass these controls. The backup increases available routes; it does not guarantee provider availability or additional quota. |
| [`chat_vision`](../llm_router.py#L705) and [`chat_audio`](../llm_router.py#L788) | Also filter empty keys and keep using an available primary. They are Gemini-only: without a working Gemini route they return `None`, rather than switching to a text-only provider for media. |
| [`provider_status`](../llm_router.py#L852) | Reports Gemini configured if either usable-looking key is present. It does not validate a key or prove the second one works. |
| [`_targets`](../llm_router.py#L951) and [`gemini_model_names`](../llm_router.py#L1032) | General health probes and model discovery currently select only the primary key, even though runtime chat can use the second key. |

The robot therefore falls back safely with this optional secret empty, provided
another configured route is available. If no AI route answers, research/draft
steps raise `AIUnavailable`; [the worker](../workforce.py#L814) follows its normal
retry policy and stops further AI work in that cycle. Tasks can eventually be
marked failed after the retry limit, so safe fallback is not a promise that all
work will finish. The empty second key alone does not explain Telegram delivery
failures; investigate those using the separate section below.

Verification for this section is offline: fake credentials and blocked network
access confirm primary-only success, no Gemini request when both keys are empty,
text-provider fallback after primary failure, and `None` when all routes fail.
Existing router, resilience, and health tests are also run. No real key was read,
requested, entered, or tested.

## Telegram silence investigation — 4 November 2026

The exact live cause of the owner's silence is **not yet verified**. This checkout
has no production Telegram/Cloudflare credentials or decrypted production database.
The changes below repair reproducible delivery/diagnostic bugs; they do not establish
that the live bot has recovered. Nothing was pushed and no live Telegram message,
workflow dispatch, webhook change, or receipt deletion was performed during this repair.

Evidence checked:

- [Run 41876181288](https://github.com/raj533125raj/chilli-gavva/actions/runs/41876181288)
  (4 November, 17:17 UTC) executed the inbox successfully on code from main.
  Both Telegram secrets are present (masked). `GEMINI_API_KEY_2` is empty.
  The only inbox output exposed is `Control inbox: KV read failed (HTTP 400)`
  followed by `Telegram inbox: done`. Output suppression is in `run_failure.run_step`;
  success meant the child exited zero, not that a message reached the owner.
- [Run 41148651835](https://github.com/raj533125raj/chilli-gavva/actions/runs/41148651835)
  (31 October, 00:10 UTC) already has the same KV HTTP 400 and green inbox.
- Read-only GitHub inspection found a separate `robot-daily-report/YYYY-MM-DD`
  receipt tag for **every day from 30 October through 4 November**. One old
  reservation therefore cannot explain all subsequent days. Under the reviewed
  code, reaching each reservation requires a successful update poll first.
  A continuously rejected token (401) or a continuously conflicting webhook/reader
  (409) would stop that path with a failed inbox, rather than create these receipts.
  This does not prove the accepted token belongs to the bot the owner is messaging.
- No reviewed code change changes the Telegram secret values. A wrong but valid
  bot token, a wrong destination chat, a separate consuming reader, or a stale
  offset needs a live check. None is labelled as the proven root cause here.

History review:

| Change | Finding |
| --- | --- |
| R58 `f44008d`, `959e807`, `a4c17c9` | Landed before the reported last message. Per-update acknowledgements precede side effects, so a crash can lose one acknowledged command/reply; they are not a permanent send reservation. Daily receipts are date-scoped. Successful output remained hidden. |
| Office integration `dfa7882` through `6f1c7bf`, R63 `2365c26` | KV failures are nonfatal and the office inbox runs independently of Telegram. Its HTTP 400 cannot itself stop the Telegram poll. |
| J8 `b3328c8` / merge `a111554` | Drill handling applies to `/autofix_drill`; ordinary approval/status handling does not require Gemini. An empty second Gemini key does not block these commands or a daily report. |
| R64 router/test isolation | Router/implicit test database changes do not reserve Telegram sends or alter its API credentials. Approval and `/status` transport tests use explicit temporary databases. |
| R65 `1453ddb` | Adds delivery-readiness notifications after update handling. Does not replace approval callbacks or polling. |
| Minutes saver `875e9db`, CI fixes `d07d8cb`, `c3f3317`, `9a7f933` | Do not remove the inbox or its Telegram secrets. The inspected recent run skipped only the unchanged test suite; it executed the inbox. |
| JA dedupe `3b9b3be` | Landed after the reported start of silence. Only deduplicates equivalent text inside one fetched batch; buttons are still processed. Scrubbing preserves nonempty approval/status messages in the transport tests. Its extra IST gate disagreed with the UTC receipt key near UTC midnight. |

Repaired behavior:

- Every inbox invocation emits fixed safe lines for configuration, webhook presence,
  HTTP outcomes for `getMe`, `getChat`, `getWebhookInfo`, `getUpdates`, `sendMessage`
  and callback acknowledgements, plus update/button/command/handled/send/other-chat
  counts and error kinds. `not_called` is explicit. Retry outcomes are counted.
  Diagnostics are printed in `finally`, including on failure, and survive the
  workflow wrapper's successful-output filter. API descriptions, request URLs,
  credentials, chat/update identifiers and message text are never copied into them.
- Token/chat preflight happens before consuming updates. A configured webhook
  stops polling with a fixed explanation. Missing configuration makes the CLI fail
  in GitHub Actions. Failed sends make the inbox fail instead of silently green.
- Failed draft announcements no longer advance their announcement marker. Successfully
  sent cards advance it one at a time. Empty scrubbed messages are rejected explicitly.
- Both daily date gates and the independent receipt now use the same IST date.
  The existing first-run-after-03:30-UTC schedule is retained. A send timeout still
  consumes that day's reservation because delivery is uncertain; automatic retries
  would risk a duplicate. A receipt is evidence of an attempt, not confirmed delivery.

### Exact owner checks for Telegram

1. In a **private local terminal**, run `python3 scripts/telegram_probe.py` with
   Python 3.11+. Enter the current bot token and owner chat ID at the hidden prompts,
   and the username of the bot you actually message. Use your private stored values;
   never paste them into chat, an issue, a command argument or a workflow log.
   Before running, send `/status` to that bot. The probe calls `getUpdates` **without
   an offset**, never acknowledges an update, and never writes the database.
2. Read only its fixed results:
   - `bot_identity=mismatch`: the accepted token belongs to a different bot.
     In BotFather, select the bot with the expected username and copy its current
     token privately. Update repository **Settings → Secrets and variables → Actions →
     TELEGRAM_BOT_TOKEN**, and the alarm Worker's secret of the same name. Deploy
     the Worker settings. Do not rotate a working token just to run this check.
   - `getMe HTTP=401x1`: replace the rejected token in both places with the current
     token for the expected bot.
   - `getChat HTTP=400x1`: replace `TELEGRAM_CHAT_ID` in GitHub and the Worker with the
     correct private chat ID, without quotes, a URL or a bot username. Send `/start`
     to the expected bot if this chat has never started it. A 403 means check that
     the bot is unblocked and has permission to access/send in this chat.
   - `webhook=set` or `getUpdates HTTP=409x1`: this project uses polling. Stop any
     other consuming bot service/Mac worker. If an unintended webhook is set, remove
     it from the service that installed it; preserve pending updates
     (`deleteWebhook` with `drop_pending_updates=false`). The probe never removes it.
   - Successful setup plus `other_chat>0` and zero owner commands after a fresh
     `/status`: compare the configured chat ID privately. Successful setup with
     zero pending updates also needs a check for another consuming reader; it does
     not prove that the owner sent nothing. A token for another valid bot is caught
     by the username comparison above.
3. Repeat with `python3 scripts/telegram_probe.py --send-test` to send **one fixed
   delivery-check message** after identity/chat checks pass. This is optional and
   independent of the once-daily report receipt. Confirm it reaches the intended chat.
4. After these code changes are reviewed and installed on main, start **Actions →
   agent-cycle → Run workflow**, send a fresh `/status`, and press Approve on one
   still-pending real draft. Confirm one approval reply and one changed draft status.
   Old updates acknowledged before a crash or failed reply cannot be reconstructed
   from Telegram. Reissue the intended command; do not bulk-approve other drafts.
5. Confirm daily receipt/send diagnostics on the next due IST day. Do not delete
   receipt tags merely to force a report: a previous send may already have delivered.

Telegram reference: [polling and update acknowledgements](https://core.telegram.org/bots/api#getupdates).

### Exact owner checks for KV HTTP 400

The REST route is `/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/keys`.
The `prefix=control:request:` and `limit=1000` query values are valid under the
[Cloudflare list-keys API](https://developers.cloudflare.com/api/resources/kv/subresources/namespaces/subresources/keys/methods/list/).
The request now uses standard query encoding (including opaque cursors) and trims
surrounding whitespace from the account ID, namespace ID and bearer token. The
signing key remains byte-for-byte unchanged. The old log does **not** say whether
the rejected request was the list, a value read, or a receipt read. New safe HTTP 400
diagnostics identify that route and report each ID's format as `valid` or `invalid`.
Valid format alone cannot prove that an ID belongs to the intended account/namespace.

1. Cloudflare dashboard → **Workers & Pages → the office/control Worker → Settings →
   Bindings**. Find the KV namespace actually used for `control:request:*`. This may
   be different from the alarm clock's `STATE` namespace.
2. **Storage & Databases → KV → that namespace**: privately copy its **namespace ID**
   (32 hexadecimal characters). Do not copy its display name, binding name (`STATE`),
   a dashboard URL, or a JSON object. Update the repository Actions secret
   **CF_KV_NAMESPACE_ID** with just that ID.
3. From that same Cloudflare account's overview, privately copy its **Account ID**
   (32 hexadecimal characters), and update **CF_ACCOUNT_ID**. Do not use a zone ID.
4. **My Profile → API Tokens**: the bearer token in **CF_API_TOKEN** must have
   **Account → Workers KV Storage → Edit** on that account. It is an API token,
   not the Global API Key. Leave **CONTROL_SIGNING_KEY** aligned with the office
   Worker; changing it independently invalidates all request/receipt signatures.
5. Run one reviewed workflow. If HTTP 400 remains, inspect only
   `Control inbox diagnostic: request=... account_id=... namespace_id=...`.
   An invalid shape identifies which secret needs correction. For valid shapes,
   compare account/namespace ownership in the dashboard; do not post the rejected
   URL, secret values, key names or API body. A 403 instead points to token scope.

### Local verification

Run `bash scripts/ci_parity.sh` using Python 3.11+ with Pillow and Poppler available.
It runs the full offline suite once with `CHILLI_TEST=1` and once without it;
both must report PASS. `CI_PARITY_PYTHON` can select an installed interpreter.
The transport regressions use fake HTTP responses, temporary databases and private
date receipts; they create no real Telegram messages, GitHub tags or Cloudflare writes.

Repair validation on 4 November: **CI mode PASS** (1,204 tests, 2 skipped) and
**local mode PASS** (1,204 tests, 2 skipped), using Python 3.11.18, Pillow 11.3.0
and Poppler. The local parity script canonicalizes the macOS temporary-directory
path for the existing drill subprocess tests. Localhost server tests ran outside
the filesystem/network sandbox. Secret guard and whitespace checks passed.
`agent_data.db.enc`, `products.tar.gz.enc`, `.scheduler_locks`, and
`web/data/status.json` are byte-for-byte unchanged from the starting revision.

The robot retries one failed run quietly. If the next run also fails, it sends one
message with the failed step, a safe problem summary, and a link to the run.
A successful run resets the count. Further failures in the same streak do not
send the same alert again once it has been delivered. If delivery fails, the next
failed run tries to deliver it again.

The repeated-failure alert ends with exactly one dispatch outcome, also printed
in the run log: `Auto-fix: started`, or `Auto-fix: not started — <reason>`.
The reasons are: `setup/workflow problem needs a human`, `already tried in the
last 24 h`, `no GitHub token`, or `GitHub refused the request`. Started means
GitHub accepted the dispatch; the later proposal still has to pass its checks.
Telegram delivery failure does not prevent dispatch, and a later notification
attempt respects the saved dispatch reservation.

When auto-fix is configured, the second failure also starts a repair proposal.
Only one new problem can start in 24 hours, including across successful runs. Workflow
file failures and setup problems go straight to this runbook because the AI cannot
edit workflows or install runner dependencies. Missing tools and apt failures use
`setup problem: <tool>` in the safe summary; unrecognized paths/modules use a fixed
generic label, never private details. A refused or timed-out dispatch keeps its
daily reservation to avoid duplicates, but is not recorded as a running repair.
If every attempted AI provider is busy, rate limited, or times out without a
proposal, auto-fix waits quietly. The next failing robot cycle after 30 minutes
starts another attempt for the same problem, up to three attempts total. The
alarm clock normally starts cycles every 30 minutes; delayed cycles delay retries.
These retries do not consume the daily allowance for a new problem. A passing
robot cycle cancels pending retries silently, including late results from an
attempt that was already running.

Busy attempts 1 and 2 send no auto-fix failure message. After busy attempt 3,
Telegram sends once: "Auto-fix tried 3 times, AI servers were busy. Use the
robot-fix-runbook." Other failures, including AI making no change, send the
manual-repair message immediately.

When the proposal's checks pass, Telegram offers **Merge** and **Close**. Merge
checks the same commit again and uses squash merging. A changed, failing, or
unfinished proposal cannot be merged by that button. Buttons are handled on the
next robot run; the recovery inbox still runs when the main tests fail.

1. Open Codex with this robot repository.
2. Paste the complete alert and ask: "Fix this failure, run the full tests twice,
   and commit the fix. Never push or print secrets. Stop if a login is needed."
3. Review Codex's result. Keep passwords, keys, tokens, and private account details
   out of the conversation.

The counter is saved separately in the workflow cache, including after a tests or
save failure. It contains the count, delivery status, daily new-problem timestamp,
last successful cycle time, and retry metadata (problem identifier, attempt,
safe summary, status, and due time). A short-lived outcome artifact contains only
safe summary and status fields. Raw provider output, private logs, and the
database are excluded.
If the cache is unavailable or has been removed, a new streak starts at zero.

## 31 Oct red runs

[41198138044](https://github.com/raj533125raj/chilli-gavva/actions/runs/41198138044),
[41199080601](https://github.com/raj533125raj/chilli-gavva/actions/runs/41199080601),
and [41201978492](https://github.com/raj533125raj/chilli-gavva/actions/runs/41201978492)
failed at **Run tests first** after the minutes-saving change omitted Poppler
from the test dependency installation. Pillow alone could not render PDF pages.
The first two cycle logs show eight launch-pack/product-delivery assertion
failures and repeated `listing images skipped ... FileNotFoundError` messages.

The second run checked out `b1955dd9987f52e608b8b4630a2531ba54cc7a69` and
restored cache `robot-failure-v1-41198138044-1`. The first run's finish log says
`temporary problem, will retry`, establishing `consecutive_failures: 1` and
`alerted: false` for the replay. With that state, a successful fake Telegram
sender, a fake GitHub token, and the exact eight failed test names from the log,
the old finish code increments to 2 and marks the alert delivered, but calls the
dispatcher zero times and prints nothing.

The precise blocker was `workflow_failure()`: it searched the entire summary for
the substring `workflow`. One failed product test was named
`test_split_workflow_confirms_encryption_then_inbox_retries_send`. That method
name falsely classified a product delivery failure as a workflow-file failure.
This predicate blocked dispatch regardless of the cached daily timestamp; no
GitHub request was attempted on that path. The absence of a dispatch was not
evidence of a GitHub API refusal. The guard now checks explicit workflow-file
markers, the setup/key steps, and workflow/parity test modules, so an incidental
word in a product method cannot block a repair. The regression test replays the
exact eight names and cached count, and confirms one accepted dispatch.

New summaries recognize the missing PDF renderer as `setup problem: pdftoppm`.
That setup failure correctly reports `Auto-fix: not started — setup/workflow
problem needs a human`, rather than silently suppressing a repair. Product tests
requiring real images now skip unless `mockups.available()`; policy, gating,
failure, and fake-image tests still run without Poppler. `tests/test_ci_parity.py`
guards the existing Pillow/Poppler installation before every full suite. It
rejects removal of either dependency or a product-only installation condition.
No workflow changes are part of this repair.

Repair verification: the full `python3 -m unittest discover -s tests` suite passed
with Pillow and Poppler available (1,173 tests, 1 skipped), then with a PATH of
normal-tool symlinks excluding both `pdftoppm` and `pdftocairo` (1,173 tests,
27 skipped). The second environment verified that neither renderer was found and
`mockups.available()` was false. Encrypted products/database, scheduler locks,
and dashboard status were unchanged.

## GitHub setup

- Put these changes on the repository's main branch before using auto-fix.
- Auto-fix prefers its separate `GEMINI_AUTOFIX_KEY`, falling back to `GEMINI_API_KEY`
  only when the separate secret is empty. The robot cycle uses `GEMINI_API_KEY`.
  Gemini quotas belong to projects; keys in the same project still share quota.
  Gemini tries `google/gemini-3.8-flash`, then `google/gemini-3.5-flash-lite`,
  then `google/gemini-flash-latest`, all with that same key route. These IDs were
  confirmed using the key-free `opencode models google` listing. A 503/server-busy
  response waits 20 seconds and retries that same model exactly once before moving
  on; other failure classes do not get that immediate retry. An authentication
  problem stops the same-key Gemini chain. Every attempt checks for a new safe
  remote proposal; an exit code alone is never success.
  The next provider is optional `NVIDIA_API_KEY`.
  `GROQ_API_KEY` remains last and is skipped for oversized full-context requests.
  Never paste a key into a conversation. Without the Gemini key, auto-fix stops safely.
- In Settings → Actions → General, allow GitHub Actions to create pull requests.
  The workflows use GitHub's automatic token; an OpenCode app is unnecessary.
- Enable squash merging. Existing branch protections still apply; the bot does
  not bypass them.

Auto-fix test jobs receive no production secrets. The notification and approval
code runs from main, and a workflow-completion event never checks out PR code
with write permissions or Telegram credentials.

Provider failures log only a fixed reason class: daily quota, per-minute rate
limit, server busy, timeout, request too large, or an authentication problem.
Daily quota requires explicit daily quota evidence; an unspecified 429 uses the
rate-limit class. The remote proposal check confirms "no change made". Raw
provider messages, keys, identifiers, and URLs are never copied into these lines.


## Auto-fix time budget (R44)

The fix job has a **24-minute hard cap**, below 25 minutes. Explicit fix-step
limits total 24 minutes: configuration/checkout/Python setup/request preparation
(1 minute each), OpenCode installation (2), Gemini chain (10), NVIDIA (2), optional
small Groq (2), three result checks (1 each), and final outcome (1).

Each Gemini attempt is capped at **70 seconds**; NVIDIA and Groq at **90 seconds**.
Process-group cleanup has at most 5 seconds for TERM and 1 for KILL/draining.
Each in-chain GitHub result check has a **10-second** cap and receives no provider
API keys. Six Gemini attempts plus three 20-second waits and all cleanup/checks
fit within 576 seconds, leaving 24 seconds inside its 10-minute step cap.
A verifier timeout/failure cannot count as success or claim that no change exists;
the bounded model order and single 503 retry still run. The workflow's independent
remote result check also runs after the chain. Uncertain checks do not classify
the whole chain as an all-busy result for a later automatic retry.
Only fixed safe model/reason/status lines reach the log; raw output is not saved.
