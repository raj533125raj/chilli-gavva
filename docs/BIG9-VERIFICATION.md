# BIG-9 verification

`scripts/ci_parity.sh` completed successfully against source commit `6bc06ab`
on branch `robot-cx9`, using Python 3.11, the CI dependencies, and the sole test
checkout `/private/tmp/chilli-ci-parity/repo`.

The complete suite reported:

```text
Total tests run: 9195 | Failures: 0 | Errors: 0 | Skipped: 18 | Time: 974.09s
```

Its last five lines were:

```text
   96.57s - chunk_93_test_cxap_buyers
   96.08s - chunk_126_test_cxn_builder_listings
----------------------------------------------------------------------
RESULT: PASSED
======================================================================
```

All 95 clock-sensitive modules also passed independently at each required UTC
time:

| UTC time | Tests | Failures | Errors | Skipped | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 19:45 | 2426 | 0 | 0 | 7 | OK |
| 01:15 | 2426 | 0 | 0 | 7 | OK |
| 07:00 | 2426 | 0 | 0 | 7 | OK |

The runner finished with:

```text
REAL PACKS RETAINED: 2 valid buyer ZIPs and listings
CI PARITY: PASS
```

`git ls-files .jobs` returned no paths. All 17 files in the protected snapshot
had unchanged hashes. The diff from the BIG-9 starting commit was empty for
`agent_data.db.enc`, `products.tar.gz.enc`, `web/data/status.json`,
`.scheduler_locks`, and `.github/workflows/agent.yml`. The frozen Office app was
not edited. The only workflow change belongs to CXCE-2: manual owner-confirmed
Pages publication, with a read-only setup check and an explained successful skip
when setup is missing.

The owner-day integration check used real buyer packs with fake Telegram and
network transport. Its complete reply table and per-task test output are recorded
in the local, untracked `.jobs/PROGRESS.md`.

Final proof and summary commits add documentation only; runtime and test source
remain at the fully tested `6bc06ab`. All commits stay local. Nothing was pushed
or published.
