# Synthetic Office Fixtures for Binny Mills

This directory documents the synthetic office snapshot fixtures used to test the Binny Mills / Binny-JG robot office interface at scale.

## Overview

The fixture generator (`scripts/make_office_fixture.py`) creates deterministic, synthetic office projections that conform strictly to the schema in `office_protocol.py`.

It enables scale testing of the Binny Mills office viewer with large cube networks and high agent counts up to 40+ teams and 6000 total agents, without requiring active backend databases or leaking proprietary internal data.

## Safety & Compliance Guarantees

1. **Marked Synthetic**: Every record (snapshot root, teams, captains, sub rooms, sub agents, main agents, approvals, counters) contains `"synthetic": true`.
2. **Fake Names Only**: Agent and department names are procedurally synthesized using fictional roles, names, and team identities.
3. **No Sensitive Data**: No real credentials, API tokens, email addresses, or internal project prompts are included.
4. **No Currency Values**: Adheres strictly to the project rule prohibiting currency amounts.
5. **No Production Overwrite**: Script guards prevent writing into `web/data` or modifying production runtime databases. Output defaults to `/tmp`.

## Supported Sizes

| Size Preset | Teams (Cubes) | Main Agents | Sub Agents | Total Agents | Approvals |
| ----------- | ------------- | ----------- | ---------- | ------------ | --------- |
| `small`     | 6             | 24          | 0          | 30           | 6         |
| `medium`    | 15            | 250         | 135        | 400          | 15        |
| `full`      | 40            | 3500        | 2460       | 6000         | 40        |

- **Small**: 6 cubes and 30 total agents (24 main agents + 6 captains).
- **Medium**: 15 cubes, 250 main agents, 15 captains, and 135 sub-agents (400 total agents).
- **Full**: 40 cubes, 3500 main agents, 40 captains, and 2460 sub-agents (6000 total agents).

## Feature Coverage

- **Honest Statuses**: All 11 statuses defined in `office_protocol.HONEST_STATUSES` are represented:
  - `working`
  - `thinking`
  - `just finished`
  - `needs you`
  - `waiting`
  - `off duty with next shift` (includes explicit `next_shift` ISO timestamps)
  - `idle`
  - `warning`
  - `error`
  - `paused`
  - `not built yet`
- **Levels & Hierarchy**: Supports `captain`, `main`, and `sub` agents organized under cubes and sub rooms.
- **Approvals & 24h Auto-Decision Rule**: Pending approval items feature elapsed wait times sampled before, at, and after the 24-hour decision boundary (e.g., 0.5h, 12.5h, 23.5h, 23.9h, 24.0h, 24.2h, 48.0h) with corresponding `auto_decision_due` deadlines.
- **Counters**: Pre-calculated snapshot metrics for active/running agents, items needing owner attention, drafts waiting, and AI provider health.

## Usage

### Command Line

```bash
# Generate small fixture to /tmp/synthetic_office_small.json
python3 scripts/make_office_fixture.py --size small --out-dir /tmp

# Generate medium fixture
python3 scripts/make_office_fixture.py --size medium --out-dir /tmp

# Generate full fixture (40 teams, 3500 main agents, 6000 total agents)
python3 scripts/make_office_fixture.py --size full --out-dir /tmp

# Custom seed and office ID
python3 scripts/make_office_fixture.py --size full --seed 123 --office-id custom-synthetic-office --out-dir /tmp
```

### Python API

```python
from scripts.make_office_fixture import generate_office_fixture

# Generate in-memory fixture validated against office_protocol
snapshot = generate_office_fixture(size="full", seed=42)
```
