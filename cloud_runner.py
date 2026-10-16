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
    elif rest_window.in_rest_window():
        print(rest_window.rest_tasks())
        result = {"status": "rest", "ran": 0, "failed": 0}
    else:
        result = workforce.run_once(max_tasks=3)
    export_status.write_status()
    print(result)
    return result


if __name__ == "__main__":
    main()
