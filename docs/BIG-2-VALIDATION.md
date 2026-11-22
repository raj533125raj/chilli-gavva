# BIG-2 verification

PATH-N source: 8afca1d. Clean Python 3.11 environment, empty temporary home containing only the installed Pillow package, CHILLI_TEST=1, four isolated workers, one module per chunk. Local HTTP tests had localhost permission. No network credentials were inherited.

Full CI command: python3.11 tools/run_tests_chunked.py

Total tests run: 8497 | Failures: 0 | Errors: 0 | Skipped: 18 | Time: 319.39s
RESULT: PASSED

All 302 chunks passed. No Mac exception is needed. The former native shutdown crash is fixed by importing the timezone module before it can be repeatedly reloaded inside temporary module snapshots.

Slowest five chunks:
- test_ocg_all_commands_real_packs: 112.91 seconds
- test_och_secret_sweep: 112.63 seconds
- test_ccf_autofix_drill: 78.14 seconds
- test_cce_robot_day: 71.38 seconds
- test_och_niche_sweep: 61.76 seconds

The separate CHILLI_SLOW_TESTS=1 sweep tested all three buyers for every product type: Ran 230 tests in 203.046s; OK. No old failure was hidden behind a skip or an expected-failure marker. All nine BIG-1 protected reply markers and the obsolete private weekly-payout marker were removed; the existing optional/platform skips remain visible in the full count.

Evidence: /private/tmp/cxn-full-8afca1d.log and /private/tmp/cxn-three-buyers.log. Nothing was pushed.

Final PATH-Q source: 6459921. A fresh shared clone and a fresh empty temporary home containing only the installed Pillow package were used. The environment was cleared with env -i; PATH, LANG=C.UTF-8, TZ=UTC, CHILLI_TEST=1 and PYTHONUNBUFFERED=1 were set explicitly. Localhost permission covered the local HTTP tests.

Final full CI command: python3.11 tools/run_tests_chunked.py

Total tests run: 8522 | Failures: 0 | Errors: 0 | Skipped: 18 | Time: 282.30s
RESULT: PASSED

All 306 chunks passed. No Mac-only exception is needed. The 18 existing skips are unchanged; no test was skipped or marked expected-failure to hide a BIG-2 failure. All new button and marketing tests ran.

Final slowest five chunks:
- 99.26s - chunk_168_test_ocg_all_commands_real_packs
- 99.21s - chunk_172_test_och_secret_sweep
- 67.52s - chunk_39_test_ccf_autofix_drill
- 62.57s - chunk_37_test_cce_robot_day
- 54.32s - chunk_170_test_och_niche_sweep

Final five log lines (verbatim):
```
   36.30s - chunk_25_test_ccb1_theme_images
   35.02s - chunk_44_test_ccg5_worksheets
----------------------------------------------------------------------
RESULT: PASSED
======================================================================
```

Evidence: /private/tmp/cxq-full-6459921.log. Product code stayed unchanged after this run; the remaining commits record verification and the completion summary. The owner must release the robot code and redeploy the Worker using docs/BUTTONS-FIX-TENGLISH.md. Nothing was pushed or deployed.
