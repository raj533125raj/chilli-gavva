# Keep the worker running without typing anything

Three ways, from simplest to most automatic. Only the first two need nothing from you beyond one command.

1. One command, everything together: `python3 start.py` starts the worker in the background plus the dashboard (http://127.0.0.1:8787). Ctrl+C stops both.
2. Worker only: `python3 worker_daemon.py`. Only one worker can run at a time (`worker.lock`). The dashboard shows "Worker: running" or "stopped".
3. Start at login on your Mac (optional, you do this once; untested on a real Mac):
   - Run `which python3` and note the path.
   - Copy `deploy/com.chilligavva.worker.plist` to `~/Library/LaunchAgents/`, replace `PYTHON_PATH` and `PROJECT_PATH` inside it.
   - Run `launchctl load ~/Library/LaunchAgents/com.chilligavva.worker.plist`.
   - Stop it with `launchctl unload ~/Library/LaunchAgents/com.chilligavva.worker.plist`.

Limits: a local worker only runs while the Mac is on and logged in. For 24/7 with the Mac off, use the cloud workflow in docs/CLOUD_SETUP.md.

What the worker does by itself every pass (default every 30 s, slows to 2 min when idle):
- obeys the kill switch (`python3 guardrails.py kill`),
- puts back tasks left half-done by a crash,
- plans the day's drafts once per day (default 2, config `daily_drafts`, never more than 10 waiting for approval),
- runs queued tasks (research, draft, review),
- promotes a better prompt after enough trials and creates a new challenger prompt,
- refreshes the dashboard snapshot and writes a heartbeat.
Nothing is published: drafts wait for your approval.
