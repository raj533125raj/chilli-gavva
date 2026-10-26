# Robot fix runbook

The robot retries one failed run quietly. If the next run also fails, it sends one
message with the failed step, a safe problem summary, and a link to the run.
A successful run resets the count. Further failures in the same streak do not
send the same alert again once it has been delivered. If delivery fails, the next
failed run tries to deliver it again.

1. Open Codex with this robot repository.
2. Paste the complete alert and ask: "Fix this failure, run the full tests twice,
   and commit the fix. Never push or print secrets. Stop if a login is needed."
3. Review Codex's result. Keep passwords, keys, tokens, and private account details
   out of the conversation.

The counter is saved separately in the workflow cache, including after a tests or
save failure. Only the count and delivery status are cached; private logs and the
database are excluded.
If the cache is unavailable or has been removed, a new streak starts at zero.
