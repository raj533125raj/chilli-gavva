"""One cycle for scheduled cloud runs (GitHub Actions) or a cron job.

Order: respect the kill switch, plan at most once per day, run a few queued tasks,
write the dashboard snapshot. It never runs the old simulated campaign cycle, so it
cannot fill the database with fake revenue every few minutes.
"""
import guardrails
import workforce
import export_status


def main():
    if guardrails.is_killed():
        result = {"status": "killed", "ran": 0, "failed": 0}
    else:
        workforce.ensure_daily_plan()
        result = workforce.run_once(max_tasks=3)
    export_status.write_status()
    print(result)
    return result


if __name__ == "__main__":
    main()
