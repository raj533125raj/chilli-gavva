# Draft queue audit, 26 November

The live decrypted database is unavailable. The supplied report proves zero recent drafts and sixteen pending proposals, but cannot prove which runtime gate stopped a particular run. Tests reproduce that shape with nine genuine upload ZIPs and a fake clock.

| Reason or gate | Code place | Finding and fix |
| --- | --- | --- |
| Proposals counted as products | guardrails.scout_product, workforce.active_draft_backlog, scheduler._queue_full | BIG-7 already repairs structured Scout products and counts only eligible product drafts. Sixteen unstructured proposals allow the writer to make a new draft. Preserve this behavior. |
| False backlog claim | auto_approve.queue_digest | All proposals incorrectly set new_drafts_paused. Use workforce.active_draft_backlog and show proposals separately. |
| False reminder/list labels | telegram_inbox.remind_waiting_drafts, list_text; tg_talk.sell_card_data | All approvals are called drafts. Use the shared product/proposal split. |
| Old proposals stay forever | auto_approve._plan; tg_restore.handle | Archive unanswered non-product Scout proposals after 72 hours; never approve them. Allow explicit owner restore, resetting age. |
| AI unavailable | workforce.step_research/step_draft, llm_router.chat | Real absent answers raise AIUnavailable and create no fake draft; keep retry and provider fallback. The same writer creates a draft when AI works. |
| Daily AI budget / product reserve | guardrails.can_call_llm; workforce.product_call_reserve/run_once | Independent safety limits remain. No evidence the live budget was exhausted. Never remove owner limits to force a draft. |
| Daily plan already enqueued | workforce.ensure_daily_plan | One plan per day prevents flooding; niche recipe tasks have a separate queue. Preserve the safety rule. |
| Niche disabled / unsupported / exhausted | scheduler.run_slot, workforce.off_limits, niche_topics.fresh_tasks | Owner settings and off-limits topics stay enforced. Known supported budget-sheet topics reach the writer; exhausted pools request fresh topics. |
| Paused/manual/killed/rest/disabled agent | guardrails.automation_allowed, workforce.run_once/claim_next, scheduler.run_now | Intentional owner and schedule gates remain. No evidence of live state is available. |
| Old parser / council outage | product_builder parsing, product_pipeline, council_approved | Claude CC-BIG-1 parser and BIG-8 waiting-AI changes are already present; these affect building approved drafts, not the product/proposal pending cap. |
