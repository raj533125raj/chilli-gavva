# Robot fix runbook

The robot retries one failed run quietly. If the next run also fails, it sends one
message with the failed step, a safe problem summary, and a link to the run.
A successful run resets the count. Further failures in the same streak do not
send the same alert again once it has been delivered. If delivery fails, the next
failed run tries to deliver it again.

When auto-fix is configured, the second failure also starts a repair proposal.
Only one new problem can start in 24 hours, including across successful runs. Workflow
file failures go straight to this runbook because the AI cannot edit workflows.
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

## GitHub setup

- Put these changes on the repository's main branch before using auto-fix.
- Auto-fix prefers its separate `GEMINI_AUTOFIX_KEY`, falling back to `GEMINI_API_KEY`
  only when the separate secret is empty. The robot cycle uses `GEMINI_API_KEY`.
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
