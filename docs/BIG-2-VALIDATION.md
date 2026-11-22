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
