# Robot fix runbook

The robot retries one failed run quietly. If the next run also fails, it sends one
message with the failed step, a safe problem summary, and a link to the run.
A successful run resets the count. Further failures in the same streak do not
send the same alert again once it has been delivered. If delivery fails, the next
failed run tries to deliver it again.

When auto-fix is configured, the second failure also starts a repair proposal.
Only one attempt can start in a day, including across successful runs. Workflow
file failures go straight to this runbook because the AI cannot edit workflows.
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
save failure. Only the count, delivery status, and last auto-fix attempt time are
cached; private logs and the database are excluded.
If the cache is unavailable or has been removed, a new streak starts at zero.

## GitHub setup

- Put these changes on the repository's main branch before using auto-fix.
- In Settings → Secrets and variables → Actions, add `NVIDIA_API_KEY` yourself.
  Never paste the value into a conversation. Without it, auto-fix stops safely.
- In Settings → Actions → General, allow GitHub Actions to create pull requests.
  The workflows use GitHub's automatic token; an OpenCode app is unnecessary.
- Enable squash merging. Existing branch protections still apply; the bot does
  not bypass them.

Auto-fix test jobs receive no production secrets. The notification and approval
code runs from main, and a workflow-completion event never checks out PR code
with write permissions or Telegram credentials.
