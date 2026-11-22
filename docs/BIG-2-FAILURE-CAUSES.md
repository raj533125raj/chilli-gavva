# BIG-2 failure causes

The saved BIG-1 run at 5204bc3 ran 8620 tests with 175 failures and 24 errors. This table groups causes, including overlapping failures caused by invalid shared pack fixtures. A new merged-head run will measure the remaining failures. No checks are removed or hidden.

| Cause | Affected modules | Fix type |
| --- | --- | --- |
| Builder listings do not meet buyer-information, keyword, title, tags and gallery requirements | product_builder, upload_pack, builder modules; test_ccc_sell_proof, test_ccg5_worksheets, test_occ_sell_flow_e2e, test_ocf_owner_journey, test_och_niche_sweep | 1: produce complete truthful listings and previews from actual built files |
| Shared old ready-pack fixtures have short titles, thirteen tags or two previews | test_cc1_sell, test_cca1_sell_images, test_cca4_sell_card, test_ccb3_bundle, test_cch2_pins, test_cch3_carousel, test_cch4_post_plan, test_listing_lint, test_o17_lint_fix, test_s2_seo_score, test_w5b_ready | 2: make valid fixtures meet current gate, preserving negative checks |
| Old Telegram equality, page position and next-action expectations | test_cc1_talk, test_cc1_telegram_e2e, test_ccg2_product_path; nine FOR CLAUDE CODE expected failures | 1 where routing/private transport is wrong; 2 where only wording changed |
| Pin and carousel generators/checkers disagree on required visible CTA, alt text or link | pin_ready, carousel_maker; test_cch2_pins, test_cch3_carousel, test_cch4_post_plan | 1: generate valid real media; 2 only for outdated fixtures |
| Canonical public amount rejection changed error text | test_listing_fields, test_o15_bundle_fields | 2: retain refusal/exit-code checks with current message |
| Owner reports: Sunday private payout, morning queue, rejection reason, transport | daily_report, telegram_inbox; test_daily_report, test_urgent_telegram, test_och_secret_sweep, test_cca4_sell_card | 1: allow amounts only on authenticated private transport; 2 for new report text |
| Runtime database documented as if missing tracked source | test_och_public_docs | 2: Claude merged runtime-file exception; real source references still checked |
| Repository fixture enumeration and Python native shutdown | test_cc2_repo_clean, test_cca4_sell_card | Diagnose environment and fix genuine cleanup/runtime faults; never suppress checks |
| Case-insensitive filesystem resolves launch.md as LAUNCH.md | marketing_index; test_t5c_index | 1: use actual directory entry spelling; prove Linux logic too |
| Slow real-product sweeps | tests/real_pack_cache.py, test_och_niche_sweep, tools/run_tests_chunked.py | Claude caching merged with one-module isolation; validate all checks including three-buyer sweep |

Type 1 changes product behaviour. Type 2 updates an outdated check or its fixture while retaining the behaviour and negative coverage. Final acceptance requires a complete clean-environment Python 3.11 run.
