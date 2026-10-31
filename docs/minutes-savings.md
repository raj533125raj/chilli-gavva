# Agent-cycle minutes savings

These are planning estimates, not measured GitHub runner timings. The reported
monthly usage is about 1,650 of 2,000 included minutes. Telegram polling cadence,
the Cloudflare alarm, workflow triggers and the Mac-worker gate are unchanged.

| Run type | Before, minutes/run | After, minutes/run | Estimated saving |
| --- | ---: | ---: | ---: |
| Scheduled, unchanged code, no approved build | 3–5 | 1–2 | 2–3 |
| Manual/dispatch or changed code, no approved build | 3–5 | 2–4 | About 1 |
| Approved product build | 4–8 | 4–8 | 0–0.5 from warm pip downloads |

Assumptions: the full suite costs roughly 1–2 minutes on a hosted Linux runner,
dependency setup costs roughly 0.5–1.5 minutes, and ordinary checkout, bot work,
inbox handling, export and safe persistence cost roughly 1–2 minutes. Model calls
and product complexity can move actual results outside these ranges. The local
full suite with Pillow took about two minutes before this workflow change; that
is a development reference, not a hosted-runner measurement. Cache overhead and
GitHub billing rounding also affect realized savings.

The backup cron has eight opportunities per day. If all 240 backup runs in a
30-day month actually execute, have unchanged code and no builds, the first row
implies about 480–720 minutes saved. This is an illustrative upper scenario,
not a forecast against the monthly total: the existing gate may already skip
backup runs. The frequent Cloudflare alarm uses `workflow_dispatch`, which
continues running the full suite. Its no-build runs benefit only from avoiding
system-package installation and reusing pip downloads. Measure the actual mix
before applying a monthly saving to the reported 1,650 minutes.

## What changed

- `setup-python` caches pip downloads using the hash of
  `.agent-image-requirements.txt`. The workflow writes that requirements file
  before setup and pins Pillow to the existing version. No dependency manifest
  or private state needs to be committed. Pillow is still installed for every
  full test run, retaining the Pillow-dependent test coverage.
- Poppler and URW fonts are installed only when the post-Telegram probe finds
  an approved niche draft eligible for building, with automation and the product
  cube enabled. The probe uses the builder's own queue selection, including
  retries and existing-folder reconciliation. It runs after inbox handling so
  new approvals receive images immediately. Installed system packages are reused
  when both are already present; otherwise only missing packages are installed.
  The builder still runs every active cycle to retain all its bookkeeping.
- Only `schedule` events can skip tests. The workflow compares the actual
  checkout SHA with a cached SHA from a completed green cycle. Known encrypted
  state, public status, scheduler locks, Markdown documentation and README changes
  do not require retesting; all other tracked changes do, including tests,
  recipes, configuration, workflows and unfamiliar file types. Missing caches,
  invalid SHAs, unavailable history and comparison errors all run the suite.
  Manual, alarm/doorbell dispatch and any push event never use this exemption.
- The baseline is advanced only after a successful active cycle and a successful
  suite or an explicit unchanged-code decision. It records the checkout SHA,
  rather than the trigger SHA, because queued runs check out the newest main.
  Baseline-cache failures cannot suppress testing or fail otherwise useful work.
- Existing job limits remain 12 minutes for the main cycle, six for recovery
  inbox and three for auto-fix result handling. Explicit step limits now bound
  checkout/setup, tests, bot work, inbox, builds, encryption and persistence.
  These are stall limits, not claimed normal-run savings. The nine-minute agent
  step leaves headroom above the worker's existing eight-minute work budget.

The secret guard, database/product encryption, `save_check`, every existing
`run_failure.py` wrapper, the recovery inbox and encrypted-state save flow remain.
After full tests, tracked encrypted fixtures and scheduler locks are restored
before decrypting the real database; test-created product/lock files are cleaned
as before. No auto-fix workflow file is changed.

GitHub's [setup-python caching documentation](https://github.com/actions/setup-python#caching-packages-dependencies)
explains the requirements-file cache key and why installing packages is still
necessary. The [cache restore documentation](https://github.com/actions/cache/blob/main/restore/README.md)
describes restoring the most recent matching baseline cache. Step and job bounds
use the documented [workflow timeout settings](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepstimeout-minutes).
