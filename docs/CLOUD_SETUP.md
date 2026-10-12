# Cloud 24/7 setup (GitHub Actions)

Optional. Everything works locally without this. Steps that need YOUR account are marked (you).

1. (you) Create a GitHub account and a new repository. Public repos get free Actions minutes; the private-repo free quota is not stated on the docs page, so check your plan.
   Warning: `agent_data.db` is committed by the workflow. In a PUBLIC repo the draft text and activity log become public. No keys are stored in it. Use a private repo if you do not want that.
2. (you) Push this project: `git remote add origin <repo url>` then `git push -u origin main`. (`config.json` is git-ignored and is not pushed.)
3. (you) Repo Settings -> Secrets and variables -> Actions -> New secret named `GROQ_API_KEY` with your Groq key.
4. (you) Settings -> Actions -> General -> Workflow permissions: Read and write.
5. (you) Actions tab -> `agent-cycle` -> Run workflow once to test.

What the workflow does every 30 minutes (at :17 and :47): runs the tests, runs one cycle (`cloud_runner.py`), commits `agent_data.db` and `web/data/status.json` if they changed.

Known limits (from GitHub docs, checked 2026-10-13)
- Schedules can be delayed or dropped under load: "near 24/7", not exact.
- In a public repo, scheduled workflows are disabled after 60 days with no repository activity. Unverified whether the bot's own commits count as activity; if the schedule stops, re-enable it in the Actions tab.
- Scheduled runs only use the default branch.

Emergency stop in the cloud: commit a file named `KILL_SWITCH` to the default branch (or set the environment variable `AGENT_PAUSED=1` in the workflow). Delete the file to resume.

Dashboard in the cloud: `web/` is static. Host it on Cloudflare Pages (free, 500 builds/month) or GitHub Pages for the non-commercial test only (Pages forbids commercial sites). The static page is read-only; chat and buttons need `python3 serve.py` on your computer.
