"""One cycle for scheduled cloud runs (GitHub Actions) or a cron job.

Order: respect the kill switch, run a few queued tasks (queued by the niche agent
schedule), write the dashboard snapshot. The old generic daily plan was removed so
only earning work runs. It never runs the old simulated campaign cycle, so it
cannot fill the database with fake revenue every few minutes, and it deletes old simulated rows
(fake money and logs) so the dashboard shows real money only.
"""
import guardrails
import workforce
import export_status


def main():
    try:  # half-written state files and clock jumps: one owner line each, the run goes on
        import state_guard
        state_guard.run()
    except Exception as exc:
        print(f"State guard skipped: {type(exc).__name__}")
    removed = export_status.purge_simulated()
    if removed:
        print("removed old simulated rows:", removed)
    try:  # drafts scored before the buyer checklist get a checklist score (status never changes)
        import qa_checklist
        rescored = qa_checklist.rescore_old_reviews()
        if rescored:
            print("QA checklist re-scored drafts:", rescored)
    except Exception as exc:
        print(f"QA re-score skipped: {type(exc).__name__}")
    import rest_window
    if guardrails.is_killed():
        result = {"status": "killed", "ran": 0, "failed": 0}
    elif not guardrails.automation_allowed():
        result = {"status": "manual", "ran": 0, "failed": 0}
    elif rest_window.in_rest_window():
        print(rest_window.rest_tasks())
        result = {"status": "rest", "ran": 0, "failed": 0}
    else:
        result = workforce.run_once(max_tasks=15)
        try:
            import scout
            scout.maybe_run()
        except Exception as exc:
            print(f"SCOUT skipped: {type(exc).__name__}")
        try:
            import trend
            trend.maybe_run()
        except Exception as exc:
            print(f"TREND skipped: {type(exc).__name__}")
        try:
            import marketing_agency
            marketing_agency.process_ready_products()
        except Exception as exc:
            print(f"Marketing agency skipped: {type(exc).__name__}")
    _tell_owner_if_ai_down()
    export_status.write_status()
    print(result)
    return result


def _tell_owner_if_ai_down():
    """This run asked the AI and not one provider answered: one owner line per UTC day (workforce's own
    AI-down alert, which also waits for Telegram). Scout/trend/marketing failures alone were silent before."""
    try:
        import sys
        router = sys.modules.get("llm_router")
        outcomes = router.process_outcomes() if router and hasattr(router, "process_outcomes") else {}
        if outcomes.get("failed") and not outcomes.get("ok"):
            print("AI down: no provider answered this run; owner told (once a day).")
            workforce._alert_ai_down()
    except Exception as exc:
        print(f"AI down notice skipped: {type(exc).__name__}")


if __name__ == "__main__":
    main()
