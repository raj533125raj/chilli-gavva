# Chilli Gavva Agent

Zero-cost autonomous agent project (Python + SQLite + Tkinter dashboard).

**Start here:** read `STATUS.md` first. It holds the current state, goals, known issues and next steps.

## Layout
Read `STATUS.md` for the full table. Short version:
- `workforce.py` (agent team), `guardrails.py` (kill switch, budget cap, approvals), `improver.py` (safe self-improvement), `engine.py` (old simulation + LLM call)
- `serve.py` + `web/index.html`: local 2D office dashboard; `dashboard.py`: old Tkinter dashboard
- `cloud_runner.py` + `.github/workflows/agent.yml`: 24/7 cloud cycle (optional)
- `versions.py` (rollback), `log_validation.py` (14-day test), `tests/` (run: `python3 -m unittest discover -s tests`)
- `agent_data.db`: SQLite database. `config.json`: local settings, git-ignored, never commit.
- `docs/`: research, plans, setup guides. `docs/memory_2026-10-02/` and `archive/` are historical and outdated.

## Run
- Dashboard: `python3 serve.py` then open http://127.0.0.1:8787
- Agent cycle: `python3 workforce.py run`
- Emergency stop: `python3 guardrails.py kill`
