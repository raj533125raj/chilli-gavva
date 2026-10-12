# Chilli Gavva Agent

Zero-cost autonomous agent project (Python + SQLite + Tkinter dashboard).

**Start here:** read `STATUS.md` first. It holds the current state, goals, known issues and next steps.

## Layout
- `engine.py`: backend engine (EnterpriseStorage, research engine, CEO/CMO/CTO/CFO agents). Currently a SIMULATION, see STATUS.md.
- `dashboard.py`: Tkinter desktop dashboard. Runs the engine cycle and shows real vs simulated numbers.
- `versions.py`: version list / known-good tags / one-click-style rollback (dry run by default).
- `docs/VALIDATION_PLAN.md`: two-week $0 test plan.
- `log_validation.py`: daily validation numbers and report.
- `docs/GROQ_SETUP.md`: free LLM key setup.
- `run_engine.py`: one-click runner for the engine (`python3 run_engine.py`).
- `agent_data.db`: SQLite database (campaigns, research_discoveries, agent_logs, health_reports).
- `config.json`: local settings and API keys. Git-ignored. Never commit it.
- `docs/memory_2026-10-02/`: older handover notes from earlier AI sessions. Their paths and claims are OUTDATED (old paths: /Users/vudathagroup/ and ~/Downloads). Trust STATUS.md over these.
- `archive/`: old copies kept for reference only. Do not run or import.

## Run
- Engine: `python3 run_engine.py`
- Dashboard: `python3 dashboard.py`
