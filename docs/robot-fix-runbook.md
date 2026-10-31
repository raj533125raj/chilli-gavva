# Robot fix runbook

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
